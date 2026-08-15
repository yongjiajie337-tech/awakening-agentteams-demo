#requires -Version 5.1

[CmdletBinding()]
param(
    [int]$ReadyTimeoutSeconds = 90
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$workspace = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$docker = Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\resources\bin\docker.exe"
$dockerConfig = Join-Path $workspace "tmp\m4\docker-config-anonymous"
$markerDirectory = Join-Path $workspace "tmp\m4\postgres"
$markerPath = Join-Path $markerDirectory "started-by-m4.json"
$expectedName = "awakening-m1-068642ac363b-postgres-1"
$expectedImageId = "sha256:9a70e4d1c03a5066080292db2dd95ee3965d3651316e21989fa0935afb8ce8ca"
$expectedProject = "awakening-m1-068642ac363b"

if (-not (Test-Path -LiteralPath $docker -PathType Leaf)) {
    throw "M4_POSTGRES_DOCKER_EXECUTABLE_MISSING"
}
if (-not (Test-Path -LiteralPath (Join-Path $dockerConfig "config.json") -PathType Leaf)) {
    throw "M4_ANONYMOUS_DOCKER_CONFIG_MISSING"
}

$previousDockerConfig = $env:DOCKER_CONFIG
try {
    $env:DOCKER_CONFIG = $dockerConfig
    $ids = @(& $docker ps -a --filter "label=com.docker.compose.service=postgres" --format "{{.ID}}")
    if ($LASTEXITCODE -ne 0 -or $ids.Count -ne 1) {
        throw "M4_POSTGRES_CONTAINER_IDENTITY_NOT_UNIQUE"
    }
    $details = @(& $docker inspect $ids[0] | ConvertFrom-Json)
    if ($LASTEXITCODE -ne 0 -or $details.Count -ne 1) {
        throw "M4_POSTGRES_CONTAINER_INSPECT_FAILED"
    }
    $container = $details[0]
    if (
        $container.Name.TrimStart("/") -cne $expectedName -or
        $container.Image -cne $expectedImageId -or
        $container.Config.Labels.'com.docker.compose.project' -cne $expectedProject -or
        $container.Config.Labels.'com.docker.compose.service' -cne "postgres" -or
        $container.HostConfig.Privileged
    ) {
        throw "M4_POSTGRES_CONTAINER_BOUNDARY_MISMATCH"
    }
    $dataMounts = @($container.Mounts | Where-Object {
        $_.Type -ceq "volume" -and $_.Destination -ceq "/var/lib/postgresql/data"
    })
    if ($dataMounts.Count -ne 1) {
        throw "M4_POSTGRES_DATA_VOLUME_IDENTITY_INVALID"
    }
    $bindings = @($container.HostConfig.PortBindings.'5432/tcp')
    if ($bindings.Count -ne 1 -or $bindings[0].HostIp -cne "127.0.0.1") {
        throw "M4_POSTGRES_PORT_BOUNDARY_INVALID"
    }

    $startedByM4 = $false
    if ($container.State.Status -ceq "exited") {
        & $docker start $container.Id | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "M4_POSTGRES_EXACT_START_FAILED"
        }
        $startedByM4 = $true
        [System.IO.Directory]::CreateDirectory($markerDirectory) | Out-Null
        if (-not (Test-Path -LiteralPath $markerPath)) {
            $marker = [ordered]@{
                schema_version = 1
                container_id = [string]$container.Id
                container_name = $expectedName
                started_by = "M4"
                started_at_utc = [DateTime]::UtcNow.ToString("o")
            }
            $json = $marker | ConvertTo-Json -Depth 4
            [System.IO.File]::WriteAllText(
                $markerPath,
                $json + [Environment]::NewLine,
                (New-Object System.Text.UTF8Encoding($false))
            )
        }
    }
    elseif ($container.State.Status -cne "running") {
        throw ("M4_POSTGRES_CONTAINER_STATE_INVALID:" + $container.State.Status)
    }

    $deadline = [DateTime]::UtcNow.AddSeconds($ReadyTimeoutSeconds)
    $health = ""
    do {
        $healthLine = @(& $docker inspect --format "{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}" $container.Id 2>$null)
        if ($LASTEXITCODE -eq 0 -and $healthLine.Count -eq 1) {
            $health = [string]$healthLine[0]
        }
        if ($health -ceq "healthy") {
            break
        }
        Start-Sleep -Seconds 2
    } while ([DateTime]::UtcNow -lt $deadline)
    if ($health -cne "healthy") {
        throw ("M4_POSTGRES_NOT_HEALTHY:" + $health)
    }

    Write-Output "M4_POSTGRES_START=PASS"
    Write-Output ("M4_POSTGRES_LIFECYCLE=" + $(if ($startedByM4) { "started_by_m4" } else { "preexisting_running" }))
    Write-Output "M4_POSTGRES_HEALTH=healthy"
    Write-Output "M4_POSTGRES_LOOPBACK_ONLY=true"
    Write-Output "M4_POSTGRES_DATA_PRESERVED=true"
}
finally {
    $env:DOCKER_CONFIG = $previousDockerConfig
}
