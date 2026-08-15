#requires -Version 5.1

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Live", "FailClosed")]
    [string]$RuntimeKind,

    [Parameter(Mandatory = $true)]
    [guid]$WindowId,

    [Parameter(Mandatory = $true)]
    [ValidateSet(
        "pre-live-stale-live",
        "pre-live-fail-closed",
        "post-live-stop",
        "pre-fail-closed-start"
    )]
    [string]$Phase,

    [ValidateRange(1024, 65535)]
    [int]$ListenerPort = 18190
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($WindowId -eq [guid]::Empty) {
    throw "M4_GATEWAY_EVIDENCE_WINDOW_ID_INVALID"
}
$validPhase = switch ($RuntimeKind) {
    "Live" { $Phase -in @("pre-live-stale-live", "post-live-stop") }
    "FailClosed" { $Phase -in @("pre-live-fail-closed", "pre-fail-closed-start") }
}
if (-not $validPhase) {
    throw "M4_GATEWAY_EVIDENCE_PHASE_KIND_MISMATCH"
}

function Resolve-M4EvidenceDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Reason
    )

    $candidate = [IO.Path]::GetFullPath($Path)
    $resolved = [IO.Path]::GetFullPath(
        (Resolve-Path -LiteralPath $candidate -ErrorAction Stop).ProviderPath
    )
    $item = Get-Item -LiteralPath $resolved -Force -ErrorAction Stop
    if (-not [StringComparer]::OrdinalIgnoreCase.Equals($candidate, $resolved) -or
        -not $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw $Reason
    }
    return $resolved
}

function Assert-M4EvidenceFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Reason
    )

    $candidate = [IO.Path]::GetFullPath($Path)
    $resolved = [IO.Path]::GetFullPath(
        (Resolve-Path -LiteralPath $candidate -ErrorAction Stop).ProviderPath
    )
    $item = Get-Item -LiteralPath $resolved -Force -ErrorAction Stop
    if (-not [StringComparer]::OrdinalIgnoreCase.Equals($candidate, $resolved) -or
        $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw $Reason
    }
    return $item
}

function Get-M4GatewayListeners {
    param([Parameter(Mandatory = $true)][int]$Port)

    return @(
        Get-NetTCPConnection -State Listen -ErrorAction Stop |
            Where-Object { [int]$_.LocalPort -eq $Port }
    )
}

$workspace = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$runtimeDirectory = Resolve-M4EvidenceDirectory `
    -Path (Join-Path $workspace "tmp\m4\gateway") `
    -Reason "M4_GATEWAY_EVIDENCE_RUNTIME_DIRECTORY_INVALID"
$archiveRootCandidate = [IO.Path]::GetFullPath((Join-Path $runtimeDirectory "archives"))
if (-not (Test-Path -LiteralPath $archiveRootCandidate)) {
    [void][IO.Directory]::CreateDirectory($archiveRootCandidate)
}
$archiveRoot = Resolve-M4EvidenceDirectory -Path $archiveRootCandidate `
    -Reason "M4_GATEWAY_EVIDENCE_ARCHIVE_ROOT_INVALID"

$prefix = if ($RuntimeKind -ceq "Live") { "live-gateway" } else { "gateway" }
$sourcePaths = @(
    (Join-Path $runtimeDirectory ($prefix + ".pid")),
    (Join-Path $runtimeDirectory ($prefix + ".stdout.log")),
    (Join-Path $runtimeDirectory ($prefix + ".stderr.log"))
)
$presentPaths = @($sourcePaths | Where-Object { Test-Path -LiteralPath $_ })
if ($presentPaths.Count -eq 0) {
    if (@(Get-M4GatewayListeners -Port $ListenerPort).Count -ne 0) {
        throw "M4_GATEWAY_EVIDENCE_PORT_LISTENER_PRESENT"
    }
    Write-Output "M4_GATEWAY_EVIDENCE_ARCHIVE=NOT_REQUIRED"
    Write-Output ("M4_GATEWAY_EVIDENCE_RUNTIME_KIND=" + $RuntimeKind)
    Write-Output ("M4_GATEWAY_EVIDENCE_PHASE=" + $Phase)
    Write-Output ("M4_GATEWAY_EVIDENCE_WINDOW_ID=" + $WindowId.ToString("D").ToLowerInvariant())
    Write-Output "M4_GATEWAY_EVIDENCE_FILE_COUNT=0"
    Write-Output "M4_GATEWAY_EVIDENCE_SECRET_VALUE_READ=false"
    return
}
if ($presentPaths.Count -ne $sourcePaths.Count) {
    throw "M4_GATEWAY_EVIDENCE_SET_INCOMPLETE"
}

$sourceItems = @()
foreach ($sourcePath in $sourcePaths) {
    $sourceItems += Assert-M4EvidenceFile -Path $sourcePath `
        -Reason "M4_GATEWAY_EVIDENCE_SOURCE_INVALID"
}
$pidText = [IO.File]::ReadAllText($sourcePaths[0]).Trim()
if ($pidText -notmatch '^[1-9][0-9]{0,9}$') {
    throw "M4_GATEWAY_EVIDENCE_PID_INVALID"
}
try {
    $recordedPid = [int]$pidText
}
catch {
    throw "M4_GATEWAY_EVIDENCE_PID_INVALID"
}
$activeProcess = @(Get-Process -Id $recordedPid -ErrorAction SilentlyContinue)
if ($activeProcess.Count -ne 0 -and -not $activeProcess[0].HasExited) {
    throw "M4_GATEWAY_EVIDENCE_PID_ACTIVE"
}
if (@(Get-M4GatewayListeners -Port $ListenerPort).Count -ne 0) {
    throw "M4_GATEWAY_EVIDENCE_PORT_LISTENER_PRESENT"
}

$windowName = $WindowId.ToString("D").ToLowerInvariant()
$windowDirectoryCandidate = [IO.Path]::GetFullPath((Join-Path $archiveRoot $windowName))
if (-not (Test-Path -LiteralPath $windowDirectoryCandidate)) {
    [void][IO.Directory]::CreateDirectory($windowDirectoryCandidate)
}
$windowDirectory = Resolve-M4EvidenceDirectory -Path $windowDirectoryCandidate `
    -Reason "M4_GATEWAY_EVIDENCE_WINDOW_DIRECTORY_INVALID"
$archiveDirectory = [IO.Path]::GetFullPath((Join-Path $windowDirectory $Phase))
if (Test-Path -LiteralPath $archiveDirectory) {
    throw "M4_GATEWAY_EVIDENCE_ARCHIVE_TARGET_EXISTS"
}
[void][IO.Directory]::CreateDirectory($archiveDirectory)
$archiveDirectory = Resolve-M4EvidenceDirectory -Path $archiveDirectory `
    -Reason "M4_GATEWAY_EVIDENCE_ARCHIVE_DIRECTORY_INVALID"

$records = @()
for ($index = 0; $index -lt $sourcePaths.Count; $index++) {
    $sourcePath = $sourcePaths[$index]
    $sourceItem = $sourceItems[$index]
    $destinationPath = [IO.Path]::GetFullPath(
        (Join-Path $archiveDirectory $sourceItem.Name)
    )
    if (Test-Path -LiteralPath $destinationPath) {
        throw "M4_GATEWAY_EVIDENCE_ARCHIVE_FILE_EXISTS"
    }
    $records += [ordered]@{
        name = $sourceItem.Name
        length = [int64]$sourceItem.Length
        sha256 = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash.ToLowerInvariant()
        source = $sourcePath
        destination = $destinationPath
    }
}

# Re-check the two safety facts immediately before the first irreversible move.
$activeProcess = @(Get-Process -Id $recordedPid -ErrorAction SilentlyContinue)
if ($activeProcess.Count -ne 0 -and -not $activeProcess[0].HasExited) {
    throw "M4_GATEWAY_EVIDENCE_PID_ACTIVE"
}
if (@(Get-M4GatewayListeners -Port $ListenerPort).Count -ne 0) {
    throw "M4_GATEWAY_EVIDENCE_PORT_LISTENER_PRESENT"
}

foreach ($record in $records) {
    [IO.File]::Move([string]$record.source, [string]$record.destination)
    $archivedItem = Assert-M4EvidenceFile -Path ([string]$record.destination) `
        -Reason "M4_GATEWAY_EVIDENCE_ARCHIVED_FILE_INVALID"
    $archivedHash = (Get-FileHash -LiteralPath ([string]$record.destination) `
        -Algorithm SHA256).Hash.ToLowerInvariant()
    if ([int64]$archivedItem.Length -ne [int64]$record.length -or
        $archivedHash -cne [string]$record.sha256) {
        throw "M4_GATEWAY_EVIDENCE_ARCHIVE_HASH_MISMATCH"
    }
}
if (@($sourcePaths | Where-Object { Test-Path -LiteralPath $_ }).Count -ne 0) {
    throw "M4_GATEWAY_EVIDENCE_SOURCE_REMAINS"
}
if (@(Get-M4GatewayListeners -Port $ListenerPort).Count -ne 0) {
    throw "M4_GATEWAY_EVIDENCE_PORT_LISTENER_PRESENT_AFTER_MOVE"
}

$manifestPath = [IO.Path]::GetFullPath((Join-Path $archiveDirectory "archive-manifest.json"))
$manifest = [ordered]@{
    schema_version = "awakening.m4.gateway-evidence-archive.v1"
    window_id = $windowName
    phase = $Phase
    runtime_kind = $RuntimeKind
    recorded_pid = $recordedPid
    listener_port = $ListenerPort
    listener_present = $false
    pid_active = $false
    file_count = 3
    delete_count = 0
    overwrite = $false
    provider_secret_value_read = $false
    archived_at_utc = [DateTime]::UtcNow.ToString("o")
    files = @($records | ForEach-Object {
        [ordered]@{
            name = [string]$_.name
            length = [int64]$_.length
            sha256 = [string]$_.sha256
        }
    })
}
$manifestBytes = [Text.Encoding]::UTF8.GetBytes(
    (($manifest | ConvertTo-Json -Depth 6) + "`n")
)
$manifestStream = [IO.File]::Open(
    $manifestPath,
    [IO.FileMode]::CreateNew,
    [IO.FileAccess]::Write,
    [IO.FileShare]::None
)
try {
    $manifestStream.Write($manifestBytes, 0, $manifestBytes.Length)
    $manifestStream.Flush()
}
finally {
    $manifestStream.Dispose()
}

$relativeArchive = $archiveDirectory.Substring($workspace.Length).TrimStart('\')
Write-Output "M4_GATEWAY_EVIDENCE_ARCHIVE=PASS"
Write-Output ("M4_GATEWAY_EVIDENCE_RUNTIME_KIND=" + $RuntimeKind)
Write-Output ("M4_GATEWAY_EVIDENCE_PHASE=" + $Phase)
Write-Output ("M4_GATEWAY_EVIDENCE_WINDOW_ID=" + $windowName)
Write-Output ("M4_GATEWAY_EVIDENCE_ARCHIVE_DIRECTORY=" + $relativeArchive)
Write-Output "M4_GATEWAY_EVIDENCE_FILE_COUNT=3"
Write-Output "M4_GATEWAY_EVIDENCE_PID_ACTIVE=false"
Write-Output "M4_GATEWAY_EVIDENCE_LISTENER_PRESENT=false"
Write-Output "M4_GATEWAY_EVIDENCE_HASH_VERIFIED=true"
Write-Output "M4_GATEWAY_EVIDENCE_DELETE_COUNT=0"
Write-Output "M4_GATEWAY_EVIDENCE_OVERWRITE=false"
Write-Output "M4_GATEWAY_EVIDENCE_SECRET_VALUE_READ=false"
