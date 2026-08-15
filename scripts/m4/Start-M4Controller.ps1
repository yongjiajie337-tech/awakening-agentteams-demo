[CmdletBinding()]
param(
    [string]$EnvPath = "tmp/m4/controller.env",
    [int]$ReadyTimeoutSeconds = 240
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-M4Executable {
    param([Parameter(Mandatory = $true)][string]$Name)

    $relative = if ($Name -eq "docker") {
        "Programs\DockerDesktop\resources\bin\docker.exe"
    }
    elseif ($Name -eq "docker-compose") {
        "Programs\DockerDesktop\resources\bin\docker-compose.exe"
    }
    else {
        throw "M4_RUNTIME_EXECUTABLE_UNSUPPORTED"
    }
    $candidate = Join-Path $env:LOCALAPPDATA $relative
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        throw ("M4_RUNTIME_EXECUTABLE_MISSING:" + $Name)
    }
    return [System.IO.Path]::GetFullPath($candidate)
}

function Read-M4Env {
    param([Parameter(Mandatory = $true)][string]$Path)

    $values = @{}
    foreach ($line in Get-Content -LiteralPath $Path -Encoding utf8) {
        if ([string]::IsNullOrWhiteSpace($line) -or $line.TrimStart().StartsWith("#")) {
            continue
        }
        $index = $line.IndexOf("=")
        if ($index -le 0) {
            throw "M4_CONTROLLER_ENV_INVALID_LINE"
        }
        $key = $line.Substring(0, $index)
        if ($values.ContainsKey($key)) {
            throw ("M4_CONTROLLER_ENV_DUPLICATE_KEY:" + $key)
        }
        $values[$key] = $line.Substring($index + 1)
    }
    return $values
}

$workspace = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$composePath = Join-Path $workspace "infra\agentteams\m4\controller.compose.yaml"
$envFullPath = [System.IO.Path]::GetFullPath((Join-Path $workspace $EnvPath))
$runtimeRoot = [System.IO.Path]::GetFullPath((Join-Path $workspace "tmp\m4"))
if (-not $envFullPath.StartsWith($runtimeRoot + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "M4_CONTROLLER_ENV_PATH_OUTSIDE_RUNTIME_DIR"
}
$envItem = Get-Item -LiteralPath $envFullPath -Force -ErrorAction Stop
if ($envItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
    throw "M4_CONTROLLER_ENV_REPARSE_POINT"
}

$values = Read-M4Env -Path $envFullPath
$required = @(
    "M4_CONTROLLER_IMAGE",
    "M4_MANAGER_IMAGE",
    "M4_WORKER_IMAGE",
    "M4_ADMIN_PASSWORD",
    "M4_MANAGER_PASSWORD",
    "M4_REGISTRATION_TOKEN",
    "M4_MINIO_PASSWORD",
    "M4_MANAGER_GATEWAY_KEY"
)
foreach ($key in $required) {
    if (-not $values.ContainsKey($key) -or [string]::IsNullOrWhiteSpace([string]$values[$key])) {
        throw ("M4_CONTROLLER_ENV_REQUIRED_KEY_MISSING:" + $key)
    }
}
foreach ($forbidden in @("HICLAW_LLM_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")) {
    if ($values.ContainsKey($forbidden)) {
        throw ("M4_PROVIDER_SECRET_FORBIDDEN_IN_CONTROLLER_ENV:" + $forbidden)
    }
}

$expectedImages = [ordered]@{
    controller = "sha256:5486f4643a04a3a7a4dd81cd7f1d6091f9b7db3a5446bd5676f567c857910978"
    manager = "sha256:3a77482fb11472ab05f85ba5d60cbc0df8d66046aa9f63b9cf99f16d87852921"
    worker = "sha256:d1078b42115ec2ea4eeaac507bc63352812291ff6e2406e813863161f074fb0b"
}
$docker = Resolve-M4Executable -Name "docker"
$compose = Resolve-M4Executable -Name "docker-compose"
$anonymousConfig = Join-Path $workspace "tmp\m4\docker-config-anonymous"
if (-not (Test-Path -LiteralPath (Join-Path $anonymousConfig "config.json") -PathType Leaf)) {
    throw "M4_ANONYMOUS_DOCKER_CONFIG_MISSING"
}
$previousDockerConfig = $env:DOCKER_CONFIG
try {
    $env:DOCKER_CONFIG = $anonymousConfig
    foreach ($name in $expectedImages.Keys) {
        $reference = [string]$values["M4_" + $name.ToUpperInvariant() + "_IMAGE"]
        $actual = @(& $docker image inspect --format "{{.Id}}" $reference 2>$null)
        if ($LASTEXITCODE -ne 0 -or $actual.Count -ne 1 -or $actual[0] -cne $expectedImages[$name]) {
            throw ("M4_RUNTIME_IMAGE_ID_MISMATCH:" + $name)
        }
    }

    & $compose --project-name awakening-m4 --env-file $envFullPath -f $composePath up -d --no-recreate --pull never controller
    if ($LASTEXITCODE -ne 0) {
        throw ("M4_CONTROLLER_COMPOSE_UP_FAILED:" + $LASTEXITCODE)
    }

    $deadline = [DateTime]::UtcNow.AddSeconds($ReadyTimeoutSeconds)
    $health = ""
    do {
        $healthLine = @(& $docker inspect --format "{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}" awakening-m4-controller 2>$null)
        if ($LASTEXITCODE -eq 0 -and $healthLine.Count -eq 1) {
            $health = [string]$healthLine[0]
        }
        if ($health -ceq "healthy") {
            break
        }
        Start-Sleep -Seconds 3
    } while ([DateTime]::UtcNow -lt $deadline)
    if ($health -cne "healthy") {
        throw ("M4_CONTROLLER_NOT_HEALTHY:" + $health)
    }

    $mounts = @(& $docker inspect --format "{{range .Mounts}}{{println .Destination}}{{end}}" awakening-m4-controller 2>$null)
    if ($LASTEXITCODE -ne 0 -or $mounts -contains "/var/run/docker.sock") {
        throw "M4_CONTROLLER_DOCKER_SOCKET_PRESENT"
    }
    $imageId = @(& $docker inspect --format "{{.Image}}" awakening-m4-controller 2>$null)
    if ($LASTEXITCODE -ne 0 -or $imageId.Count -ne 1 -or $imageId[0] -cne $expectedImages.controller) {
        throw "M4_CONTROLLER_LIVE_IMAGE_ID_MISMATCH"
    }
    $networkMode = @(& $docker inspect --format "{{.HostConfig.NetworkMode}}" awakening-m4-controller 2>$null)
    if ($LASTEXITCODE -ne 0 -or $networkMode.Count -ne 1 -or $networkMode[0] -cne "awakening-m4-net") {
        throw "M4_CONTROLLER_NETWORK_MISMATCH"
    }

    Write-Output "M4_CONTROLLER_START=PASS"
    Write-Output "M4_CONTROLLER_HEALTH=healthy"
    Write-Output "M4_CONTROLLER_IMAGE_ID=$($imageId[0])"
    Write-Output "M4_CONTROLLER_NETWORK=awakening-m4-net"
    Write-Output "M4_CONTROLLER_DOCKER_SOCKET=false"
    Write-Output "M4_PROVIDER_KEY_PRESENT=false"
}
finally {
    $env:DOCKER_CONFIG = $previousDockerConfig
}
