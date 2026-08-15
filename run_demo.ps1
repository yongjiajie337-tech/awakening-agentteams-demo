#requires -Version 5.1

<#
.SYNOPSIS
Entry point for reviewing or reproducing the Awakening AgentTeams Demo.

.DESCRIPTION
This package is a dual-layer review package.  It can verify itself offline,
but it does not bootstrap a live AgentTeams deployment.  Live actions are
delegated to an explicitly supplied, prepared reference workspace whose
frozen Demo sources match this release.

The default mode only prints a runbook.  The public wrapper never copies,
prints, or hashes a credential value.  The delegated reference lifecycle may
read its existing protected credential only at an explicitly confirmed live
stage.
#>

[CmdletBinding()]
param(
    [ValidateSet("PrintRunbook", "Preflight", "Live")]
    [string]$Mode = "PrintRunbook",

    [string]$ReferenceWorkspace = "",

    [guid]$DemoRunId = [guid]::Empty,

    [string]$LiveStep = "",

    [string]$HumanMatrixUserId = "",

    [ValidateRange(30, 900)]
    [int]$HumanRequestTimeoutSeconds = 600,

    [switch]$IUnderstandThisUsesDockerAndNetwork,

    [switch]$IUnderstandThisChangesReferenceState,

    [switch]$IUnderstandThisMayReadProtectedSecret,

    [switch]$IUnderstandThisMayCallProvider
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$packageRoot = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\', '/')
$referenceRunnerRelative = "scripts\demo\Invoke-AgentTeamsInPlaceDemo.ps1"
$referencePinsRelative = "config\reference-source-pins.json"
$script:referencePinCount = 0

function Write-Runbook {
    Write-Output "AWAKENING_DEMO_ENTRYPOINT=RUNBOOK_ONLY"
    Write-Output "PACKAGE_LIVE_BOOTSTRAP=false"
    Write-Output "PACKAGE_CREDENTIAL_INCLUDED=false"
    Write-Output ""
    Write-Output "1. Immediate package-only verification (no Python third-party dependency, Docker, network, Provider, or Secret):"
    Write-Output '   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\verify_offline.ps1 -Mode PackageOnly'
    Write-Output "   For the recommended 81-test Full profile, first create the external locked Python 3.12 environment documented in QUICKSTART_WINDOWS.md."
    Write-Output ""
    Write-Output "2. Prepare a separate compatible AgentTeams v1.1.2 reference workspace."
    Write-Output "   The reference workspace must already contain its protected local runtime configuration."
    Write-Output ""
    Write-Output "3. Create a fresh Demo execution-window UUID, then run the reference preflight:"
    Write-Output '   $demoRunId = [guid]::NewGuid()'
    Write-Output '   .\run_demo.ps1 -Mode Preflight -ReferenceWorkspace "<ABSOLUTE_REFERENCE_WORKSPACE>" -DemoRunId $demoRunId -IUnderstandThisUsesDockerAndNetwork -IUnderstandThisChangesReferenceState'
    Write-Output ""
    Write-Output "4. Execute one live stage at a time with the same DemoRunId:"
    Write-Output '   .\run_demo.ps1 -Mode Live -ReferenceWorkspace "<ABSOLUTE_REFERENCE_WORKSPACE>" -DemoRunId $demoRunId -LiveStep StartInfrastructure -IUnderstandThisUsesDockerAndNetwork -IUnderstandThisChangesReferenceState -IUnderstandThisMayReadProtectedSecret'
    Write-Output '   .\run_demo.ps1 -Mode Live -ReferenceWorkspace "<ABSOLUTE_REFERENCE_WORKSPACE>" -DemoRunId $demoRunId -LiveStep AwaitHumanRequest -HumanMatrixUserId "<YOUR_EXISTING_HUMAN_MATRIX_USER_ID>" -IUnderstandThisUsesDockerAndNetwork -IUnderstandThisChangesReferenceState -IUnderstandThisMayReadProtectedSecret'
    Write-Output "   Keep AwaitHumanRequest running. Only after it prints DEMO_HUMAN_ACTION, send the exact printed message once in Element."
    Write-Output '   .\run_demo.ps1 -Mode Live -ReferenceWorkspace "<ABSOLUTE_REFERENCE_WORKSPACE>" -DemoRunId $demoRunId -LiveStep StartLiveGateway -IUnderstandThisUsesDockerAndNetwork -IUnderstandThisChangesReferenceState -IUnderstandThisMayReadProtectedSecret'
    Write-Output '   .\run_demo.ps1 -Mode Live -ReferenceWorkspace "<ABSOLUTE_REFERENCE_WORKSPACE>" -DemoRunId $demoRunId -LiveStep RunChain -IUnderstandThisUsesDockerAndNetwork -IUnderstandThisChangesReferenceState -IUnderstandThisMayReadProtectedSecret -IUnderstandThisMayCallProvider'
    Write-Output '   .\run_demo.ps1 -Mode Live -ReferenceWorkspace "<ABSOLUTE_REFERENCE_WORKSPACE>" -DemoRunId $demoRunId -LiveStep StopRestore -IUnderstandThisUsesDockerAndNetwork -IUnderstandThisChangesReferenceState -IUnderstandThisMayReadProtectedSecret'
    Write-Output ""
    Write-Output "The full operator sequence and recovery rules are documented in QUICKSTART_WINDOWS.md."
}

function Assert-RegularFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Failure
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw ($Failure + ":MISSING")
    }
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw ($Failure + ":REPARSE_POINT")
    }
    return $item.FullName
}

function Resolve-ReferenceWorkspace {
    if ([string]::IsNullOrWhiteSpace($ReferenceWorkspace)) {
        throw "REFERENCE_WORKSPACE_EXPLICIT_PATH_REQUIRED"
    }
    try {
        $candidate = [IO.Path]::GetFullPath($ReferenceWorkspace)
    }
    catch {
        throw "REFERENCE_WORKSPACE_PATH_INVALID"
    }
    try {
        $present = Test-Path -LiteralPath $candidate -ErrorAction Stop
    }
    catch {
        throw "REFERENCE_WORKSPACE_PATH_INVALID"
    }
    if (-not $present) {
        throw "REFERENCE_WORKSPACE_NOT_FOUND"
    }
    try {
        $item = Get-Item -LiteralPath $candidate -Force -ErrorAction Stop
    }
    catch {
        throw "REFERENCE_WORKSPACE_METADATA_UNAVAILABLE"
    }
    if (-not $item.PSIsContainer) {
        throw "REFERENCE_WORKSPACE_NOT_DIRECTORY"
    }
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "REFERENCE_WORKSPACE_REPARSE_POINT_DENIED"
    }
    $full = [IO.Path]::GetFullPath($item.FullName).TrimEnd('\', '/')
    if ([string]::Equals($full, $packageRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "PACKAGE_ROOT_IS_NOT_A_LIVE_REFERENCE_WORKSPACE"
    }
    return $full
}

function Assert-ReferenceProfile {
    param([Parameter(Mandatory = $true)][string]$Workspace)

    $pinFile = Assert-RegularFile -Path (Join-Path $packageRoot $referencePinsRelative) `
        -Failure "REFERENCE_SOURCE_PINS_INVALID"
    $pins = Get-Content -LiteralPath $pinFile -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([string]$pins.schema_version -cne
            "awakening.agentteams.demo.reference-source-pins.v1" -or
        [string]$pins.agentteams_version -cne "v1.1.2" -or
        [string]$pins.hash_algorithm -cne "sha256") {
        throw "REFERENCE_SOURCE_PINS_SCHEMA_INVALID"
    }
    $pinRecords = @($pins.files)
    if ($pinRecords.Count -le 0 -or
        [int]$pins.file_count -ne $pinRecords.Count) {
        throw "REFERENCE_SOURCE_PINS_COUNT_INVALID"
    }
    $seen = @{}
    foreach ($entry in $pinRecords) {
        $relative = [string]$entry.path
        $expectedHash = [string]$entry.sha256
        $parts = @($relative.Split('/'))
        if ([string]::IsNullOrWhiteSpace($relative) -or
            [IO.Path]::IsPathRooted($relative) -or
            $relative.Contains('\') -or
            $parts -contains ".." -or
            $relative -notmatch '^[A-Za-z0-9._/-]+$' -or
            $expectedHash -notmatch '^[0-9a-f]{64}$' -or
            $seen.ContainsKey($relative)) {
            throw "REFERENCE_SOURCE_PIN_ENTRY_INVALID"
        }
        $seen[$relative] = $true
        $candidate = Join-Path $Workspace ($relative.Replace('/', '\'))
        $resolved = Assert-RegularFile -Path $candidate `
            -Failure ("REFERENCE_PROFILE_FILE_INVALID:" + $relative)
        $actual = (Get-FileHash -LiteralPath $resolved -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -cne $expectedHash) {
            throw ("REFERENCE_PROFILE_HASH_MISMATCH:" + $relative)
        }
    }
    $script:referencePinCount = $pinRecords.Count

    foreach ($relative in @(
        ".venv\Scripts\python.exe",
        "scripts\m4\Start-M4Postgres.ps1",
        "scripts\m4\Start-M4Controller.ps1",
        "scripts\m4\Refresh-M4RuntimeSaTokens.ps1",
        "scripts\m4\Start-M4FailClosedGateway.ps1",
        "scripts\m4\Start-M4HostRelay.ps1",
        "scripts\m4\Start-M4Agents.ps1",
        "scripts\m4\Move-M4GatewayRuntimeEvidence.ps1",
        "infra\agentteams\m4\controller.compose.yaml"
    )) {
        [void](Assert-RegularFile -Path (Join-Path $Workspace $relative) `
            -Failure ("REFERENCE_REQUIRED_FILE_INVALID:" + $relative))
    }

    $lockPath = Join-Path $Workspace "infra\agentteams\m4\runtime-images.lock.json"
    $lock = Get-Content -LiteralPath $lockPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([string]$lock.agentteams_version -cne "v1.1.2") {
        throw "REFERENCE_AGENTTEAMS_VERSION_UNSUPPORTED"
    }

    return (Join-Path $Workspace $referenceRunnerRelative)
}

function Invoke-ReferenceAction {
    param(
        [Parameter(Mandatory = $true)][string]$Runner,
        [Parameter(Mandatory = $true)][string]$Action,
        [Parameter(Mandatory = $true)][string[]]$AdditionalArguments
    )
    $powerShellCandidates = @(
        (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"),
        (Join-Path $PSHOME "powershell.exe")
    )
    $powerShellExe = @($powerShellCandidates | Where-Object {
        Test-Path -LiteralPath $_ -PathType Leaf
    } | Select-Object -First 1)
    if ($powerShellExe.Count -ne 1) {
        throw "WINDOWS_POWERSHELL_5_1_NOT_FOUND"
    }
    $arguments = @(
        "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", $Runner, "-Action", $Action
    ) + $AdditionalArguments
    Write-Output ("REFERENCE_ACTION_BEGIN=" + $Action)
    & $powerShellExe[0] @arguments
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw ("REFERENCE_ACTION_FAILED:" + $Action + ":" + $exitCode)
    }
    Write-Output ("REFERENCE_ACTION_END=" + $Action)
}

if ($Mode -ceq "PrintRunbook") {
    Write-Runbook
    exit 0
}

$reference = Resolve-ReferenceWorkspace
$runner = Assert-ReferenceProfile -Workspace $reference
Write-Output "REFERENCE_PROFILE=PASS"
Write-Output "REFERENCE_AGENTTEAMS_VERSION=v1.1.2"
Write-Output ("REFERENCE_SOURCE_PIN_COUNT=" + $script:referencePinCount)
Write-Output "REFERENCE_PROFILE_CHECK_SECRET_CONTENT_READ=false"

if ($Mode -ceq "Preflight") {
    if (-not $IUnderstandThisUsesDockerAndNetwork) {
        throw "PREFLIGHT_DOCKER_AND_NETWORK_ACKNOWLEDGEMENT_REQUIRED"
    }
    if (-not $IUnderstandThisChangesReferenceState) {
        throw "PREFLIGHT_FRESH_EVIDENCE_WRITE_ACKNOWLEDGEMENT_REQUIRED"
    }
    if ($DemoRunId -eq [guid]::Empty) {
        $DemoRunId = [guid]::NewGuid()
    }
    Write-Output ("DEMO_EXECUTION_WINDOW_ID=" + $DemoRunId.ToString("D").ToLowerInvariant())
    Invoke-ReferenceAction -Runner $runner -Action "OfflineCheck" -AdditionalArguments @()
    Invoke-ReferenceAction -Runner $runner -Action "Preflight" `
        -AdditionalArguments @("-DemoRunId", $DemoRunId.ToString("D"))
    Write-Output "PACKAGE_REFERENCE_PREFLIGHT=PASS"
    exit 0
}

$allowedLiveSteps = @(
    "StartInfrastructure",
    "AwaitHumanRequest",
    "StartLiveGateway",
    "RunChain",
    "StopRestore"
)
if ($LiveStep -notin $allowedLiveSteps) {
    throw ("LIVE_STEP_REQUIRED:" + ($allowedLiveSteps -join ","))
}
if ($DemoRunId -eq [guid]::Empty) {
    throw "LIVE_DEMO_RUN_ID_REQUIRED"
}
if (-not $IUnderstandThisUsesDockerAndNetwork) {
    throw "LIVE_DOCKER_AND_NETWORK_ACKNOWLEDGEMENT_REQUIRED"
}
if (-not $IUnderstandThisChangesReferenceState) {
    throw "LIVE_REFERENCE_STATE_CHANGE_ACKNOWLEDGEMENT_REQUIRED"
}
if (-not $IUnderstandThisMayReadProtectedSecret) {
    throw "LIVE_PROTECTED_SECRET_READ_ACKNOWLEDGEMENT_REQUIRED"
}
if ($LiveStep -ceq "RunChain" -and -not $IUnderstandThisMayCallProvider) {
    throw "LIVE_PROVIDER_CALL_ACKNOWLEDGEMENT_REQUIRED"
}
if ($LiveStep -ceq "AwaitHumanRequest" -and
    [string]::IsNullOrWhiteSpace($HumanMatrixUserId)) {
    throw "LIVE_HUMAN_MATRIX_USER_ID_REQUIRED"
}

$stepArguments = @("-DemoRunId", $DemoRunId.ToString("D"))
if ($LiveStep -ceq "AwaitHumanRequest") {
    $stepArguments += @(
        "-HumanMatrixUserId", $HumanMatrixUserId,
        "-ControlPeerUserId", "none",
        "-HumanRequestTimeoutSeconds", [string]$HumanRequestTimeoutSeconds
    )
}

Invoke-ReferenceAction -Runner $runner -Action "OfflineCheck" -AdditionalArguments @()
Invoke-ReferenceAction -Runner $runner -Action $LiveStep `
    -AdditionalArguments $stepArguments
Write-Output ("PACKAGE_REFERENCE_LIVE_STEP=PASS:" + $LiveStep)
Write-Output "PACKAGE_SECRET_COPIED=false"
