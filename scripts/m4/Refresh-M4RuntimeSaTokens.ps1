#requires -Version 5.1

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-M4SandboxSid {
    try {
        $account = New-Object System.Security.Principal.NTAccount(
            $env:COMPUTERNAME,
            "CodexSandboxUsers"
        )
        return $account.Translate(
            [System.Security.Principal.SecurityIdentifier]
        )
    }
    catch {
        throw "M4_RUNTIME_SA_REFRESH_SANDBOX_IDENTITY_INVALID"
    }
}

function Get-M4NormalizedRights {
    param(
        [Parameter(Mandatory = $true)]
        [System.Security.Principal.SecurityIdentifier]$Sid,

        [Parameter(Mandatory = $true)]
        [System.Security.AccessControl.FileSystemRights]$Rights
    )

    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
        $Sid,
        $Rights,
        [System.Security.AccessControl.InheritanceFlags]::None,
        [System.Security.AccessControl.PropagationFlags]::None,
        [System.Security.AccessControl.AccessControlType]::Allow
    )
    return [int64]$rule.FileSystemRights
}

function Assert-M4TokenAcl {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [System.Security.Principal.SecurityIdentifier]$HostSid,

        [Parameter(Mandatory = $true)]
        [System.Security.Principal.SecurityIdentifier]$SandboxSid
    )

    $systemSid = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-18")
    $administratorsSid = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-32-544")
    $expected = @{
        $HostSid.Value = Get-M4NormalizedRights -Sid $HostSid `
            -Rights ([System.Security.AccessControl.FileSystemRights]::FullControl)
        $systemSid.Value = Get-M4NormalizedRights -Sid $systemSid `
            -Rights ([System.Security.AccessControl.FileSystemRights]::FullControl)
        $administratorsSid.Value = Get-M4NormalizedRights -Sid $administratorsSid `
            -Rights ([System.Security.AccessControl.FileSystemRights]::FullControl)
        $SandboxSid.Value = Get-M4NormalizedRights -Sid $SandboxSid `
            -Rights ([System.Security.AccessControl.FileSystemRights]::Modify)
    }

    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($item.PSIsContainer -or $item.Length -lt 64 -or
        ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "M4_RUNTIME_SA_REFRESH_TOKEN_FILE_INVALID"
    }
    $acl = Get-Acl -LiteralPath $Path -ErrorAction Stop
    $owner = $acl.GetOwner([System.Security.Principal.SecurityIdentifier])
    $rules = @($acl.Access)
    if (-not $acl.AreAccessRulesProtected -or $owner.Value -cne $HostSid.Value -or
        $rules.Count -ne 4) {
        throw "M4_RUNTIME_SA_REFRESH_TOKEN_ACL_INVALID"
    }
    foreach ($rule in $rules) {
        $sid = $rule.IdentityReference.Translate(
            [System.Security.Principal.SecurityIdentifier]
        ).Value
        if (-not $expected.ContainsKey($sid) -or $rule.IsInherited -or
            $rule.AccessControlType -ne
                [System.Security.AccessControl.AccessControlType]::Allow -or
            [int64]$rule.FileSystemRights -ne [int64]$expected[$sid] -or
            $rule.InheritanceFlags -ne
                [System.Security.AccessControl.InheritanceFlags]::None -or
            $rule.PropagationFlags -ne
                [System.Security.AccessControl.PropagationFlags]::None) {
            throw "M4_RUNTIME_SA_REFRESH_TOKEN_ACL_INVALID"
        }
    }
    return $acl.Sddl
}

$workspace = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$docker = Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\resources\bin\docker.exe"
$dockerConfig = Join-Path $workspace "tmp\m4\docker-config-anonymous"
$secretRoot = Join-Path $workspace "tmp\m4\m4-runtime-secrets-v1"
$refreshScript = Join-Path $workspace "infra\agentteams\m4\runtime\refresh-sa-tokens.sh"
$controller = "awakening-m4-controller"
$expectedControllerImage = "sha256:5486f4643a04a3a7a4dd81cd7f1d6091f9b7db3a5446bd5676f567c857910978"
$expectedAgentImages = [ordered]@{
    "awakening-m4-manager" = "sha256:3a77482fb11472ab05f85ba5d60cbc0df8d66046aa9f63b9cf99f16d87852921"
    "awakening-m4-worker-role-project-architect" = "sha256:d1078b42115ec2ea4eeaac507bc63352812291ff6e2406e813863161f074fb0b"
    "awakening-m4-worker-execution-evidence-coach" = "sha256:d1078b42115ec2ea4eeaac507bc63352812291ff6e2406e813863161f074fb0b"
    "awakening-m4-worker-independent-quality-reviewer" = "sha256:d1078b42115ec2ea4eeaac507bc63352812291ff6e2406e813863161f074fb0b"
}
$targets = [ordered]@{
    "manager.sa-token" = "system:serviceaccount:default:awakening-m4-manager"
    "ROLE_PROJECT_ARCHITECT.sa-token" = "system:serviceaccount:default:awakening-m4-worker-role-project-architect"
    "EXECUTION_EVIDENCE_COACH.sa-token" = "system:serviceaccount:default:awakening-m4-worker-execution-evidence-coach"
    "INDEPENDENT_QUALITY_REVIEWER.sa-token" = "system:serviceaccount:default:awakening-m4-worker-independent-quality-reviewer"
}

foreach ($path in @($docker, (Join-Path $dockerConfig "config.json"), $secretRoot, $refreshScript)) {
    $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "M4_RUNTIME_SA_REFRESH_INPUT_REPARSE_POINT"
    }
}
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
if ($null -eq $identity.User -or [string]::IsNullOrWhiteSpace($identity.Name)) {
    throw "M4_RUNTIME_SA_REFRESH_HOST_IDENTITY_INVALID"
}
$hostSid = $identity.User
$sandboxSid = Resolve-M4SandboxSid
if ($hostSid.Value -ceq $sandboxSid.Value) {
    throw "M4_RUNTIME_SA_REFRESH_HOST_IDENTITY_INVALID"
}

$originalSddl = @{}
foreach ($filename in $targets.Keys) {
    $target = Join-Path $secretRoot $filename
    $originalSddl[$filename] = Assert-M4TokenAcl -Path $target `
        -HostSid $hostSid -SandboxSid $sandboxSid
}

$refreshId = [guid]::NewGuid().ToString("N")
$containerScript = "/tmp/awakening-m4-sa-refresh-" + $refreshId + ".sh"
$containerDirectory = "/tmp/awakening-m4-sa-refresh-" + $refreshId
$hostStageDirectory = Join-Path $secretRoot (".sa-refresh-" + $refreshId)
if (Test-Path -LiteralPath $hostStageDirectory) {
    throw "M4_RUNTIME_SA_REFRESH_STAGE_EXISTS"
}

$previousDockerConfig = $env:DOCKER_CONFIG
$containerScriptCreated = $false
$containerDirectoryCreated = $false
$hostStageCreated = $false
$cleanupFailed = $false
try {
    $env:DOCKER_CONFIG = $dockerConfig
    $controllerDetails = @(& $docker inspect $controller 2>$null | ConvertFrom-Json)
    if ($LASTEXITCODE -ne 0 -or $controllerDetails.Count -ne 1) {
        throw "M4_RUNTIME_SA_REFRESH_CONTROLLER_INSPECT_FAILED"
    }
    $controllerRecord = $controllerDetails[0]
    $controllerNetworks = @($controllerRecord.NetworkSettings.Networks.PSObject.Properties.Name)
    if ($controllerRecord.Image -cne $expectedControllerImage -or
        $controllerRecord.State.Status -cne "running" -or
        [string]$controllerRecord.State.Health.Status -cne "healthy" -or
        $controllerRecord.HostConfig.Privileged -or
        $controllerRecord.HostConfig.NetworkMode -cne "awakening-m4-net" -or
        $controllerNetworks.Count -ne 1 -or
        $controllerNetworks[0] -cne "awakening-m4-net" -or
        $null -ne ($controllerRecord.Mounts | Where-Object {
            $_.Destination -ceq "/var/run/docker.sock"
        })) {
        throw "M4_RUNTIME_SA_REFRESH_CONTROLLER_BOUNDARY_INVALID"
    }
    foreach ($agentName in $expectedAgentImages.Keys) {
        $details = @(& $docker inspect $agentName 2>$null | ConvertFrom-Json)
        if ($LASTEXITCODE -ne 0 -or $details.Count -ne 1 -or
            $details[0].Image -cne $expectedAgentImages[$agentName] -or
            $details[0].State.Status -cne "exited") {
            throw ("M4_RUNTIME_SA_REFRESH_AGENT_NOT_STOPPED:" + $agentName)
        }
    }

    & $docker cp $refreshScript ($controller + ":" + $containerScript) | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "M4_RUNTIME_SA_REFRESH_SCRIPT_COPY_FAILED"
    }
    $containerScriptCreated = $true
    $safeOutput = @(& $docker exec $controller /bin/bash $containerScript `
        $containerDirectory 2>$null)
    if ($LASTEXITCODE -ne 0) {
        throw "M4_RUNTIME_SA_REFRESH_MINT_FAILED"
    }
    $containerDirectoryCreated = $true
    $identityLines = @($safeOutput | Where-Object {
        $_ -match '^M4_RUNTIME_SA_REFRESH_IDENTITY=system:serviceaccount:default:awakening-m4-(?:manager|worker-[a-z-]+)\|remaining=[0-9]{1,5}$'
    })
    if ($safeOutput.Count -ne 8 -or $identityLines.Count -ne 4 -or
        $safeOutput[4] -cne "M4_RUNTIME_SA_REFRESH_MINT=PASS" -or
        $safeOutput[5] -cne "M4_RUNTIME_SA_REFRESH_TOKEN_COUNT=4" -or
        $safeOutput[6] -cne "M4_RUNTIME_SA_REFRESH_TTL_SECONDS=7200" -or
        $safeOutput[7] -cne "M4_RUNTIME_SA_REFRESH_SECRET_ECHOED=false") {
        throw "M4_RUNTIME_SA_REFRESH_OUTPUT_INVALID"
    }

    [System.IO.Directory]::CreateDirectory($hostStageDirectory) | Out-Null
    $hostStageCreated = $true
    foreach ($filename in $targets.Keys) {
        $stage = Join-Path $hostStageDirectory $filename
        & $docker cp ($controller + ":" + $containerDirectory + "/" + $filename) `
            $stage | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw ("M4_RUNTIME_SA_REFRESH_TOKEN_COPY_FAILED:" + $filename)
        }
        $stageItem = Get-Item -LiteralPath $stage -Force -ErrorAction Stop
        if ($stageItem.PSIsContainer -or $stageItem.Length -lt 64 -or
            ($stageItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw ("M4_RUNTIME_SA_REFRESH_STAGE_INVALID:" + $filename)
        }
        $stageRules = @((Get-Acl -LiteralPath $stage).Access)
        $stageSids = @($stageRules | ForEach-Object {
            $_.IdentityReference.Translate(
                [System.Security.Principal.SecurityIdentifier]
            ).Value
        } | Sort-Object -Unique)
        $expectedStageSids = @(
            $hostSid.Value,
            "S-1-5-18",
            "S-1-5-32-544",
            $sandboxSid.Value
        ) | Sort-Object -Unique
        if ($stageSids.Count -ne 4 -or $expectedStageSids.Count -ne 4) {
            throw ("M4_RUNTIME_SA_REFRESH_STAGE_ACL_INVALID:" + $filename)
        }
        for ($index = 0; $index -lt 4; $index++) {
            if ($stageSids[$index] -cne $expectedStageSids[$index]) {
                throw ("M4_RUNTIME_SA_REFRESH_STAGE_ACL_INVALID:" + $filename)
            }
        }
    }

    foreach ($filename in $targets.Keys) {
        $stage = Join-Path $hostStageDirectory $filename
        $target = Join-Path $secretRoot $filename
        $bytes = [System.IO.File]::ReadAllBytes($stage)
        try {
            $stream = New-Object System.IO.FileStream(
                $target,
                [System.IO.FileMode]::Open,
                [System.IO.FileAccess]::Write,
                [System.IO.FileShare]::None
            )
            try {
                $stream.SetLength(0)
                $stream.Write($bytes, 0, $bytes.Length)
                $stream.Flush($true)
            }
            finally {
                $stream.Dispose()
            }
        }
        finally {
            [Array]::Clear($bytes, 0, $bytes.Length)
        }
        $afterSddl = Assert-M4TokenAcl -Path $target `
            -HostSid $hostSid -SandboxSid $sandboxSid
        if ($afterSddl -cne $originalSddl[$filename]) {
            throw ("M4_RUNTIME_SA_REFRESH_TOKEN_ACL_CHANGED:" + $filename)
        }
    }

    foreach ($line in $safeOutput) {
        Write-Output $line
    }
    Write-Output "M4_RUNTIME_SA_REFRESH=PASS"
    Write-Output "M4_RUNTIME_SA_REFRESH_ACL_PRESERVED=true"
    Write-Output "M4_RUNTIME_SA_REFRESH_AGENT_CONTAINER_COUNT=4"
    Write-Output "M4_RUNTIME_SA_REFRESH_PROVIDER_SECRET_READ=false"
}
finally {
    if ($hostStageCreated -and (Test-Path -LiteralPath $hostStageDirectory)) {
        try {
            foreach ($filename in $targets.Keys) {
                $stage = Join-Path $hostStageDirectory $filename
                if (Test-Path -LiteralPath $stage) {
                    [System.IO.File]::Delete($stage)
                }
            }
            [System.IO.Directory]::Delete($hostStageDirectory, $false)
        }
        catch {
            $cleanupFailed = $true
        }
    }
    if ($containerDirectoryCreated) {
        foreach ($filename in $targets.Keys) {
            & $docker exec $controller rm -f -- ($containerDirectory + "/" + $filename) `
                2>$null | Out-Null
            if ($LASTEXITCODE -ne 0) {
                $cleanupFailed = $true
            }
        }
        & $docker exec $controller rmdir -- $containerDirectory 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) {
            $cleanupFailed = $true
        }
    }
    if ($containerScriptCreated) {
        & $docker exec $controller rm -f -- $containerScript 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) {
            $cleanupFailed = $true
        }
    }
    $env:DOCKER_CONFIG = $previousDockerConfig
    if ($cleanupFailed) {
        throw "M4_RUNTIME_SA_REFRESH_CLEANUP_FAILED"
    }
}

