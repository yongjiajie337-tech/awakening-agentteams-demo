#requires -Version 5.1

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet(
        "OfflineCheck",
        "Preflight",
        "StartInfrastructure",
        "ResumeAdmissionCheck",
        "ResumeInfrastructure",
        "AwaitHumanRequest",
        "StartLiveGateway",
        "RunChain",
        "StopRestore"
    )]
    [string]$Action,

    [guid]$DemoRunId = [guid]::Empty,

    [string]$HumanRequestEventId = "",

    [string]$HumanMatrixUserId = "",

    [ValidateSet(
        "none",
        "@role_project_architect:matrix-m4.local:8080",
        "@execution_evidence_coach:matrix-m4.local:8080",
        "@independent_quality_reviewer:matrix-m4.local:8080"
    )]
    [string]$ControlPeerUserId = "none",

    [guid]$DemoRequestId = [guid]::Empty,

    [ValidateRange(30, 900)]
    [int]$HumanRequestTimeoutSeconds = 600,

    [ValidateRange(30, 900)]
    [int]$ReadyTimeoutSeconds = 300,

    [ValidateRange(1, 7)]
    [int]$ResumeAttempt = 1
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptPath = [IO.Path]::GetFullPath($PSCommandPath)
$workspace = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$python = Join-Path $workspace ".venv\Scripts\python.exe"
$powershell = Join-Path $PSHOME "powershell.exe"
$docker = Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\resources\bin\docker.exe"
$dockerCompose = Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\resources\bin\docker-compose.exe"
$dockerConfig = Join-Path $workspace "tmp\m4\docker-config-anonymous"
$coreCli = Join-Path $workspace "scripts\demo\agentteams_in_place_demo.py"
$matrixControlSource = Join-Path $workspace "infra\agentteams\demo\runtime\demo-matrix-control.sh"
$m5Secret = Join-Path $workspace ".env.m5.provider"
$m4ProviderSecret = Join-Path $workspace ".env.m4.provider"
$curl = Join-Path $env:SystemRoot "System32\curl.exe"
$demoProviderHostname = "dashscope.aliyuncs.com"
$demoProviderDohUri = "https://dns.google/resolve?name=dashscope.aliyuncs.com&type=A"
$demoProviderProbeUri = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

$startPostgres = Join-Path $workspace "scripts\m4\Start-M4Postgres.ps1"
$startController = Join-Path $workspace "scripts\m4\Start-M4Controller.ps1"
$refreshTokens = Join-Path $workspace "scripts\m4\Refresh-M4RuntimeSaTokens.ps1"
$startFailClosed = Join-Path $workspace "scripts\m4\Start-M4FailClosedGateway.ps1"
$startHostRelay = Join-Path $workspace "scripts\m4\Start-M4HostRelay.ps1"
$startDemoHostRelay = Join-Path $workspace "scripts\demo\Start-AgentTeamsDemoHostRelay.ps1"
$startDemoHostRelaySha256 = "f9146645d4eeb0c39c33a8ed0360553e8e9cfeacfae957ab65f8ab1de1a06f81"
$workerGatewaySyncSource = Join-Path $workspace "infra\agentteams\demo\runtime\demo-worker-gateway-key-sync.sh"
$workerGatewaySyncSha256 = "a4a3bd3c2948e6fcc76dbd5f60f38a5f40a58e0ee3cef394ab72c51889ff5c13"
$workerEntrypointSource = Join-Path $workspace "infra\agentteams\m4\runtime\worker-entrypoint.sh"
$workerEntrypointSha256 = "bf027122f86b4d6b418682883253c74035f77e30a1e8f3b6862cefbb699c6739"
$startAgents = Join-Path $workspace "scripts\m4\Start-M4Agents.ps1"
$archiveGatewayEvidence = Join-Path $workspace "scripts\m4\Move-M4GatewayRuntimeEvidence.ps1"

$exactContainers = @(
    "awakening-m4-controller",
    "awakening-m4-host-relay",
    "awakening-m1-068642ac363b-agentteams-infra-1",
    "awakening-m4-manager",
    "awakening-m4-worker-role-project-architect",
    "awakening-m4-worker-execution-evidence-coach",
    "awakening-m4-worker-independent-quality-reviewer",
    "awakening-m1-068642ac363b-postgres-1"
)
$agentContainers = @(
    "awakening-m4-manager",
    "awakening-m4-worker-role-project-architect",
    "awakening-m4-worker-execution-evidence-coach",
    "awakening-m4-worker-independent-quality-reviewer"
)
$workerGatewaySyncTargets = @(
    [ordered]@{
        name = "awakening-m4-worker-role-project-architect"
        role = "role_project_architect"
        short_name = "architect"
    },
    [ordered]@{
        name = "awakening-m4-worker-independent-quality-reviewer"
        role = "independent_quality_reviewer"
        short_name = "reviewer"
    }
)
$managedContainerStopOrder = @(
    "awakening-m4-manager",
    "awakening-m4-worker-role-project-architect",
    "awakening-m4-worker-execution-evidence-coach",
    "awakening-m4-worker-independent-quality-reviewer",
    "awakening-m4-host-relay",
    "awakening-m4-controller",
    "awakening-m1-068642ac363b-postgres-1"
)
$ports = @(18180, 18188, 18190, 18191)

function Assert-RegularFile {
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
        $item.PSIsContainer -or $item.Length -le 0 -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw $Reason
    }
    return $resolved
}

function Assert-RegularDirectory {
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

function Assert-DemoHostRelayHelper {
    $resolved = Assert-RegularFile -Path $startDemoHostRelay `
        -Reason "DEMO_HOST_RELAY_HELPER_INVALID"
    $actualHash = (Get-FileHash -LiteralPath $resolved -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -cne $startDemoHostRelaySha256) {
        throw "DEMO_HOST_RELAY_HELPER_HASH_MISMATCH"
    }
    return $resolved
}

function Assert-DemoWorkerGatewaySyncHelper {
    $resolved = Assert-RegularFile -Path $workerGatewaySyncSource `
        -Reason "DEMO_WORKER_GATEWAY_SYNC_HELPER_INVALID"
    $actualHash = (Get-FileHash -LiteralPath $resolved -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -cne $workerGatewaySyncSha256) {
        throw "DEMO_WORKER_GATEWAY_SYNC_HELPER_HASH_MISMATCH"
    }
    return $resolved
}

function Assert-DemoWorkerEntrypointGuard {
    $resolved = Assert-RegularFile -Path $workerEntrypointSource `
        -Reason "DEMO_WORKER_ENTRYPOINT_GUARD_INVALID"
    $actualHash = (Get-FileHash -LiteralPath $resolved -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -cne $workerEntrypointSha256) {
        throw "DEMO_WORKER_ENTRYPOINT_GUARD_HASH_MISMATCH"
    }
    return $resolved
}

function Get-DemoPaths {
    if ($DemoRunId -eq [guid]::Empty) {
        throw "DEMO_RUN_ID_REQUIRED"
    }
    $runName = $DemoRunId.ToString("D").ToLowerInvariant()
    if ($runName -notmatch '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$') {
        throw "DEMO_RUN_ID_MUST_BE_UUID_V4"
    }
    $base = [IO.Path]::GetFullPath(
        (Join-Path $workspace "tmp\demo\agentteams-in-place")
    )
    $root = Join-Path $base (".preflight-" + $runName)
    $coreRun = Join-Path $base $runName
    return [ordered]@{
        Root = $root
        CoreRun = $coreRun
        Runtime = (Join-Path $coreRun "runtime")
        Baseline = (Join-Path $root "preflight-baseline.json")
        CoreBinding = (Join-Path $root "core-binding.json")
        Journal = (Join-Path $root "lifecycle.jsonl")
        MatrixEvents = (Join-Path $root "matrix-events.jsonl")
        Recovery = (Join-Path $root "recovery")
        ResumeMarker = (Join-Path $root "resume-infrastructure.json")
        ResumeRecoveryMarker = (Join-Path $root "resume-infrastructure-recovery.json")
        ResumeRelayRecoveryMarker = (Join-Path $root "resume-infrastructure-relay-recovery.json")
        ResumeLogPolicyRecoveryMarker = (Join-Path $root "resume-infrastructure-log-policy-recovery.json")
        ResumeWiringRecoveryMarker = (Join-Path $root "resume-infrastructure-wiring-recovery.json")
        ResumeDemoRelayStageRecoveryMarker = (Join-Path $root "resume-infrastructure-demo-relay-stage-recovery.json")
        ResumeDemoRelayPrestateBoundaryRecoveryMarker = (Join-Path $root "resume-infrastructure-demo-relay-prestate-boundary-recovery.json")
        StatePid = (Join-Path $coreRun "runtime\state-mcp.pid")
        StateStdout = (Join-Path $coreRun "runtime\state-mcp.stdout.log")
        StateStderr = (Join-Path $coreRun "runtime\state-mcp.stderr.log")
        LivePid = (Join-Path $coreRun "runtime\demo-live-gateway.pid")
        LiveStdout = (Join-Path $coreRun "runtime\demo-live-gateway.stdout.log")
        LiveStderr = (Join-Path $coreRun "runtime\demo-live-gateway.stderr.log")
        LiveReady = (Join-Path $coreRun "runtime\demo-live-gateway.ready.json")
        PrepareStdout = (Join-Path $root "prepare.stdout.log")
        PrepareStderr = (Join-Path $root "prepare.stderr.log")
        RunStdout = (Join-Path $root "run-chain.stdout.log")
        RunStderr = (Join-Path $root "run-chain.stderr.log")
        HumanRequest = (Join-Path $root "human-request.json")
        FailClosedMarker = (Join-Path $root "fail-closed-started.marker")
        FailClosedRecoveryMarker = (Join-Path $root "fail-closed-recovery-started.marker")
        FailClosedRelayRecoveryMarker = (Join-Path $root "fail-closed-relay-recovery-started.marker")
        FailClosedLogPolicyRecoveryMarker = (Join-Path $root "fail-closed-log-policy-recovery-started.marker")
        FailClosedWiringRecoveryMarker = (Join-Path $root "fail-closed-wiring-recovery-started.marker")
        FailClosedDemoRelayStageRecoveryMarker = (Join-Path $root "fail-closed-demo-relay-stage-recovery-started.marker")
        FailClosedDemoRelayPrestateBoundaryRecoveryMarker = (Join-Path $root "fail-closed-demo-relay-prestate-boundary-recovery-started.marker")
    }
}

function Write-JournalRecord {
    param(
        [Parameter(Mandatory = $true)]$Paths,
        [Parameter(Mandatory = $true)][string]$Kind,
        [Parameter(Mandatory = $true)][string]$Status,
        [hashtable]$Details = @{},
        [ValidateSet("false", "true", "unknown")]
        [string]$SecretValueReadStatus = "false"
    )

    $secretValueRead = if ($SecretValueReadStatus -ceq "unknown") {
        $null
    }
    else {
        $SecretValueReadStatus -ceq "true"
    }
    $record = [ordered]@{
        schema_version = "awakening.demo.lifecycle.v1"
        demo_run_id = $DemoRunId.ToString("D").ToLowerInvariant()
        recorded_at_utc = [DateTime]::UtcNow.ToString("o")
        kind = $Kind
        status = $Status
        details = $Details
        secret_value_read = $secretValueRead
        secret_value_hashed = $false
        secret_value_echoed = $false
    }
    $line = ($record | ConvertTo-Json -Depth 8 -Compress) + "`n"
    [IO.File]::AppendAllText($Paths.Journal, $line, (New-Object Text.UTF8Encoding($false)))
}

function Write-JsonCreateNew {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Value
    )

    $bytes = [Text.Encoding]::UTF8.GetBytes(
        (($Value | ConvertTo-Json -Depth 12) + "`n")
    )
    $stream = [IO.File]::Open(
        $Path,
        [IO.FileMode]::CreateNew,
        [IO.FileAccess]::Write,
        [IO.FileShare]::None
    )
    try {
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
    }
    finally {
        $stream.Dispose()
    }
}

function Write-MatrixEventRecord {
    param(
        [Parameter(Mandatory = $true)]$Paths,
        [Parameter(Mandatory = $true)]$Value
    )

    $line = ($Value | ConvertTo-Json -Depth 8 -Compress) + "`n"
    [IO.File]::AppendAllText(
        $Paths.MatrixEvents,
        $line,
        (New-Object Text.UTF8Encoding($false))
    )
}

function Get-SafeFileFingerprint {
    param([Parameter(Mandatory = $true)][string]$Path)

    $resolved = Assert-RegularFile -Path $Path -Reason "DEMO_FINGERPRINT_INPUT_INVALID"
    $item = Get-Item -LiteralPath $resolved -Force -ErrorAction Stop
    return [ordered]@{
        path = $resolved.Substring($workspace.Length).TrimStart('\')
        length = [int64]$item.Length
        sha256 = (Get-FileHash -LiteralPath $resolved -Algorithm SHA256).Hash.ToLowerInvariant()
        last_write_utc = $item.LastWriteTimeUtc.ToString("o")
    }
}

function Invoke-M4Script {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string[]]$Arguments = @(),
        [Parameter(Mandatory = $true)][string[]]$SuccessMarkers
    )

    $allArguments = @(
        "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", $Path
    ) + @($Arguments)
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = @(& $powershell @allArguments 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    $markerPresent = $false
    foreach ($marker in $SuccessMarkers) {
        if ($output -ccontains $marker) {
            $markerPresent = $true
        }
    }
    if ($exitCode -ne 0 -or -not $markerPresent) {
        $fixedCodes = @()
        foreach ($line in $output) {
            foreach ($match in [regex]::Matches(
                [string]$line,
                '\b(?:DEMO|M4)_[A-Z0-9_]+(?::[A-Za-z0-9_.-]+)?'
            )) {
                $fixedCodes += [string]$match.Value
            }
        }
        $failureCode = if ($fixedCodes.Count -eq 0) {
            "DEMO_CHILD_FAILURE_UNCLASSIFIED"
        }
        else {
            [string]$fixedCodes[$fixedCodes.Count - 1]
        }
        throw (
            "DEMO_M4_SCRIPT_FAILED:" + [IO.Path]::GetFileName($Path) + ":" +
            $failureCode
        )
    }
    foreach ($line in $output) {
        Write-Output ([string]$line)
    }
}

function Use-DockerConfig {
    param([Parameter(Mandatory = $true)][scriptblock]$Body)

    $previous = $env:DOCKER_CONFIG
    try {
        $env:DOCKER_CONFIG = $dockerConfig
        & $Body
    }
    finally {
        $env:DOCKER_CONFIG = $previous
    }
}

function Get-DemoWorkerGatewaySyncContainerProjection {
    param([Parameter(Mandatory = $true)][string]$Name)

    if ($Name -cnotin @($workerGatewaySyncTargets | ForEach-Object { [string]$_.name })) {
        throw "DEMO_WORKER_GATEWAY_SYNC_CONTAINER_INVALID"
    }
    $format = "{{.Id}}|{{.Image}}|{{.State.Status}}|{{.State.ExitCode}}|" +
        "{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}|" +
        "{{.RestartCount}}|{{.HostConfig.RestartPolicy.Name}}"
    $lines = @(& $docker inspect --type container --format $format $Name 2>$null)
    if ($LASTEXITCODE -ne 0 -or $lines.Count -ne 1) {
        throw ("DEMO_WORKER_GATEWAY_SYNC_CONTAINER_INSPECT_FAILED:" + $Name)
    }
    $parts = [string]$lines[0] -split '\|', 7
    $exitCode = 0
    $restartCount = [int64]0
    if ($parts.Count -ne 7 -or $parts[0] -notmatch '^[0-9a-f]{64}$' -or
        $parts[1] -notmatch '^sha256:[0-9a-f]{64}$' -or
        $parts[2] -notin @("created", "running", "paused", "restarting", "removing", "exited", "dead") -or
        -not [int]::TryParse($parts[3], [ref]$exitCode) -or
        $parts[4] -notin @("missing", "starting", "healthy", "unhealthy") -or
        -not [int64]::TryParse($parts[5], [ref]$restartCount) -or
        $restartCount -lt 0 -or $parts[6] -cne "no") {
        throw ("DEMO_WORKER_GATEWAY_SYNC_CONTAINER_PROJECTION_INVALID:" + $Name)
    }
    return [pscustomobject][ordered]@{
        name = $Name
        id = [string]$parts[0]
        image_id = [string]$parts[1]
        state = [string]$parts[2]
        exit_code = $exitCode
        health = [string]$parts[4]
        restart_count = $restartCount
        restart_policy = [string]$parts[6]
    }
}

function Get-DemoWorkerGatewaySyncTempContainerCount {
    param([Parameter(Mandatory = $true)][string]$Name)

    if ($Name -notmatch '^awakening-demo-key-sync-[0-9a-f]{12}-(architect|reviewer)$') {
        throw "DEMO_WORKER_GATEWAY_SYNC_TEMP_NAME_INVALID"
    }
    $names = @(& $docker container ls --all `
        --filter ("name=^/" + $Name + "$") --format "{{.Names}}" 2>$null)
    if ($LASTEXITCODE -ne 0 -or @($names | Where-Object { [string]$_ -cne $Name }).Count -ne 0) {
        throw "DEMO_WORKER_GATEWAY_SYNC_TEMP_DISCOVERY_FAILED"
    }
    return @($names).Count
}

function Remove-DemoWorkerGatewaySyncTempResidue {
    foreach ($target in $workerGatewaySyncTargets) {
        $temporaryName = "awakening-demo-key-sync-" +
            $DemoRunId.ToString("N").Substring(0, 12) + "-" +
            [string]$target.short_name
        $count = Get-DemoWorkerGatewaySyncTempContainerCount -Name $temporaryName
        if ($count -eq 1) {
            $removeOutput = @(& $docker rm --force $temporaryName 2>&1)
            if ($LASTEXITCODE -ne 0 -or @($removeOutput).Count -ne 1 -or
                [string]$removeOutput[0] -cne $temporaryName) {
                throw "DEMO_WORKER_GATEWAY_SYNC_TEMP_CLEANUP_FAILED"
            }
        }
        if ((Get-DemoWorkerGatewaySyncTempContainerCount -Name $temporaryName) -ne 0) {
            throw "DEMO_WORKER_GATEWAY_SYNC_TEMP_RESIDUE_PRESENT"
        }
    }
}

function Invoke-DemoWorkerGatewaySyncHelper {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)]$Target,
        [Parameter(Mandatory = $true)]$Container,
        [Parameter(Mandatory = $true)]
        [ValidateSet("apply", "inspect", "probe")]
        [string]$Command
    )

    if ([string]$Target.name -cne [string]$Container.name -or
        [string]$Target.role -notin @(
            "role_project_architect", "independent_quality_reviewer"
        ) -or [string]$Target.short_name -notin @("architect", "reviewer")) {
        throw "DEMO_WORKER_GATEWAY_SYNC_TARGET_INVALID"
    }
    $temporaryName = $null
    $remoteHelperPath = "/root/.awakening-demo-worker-gateway-key-sync.sh"
    $copiedToContainer = $false
    if ($Command -ceq "apply") {
        $temporaryName = "awakening-demo-key-sync-" +
            $DemoRunId.ToString("N").Substring(0, 12) + "-" +
            [string]$Target.short_name
        if ((Get-DemoWorkerGatewaySyncTempContainerCount -Name $temporaryName) -ne 0) {
            throw "DEMO_WORKER_GATEWAY_SYNC_TEMP_ALREADY_EXISTS"
        }
        $arguments = @(
            "run", "--rm", "--name", $temporaryName,
            "--network", "none", "--pull", "never",
            "--volumes-from", ([string]$Container.id + ":rw"),
            "--mount", (
                "type=bind,source=" + $Source +
                ",target=" + $remoteHelperPath + ",readonly"
            ),
            "--entrypoint", "/bin/bash", [string]$Container.image_id,
            $remoteHelperPath, "apply", [string]$Target.role
        )
    }
    else {
        $absenceOutput = @(& $docker exec ([string]$Container.id) /bin/bash -c `
            ("test ! -e " + $remoteHelperPath + " && test ! -L " + $remoteHelperPath) 2>&1)
        if ($LASTEXITCODE -ne 0 -or @($absenceOutput).Count -ne 0) {
            throw "DEMO_WORKER_GATEWAY_SYNC_REMOTE_HELPER_PRESTATE_INVALID"
        }
        $copiedToContainer = $true
        $copyOutput = @(& $docker cp $Source `
            ([string]$Container.id + ":" + $remoteHelperPath) 2>&1)
        if ($LASTEXITCODE -ne 0 -or @($copyOutput).Count -ne 0) {
            throw "DEMO_WORKER_GATEWAY_SYNC_REMOTE_HELPER_COPY_FAILED"
        }
        $chmodOutput = @(& $docker exec ([string]$Container.id) `
            /bin/chmod 600 $remoteHelperPath 2>&1)
        if ($LASTEXITCODE -ne 0 -or @($chmodOutput).Count -ne 0) {
            throw "DEMO_WORKER_GATEWAY_SYNC_REMOTE_HELPER_MODE_FAILED"
        }
        $arguments = @(
            "exec", [string]$Container.id, "/bin/bash", $remoteHelperPath,
            $Command, [string]$Target.role
        )
    }

    try {
        $output = @(& $docker @arguments 2>&1)
        $exitCode = $LASTEXITCODE
        $stdout = [string]::Join("`n", @($output | ForEach-Object { [string]$_ }))
        if ($exitCode -ne 0 -or
            [Text.Encoding]::UTF8.GetByteCount($stdout) -gt 4096) {
            throw ("DEMO_WORKER_GATEWAY_SYNC_EXECUTION_FAILED:" + $Command)
        }
        $lines = @($stdout -split "`r?`n" | Where-Object {
            -not [string]::IsNullOrWhiteSpace($_)
        })
        $roleLine = "DEMO_WORKER_GATEWAY_SYNC_ROLE=" + [string]$Target.role
        if ($Command -ceq "apply") {
            if ($lines.Count -ne 7 -or
                $lines[0] -cne "DEMO_WORKER_GATEWAY_SYNC=PASS" -or
                $lines[1] -cne "DEMO_WORKER_GATEWAY_SYNC_COMMAND=apply" -or
                $lines[2] -cne $roleLine -or
                $lines[3] -notmatch '^DEMO_WORKER_GATEWAY_SYNC_CHANGED=(true|false)$' -or
                $lines[4] -cne "DEMO_WORKER_GATEWAY_SYNC_SECRET_READ=true" -or
                $lines[5] -cne "DEMO_WORKER_GATEWAY_SYNC_SECRET_HASHED=false" -or
                $lines[6] -cne "DEMO_WORKER_GATEWAY_SYNC_SECRET_ECHOED=false") {
                throw "DEMO_WORKER_GATEWAY_SYNC_APPLY_OUTPUT_INVALID"
            }
            return [pscustomobject]@{
                command = "apply"
                role = [string]$Target.role
                changed = $lines[3].EndsWith("=true", [StringComparison]::Ordinal)
            }
        }
        if ($Command -ceq "inspect") {
            if ($lines.Count -ne 7 -or
                $lines[0] -cne "DEMO_WORKER_GATEWAY_SYNC=PASS" -or
                $lines[1] -cne "DEMO_WORKER_GATEWAY_SYNC_COMMAND=inspect" -or
                $lines[2] -cne $roleLine -or
                $lines[3] -cne "DEMO_WORKER_GATEWAY_SYNC_MATCH=true" -or
                $lines[4] -cne "DEMO_WORKER_GATEWAY_SYNC_SECRET_READ=true" -or
                $lines[5] -cne "DEMO_WORKER_GATEWAY_SYNC_SECRET_HASHED=false" -or
                $lines[6] -cne "DEMO_WORKER_GATEWAY_SYNC_SECRET_ECHOED=false") {
                throw "DEMO_WORKER_GATEWAY_SYNC_INSPECT_OUTPUT_INVALID"
            }
            return [pscustomobject]@{
                command = "inspect"
                role = [string]$Target.role
                match = $true
            }
        }
        if ($lines.Count -ne 8 -or
            $lines[0] -cne "DEMO_WORKER_GATEWAY_SYNC=PASS" -or
            $lines[1] -cne "DEMO_WORKER_GATEWAY_SYNC_COMMAND=probe" -or
            $lines[2] -cne $roleLine -or
            $lines[3] -cne "DEMO_WORKER_GATEWAY_SYNC_GATEWAY_AUTH=PASS" -or
            $lines[4] -cne "DEMO_WORKER_GATEWAY_SYNC_PROVIDER_CALL_COUNT=0" -or
            $lines[5] -cne "DEMO_WORKER_GATEWAY_SYNC_SECRET_READ=true" -or
            $lines[6] -cne "DEMO_WORKER_GATEWAY_SYNC_SECRET_HASHED=false" -or
            $lines[7] -cne "DEMO_WORKER_GATEWAY_SYNC_SECRET_ECHOED=false") {
            throw "DEMO_WORKER_GATEWAY_SYNC_PROBE_OUTPUT_INVALID"
        }
        return [pscustomobject]@{
            command = "probe"
            role = [string]$Target.role
            gateway_auth = $true
            provider_call_count = 0
        }
    }
    finally {
        if ($copiedToContainer) {
            $removeOutput = @(& $docker exec ([string]$Container.id) `
                /bin/rm -f -- $remoteHelperPath 2>&1)
            if ($LASTEXITCODE -ne 0 -or @($removeOutput).Count -ne 0) {
                throw "DEMO_WORKER_GATEWAY_SYNC_REMOTE_HELPER_CLEANUP_FAILED"
            }
            $absenceOutput = @(& $docker exec ([string]$Container.id) /bin/bash -c `
                ("test ! -e " + $remoteHelperPath + " && test ! -L " + $remoteHelperPath) 2>&1)
            if ($LASTEXITCODE -ne 0 -or @($absenceOutput).Count -ne 0) {
                throw "DEMO_WORKER_GATEWAY_SYNC_REMOTE_HELPER_RESIDUE_PRESENT"
            }
        }
        if ($null -ne $temporaryName) {
            $residueCount = Get-DemoWorkerGatewaySyncTempContainerCount -Name $temporaryName
            if ($residueCount -eq 1) {
                $null = @(& $docker rm --force $temporaryName 2>$null)
                if ($LASTEXITCODE -ne 0) {
                    throw "DEMO_WORKER_GATEWAY_SYNC_TEMP_CLEANUP_FAILED"
                }
                $residueCount = Get-DemoWorkerGatewaySyncTempContainerCount -Name $temporaryName
            }
            if ($residueCount -ne 0) {
                throw "DEMO_WORKER_GATEWAY_SYNC_TEMP_RESIDUE_PRESENT"
            }
        }
    }
}

function Invoke-DemoWorkerGatewayCredentialSync {
    param(
        [Parameter(Mandatory = $true)]$Paths,
        [Parameter(Mandatory = $true)]$Baseline,
        [Parameter(Mandatory = $true)]
        [ValidateSet("apply", "verify")]
        [string]$Mode,
        [int]$ChangedCount = -1
    )

    $source = Assert-DemoWorkerGatewaySyncHelper
    if ($Mode -ceq "verify" -and $ChangedCount -notin @(0, 1, 2)) {
        throw "DEMO_WORKER_GATEWAY_SYNC_CHANGED_COUNT_INVALID"
    }
    $results = @(Use-DockerConfig {
        foreach ($target in $workerGatewaySyncTargets) {
            $baselineRecords = @($Baseline.containers | Where-Object {
                [string]$_.name -ceq [string]$target.name
            })
            if ($baselineRecords.Count -ne 1) {
                throw "DEMO_WORKER_GATEWAY_SYNC_BASELINE_TARGET_INVALID"
            }
            $baselineRecord = $baselineRecords[0]
            if ($Mode -ceq "apply") {
                $fullCurrent = Get-ContainerProjection -Name ([string]$target.name)
                Assert-ContainerMatchesBaselineFrozenProjection `
                    -BaselineRecord $baselineRecord -Current $fullCurrent
            }
            $current = Get-DemoWorkerGatewaySyncContainerProjection -Name ([string]$target.name)
            if ([string]$current.id -cne [string]$baselineRecord.id -or
                [string]$current.image_id -cne [string]$baselineRecord.image_id -or
                [string]$current.restart_policy -cne [string]$baselineRecord.restart_policy -or
                [int64]$current.restart_count -ne [int64]$baselineRecord.restart_count) {
                throw ("DEMO_WORKER_GATEWAY_SYNC_IDENTITY_DRIFT:" + [string]$target.role)
            }
            if ($Mode -ceq "apply") {
                if ([string]$baselineRecord.state -cne "exited" -or
                    [int]$baselineRecord.exit_code -ne 0 -or
                    [string]$current.state -cne "exited" -or $current.exit_code -ne 0) {
                    throw ("DEMO_WORKER_GATEWAY_SYNC_PRESTATE_INVALID:" + [string]$target.role)
                }
                Write-Output (Invoke-DemoWorkerGatewaySyncHelper -Source $source `
                    -Target $target -Container $current -Command "apply")
            }
            else {
                if ([string]$current.state -cne "running" -or
                    [string]$current.health -cne "healthy") {
                    throw ("DEMO_WORKER_GATEWAY_SYNC_RUNTIME_INVALID:" + [string]$target.role)
                }
                Write-Output (Invoke-DemoWorkerGatewaySyncHelper -Source $source `
                    -Target $target -Container $current -Command "inspect")
                Write-Output (Invoke-DemoWorkerGatewaySyncHelper -Source $source `
                    -Target $target -Container $current -Command "probe")
            }
        }
    })
    if ($Mode -ceq "apply") {
        if ($results.Count -ne 2 -or
            @($results | Where-Object { [string]$_.command -cne "apply" }).Count -ne 0) {
            throw "DEMO_WORKER_GATEWAY_SYNC_APPLY_RESULT_INVALID"
        }
        $actualChangedCount = @($results | Where-Object { [bool]$_.changed }).Count
        Write-JournalRecord -Paths $Paths -Kind "worker-gateway-credential-sync" `
            -Status "applied" -SecretValueReadStatus "true" -Details @{
                repair_scope = "demo-only-forward-alignment"
                role_count = 2
                changed_count = $actualChangedCount
                helper_delivery = "read-only-bind"
                secret_in_argv = $false
                secret_in_environment = $false
                temporary_network_mode = "none"
                temporary_container_residue_count = 0
                provider_call_count = 0
                stale_credential_restore_allowed = $false
            }
        return [pscustomobject]@{ changed_count = $actualChangedCount }
    }
    if ($results.Count -ne 4 -or
        @($results | Where-Object { [string]$_.command -ceq "inspect" }).Count -ne 2 -or
        @($results | Where-Object { [string]$_.command -ceq "probe" }).Count -ne 2 -or
        @($results | Where-Object {
            [string]$_.command -ceq "probe" -and [int]$_.provider_call_count -ne 0
        }).Count -ne 0) {
        throw "DEMO_WORKER_GATEWAY_SYNC_VERIFY_RESULT_INVALID"
    }
    Write-JournalRecord -Paths $Paths -Kind "worker-gateway-credential-sync" `
        -Status "completed" -SecretValueReadStatus "true" -Details @{
            repair_scope = "demo-only-forward-alignment"
            role_count = 2
            changed_count = $ChangedCount
            inspected_count = 2
            authenticated_probe_count = 2
            authenticated_http_status = 403
            authenticated_reason = "CALL_PLAN_UNAVAILABLE"
            current_persistent_config_aligned = $true
            permanent_future_merge_guarantee = $false
            provider_call_count = 0
            temporary_container_residue_count = 0
            running_container_helper_delivery = "temporary-docker-copy"
            running_container_helper_residue_count = 0
            m5_history_modified = $false
            stale_credential_restore_allowed = $false
        }
    return [pscustomobject]@{
        changed_count = $ChangedCount
        inspected_count = 2
        authenticated_probe_count = 2
    }
}

function Get-ContainerProjection {
    param([Parameter(Mandatory = $true)][string]$Name)

    $documents = @(& $docker inspect $Name 2>$null | ConvertFrom-Json)
    if ($LASTEXITCODE -ne 0 -or $documents.Count -ne 1) {
        throw ("DEMO_CONTAINER_INSPECT_FAILED:" + $Name)
    }
    $container = $documents[0]
    $networks = @($container.NetworkSettings.Networks.PSObject.Properties.Name | Sort-Object)
    $mounts = @($container.Mounts | ForEach-Object {
        $mountNameProperty = $_.PSObject.Properties["Name"]
        $mountName = if ($null -eq $mountNameProperty) {
            ""
        }
        else {
            [string]$mountNameProperty.Value
        }
        [ordered]@{
            type = [string]$_.Type
            name = $mountName
            source = [string]$_.Source
            destination = [string]$_.Destination
            rw = [bool]$_.RW
        }
    } | Sort-Object destination)
    $published = @($container.HostConfig.PortBindings.PSObject.Properties | Where-Object {
        $null -ne $_.Value -and @($_.Value).Count -gt 0
    } | ForEach-Object {
        [ordered]@{
            container_port = [string]$_.Name
            host = @($_.Value | ForEach-Object {
                ([string]$_.HostIp + ":" + [string]$_.HostPort)
            } | Sort-Object)
        }
    } | Sort-Object container_port)
    $health = "missing"
    $healthProperty = $container.State.PSObject.Properties["Health"]
    if ($null -ne $healthProperty -and $null -ne $healthProperty.Value) {
        $health = [string]$healthProperty.Value.Status
    }
    return [ordered]@{
        name = $Name
        id = [string]$container.Id
        image_id = [string]$container.Image
        state = [string]$container.State.Status
        exit_code = [int]$container.State.ExitCode
        health = $health
        restart_count = [int64]$container.RestartCount
        restart_policy = [string]$container.HostConfig.RestartPolicy.Name
        network_mode = [string]$container.HostConfig.NetworkMode
        networks = $networks
        mounts = $mounts
        published_ports = $published
    }
}

function Get-ListenerCount {
    param([Parameter(Mandatory = $true)][int]$Port)
    return @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue).Count
}

function Get-HttpStatus {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [ValidateRange(1, 10)][int]$TimeoutSeconds = 3
    )

    $status = 0
    try {
        $request = [Net.HttpWebRequest]::Create($Uri)
        $request.Method = "POST"
        $request.ContentType = "application/json"
        $request.Timeout = $TimeoutSeconds * 1000
        $bytes = [Text.Encoding]::UTF8.GetBytes("{}")
        $request.ContentLength = $bytes.Length
        $stream = $request.GetRequestStream()
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Dispose()
        $response = $request.GetResponse()
        $status = [int]$response.StatusCode
        $response.Dispose()
    }
    catch [Net.WebException] {
        if ($null -ne $_.Exception.Response) {
            $status = [int]$_.Exception.Response.StatusCode
            $_.Exception.Response.Dispose()
        }
    }
    return $status
}

function Wait-HttpStatus {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][int]$ExpectedStatus,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        if ((Get-HttpStatus -Uri $Uri) -eq $ExpectedStatus) {
            return
        }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    throw ("DEMO_HTTP_STATUS_TIMEOUT:" + $ExpectedStatus)
}

function Get-ExactProcessRecord {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Parameter(Mandatory = $true)][string[]]$RequiredCommandFragments
    )

    $records = @(Get-CimInstance -ClassName Win32_Process `
        -Filter ("ProcessId = " + $ProcessId) -ErrorAction Stop)
    if ($records.Count -ne 1 -or [string]::IsNullOrWhiteSpace([string]$records[0].CommandLine)) {
        throw "DEMO_PROCESS_IDENTITY_UNAVAILABLE"
    }
    $commandLine = [string]$records[0].CommandLine
    foreach ($fragment in $RequiredCommandFragments) {
        if ($commandLine.IndexOf($fragment, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
            throw "DEMO_PROCESS_ARGUMENT_MISMATCH"
        }
    }
    return $records[0]
}

function Stop-ExactProcessTree {
    param(
        [Parameter(Mandatory = $true)][string]$PidPath,
        [Parameter(Mandatory = $true)][string[]]$RequiredCommandFragments,
        [int]$ListenerPort = 0
    )

    if (-not (Test-Path -LiteralPath $PidPath -PathType Leaf)) {
        return
    }
    $pidText = [IO.File]::ReadAllText($PidPath).Trim()
    if ($pidText -notmatch '^[1-9][0-9]{0,9}$') {
        throw "DEMO_PID_FILE_INVALID"
    }
    $processId = [int]$pidText
    $candidate = @(Get-Process -Id $processId -ErrorAction SilentlyContinue)
    if ($candidate.Count -eq 0) {
        if ($ListenerPort -le 0) {
            return
        }
        $orphanListeners = @(Get-NetTCPConnection -State Listen -LocalPort $ListenerPort `
            -ErrorAction SilentlyContinue)
        if ($orphanListeners.Count -eq 0) {
            return
        }
        if ($orphanListeners.Count -ne 1) {
            throw "DEMO_ORPHAN_LISTENER_CARDINALITY_INVALID"
        }
        $orphanListener = $orphanListeners[0]
        if ([string]$orphanListener.LocalAddress -cne "127.0.0.1") {
            throw "DEMO_ORPHAN_LISTENER_ADDRESS_INVALID"
        }
        $orphanPid = [int]$orphanListener.OwningProcess
        if ($orphanPid -eq $processId) {
            throw "DEMO_ORPHAN_LISTENER_PID_AMBIGUOUS"
        }
        $orphanRecord = Get-ExactProcessRecord -ProcessId $orphanPid `
            -RequiredCommandFragments $RequiredCommandFragments
        if ([int]$orphanRecord.ParentProcessId -ne $processId) {
            throw "DEMO_LISTENER_PARENT_MISMATCH"
        }
        $orphanProcess = Get-Process -Id $orphanPid -ErrorAction Stop
        try {
            Stop-Process -Id $orphanPid -Force -ErrorAction Stop
        }
        catch {
            if (@(Get-Process -Id $orphanPid -ErrorAction SilentlyContinue).Count -ne 0) {
                throw "DEMO_LISTENER_PROCESS_STOP_FAILED"
            }
        }
        if (-not $orphanProcess.WaitForExit(5000) -or
            @(Get-Process -Id $orphanPid -ErrorAction SilentlyContinue).Count -ne 0) {
            throw "DEMO_LISTENER_PROCESS_STOP_FAILED"
        }
        if ((Get-ListenerCount -Port $ListenerPort) -ne 0) {
            throw "DEMO_PROCESS_PORT_REMAINS_BOUND"
        }
        return
    }
    [void](Get-ExactProcessRecord -ProcessId $processId `
        -RequiredCommandFragments $RequiredCommandFragments)

    if ($ListenerPort -gt 0) {
        $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $ListenerPort `
            -ErrorAction SilentlyContinue)
        foreach ($listener in $listeners) {
            $listenerPid = [int]$listener.OwningProcess
            if ($listenerPid -eq $processId) {
                continue
            }
            $listenerRecord = Get-ExactProcessRecord -ProcessId $listenerPid `
                -RequiredCommandFragments $RequiredCommandFragments
            if ([int]$listenerRecord.ParentProcessId -ne $processId) {
                throw "DEMO_LISTENER_PARENT_MISMATCH"
            }
            $listenerProcess = Get-Process -Id $listenerPid -ErrorAction Stop
            try {
                Stop-Process -Id $listenerPid -Force -ErrorAction Stop
            }
            catch {
                if (@(Get-Process -Id $listenerPid -ErrorAction SilentlyContinue).Count -ne 0) {
                    throw "DEMO_LISTENER_PROCESS_STOP_FAILED"
                }
            }
            if (-not $listenerProcess.WaitForExit(5000) -or
                @(Get-Process -Id $listenerPid -ErrorAction SilentlyContinue).Count -ne 0) {
                throw "DEMO_LISTENER_PROCESS_STOP_FAILED"
            }
        }
    }

    $candidate = @(Get-Process -Id $processId -ErrorAction SilentlyContinue)
    if ($candidate.Count -eq 1) {
        try {
            Stop-Process -Id $processId -Force -ErrorAction Stop
        }
        catch {
            if (@(Get-Process -Id $processId -ErrorAction SilentlyContinue).Count -ne 0) {
                throw "DEMO_PROCESS_STOP_FAILED"
            }
        }
        if (-not $candidate[0].WaitForExit(5000) -and
            @(Get-Process -Id $processId -ErrorAction SilentlyContinue).Count -ne 0) {
            throw "DEMO_PROCESS_STOP_FAILED"
        }
    }
    if (@(Get-Process -Id $processId -ErrorAction SilentlyContinue).Count -ne 0) {
        throw "DEMO_PROCESS_STOP_FAILED"
    }
    if ($ListenerPort -gt 0 -and (Get-ListenerCount -Port $ListenerPort) -ne 0) {
        throw "DEMO_PROCESS_PORT_REMAINS_BOUND"
    }
}

function Start-DemoStateMcp {
    param([Parameter(Mandatory = $true)]$Paths)

    foreach ($target in @($Paths.StatePid, $Paths.StateStdout, $Paths.StateStderr)) {
        if (Test-Path -LiteralPath $target) {
            throw "DEMO_STATE_RUNTIME_TARGET_EXISTS"
        }
    }
    $arguments = @(
        "-m", "awakening.adapters.m4.state_http_runtime",
        "--m2-env", (Join-Path $workspace ".env.m2"),
        "--m4-env", (Join-Path $workspace ".env.m4"),
        "--fixture-state", (Join-Path $workspace "tmp\m4\state\runtime-state.json"),
        "--host", "127.0.0.1",
        "--port", "18191"
    )
    $oldPythonPath = $env:PYTHONPATH
    try {
        $env:PYTHONPATH = Join-Path $workspace "src"
        $process = Start-Process -FilePath $python -ArgumentList $arguments `
            -WorkingDirectory $workspace -WindowStyle Hidden `
            -RedirectStandardOutput $Paths.StateStdout `
            -RedirectStandardError $Paths.StateStderr -PassThru
    }
    finally {
        $env:PYTHONPATH = $oldPythonPath
    }
    [IO.File]::WriteAllText(
        $Paths.StatePid,
        [string]$process.Id,
        (New-Object Text.UTF8Encoding($false))
    )
    Wait-HttpStatus -Uri "http://127.0.0.1:18191/mcp" `
        -ExpectedStatus 401 -TimeoutSeconds $ReadyTimeoutSeconds
    Write-JournalRecord -Paths $Paths -Kind "state-mcp" -Status "started" `
        -Details @{ pid = $process.Id; port = 18191 }
}

function Stop-DemoStateMcp {
    param([Parameter(Mandatory = $true)]$Paths)

    Stop-ExactProcessTree -PidPath $Paths.StatePid -ListenerPort 18191 `
        -RequiredCommandFragments @(
            "awakening.adapters.m4.state_http_runtime",
            "--fixture-state",
            "runtime-state.json",
            "--port",
            "18191"
        )
    Write-JournalRecord -Paths $Paths -Kind "state-mcp" -Status "stopped"
}

function Stop-M4FailClosedGateway {
    $pidPath = Join-Path $workspace "tmp\m4\gateway\gateway.pid"
    Stop-ExactProcessTree -PidPath $pidPath -ListenerPort 18190 `
        -RequiredCommandFragments @(
            "awakening.model_gateway.m4.fail_closed_runtime",
            "gateway-credentials.env",
            "--port",
            "18190"
    )
}

function Test-DemoPublicGlobalIPv4 {
    param([Parameter(Mandatory = $true)][string]$Address)

    if ($Address -notmatch '^(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})$') {
        return $false
    }
    $octets = @($Address.Split('.') | ForEach-Object { [int]$_ })
    if ($octets.Count -ne 4 -or @($octets | Where-Object { $_ -gt 255 }).Count -ne 0) {
        return $false
    }

    $first = $octets[0]
    $second = $octets[1]
    $third = $octets[2]
    if ($first -eq 0 -or
        $first -eq 10 -or
        $first -eq 127 -or
        ($first -eq 100 -and $second -ge 64 -and $second -le 127) -or
        ($first -eq 169 -and $second -eq 254) -or
        ($first -eq 172 -and $second -ge 16 -and $second -le 31) -or
        ($first -eq 192 -and $second -eq 0 -and $third -eq 0) -or
        ($first -eq 192 -and $second -eq 0 -and $third -eq 2) -or
        ($first -eq 192 -and $second -eq 88 -and $third -eq 99) -or
        ($first -eq 192 -and $second -eq 168) -or
        ($first -eq 198 -and ($second -eq 18 -or $second -eq 19)) -or
        ($first -eq 198 -and $second -eq 51 -and $third -eq 100) -or
        ($first -eq 203 -and $second -eq 0 -and $third -eq 113) -or
        $first -ge 224) {
        return $false
    }
    return $true
}

function Get-DemoProviderBindingSha256 {
    param([Parameter(Mandatory = $true)][string[]]$Addresses)

    if ($Addresses.Count -lt 1 -or $Addresses.Count -gt 8) {
        throw "DEMO_PROVIDER_TRANSPORT_BINDING_INVALID"
    }
    $canonical = [string]::Join(',', $Addresses)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        $digest = $algorithm.ComputeHash([Text.Encoding]::ASCII.GetBytes($canonical))
    }
    finally {
        $algorithm.Dispose()
    }
    return ([BitConverter]::ToString($digest).Replace('-', '').ToLowerInvariant())
}

function Get-DemoProviderTransportBinding {
    $curlPath = Assert-RegularFile -Path $curl `
        -Reason "DEMO_PROVIDER_TRANSPORT_CURL_INVALID"
    $oldErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $dohOutput = @(& $curlPath `
            -q --silent --show-error --fail --ipv4 --http1.1 `
            --proto "=https" --tlsv1.2 --connect-timeout 10 --max-time 20 `
            --max-filesize 65536 --noproxy "*" `
            --header "Accept: application/dns-json" `
            $demoProviderDohUri 2>$null)
        $dohExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $oldErrorActionPreference
    }
    if ($dohExitCode -ne 0 -or $dohOutput.Count -eq 0) {
        throw "DEMO_PROVIDER_DOH_TRANSPORT_FAILED"
    }

    try {
        $response = (($dohOutput -join "`n") | ConvertFrom-Json -ErrorAction Stop)
        $questions = @($response.Question)
        if ([int]$response.Status -ne 0 -or $questions.Count -ne 1 -or
            ([string]$questions[0].name).TrimEnd('.') -cne $demoProviderHostname -or
            [int]$questions[0].type -ne 1) {
            throw "invalid"
        }
        $aAnswers = @($response.Answer | Where-Object { [int]$_.type -eq 1 })
    }
    catch {
        throw "DEMO_PROVIDER_DOH_RESPONSE_INVALID"
    }

    $resolvedIPv4 = @()
    foreach ($answer in $aAnswers) {
        $candidate = [string]$answer.data
        if (-not (Test-DemoPublicGlobalIPv4 -Address $candidate)) {
            throw "DEMO_PROVIDER_DOH_NON_GLOBAL_IPV4_REJECTED"
        }
        if ($resolvedIPv4 -cnotcontains $candidate) {
            $resolvedIPv4 += $candidate
        }
    }
    if ($resolvedIPv4.Count -lt 1 -or $resolvedIPv4.Count -gt 8) {
        throw "DEMO_PROVIDER_DOH_IPV4_COUNT_INVALID"
    }

    $reachableIPv4 = @()
    foreach ($candidate in $resolvedIPv4) {
        $resolveValue = "{0}:443:{1}" -f $demoProviderHostname, $candidate
        $oldErrorActionPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            $probeOutput = @(& $curlPath `
                -q --silent --show-error --output NUL `
                --write-out "%{http_code}|%{ssl_verify_result}|%{remote_ip}" `
                --ipv4 --http1.1 --proto "=https" --tlsv1.2 `
                --connect-timeout 5 --max-time 10 --noproxy "*" `
                --resolve $resolveValue --request POST `
                --header "Content-Type: application/json" `
                --header "Content-Length: 0" `
                $demoProviderProbeUri 2>$null)
            $probeExitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $oldErrorActionPreference
        }
        $probeProjection = ($probeOutput -join "").Trim()
        $probeParts = @($probeProjection.Split('|'))
        if ($probeExitCode -eq 0 -and $probeParts.Count -eq 3 -and
            [string]$probeParts[0] -match '^[2-5][0-9]{2}$' -and
            [string]$probeParts[1] -ceq '0' -and
            [string]$probeParts[2] -ceq $candidate) {
            $reachableIPv4 += $candidate
        }
    }
    if ($reachableIPv4.Count -lt 1 -or $reachableIPv4.Count -gt 8) {
        throw "DEMO_PROVIDER_TRANSPORT_PREFLIGHT_FAILED"
    }

    $bindingSha256 = Get-DemoProviderBindingSha256 -Addresses $reachableIPv4
    return [pscustomobject][ordered]@{
        reachable_ipv4 = [string[]]$reachableIPv4
        resolved_ipv4_count = $resolvedIPv4.Count
        reachable_ipv4_count = $reachableIPv4.Count
        binding_sha256 = $bindingSha256
        source = "google-doh"
    }
}

function Assert-DemoLiveGatewayMarkers {
    param(
        [Parameter(Mandatory = $true)]$Paths,
        [Parameter(Mandatory = $true)][int]$ExpectedResolverCount,
        [Parameter(Mandatory = $true)][string]$ExpectedBindingSha256
    )

    if ($ExpectedResolverCount -lt 1 -or $ExpectedResolverCount -gt 8 -or
        $ExpectedBindingSha256 -notmatch '^[0-9a-f]{64}$') {
        throw "DEMO_LIVE_MARKER_EXPECTATION_INVALID"
    }
    $readyPath = Assert-RegularFile -Path $Paths.LiveReady `
        -Reason "DEMO_LIVE_READY_EVIDENCE_INVALID"
    $ready = Get-Content -LiteralPath $readyPath -Raw -Encoding UTF8 |
        ConvertFrom-Json -ErrorAction Stop
    $expectedFields = @(
        "authorization_id", "host", "live_config_sha256", "manager_plan_count",
        "mode", "port", "provider_dns_override_count",
        "provider_dns_override_sha256", "provider_hostname",
        "provider_secret_echoed", "provider_secret_read", "run_id",
        "schema_version", "single_use_plan_claim_count", "single_use_plan_count"
    )
    $actualFields = @($ready.PSObject.Properties.Name | Sort-Object)
    if (@(Compare-Object -ReferenceObject $expectedFields `
        -DifferenceObject $actualFields).Count -ne 0) {
        throw "DEMO_LIVE_READY_FIELDS_INVALID"
    }
    $coreBinding = Read-CoreBinding -Paths $Paths
    if ([string]$ready.schema_version -cne "awakening.demo.live-gateway-ready.v1" -or
        [string]$ready.authorization_id -cne "AUTH-DEMO-001" -or
        [string]$ready.mode -cne "live" -or
        [string]$ready.run_id -cne [string]$coreBinding.run_id -or
        [string]$ready.live_config_sha256 -cne [string]$coreBinding.live_config_sha256 -or
        [string]$ready.host -cne "127.0.0.1" -or [int]$ready.port -ne 18190 -or
        [string]$ready.provider_hostname -cne $demoProviderHostname -or
        [int]$ready.provider_dns_override_count -ne $ExpectedResolverCount -or
        [string]$ready.provider_dns_override_sha256 -cne $ExpectedBindingSha256 -or
        $ready.provider_secret_read -ne $true -or
        [bool]$ready.provider_secret_echoed -or
        [int]$ready.single_use_plan_count -ne 3 -or
        [int]$ready.single_use_plan_claim_count -ne 0 -or
        [int]$ready.manager_plan_count -ne 0) {
        throw "DEMO_LIVE_READY_BINDING_INVALID"
    }
}

function Get-DemoLiveGatewaySecretReadStatus {
    param([Parameter(Mandatory = $true)]$Paths)

    if (Test-Path -LiteralPath $Paths.LiveReady -PathType Leaf) {
        try {
            $ready = Get-Content -LiteralPath $Paths.LiveReady -Raw -Encoding UTF8 |
                ConvertFrom-Json -ErrorAction Stop
            if ([string]$ready.schema_version -ceq
                "awakening.demo.live-gateway-ready.v1" -and
                $ready.provider_secret_read -eq $true -and
                -not [bool]$ready.provider_secret_echoed) {
                return "true"
            }
        }
        catch {
            return "unknown"
        }
    }
    if (-not (Test-Path -LiteralPath $Paths.LiveStdout -PathType Leaf)) {
        return "unknown"
    }
    try {
        $lines = [IO.File]::ReadAllLines($Paths.LiveStdout)
    }
    catch {
        return "unknown"
    }
    $read = @($lines | Where-Object {
        ([string]$_).StartsWith("AUTH_DEMO_001_GATEWAY_PROVIDER_SECRET_READ=")
    })
    $echo = @($lines | Where-Object {
        ([string]$_).StartsWith("AUTH_DEMO_001_GATEWAY_PROVIDER_SECRET_ECHOED=")
    })
    if ($read.Count -eq 1 -and [string]$read[0] -ceq
        "AUTH_DEMO_001_GATEWAY_PROVIDER_SECRET_READ=true" -and
        $echo.Count -eq 1 -and [string]$echo[0] -ceq
        "AUTH_DEMO_001_GATEWAY_PROVIDER_SECRET_ECHOED=false") {
        return "true"
    }
    return "unknown"
}

function Move-DemoFailedLiveEvidenceForRetry {
    param([Parameter(Mandatory = $true)]$Paths)

    $targets = @(
        $Paths.LivePid, $Paths.LiveStdout, $Paths.LiveStderr, $Paths.LiveReady
    )
    $present = @($targets | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf })
    if ($present.Count -eq 0) {
        return
    }
    foreach ($required in @($Paths.LivePid, $Paths.LiveStdout, $Paths.LiveStderr)) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
            throw "DEMO_LIVE_RETRY_EVIDENCE_SET_INVALID"
        }
    }
    if ($present.Count -notin @(3, 4) -or
        (Test-Path -LiteralPath (Join-Path $Paths.CoreRun "result.json"))) {
        throw "DEMO_LIVE_RETRY_EVIDENCE_SET_INVALID"
    }

    $pidText = [IO.File]::ReadAllText($Paths.LivePid).Trim()
    if ($pidText -notmatch '^[1-9][0-9]{0,9}$' -or
        @(Get-Process -Id ([int]$pidText) -ErrorAction SilentlyContinue).Count -ne 0) {
        throw "DEMO_LIVE_RETRY_PROCESS_STILL_ACTIVE"
    }
    $records = @([IO.File]::ReadAllLines($Paths.Journal) | ForEach-Object {
        $_ | ConvertFrom-Json -ErrorAction Stop
    })
    $failed = @($records | Where-Object {
        [string]$_.kind -ceq "demo-live-gateway" -and
        [string]$_.status -ceq "failed"
    })
    $stopped = @($records | Where-Object {
        [string]$_.kind -ceq "demo-live-gateway" -and
        [string]$_.status -ceq "stopped"
    })
    $started = @($records | Where-Object {
        [string]$_.kind -ceq "demo-live-gateway" -and
        [string]$_.status -ceq "started"
    })
    $archiveRecords = @($records | Where-Object {
        [string]$_.kind -ceq "demo-live-gateway-evidence"
    })
    $runRecords = @($records | Where-Object { [string]$_.kind -ceq "run-chain" })
    $nextRetryAttempt = $archiveRecords.Count + 1
    if ($nextRetryAttempt -notin @(1, 2) -or
        $failed.Count -ne $nextRetryAttempt -or
        $stopped.Count -ne $nextRetryAttempt -or
        $started.Count -ne 0 -or $runRecords.Count -ne 0 -or
        @($failed | Where-Object {
            [int]$_.details.provider_call_count -ne 0
        }).Count -ne 0) {
        throw "DEMO_LIVE_RETRY_NOT_AUTHORIZED"
    }
    for ($index = 1; $index -le $archiveRecords.Count; $index += 1) {
        $matching = @($archiveRecords | Where-Object {
            [int]$_.details.retry_attempt -eq $index -and
            [int]$_.details.provider_call_count -eq 0
        })
        if ($matching.Count -ne 1) {
            throw "DEMO_LIVE_RETRY_PRIOR_ARCHIVE_INVALID"
        }
    }

    $secretReadStatus = Get-DemoLiveGatewaySecretReadStatus -Paths $Paths
    $failClosedArchiveWindowId = [guid]::NewGuid().ToString("D").ToLowerInvariant()
    $archiveRoot = Join-Path $Paths.Recovery `
        ("demo-live-start-retry-" + $nextRetryAttempt)
    if (Test-Path -LiteralPath $archiveRoot) {
        throw "DEMO_LIVE_RETRY_ARCHIVE_EXISTS"
    }
    [IO.Directory]::CreateDirectory($Paths.Recovery) | Out-Null
    [IO.Directory]::CreateDirectory($archiveRoot) | Out-Null
    $rootFull = [IO.Path]::GetFullPath($Paths.Root).TrimEnd('\') + '\'
    $archiveFull = [IO.Path]::GetFullPath($archiveRoot).TrimEnd('\') + '\'
    if (-not $archiveFull.StartsWith($rootFull, [StringComparison]::OrdinalIgnoreCase)) {
        throw "DEMO_LIVE_RETRY_ARCHIVE_OUTSIDE_ROOT"
    }

    $files = @()
    foreach ($source in $present) {
        $sourceFull = [IO.Path]::GetFullPath($source)
        $item = Get-Item -LiteralPath $sourceFull -Force -ErrorAction Stop
        if ($item.PSIsContainer -or
            ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "DEMO_LIVE_RETRY_EVIDENCE_FILE_INVALID"
        }
        $beforeHash = (Get-FileHash -LiteralPath $sourceFull -Algorithm SHA256).Hash.ToLowerInvariant()
        $destination = Join-Path $archiveRoot $item.Name
        if (Test-Path -LiteralPath $destination) {
            throw "DEMO_LIVE_RETRY_ARCHIVE_COLLISION"
        }
        Move-Item -LiteralPath $sourceFull -Destination $destination -ErrorAction Stop
        $afterHash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($afterHash -cne $beforeHash) {
            throw "DEMO_LIVE_RETRY_ARCHIVE_HASH_MISMATCH"
        }
        $files += [ordered]@{
            name = $item.Name
            length = [int64]$item.Length
            sha256 = $beforeHash
        }
    }
    Write-JsonCreateNew -Path (Join-Path $archiveRoot "archive-manifest.json") `
        -Value ([ordered]@{
            schema_version = "awakening.demo.live-start-failure-archive.v1"
            execution_window_id = $DemoRunId.ToString("D").ToLowerInvariant()
            retry_attempt = $nextRetryAttempt
            fail_closed_archive_window_id = $failClosedArchiveWindowId
            file_count = $files.Count
            files = $files
            provider_secret_read_status = $secretReadStatus
            provider_call_count = 0
            overwrite = $false
        })
    Write-JournalRecord -Paths $Paths -Kind "demo-live-gateway-evidence" `
        -Status "archived" -SecretValueReadStatus $secretReadStatus `
        -Details @{
            retry_attempt = $nextRetryAttempt
            fail_closed_archive_window_id = $failClosedArchiveWindowId
            file_count = $files.Count
            provider_call_count = 0
        }
}

function Read-DemoLiveRetryArchiveManifest {
    param(
        [Parameter(Mandatory = $true)]$Paths,
        [Parameter(Mandatory = $true)][ValidateRange(1, 2)][int]$RetryAttempt
    )

    $archiveRoot = Join-Path $Paths.Recovery `
        ("demo-live-start-retry-" + $RetryAttempt)
    $manifestPath = Join-Path $archiveRoot "archive-manifest.json"
    [void](Assert-RegularDirectory -Path $archiveRoot `
        -Reason "DEMO_LIVE_RETRY_ARCHIVE_DIRECTORY_INVALID")
    [void](Assert-RegularFile -Path $manifestPath `
        -Reason "DEMO_LIVE_RETRY_ARCHIVE_MANIFEST_INVALID")
    $document = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 |
        ConvertFrom-Json -ErrorAction Stop
    $expectedDocumentFields = @(
        "schema_version",
        "execution_window_id",
        "retry_attempt",
        "fail_closed_archive_window_id",
        "file_count",
        "files",
        "provider_secret_read_status",
        "provider_call_count",
        "overwrite"
    ) | Sort-Object
    $actualDocumentFields = @($document.PSObject.Properties.Name | Sort-Object)
    $documentFieldDiff = @(Compare-Object -ReferenceObject $expectedDocumentFields `
        -DifferenceObject $actualDocumentFields)
    if ($documentFieldDiff.Count -ne 0 -or
        [string]$document.schema_version -cne
            "awakening.demo.live-start-failure-archive.v1" -or
        [string]$document.execution_window_id -cne
            $DemoRunId.ToString("D").ToLowerInvariant() -or
        [int]$document.retry_attempt -ne $RetryAttempt -or
        [string]$document.fail_closed_archive_window_id -notmatch
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' -or
        [string]$document.provider_secret_read_status -notin @("true", "unknown") -or
        [int]$document.provider_call_count -ne 0 -or
        [bool]$document.overwrite) {
        throw "DEMO_LIVE_RETRY_ARCHIVE_MANIFEST_INVALID"
    }

    $files = @($document.files)
    if ($files.Count -notin @(3, 4) -or
        [int]$document.file_count -ne $files.Count) {
        throw "DEMO_LIVE_RETRY_ARCHIVE_MANIFEST_INVALID"
    }
    $requiredNames = @(
        "demo-live-gateway.pid",
        "demo-live-gateway.stdout.log",
        "demo-live-gateway.stderr.log"
    )
    $allowedNames = @($requiredNames + "demo-live-gateway.ready.json")
    $seenNames = @{}
    foreach ($file in $files) {
        $expectedFileFields = @("length", "name", "sha256")
        $actualFileFields = @($file.PSObject.Properties.Name | Sort-Object)
        $fileFieldDiff = @(Compare-Object -ReferenceObject $expectedFileFields `
            -DifferenceObject $actualFileFields)
        $name = [string]$file.name
        if ($fileFieldDiff.Count -ne 0 -or
            $name -notin $allowedNames -or
            $seenNames.ContainsKey($name) -or
            [int64]$file.length -lt 0 -or
            [string]$file.sha256 -notmatch '^[0-9a-f]{64}$') {
            throw "DEMO_LIVE_RETRY_ARCHIVE_FILE_INVALID"
        }
        $seenNames[$name] = $true
        $archivedPath = Join-Path $archiveRoot $name
        $candidatePath = [IO.Path]::GetFullPath($archivedPath)
        $resolvedPath = [IO.Path]::GetFullPath(
            (Resolve-Path -LiteralPath $candidatePath -ErrorAction Stop).ProviderPath
        )
        $item = Get-Item -LiteralPath $resolvedPath -Force -ErrorAction Stop
        if (-not [StringComparer]::OrdinalIgnoreCase.Equals(
                $candidatePath, $resolvedPath
            ) -or $item.PSIsContainer -or
            ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "DEMO_LIVE_RETRY_ARCHIVE_FILE_INVALID"
        }
        $actualHash = (Get-FileHash -LiteralPath $resolvedPath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ([int64]$item.Length -ne [int64]$file.length -or
            $actualHash -cne [string]$file.sha256) {
            throw "DEMO_LIVE_RETRY_ARCHIVE_FILE_INVALID"
        }
    }
    foreach ($requiredName in $requiredNames) {
        if (-not $seenNames.ContainsKey($requiredName)) {
            throw "DEMO_LIVE_RETRY_ARCHIVE_FILE_INVALID"
        }
    }
    $actualArchiveNames = @(Get-ChildItem -LiteralPath $archiveRoot -Force |
        ForEach-Object { $_.Name } | Sort-Object)
    $expectedArchiveNames = @($allowedNames | Where-Object {
        $seenNames.ContainsKey($_)
    }) + "archive-manifest.json"
    $expectedArchiveNames = @($expectedArchiveNames | Sort-Object)
    $archiveDiff = @(Compare-Object -ReferenceObject $expectedArchiveNames `
        -DifferenceObject $actualArchiveNames)
    if ($archiveDiff.Count -ne 0) {
        throw "DEMO_LIVE_RETRY_ARCHIVE_FILE_SET_INVALID"
    }
    return $document
}

function Start-CoreLiveGateway {
    param(
        [Parameter(Mandatory = $true)]$Paths,
        [Parameter(Mandatory = $true)][string[]]$ResolvedProviderIPv4,
        [Parameter(Mandatory = $true)][int]$ReachableProviderIPv4Count,
        [Parameter(Mandatory = $true)][string]$ProviderBindingSha256,
        [ValidateRange(1, 3)][int]$LiveStartAttempt = 1
    )

    foreach ($target in @(
        $Paths.LivePid, $Paths.LiveStdout, $Paths.LiveStderr, $Paths.LiveReady
    )) {
        if (Test-Path -LiteralPath $target) {
            throw "DEMO_LIVE_RUNTIME_TARGET_EXISTS"
        }
    }
    if ($ResolvedProviderIPv4.Count -lt 1 -or $ResolvedProviderIPv4.Count -gt 8 -or
        $ReachableProviderIPv4Count -lt 1 -or
        $ReachableProviderIPv4Count -ne $ResolvedProviderIPv4.Count -or
        $ProviderBindingSha256 -notmatch '^[0-9a-f]{64}$' -or
        (Get-DemoProviderBindingSha256 -Addresses $ResolvedProviderIPv4) -cne
            $ProviderBindingSha256) {
        throw "DEMO_PROVIDER_TRANSPORT_BINDING_INVALID"
    }
    foreach ($candidate in $ResolvedProviderIPv4) {
        if (-not (Test-DemoPublicGlobalIPv4 -Address $candidate)) {
            throw "DEMO_PROVIDER_TRANSPORT_BINDING_INVALID"
        }
    }
    if (@($ResolvedProviderIPv4 | Sort-Object -Unique).Count -ne
        $ResolvedProviderIPv4.Count) {
        throw "DEMO_PROVIDER_TRANSPORT_BINDING_INVALID"
    }

    $providerResolverEnvironmentName = "AWAKENING_DEMO_PROVIDER_RESOLVED_IPV4"
    $providerResolverEnvironmentPath = "Env:\" + $providerResolverEnvironmentName
    $providerResolverEnvironmentPresent = Test-Path -LiteralPath $providerResolverEnvironmentPath
    $oldProviderResolverEnvironment = [Environment]::GetEnvironmentVariable(
        $providerResolverEnvironmentName,
        [EnvironmentVariableTarget]::Process
    )
    $oldPythonPath = $env:PYTHONPATH
    $childEnvironmentNames = @(
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"
    )
    $childEnvironment = @{}
    foreach ($name in $childEnvironmentNames) {
        $childEnvironment[$name] = [ordered]@{
            present = Test-Path -LiteralPath ("Env:\" + $name)
            value = [Environment]::GetEnvironmentVariable(
                $name,
                [EnvironmentVariableTarget]::Process
            )
        }
    }
    try {
        $env:PYTHONPATH = Join-Path $workspace "src"
        [Environment]::SetEnvironmentVariable(
            $providerResolverEnvironmentName,
            [string]::Join(',', $ResolvedProviderIPv4),
            [EnvironmentVariableTarget]::Process
        )
        foreach ($name in @("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")) {
            [Environment]::SetEnvironmentVariable(
                $name,
                $null,
                [EnvironmentVariableTarget]::Process
            )
        }
        [Environment]::SetEnvironmentVariable(
            "NO_PROXY",
            "*",
            [EnvironmentVariableTarget]::Process
        )
        $process = Start-Process -FilePath $python -ArgumentList @(
            $coreCli, "serve-gateway", "--mode", "live", "--run-dir", $Paths.CoreRun
        ) -WorkingDirectory $workspace -WindowStyle Hidden `
          -RedirectStandardOutput $Paths.LiveStdout `
          -RedirectStandardError $Paths.LiveStderr -PassThru
    }
    finally {
        $env:PYTHONPATH = $oldPythonPath
        foreach ($name in $childEnvironmentNames) {
            if ([bool]$childEnvironment[$name].present) {
                [Environment]::SetEnvironmentVariable(
                    $name,
                    [string]$childEnvironment[$name].value,
                    [EnvironmentVariableTarget]::Process
                )
            }
            else {
                [Environment]::SetEnvironmentVariable(
                    $name,
                    $null,
                    [EnvironmentVariableTarget]::Process
                )
            }
        }
        if ($providerResolverEnvironmentPresent) {
            [Environment]::SetEnvironmentVariable(
                $providerResolverEnvironmentName,
                $oldProviderResolverEnvironment,
                [EnvironmentVariableTarget]::Process
            )
        }
        else {
            [Environment]::SetEnvironmentVariable(
                $providerResolverEnvironmentName,
                $null,
                [EnvironmentVariableTarget]::Process
            )
        }
    }
    [IO.File]::WriteAllText(
        $Paths.LivePid,
        [string]$process.Id,
        (New-Object Text.UTF8Encoding($false))
    )
    Wait-HttpStatus -Uri "http://127.0.0.1:18190/v1/chat/completions" `
        -ExpectedStatus 401 -TimeoutSeconds $ReadyTimeoutSeconds
    Assert-DemoLiveGatewayMarkers -Paths $Paths `
        -ExpectedResolverCount $ResolvedProviderIPv4.Count `
        -ExpectedBindingSha256 $ProviderBindingSha256
    Write-JournalRecord -Paths $Paths -Kind "demo-live-gateway" -Status "started" `
        -SecretValueReadStatus "true" `
        -Details @{
            pid = $process.Id
            port = 18190
            provider_resolver_override_ipv4_count = $ResolvedProviderIPv4.Count
            provider_transport_reachable_ipv4_count = $ReachableProviderIPv4Count
            provider_resolver_binding_sha256 = $ProviderBindingSha256
            provider_resolver_override_scope = "child_process_only"
            provider_raw_response_persisted = $false
            provider_secret_reader = "demo-live-gateway"
            lifecycle_script_secret_read = $false
            live_start_attempt = $LiveStartAttempt
            system_network_modified = $false
        }
}

function Stop-CoreLiveGateway {
    param([Parameter(Mandatory = $true)]$Paths)

    Stop-ExactProcessTree -PidPath $Paths.LivePid -ListenerPort 18190 `
        -RequiredCommandFragments @(
            "agentteams_in_place_demo.py",
            "serve-gateway",
            "--mode",
            "live",
            "--run-dir",
            $Paths.CoreRun
        )
    Write-JournalRecord -Paths $Paths -Kind "demo-live-gateway" -Status "stopped"
}

function Assert-DemoLiveGatewayRuntimeBinding {
    param([Parameter(Mandatory = $true)]$Paths)

    $pidPath = Assert-RegularFile -Path $Paths.LivePid -Reason "DEMO_LIVE_PID_INVALID"
    $pidText = [IO.File]::ReadAllText($pidPath).Trim()
    if ($pidText -notmatch '^[1-9][0-9]{0,9}$') {
        throw "DEMO_LIVE_PID_INVALID"
    }
    $processId = [int]$pidText
    $requiredFragments = @(
        "agentteams_in_place_demo.py",
        "serve-gateway",
        "--mode",
        "live",
        "--run-dir",
        $Paths.CoreRun
    )
    [void](Get-ExactProcessRecord -ProcessId $processId `
        -RequiredCommandFragments $requiredFragments)

    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort 18190 `
        -ErrorAction SilentlyContinue)
    if ($listeners.Count -ne 1 -or
        [string]$listeners[0].LocalAddress -cne "127.0.0.1") {
        throw "DEMO_LIVE_LISTENER_IDENTITY_INVALID"
    }
    $listenerPid = [int]$listeners[0].OwningProcess
    if ($listenerPid -ne $processId) {
        $listenerRecord = Get-ExactProcessRecord -ProcessId $listenerPid `
            -RequiredCommandFragments $requiredFragments
        if ([int]$listenerRecord.ParentProcessId -ne $processId) {
            throw "DEMO_LIVE_LISTENER_PARENT_INVALID"
        }
    }

    $journalPath = Assert-RegularFile -Path $Paths.Journal `
        -Reason "DEMO_LIFECYCLE_JOURNAL_INVALID"
    $records = @([IO.File]::ReadAllLines($journalPath) | ForEach-Object {
        $_ | ConvertFrom-Json -ErrorAction Stop
    })
    $started = @($records | Where-Object {
        [string]$_.kind -ceq "demo-live-gateway" -and
        [string]$_.status -ceq "started"
    })
    if ($started.Count -ne 1) {
        throw "DEMO_LIVE_LIFECYCLE_BINDING_INVALID"
    }
    $liveStartAttempt = [int]$started[0].details.live_start_attempt
    $transport = @($records | Where-Object {
        [string]$_.kind -ceq "provider-transport-preflight" -and
        [string]$_.status -ceq "completed" -and
        [string]$_.details.phase -ceq "before-live-switch" -and
        $null -ne $_.details.PSObject.Properties["live_start_attempt"] -and
        [int]$_.details.live_start_attempt -eq $liveStartAttempt
    })
    if ($liveStartAttempt -notin @(1, 2, 3) -or $transport.Count -ne 1 -or
        [int]$started[0].details.pid -ne $processId -or
        [int]$started[0].details.port -ne 18190 -or
        $started[0].secret_value_read -ne $true -or
        [bool]$started[0].secret_value_hashed -or
        [bool]$started[0].secret_value_echoed -or
        [int]$transport[0].details.single_use_plan_claim_count -ne 0 -or
        [bool]$transport[0].details.authorization_header_sent -or
        [bool]$transport[0].details.provider_model_request_sent) {
        throw "DEMO_LIVE_LIFECYCLE_BINDING_INVALID"
    }
    $expectedCount = [int]$started[0].details.provider_resolver_override_ipv4_count
    $expectedHash = [string]$started[0].details.provider_resolver_binding_sha256
    if ($expectedCount -ne [int]$transport[0].details.reachable_ipv4_count -or
        $expectedHash -cne [string]$transport[0].details.binding_sha256) {
        throw "DEMO_LIVE_TRANSPORT_BINDING_INVALID"
    }
    Assert-DemoLiveGatewayMarkers -Paths $Paths `
        -ExpectedResolverCount $expectedCount `
        -ExpectedBindingSha256 $expectedHash
    if ((Get-HttpStatus -Uri "http://127.0.0.1:18190/v1/chat/completions") -ne 401) {
        throw "DEMO_LIVE_HTTP_BOUNDARY_INVALID"
    }
}

function Read-Baseline {
    param([Parameter(Mandatory = $true)]$Paths)

    [void](Assert-RegularFile -Path $Paths.Baseline -Reason "DEMO_BASELINE_INVALID")
    return (Get-Content -LiteralPath $Paths.Baseline -Raw -Encoding UTF8 | ConvertFrom-Json)
}

function New-CoreBinding {
    param([Parameter(Mandatory = $true)]$Paths)

    if (Test-Path -LiteralPath $Paths.CoreBinding) {
        throw "DEMO_CORE_BINDING_ALREADY_EXISTS"
    }
    $configPath = Assert-RegularFile `
        -Path (Join-Path $Paths.CoreRun "live-gateway-config.json") `
        -Reason "DEMO_CORE_LIVE_CONFIG_INVALID"
    $config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 |
        ConvertFrom-Json
    $runId = [string]$config.state_binding.run_id
    $programId = [string]$config.state_binding.program_id
    $snapshotId = [string]$config.state_binding.runtime_config_snapshot_id
    $uuidV4 = '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    $uuidAny = '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    if ([string]$config.authorization_id -cne "AUTH-DEMO-001" -or
        $runId -notmatch $uuidV4 -or $programId -notmatch $uuidAny -or
        $snapshotId -notmatch $uuidAny) {
        throw "DEMO_CORE_LIVE_BINDING_INVALID"
    }

    $packageNames = @(
        "role_project_architect.json",
        "execution_evidence_coach.json",
        "independent_quality_reviewer.json"
    )
    $requestIds = @()
    foreach ($packageName in $packageNames) {
        $packagePath = Assert-RegularFile `
            -Path (Join-Path $Paths.CoreRun ("packages\" + $packageName)) `
            -Reason "DEMO_CORE_PACKAGE_INVALID"
        $package = Get-Content -LiteralPath $packagePath -Raw -Encoding UTF8 |
            ConvertFrom-Json
        $requestId = [string]$package.demo_request_id
        if ([string]$package.authorization_id -cne "AUTH-DEMO-001" -or
            $requestId -notmatch $uuidV4 -or
            [string]$package.state_binding.run_id -cne $runId -or
            [string]$package.state_binding.program_id -cne $programId -or
            [string]$package.state_binding.runtime_config_snapshot_id -cne $snapshotId) {
            throw "DEMO_CORE_PACKAGE_BINDING_INVALID"
        }
        $requestIds += $requestId
    }
    $uniqueRequestIds = @($requestIds | Sort-Object -Unique)
    if ($uniqueRequestIds.Count -ne 1) {
        throw "DEMO_CORE_REQUEST_BINDING_INVALID"
    }

    $binding = [ordered]@{
        schema_version = "awakening.demo.core-binding.v1"
        execution_window_id = $DemoRunId.ToString("D").ToLowerInvariant()
        authorization_id = "AUTH-DEMO-001"
        run_id = $runId
        demo_request_id = [string]$uniqueRequestIds[0]
        program_id = $programId
        runtime_config_snapshot_id = $snapshotId
        live_config_sha256 = (Get-FileHash -LiteralPath $configPath -Algorithm SHA256).Hash.ToLowerInvariant()
        package_count = 3
        captured_at_utc = [DateTime]::UtcNow.ToString("o")
    }
    Write-JsonCreateNew -Path $Paths.CoreBinding -Value $binding
    return $binding
}

function Read-CoreBinding {
    param([Parameter(Mandatory = $true)]$Paths)

    $bindingPath = Assert-RegularFile -Path $Paths.CoreBinding `
        -Reason "DEMO_CORE_BINDING_INVALID"
    $binding = Get-Content -LiteralPath $bindingPath -Raw -Encoding UTF8 |
        ConvertFrom-Json
    $configPath = Assert-RegularFile `
        -Path (Join-Path $Paths.CoreRun "live-gateway-config.json") `
        -Reason "DEMO_CORE_LIVE_CONFIG_INVALID"
    $actualConfigHash = (Get-FileHash -LiteralPath $configPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $uuidV4 = '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    if ([string]$binding.schema_version -cne "awakening.demo.core-binding.v1" -or
        [string]$binding.execution_window_id -cne $DemoRunId.ToString("D").ToLowerInvariant() -or
        [string]$binding.authorization_id -cne "AUTH-DEMO-001" -or
        [string]$binding.run_id -notmatch $uuidV4 -or
        [string]$binding.demo_request_id -notmatch $uuidV4 -or
        [int]$binding.package_count -ne 3 -or
        [string]$binding.live_config_sha256 -cne $actualConfigHash) {
        throw "DEMO_CORE_BINDING_INVALID"
    }
    return $binding
}

function Assert-ContainerMatchesBaselineIdentity {
    param(
        [Parameter(Mandatory = $true)]$BaselineRecord,
        [Parameter(Mandatory = $true)]$Current
    )
    if ([string]$Current.id -cne [string]$BaselineRecord.id -or
        [string]$Current.image_id -cne [string]$BaselineRecord.image_id -or
        [string]$Current.restart_policy -cne [string]$BaselineRecord.restart_policy) {
        throw ("DEMO_CONTAINER_IDENTITY_DRIFT:" + [string]$Current.name)
    }
}

function Assert-ContainerMatchesBaselineFrozenProjection {
    param(
        [Parameter(Mandatory = $true)]$BaselineRecord,
        [Parameter(Mandatory = $true)]$Current
    )

    Assert-ContainerMatchesBaselineIdentity -BaselineRecord $BaselineRecord -Current $Current
    $currentMounts = @($Current.mounts)
    $baselineMounts = @($BaselineRecord.mounts)
    $currentMountKeys = @($currentMounts | ForEach-Object {
        [string]$_.destination
    } | Sort-Object)
    $baselineMountKeys = @($baselineMounts | ForEach-Object {
        [string]$_.destination
    } | Sort-Object)
    $mountsMatch = (
        $currentMounts.Count -eq $baselineMounts.Count -and
        @($currentMountKeys | Sort-Object -Unique).Count -eq $currentMountKeys.Count -and
        @($baselineMountKeys | Sort-Object -Unique).Count -eq $baselineMountKeys.Count -and
        [string]::Join("|", $currentMountKeys) -ceq
            [string]::Join("|", $baselineMountKeys)
    )
    if ($mountsMatch) {
        foreach ($currentMount in $currentMounts) {
            $baselineMatches = @($baselineMounts | Where-Object {
                [string]$_.destination -ceq [string]$currentMount.destination
            })
            if ($baselineMatches.Count -ne 1) {
                $mountsMatch = $false
                break
            }
            $baselineMount = $baselineMatches[0]
            if ([string]$currentMount.type -cne [string]$baselineMount.type -or
                [string]$currentMount.name -cne [string]$baselineMount.name -or
                [string]$currentMount.source -cne [string]$baselineMount.source -or
                [bool]$currentMount.rw -ne [bool]$baselineMount.rw) {
                $mountsMatch = $false
                break
            }
        }
    }
    $currentPublished = @($Current.published_ports)
    $baselinePublished = @($BaselineRecord.published_ports)
    $currentPublishedKeys = @($currentPublished | ForEach-Object {
        [string]$_.container_port
    } | Sort-Object)
    $baselinePublishedKeys = @($baselinePublished | ForEach-Object {
        [string]$_.container_port
    } | Sort-Object)
    $publishedMatch = (
        $currentPublished.Count -eq $baselinePublished.Count -and
        @($currentPublishedKeys | Sort-Object -Unique).Count -eq $currentPublishedKeys.Count -and
        @($baselinePublishedKeys | Sort-Object -Unique).Count -eq $baselinePublishedKeys.Count -and
        [string]::Join("|", $currentPublishedKeys) -ceq
            [string]::Join("|", $baselinePublishedKeys)
    )
    if ($publishedMatch) {
        foreach ($currentPort in $currentPublished) {
            $baselineMatches = @($baselinePublished | Where-Object {
                [string]$_.container_port -ceq [string]$currentPort.container_port
            })
            if ($baselineMatches.Count -ne 1 -or
                [string]::Join("|", @($currentPort.host)) -cne
                    [string]::Join("|", @($baselineMatches[0].host))) {
                $publishedMatch = $false
                break
            }
        }
    }
    if ([string]$Current.name -cne [string]$BaselineRecord.name -or
        [string]$Current.state -cne [string]$BaselineRecord.state -or
        [int]$Current.exit_code -ne [int]$BaselineRecord.exit_code -or
        [string]$Current.health -cne [string]$BaselineRecord.health -or
        [int64]$Current.restart_count -ne [int64]$BaselineRecord.restart_count -or
        [string]$Current.network_mode -cne [string]$BaselineRecord.network_mode -or
        [string]::Join("|", @($Current.networks)) -cne
            [string]::Join("|", @($BaselineRecord.networks)) -or
        -not $mountsMatch -or -not $publishedMatch) {
        throw ("DEMO_CONTAINER_FROZEN_PROJECTION_DRIFT:" + [string]$Current.name)
    }
}

function Restore-Containers {
    param([Parameter(Mandatory = $true)]$Baseline)

    $restoreFailures = [Collections.Generic.List[string]]::new()
    Use-DockerConfig {
        foreach ($name in $managedContainerStopOrder) {
            try {
                $baselineRecord = @($Baseline.containers | Where-Object { $_.name -ceq $name })
                if ($baselineRecord.Count -ne 1) {
                    throw ("DEMO_BASELINE_CONTAINER_MISSING:" + $name)
                }
                $current = Get-ContainerProjection -Name $name
                Assert-ContainerMatchesBaselineIdentity -BaselineRecord $baselineRecord[0] `
                    -Current $current
                $desired = [string]$baselineRecord[0].state
                if ($desired -ceq "exited" -and $current.state -ceq "running") {
                    $timeout = if ($name -ceq "awakening-m4-host-relay") { 10 } else { 30 }
                    & $docker stop --time $timeout $name 2>$null | Out-Null
                    if ($LASTEXITCODE -ne 0) {
                        $afterCommand = Get-ContainerProjection -Name $name
                        if ($afterCommand.state -cne "exited") {
                            throw ("DEMO_CONTAINER_STOP_COMMAND_FAILED:" + $name)
                        }
                    }
                }
                elseif ($desired -ceq "running" -and $current.state -ceq "exited") {
                    & $docker start $name 2>$null | Out-Null
                    if ($LASTEXITCODE -ne 0) {
                        throw ("DEMO_CONTAINER_RESTART_FAILED:" + $name)
                    }
                }
                elseif ($current.state -cne $desired) {
                    throw ("DEMO_CONTAINER_RESTORE_STATE_UNSUPPORTED:" + $name)
                }

                $after = Get-ContainerProjection -Name $name
                Assert-ContainerMatchesBaselineIdentity -BaselineRecord $baselineRecord[0] `
                    -Current $after
                if ($after.state -cne $desired -or
                    [int64]$after.restart_count -ne [int64]$baselineRecord[0].restart_count) {
                    throw ("DEMO_CONTAINER_RESTORE_VERIFY_FAILED:" + $name)
                }
                if ($desired -ceq "exited" -and [int]$after.exit_code -ne 0) {
                    throw ("DEMO_CONTAINER_RESTORE_EXIT_CODE_INVALID:" + $name)
                }
            }
            catch {
                [void]$restoreFailures.Add($name)
            }
        }
    }
    if ($restoreFailures.Count -ne 0) {
        throw ("DEMO_CONTAINER_RESTORE_FAILED:" +
            ((@($restoreFailures | Sort-Object -Unique)) -join ","))
    }
}

function Invoke-StopRestoreInternal {
    param(
        [Parameter(Mandatory = $true)]$Paths,
        [Parameter(Mandatory = $true)]$Baseline
    )

    $failures = @()
    try { Stop-CoreLiveGateway -Paths $Paths } catch { $failures += "demo-live-gateway" }
    try {
        Use-DockerConfig { Remove-DemoWorkerGatewaySyncTempResidue }
    }
    catch { $failures += "worker-gateway-sync-temp-cleanup" }

    $agentRunning = $false
    try {
        $agentStates = @(Use-DockerConfig {
            foreach ($name in $agentContainers) {
                Write-Output ([string](Get-ContainerProjection -Name $name).state)
            }
        })
        $agentRunning = $agentStates -ccontains "running"
        if ($agentRunning -and (Get-ListenerCount -Port 18190) -eq 0) {
            Invoke-M4Script -Path $startFailClosed `
                -Arguments @(
                    "-LaunchViaCim",
                    "-WindowId", ([guid]::NewGuid().ToString("D"))
                ) `
                -SuccessMarkers @("M4_FAIL_CLOSED_GATEWAY_START=PASS")
        }
    }
    catch { $failures += "fail-closed-restore" }

    try { Stop-DemoStateMcp -Paths $Paths } catch { $failures += "state-mcp" }
    try { Restore-Containers -Baseline $Baseline } catch { $failures += "containers" }
    try {
        $runArchiveManifest = Join-Path $workspace (
            "tmp\m4\gateway\archives\" +
            $DemoRunId.ToString("D").ToLowerInvariant() +
            "\pre-fail-closed-start\archive-manifest.json"
        )
        if ((Test-Path -LiteralPath $Paths.FailClosedMarker -PathType Leaf) -or
            (Test-Path -LiteralPath $Paths.FailClosedRecoveryMarker -PathType Leaf) -or
            (Test-Path -LiteralPath $Paths.FailClosedRelayRecoveryMarker -PathType Leaf) -or
            (Test-Path -LiteralPath $Paths.FailClosedLogPolicyRecoveryMarker -PathType Leaf) -or
            (Test-Path -LiteralPath $Paths.FailClosedWiringRecoveryMarker -PathType Leaf) -or
            (Test-Path -LiteralPath $Paths.FailClosedDemoRelayStageRecoveryMarker -PathType Leaf) -or
            (Test-Path -LiteralPath $Paths.FailClosedDemoRelayPrestateBoundaryRecoveryMarker -PathType Leaf) -or
            (Test-Path -LiteralPath $runArchiveManifest -PathType Leaf)) {
            Stop-M4FailClosedGateway
            $archiveId = [guid]::NewGuid()
            Invoke-M4Script -Path $archiveGatewayEvidence -Arguments @(
                "-RuntimeKind", "FailClosed",
                "-WindowId", $archiveId.ToString("D"),
                "-Phase", "pre-live-fail-closed"
            ) -SuccessMarkers @(
                "M4_GATEWAY_EVIDENCE_ARCHIVE=PASS",
                "M4_GATEWAY_EVIDENCE_ARCHIVE=NOT_REQUIRED"
            )
        }
    }
    catch { $failures += "fail-closed-stop" }

    foreach ($port in $ports) {
        if ((Get-ListenerCount -Port $port) -ne 0) {
            $failures += ("port-" + $port)
        }
    }

    try {
        Use-DockerConfig {
            foreach ($baselineRecord in $Baseline.containers) {
                $current = Get-ContainerProjection -Name ([string]$baselineRecord.name)
                Assert-ContainerMatchesBaselineIdentity -BaselineRecord $baselineRecord `
                    -Current $current
                if ($current.state -cne [string]$baselineRecord.state -or
                    [int64]$current.restart_count -ne [int64]$baselineRecord.restart_count) {
                    throw ("DEMO_POSTSTATE_MISMATCH:" + [string]$baselineRecord.name)
                }
            }
        }
    }
    catch { $failures += "exact-eight-postverify" }

    if ($failures.Count -ne 0) {
        Write-JournalRecord -Paths $Paths -Kind "restore" -Status "failed" `
            -Details @{ failures = @($failures | Sort-Object -Unique) }
        throw ("DEMO_RESTORE_FAILED:" + ((@($failures | Sort-Object -Unique)) -join ","))
    }
    Write-JournalRecord -Paths $Paths -Kind "restore" -Status "completed" `
        -Details @{ exact_container_count = 8; listener_count = 0 }
}

function Move-DemoStateRuntimeEvidenceToArchive {
    param([Parameter(Mandatory = $true)]$Paths)

    $allTargets = @(
        $Paths.StatePid,
        $Paths.StateStdout,
        $Paths.StateStderr
    )
    $targets = @($allTargets | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf })
    if ($targets.Count -eq 0) {
        return
    }
    if ($targets.Count -ne 3) {
        throw "DEMO_STATE_RUNTIME_EVIDENCE_SET_INVALID"
    }
    if ((Get-ListenerCount -Port 18191) -ne 0) {
        throw "DEMO_STATE_RUNTIME_ARCHIVE_LISTENER_ACTIVE"
    }
    [void](Assert-RegularDirectory -Path $Paths.Root `
        -Reason "DEMO_RECOVERY_PARENT_INVALID")
    $archiveRoot = $Paths.Recovery
    if (-not (Test-Path -LiteralPath $archiveRoot)) {
        [void][IO.Directory]::CreateDirectory($archiveRoot)
    }
    [void](Assert-RegularDirectory -Path $archiveRoot `
        -Reason "DEMO_STATE_RUNTIME_ARCHIVE_ROOT_INVALID")
    $archiveId = [guid]::NewGuid().ToString("D").ToLowerInvariant()
    $archiveDirectory = Join-Path $archiveRoot $archiveId
    [void][IO.Directory]::CreateDirectory($archiveDirectory)
    $stateArchive = Join-Path $archiveDirectory "state-mcp"
    [void][IO.Directory]::CreateDirectory($stateArchive)
    $evidence = @()
    foreach ($target in $targets) {
        $candidate = [IO.Path]::GetFullPath($target)
        $resolved = [IO.Path]::GetFullPath(
            (Resolve-Path -LiteralPath $candidate -ErrorAction Stop).ProviderPath
        )
        $item = Get-Item -LiteralPath $resolved -Force -ErrorAction Stop
        if (-not [StringComparer]::OrdinalIgnoreCase.Equals($candidate, $resolved) -or
            $item.PSIsContainer -or
            ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "DEMO_STATE_RUNTIME_EVIDENCE_INVALID"
        }
        if ([StringComparer]::OrdinalIgnoreCase.Equals($target, $Paths.StatePid) -and
            $item.Length -le 0) {
            throw "DEMO_STATE_RUNTIME_PID_EVIDENCE_INVALID"
        }
        $beforeHash = (Get-FileHash -LiteralPath $resolved -Algorithm SHA256).Hash.ToLowerInvariant()
        $destination = Join-Path $stateArchive ([IO.Path]::GetFileName($target))
        [IO.File]::Move($target, $destination)
        $afterHash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($afterHash -cne $beforeHash) {
            throw "DEMO_STATE_RUNTIME_ARCHIVE_HASH_MISMATCH"
        }
        $evidence += [ordered]@{
            name = [IO.Path]::GetFileName($target)
            length = [int64]$item.Length
            sha256 = $afterHash
        }
    }
    Write-JsonCreateNew -Path (Join-Path $archiveDirectory "manifest.json") -Value ([ordered]@{
        schema_version = "awakening.demo.state-runtime-archive.v1"
        execution_window_id = $DemoRunId.ToString("D").ToLowerInvariant()
        archive_id = $archiveId
        files = $evidence
        secret_value_read = $false
        provider_called = $false
    })
    Write-JournalRecord -Paths $Paths -Kind "state-mcp-evidence" -Status "archived" `
        -Details @{
            archive_id = $archiveId
            file_count = $targets.Count
        }
}

function Assert-ExactPrepareMarker {
    param(
        [Parameter(Mandatory = $true)][string[]]$Lines,
        [Parameter(Mandatory = $true)][string]$Marker
    )

    if (@($Lines | Where-Object { [string]$_ -ceq $Marker }).Count -ne 1) {
        throw ("DEMO_RESUME_PREPARE_MARKER_INVALID:" + $Marker.Split('=')[0])
    }
}

function Assert-AttemptSixRecoveryEvidence {
    param(
        [Parameter(Mandatory = $true)]$Paths,
        [Parameter(Mandatory = $true)]$Baseline,
        [Parameter(Mandatory = $true)][object[]]$Records
    )

    $journalPath = Assert-RegularFile -Path $Paths.Journal `
        -Reason "DEMO_RESUME_ATTEMPT6_JOURNAL_INVALID"
    $journalItem = Get-Item -LiteralPath $journalPath -Force -ErrorAction Stop
    $journalHash = (Get-FileHash -LiteralPath $journalPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($journalItem.Length -ne 17172 -or
        $journalHash -cne "3a108d01185e075ad70893212bead29470950e55b30d181f9091d58d3dd7b188" -or
        $Records.Count -ne 49) {
        throw "DEMO_RESUME_ATTEMPT6_JOURNAL_PIN_INVALID"
    }

    $suffix = @($Records[39..48])
    if ([string]$suffix[0].kind -cne "resume-infrastructure-wiring-recovery" -or
        [string]$suffix[0].status -cne "started" -or
        [string]$suffix[0].details.recovery_id -cne "bcd067ac-a126-469a-aae2-54c3a74ba95b" -or
        [int]$suffix[0].details.resume_attempt -ne 5 -or
        [int]$suffix[0].details.prepare_invocation_count -ne 0 -or
        [int]$suffix[0].details.provider_call_count -ne 0 -or
        [string]$suffix[1].kind -cne "state-mcp" -or
        [string]$suffix[1].status -cne "started" -or
        [int]$suffix[1].details.pid -ne 5948 -or
        [int]$suffix[1].details.port -ne 18191 -or
        [string]$suffix[2].kind -cne "fail-closed-gateway" -or
        [string]$suffix[2].status -cne "started" -or
        [string]$suffix[2].details.window_id -cne "ec5092a8-5e2c-48eb-9070-7cd9c6e77d67" -or
        [int]$suffix[2].details.provider_call_count -ne 0 -or
        [string]$suffix[3].kind -cne "resume-infrastructure-wiring-recovery" -or
        [string]$suffix[3].status -cne "failed" -or
        [string]$suffix[3].details.recovery_id -cne "bcd067ac-a126-469a-aae2-54c3a74ba95b" -or
        [int]$suffix[3].details.resume_attempt -ne 5 -or
        [int]$suffix[3].details.prepare_invocation_count -ne 0 -or
        [int]$suffix[3].details.provider_call_count -ne 0 -or
        [string]$suffix[3].details.failure_code -cne
            "DEMO_M4_SCRIPT_FAILED:Start-AgentTeamsDemoHostRelay.ps1:DEMO_CHILD_FAILURE_UNCLASSIFIED" -or
        [string]$suffix[4].kind -cne "demo-live-gateway" -or
        [string]$suffix[4].status -cne "stopped" -or
        [string]$suffix[5].kind -cne "state-mcp" -or
        [string]$suffix[5].status -cne "stopped" -or
        [string]$suffix[6].kind -cne "restore" -or
        [string]$suffix[6].status -cne "failed" -or
        @($suffix[6].details.failures).Count -ne 1 -or
        [string]@($suffix[6].details.failures)[0] -cne "fail-closed-stop" -or
        [string]$suffix[7].kind -cne "demo-live-gateway" -or
        [string]$suffix[7].status -cne "stopped" -or
        [string]$suffix[8].kind -cne "state-mcp" -or
        [string]$suffix[8].status -cne "stopped" -or
        [string]$suffix[9].kind -cne "restore" -or
        [string]$suffix[9].status -cne "completed" -or
        [int]$suffix[9].details.exact_container_count -ne 8 -or
        [int]$suffix[9].details.listener_count -ne 0) {
        throw "DEMO_RESUME_ATTEMPT6_ORDERED_RECOVERY_INVALID"
    }

    foreach ($record in $Records) {
        if ([bool]$record.secret_value_read -or [bool]$record.secret_value_hashed -or
            [bool]$record.secret_value_echoed) {
            throw "DEMO_RESUME_ATTEMPT6_SECRET_HISTORY_INVALID"
        }
        $providerProperty = $record.details.PSObject.Properties["provider_call_count"]
        if ($null -ne $providerProperty -and [int]$providerProperty.Value -ne 0) {
            throw "DEMO_RESUME_ATTEMPT6_PROVIDER_HISTORY_INVALID"
        }
    }
    if (@($Records | Where-Object {
        [string]$_.kind -ceq "prepare" -and [string]$_.status -ceq "completed"
    }).Count -ne 1 -or
        @($Records | Where-Object {
            [string]$_.kind -ceq "infrastructure" -and [string]$_.status -ceq "ready"
        }).Count -ne 0 -or
        @($Records | Where-Object { [string]$_.kind -ceq "human-request" }).Count -ne 0 -or
        @($Records | Where-Object {
            [string]$_.kind -ceq "demo-live-gateway" -and [string]$_.status -ceq "started"
        }).Count -ne 0 -or
        @($Records | Where-Object { [string]$_.kind -ceq "run-chain" }).Count -ne 0) {
        throw "DEMO_RESUME_ATTEMPT6_HISTORY_SCOPE_INVALID"
    }

    $attemptKinds = @(
        "resume-infrastructure",
        "resume-infrastructure-recovery",
        "resume-infrastructure-relay-recovery",
        "resume-infrastructure-log-policy-recovery",
        "resume-infrastructure-wiring-recovery"
    )
    foreach ($attemptKind in $attemptKinds) {
        $attemptRecords = @($Records | Where-Object { [string]$_.kind -ceq $attemptKind })
        if ($attemptRecords.Count -ne 2 -or
            @($attemptRecords | Where-Object { [string]$_.status -ceq "started" }).Count -ne 1 -or
            @($attemptRecords | Where-Object { [string]$_.status -ceq "failed" }).Count -ne 1 -or
            @($attemptRecords | Where-Object { [string]$_.status -ceq "completed" }).Count -ne 0) {
            throw "DEMO_RESUME_ATTEMPT6_PRIOR_ATTEMPT_SET_INVALID"
        }
    }

    $archiveManifestPath = Join-Path $workspace `
        "tmp\m4\gateway\archives\0c6c1b97-4fa5-4de7-b2da-9ca24f30e22a\pre-live-fail-closed\archive-manifest.json"
    $archiveManifestPath = Assert-RegularFile -Path $archiveManifestPath `
        -Reason "DEMO_RESUME_ATTEMPT6_CLEANUP_MANIFEST_INVALID"
    $archiveManifestItem = Get-Item -LiteralPath $archiveManifestPath -Force -ErrorAction Stop
    if ($archiveManifestItem.Length -ne 1232 -or
        (Get-FileHash -LiteralPath $archiveManifestPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne
            "30616330cf8fd76f0701a690729ddf1673fbf505ad83fc3d07d1442465e7ba71") {
        throw "DEMO_RESUME_ATTEMPT6_CLEANUP_MANIFEST_PIN_INVALID"
    }
    $archiveManifest = Get-Content -LiteralPath $archiveManifestPath -Raw -Encoding UTF8 |
        ConvertFrom-Json
    if ([string]$archiveManifest.schema_version -cne "awakening.m4.gateway-evidence-archive.v1" -or
        [string]$archiveManifest.window_id -cne "0c6c1b97-4fa5-4de7-b2da-9ca24f30e22a" -or
        [string]$archiveManifest.phase -cne "pre-live-fail-closed" -or
        [string]$archiveManifest.runtime_kind -cne "FailClosed" -or
        [int]$archiveManifest.recorded_pid -ne 25440 -or
        [int]$archiveManifest.listener_port -ne 18190 -or
        [bool]$archiveManifest.listener_present -or [bool]$archiveManifest.pid_active -or
        [int]$archiveManifest.file_count -ne 3 -or [int]$archiveManifest.delete_count -ne 0 -or
        [bool]$archiveManifest.overwrite -or [bool]$archiveManifest.provider_secret_value_read) {
        throw "DEMO_RESUME_ATTEMPT6_CLEANUP_MANIFEST_CONTENT_INVALID"
    }
    $archiveDirectory = Split-Path -Path $archiveManifestPath -Parent
    $expectedArchiveFiles = @(
        [ordered]@{ name = "gateway.pid"; length = 5; sha256 = "a6db9e5506ffb6452f1e460711f5beda137bab27a440286c3d2a5c95f12e4a6f" },
        [ordered]@{ name = "gateway.stdout.log"; length = 85; sha256 = "986773b2987bd90ccf2a276496bbb0e769134c461bf73a4f5cee5526508d881f" },
        [ordered]@{ name = "gateway.stderr.log"; length = 0; sha256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" }
    )
    foreach ($expected in $expectedArchiveFiles) {
        $manifestRecords = @($archiveManifest.files | Where-Object {
            [string]$_.name -ceq [string]$expected.name
        })
        $archivePath = Join-Path $archiveDirectory ([string]$expected.name)
        $archiveItem = Get-Item -LiteralPath $archivePath -Force -ErrorAction Stop
        if ($manifestRecords.Count -ne 1 -or $archiveItem.PSIsContainer -or
            ($archiveItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
            [int64]$manifestRecords[0].length -ne [int64]$expected.length -or
            [string]$manifestRecords[0].sha256 -cne [string]$expected.sha256 -or
            [int64]$archiveItem.Length -ne [int64]$expected.length -or
            (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant() -cne
                [string]$expected.sha256) {
            throw "DEMO_RESUME_ATTEMPT6_CLEANUP_ARCHIVE_FILE_INVALID"
        }
    }
    foreach ($activeGatewayPath in @(
        (Join-Path $workspace "tmp\m4\gateway\gateway.pid"),
        (Join-Path $workspace "tmp\m4\gateway\gateway.stdout.log"),
        (Join-Path $workspace "tmp\m4\gateway\gateway.stderr.log")
    )) {
        if (Test-Path -LiteralPath $activeGatewayPath) {
            throw "DEMO_RESUME_ATTEMPT6_ACTIVE_GATEWAY_EVIDENCE_PRESENT"
        }
    }

    $statePins = @(
        [ordered]@{ path = $Paths.StatePid; length = 4; sha256 = "2dc1d0bc63dfe5cec373181124f4102685dd86ad708711e27bcc40139527ea95" },
        [ordered]@{ path = $Paths.StateStdout; length = 92; sha256 = "3a1ed09ad9b0edf9a8b07a97c20d8cd6c3941011ac5ed75da41dd8ff4b0c72fa" },
        [ordered]@{ path = $Paths.StateStderr; length = 0; sha256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" }
    )
    foreach ($statePin in $statePins) {
        $stateItem = Get-Item -LiteralPath ([string]$statePin.path) -Force -ErrorAction Stop
        if ($stateItem.PSIsContainer -or
            ($stateItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
            [int64]$stateItem.Length -ne [int64]$statePin.length -or
            (Get-FileHash -LiteralPath ([string]$statePin.path) -Algorithm SHA256).Hash.ToLowerInvariant() -cne
                [string]$statePin.sha256) {
            throw "DEMO_RESUME_ATTEMPT6_STATE_EVIDENCE_INVALID"
        }
    }
    if ([IO.File]::ReadAllText($Paths.StatePid).Trim() -cne "5948" -or
        @(Get-Process -Id 5948 -ErrorAction SilentlyContinue).Count -ne 0 -or
        (Get-ListenerCount -Port 18191) -ne 0) {
        throw "DEMO_RESUME_ATTEMPT6_STATE_RUNTIME_NOT_QUIESCENT"
    }

    $corePins = @(
        [ordered]@{ path = $coreCli; length = 49893; sha256 = "00663cebba273ea1eeb862531e744e49ac46ad16db69319999079cb51dd48483" },
        [ordered]@{ path = $matrixControlSource; length = 28283; sha256 = "4d99c125c9b884145da869f5b3e5bb990abbcdedb12f00fc60de0003e599e2bc" },
        [ordered]@{ path = (Join-Path $Paths.CoreRun "live-gateway-config.json"); length = 2826; sha256 = "7b20b12ca2cdcc9ba3f588cb1ad2d01f8e595a0a82c7acc5a7ec8e93031246db" },
        [ordered]@{ path = (Join-Path $Paths.CoreRun "packages\role_project_architect.json"); length = 1126; sha256 = "5e2d73a84f174edbad66f4dc4c3e62cd2ea87fe01b1cbe8cdb400dfae8476792" },
        [ordered]@{ path = (Join-Path $Paths.CoreRun "packages\execution_evidence_coach.json"); length = 1056; sha256 = "90dfb5de0694a6d9e02b76f0b6eb2d7546e91760b3d4fcd381cafea816e01b37" },
        [ordered]@{ path = (Join-Path $Paths.CoreRun "packages\independent_quality_reviewer.json"); length = 1352; sha256 = "7e3610f013d20bcf03598bde25fcc1fb666c301ec5ef79639b34a15d13973392" },
        [ordered]@{ path = $Paths.CoreBinding; length = 610; sha256 = "bb82a744fd64170c5dd825d979405cd80d86062577bdcc9276fc4fe11399bc32" },
        [ordered]@{ path = $Paths.PrepareStdout; length = 1500; sha256 = "b02caf94c2fdc2cc945a4e1cd943d04d0cebFdf2246005c521cf0c4a98428757" },
        [ordered]@{ path = $Paths.Baseline; length = 28494; sha256 = "bc8c4431b78df6523221caeaedbedd60b962449d51376ccda1e7a16c9d1c3e3c" }
    )
    foreach ($corePin in $corePins) {
        $corePath = Assert-RegularFile -Path ([string]$corePin.path) `
            -Reason "DEMO_RESUME_ATTEMPT6_CORE_PIN_INPUT_INVALID"
        $coreItem = Get-Item -LiteralPath $corePath -Force -ErrorAction Stop
        if ([int64]$coreItem.Length -ne [int64]$corePin.length -or
            (Get-FileHash -LiteralPath $corePath -Algorithm SHA256).Hash.ToLowerInvariant() -cne
                ([string]$corePin.sha256).ToLowerInvariant()) {
            throw "DEMO_RESUME_ATTEMPT6_CORE_PIN_INVALID"
        }
    }
    foreach ($frozen in $Baseline.frozen_file_fingerprints) {
        $current = Get-SafeFileFingerprint -Path (Join-Path $workspace ([string]$frozen.path))
        if ([string]$current.path -cne [string]$frozen.path -or
            [int64]$current.length -ne [int64]$frozen.length -or
            [string]$current.sha256 -cne [string]$frozen.sha256 -or
            [string]$current.last_write_utc -cne [string]$frozen.last_write_utc) {
            throw "DEMO_RESUME_ATTEMPT6_FROZEN_INPUT_DRIFT"
        }
    }
}

function Assert-AttemptSevenRecoveryEvidence {
    param(
        [Parameter(Mandatory = $true)]$Paths,
        [Parameter(Mandatory = $true)]$Baseline,
        [Parameter(Mandatory = $true)][object[]]$Records
    )

    $journalPath = Assert-RegularFile -Path $Paths.Journal `
        -Reason "DEMO_RESUME_ATTEMPT7_JOURNAL_INVALID"
    $journalItem = Get-Item -LiteralPath $journalPath -Force -ErrorAction Stop
    if ($journalItem.Length -ne 20738 -or
        (Get-FileHash -LiteralPath $journalPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne
            "fc84fa3dcd8ace07f5eac5056f7777825ccd4dff5e6617b805606d0bd8520874" -or
        $Records.Count -ne 59) {
        throw "DEMO_RESUME_ATTEMPT7_JOURNAL_PIN_INVALID"
    }

    $suffix = @($Records[49..58])
    $cleanupFailures = @($suffix[6].details.failures)
    if ([string]$suffix[0].kind -cne "state-mcp-evidence" -or
        [string]$suffix[0].status -cne "archived" -or
        [int]$suffix[0].details.file_count -ne 3 -or
        [string]$suffix[0].details.archive_id -cne "a3502760-fb7c-414a-8b0e-d96615146889" -or
        [string]$suffix[1].kind -cne "resume-infrastructure-demo-relay-stage-recovery" -or
        [string]$suffix[1].status -cne "started" -or
        [string]$suffix[1].details.recovery_id -cne "6d952254-de40-4b82-9f73-b248ec871ad3" -or
        [int]$suffix[1].details.resume_attempt -ne 6 -or
        [int]$suffix[1].details.prepare_invocation_count -ne 0 -or
        [int]$suffix[1].details.provider_call_count -ne 0 -or
        [string]$suffix[2].kind -cne "state-mcp" -or
        [string]$suffix[2].status -cne "started" -or
        [int]$suffix[2].details.pid -ne 9040 -or [int]$suffix[2].details.port -ne 18191 -or
        [string]$suffix[3].kind -cne "fail-closed-gateway" -or
        [string]$suffix[3].status -cne "started" -or
        [string]$suffix[3].details.window_id -cne "07197912-206b-4e17-8dae-dd77a5840d89" -or
        [int]$suffix[3].details.provider_call_count -ne 0 -or
        [string]$suffix[4].kind -cne "resume-infrastructure-demo-relay-stage-recovery" -or
        [string]$suffix[4].status -cne "failed" -or
        [string]$suffix[4].details.recovery_id -cne "6d952254-de40-4b82-9f73-b248ec871ad3" -or
        [int]$suffix[4].details.resume_attempt -ne 6 -or
        [int]$suffix[4].details.prepare_invocation_count -ne 0 -or
        [int]$suffix[4].details.provider_call_count -ne 0 -or
        [string]$suffix[4].details.failure_code -cne
            "DEMO_M4_SCRIPT_FAILED:Start-AgentTeamsDemoHostRelay.ps1:DEMO_HOST_RELAY_STAGE_FAILED:prestate-boundary" -or
        [string]$suffix[5].kind -cne "demo-live-gateway" -or
        [string]$suffix[5].status -cne "stopped" -or
        [string]$suffix[6].kind -cne "restore" -or
        [string]$suffix[6].status -cne "failed" -or
        $cleanupFailures.Count -ne 2 -or
        [string]$cleanupFailures[0] -cne "fail-closed-stop" -or
        [string]$cleanupFailures[1] -cne "state-mcp" -or
        [string]$suffix[7].kind -cne "demo-live-gateway" -or
        [string]$suffix[7].status -cne "stopped" -or
        [string]$suffix[8].kind -cne "state-mcp" -or
        [string]$suffix[8].status -cne "stopped" -or
        [string]$suffix[9].kind -cne "restore" -or
        [string]$suffix[9].status -cne "completed" -or
        [int]$suffix[9].details.exact_container_count -ne 8 -or
        [int]$suffix[9].details.listener_count -ne 0) {
        throw "DEMO_RESUME_ATTEMPT7_ORDERED_RECOVERY_INVALID"
    }

    foreach ($record in $Records) {
        if ([bool]$record.secret_value_read -or [bool]$record.secret_value_hashed -or
            [bool]$record.secret_value_echoed) {
            throw "DEMO_RESUME_ATTEMPT7_SECRET_HISTORY_INVALID"
        }
        $providerProperty = $record.details.PSObject.Properties["provider_call_count"]
        if ($null -ne $providerProperty -and [int]$providerProperty.Value -ne 0) {
            throw "DEMO_RESUME_ATTEMPT7_PROVIDER_HISTORY_INVALID"
        }
    }
    $attemptKinds = @(
        "resume-infrastructure",
        "resume-infrastructure-recovery",
        "resume-infrastructure-relay-recovery",
        "resume-infrastructure-log-policy-recovery",
        "resume-infrastructure-wiring-recovery",
        "resume-infrastructure-demo-relay-stage-recovery"
    )
    foreach ($attemptKind in $attemptKinds) {
        $attemptRecords = @($Records | Where-Object { [string]$_.kind -ceq $attemptKind })
        if ($attemptRecords.Count -ne 2 -or
            @($attemptRecords | Where-Object { [string]$_.status -ceq "started" }).Count -ne 1 -or
            @($attemptRecords | Where-Object { [string]$_.status -ceq "failed" }).Count -ne 1 -or
            @($attemptRecords | Where-Object { [string]$_.status -ceq "completed" }).Count -ne 0) {
            throw "DEMO_RESUME_ATTEMPT7_PRIOR_ATTEMPT_SET_INVALID"
        }
    }
    if (@($Records | Where-Object {
        [string]$_.kind -ceq "prepare" -and [string]$_.status -ceq "completed"
    }).Count -ne 1 -or
        @($Records | Where-Object {
            [string]$_.kind -ceq "infrastructure" -and [string]$_.status -ceq "ready"
        }).Count -ne 0 -or
        @($Records | Where-Object { [string]$_.kind -ceq "human-request" }).Count -ne 0 -or
        @($Records | Where-Object {
            [string]$_.kind -ceq "demo-live-gateway" -and [string]$_.status -ceq "started"
        }).Count -ne 0 -or
        @($Records | Where-Object { [string]$_.kind -ceq "run-chain" }).Count -ne 0) {
        throw "DEMO_RESUME_ATTEMPT7_HISTORY_SCOPE_INVALID"
    }

    $archiveManifestPath = Join-Path $workspace `
        "tmp\m4\gateway\archives\05b4aa81-5eff-4351-a070-641c481bbe1c\pre-live-fail-closed\archive-manifest.json"
    $archiveManifestPath = Assert-RegularFile -Path $archiveManifestPath `
        -Reason "DEMO_RESUME_ATTEMPT7_CLEANUP_MANIFEST_INVALID"
    $archiveItem = Get-Item -LiteralPath $archiveManifestPath -Force -ErrorAction Stop
    if ($archiveItem.Length -ne 1231 -or
        (Get-FileHash -LiteralPath $archiveManifestPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne
            "660377cc2181fe0b5db207dcaff11206149da9b54661ef0d7986ec6d13fce5b2") {
        throw "DEMO_RESUME_ATTEMPT7_CLEANUP_MANIFEST_PIN_INVALID"
    }
    $manifest = Get-Content -LiteralPath $archiveManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([string]$manifest.schema_version -cne "awakening.m4.gateway-evidence-archive.v1" -or
        [string]$manifest.window_id -cne "05b4aa81-5eff-4351-a070-641c481bbe1c" -or
        [string]$manifest.phase -cne "pre-live-fail-closed" -or
        [string]$manifest.runtime_kind -cne "FailClosed" -or
        [int]$manifest.recorded_pid -ne 3168 -or [int]$manifest.listener_port -ne 18190 -or
        [bool]$manifest.listener_present -or [bool]$manifest.pid_active -or
        [int]$manifest.file_count -ne 3 -or [int]$manifest.delete_count -ne 0 -or
        [bool]$manifest.overwrite -or [bool]$manifest.provider_secret_value_read) {
        throw "DEMO_RESUME_ATTEMPT7_CLEANUP_MANIFEST_CONTENT_INVALID"
    }
    $archiveDirectory = Split-Path -Path $archiveManifestPath -Parent
    $expectedArchiveFiles = @(
        [ordered]@{ name = "gateway.pid"; length = 4; sha256 = "4fdc8d7d404bc07349ffce4cd89e1086a602d2d0333732a7b0c917314035492d" },
        [ordered]@{ name = "gateway.stdout.log"; length = 85; sha256 = "986773b2987bd90ccf2a276496bbb0e769134c461bf73a4f5cee5526508d881f" },
        [ordered]@{ name = "gateway.stderr.log"; length = 0; sha256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" }
    )
    foreach ($expected in $expectedArchiveFiles) {
        $manifestRecord = @($manifest.files | Where-Object {
            [string]$_.name -ceq [string]$expected.name
        })
        $archivePath = Join-Path $archiveDirectory ([string]$expected.name)
        $fileItem = Get-Item -LiteralPath $archivePath -Force -ErrorAction Stop
        if ($manifestRecord.Count -ne 1 -or $fileItem.PSIsContainer -or
            ($fileItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
            [int64]$manifestRecord[0].length -ne [int64]$expected.length -or
            [string]$manifestRecord[0].sha256 -cne [string]$expected.sha256 -or
            [int64]$fileItem.Length -ne [int64]$expected.length -or
            (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant() -cne
                [string]$expected.sha256) {
            throw "DEMO_RESUME_ATTEMPT7_CLEANUP_ARCHIVE_FILE_INVALID"
        }
    }
    foreach ($activePath in @(
        (Join-Path $workspace "tmp\m4\gateway\gateway.pid"),
        (Join-Path $workspace "tmp\m4\gateway\gateway.stdout.log"),
        (Join-Path $workspace "tmp\m4\gateway\gateway.stderr.log")
    )) {
        if (Test-Path -LiteralPath $activePath) {
            throw "DEMO_RESUME_ATTEMPT7_ACTIVE_GATEWAY_EVIDENCE_PRESENT"
        }
    }

    $statePins = @(
        [ordered]@{ path = $Paths.StatePid; length = 4; sha256 = "7fdc10869c66195e1fc846ef77477bd906490960997f6e2ad873feb1383e5af9" },
        [ordered]@{ path = $Paths.StateStdout; length = 92; sha256 = "3a1ed09ad9b0edf9a8b07a97c20d8cd6c3941011ac5ed75da41dd8ff4b0c72fa" },
        [ordered]@{ path = $Paths.StateStderr; length = 0; sha256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" }
    )
    foreach ($statePin in $statePins) {
        $stateItem = Get-Item -LiteralPath ([string]$statePin.path) -Force -ErrorAction Stop
        if ($stateItem.PSIsContainer -or
            ($stateItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
            [int64]$stateItem.Length -ne [int64]$statePin.length -or
            (Get-FileHash -LiteralPath ([string]$statePin.path) -Algorithm SHA256).Hash.ToLowerInvariant() -cne
                [string]$statePin.sha256) {
            throw "DEMO_RESUME_ATTEMPT7_STATE_EVIDENCE_INVALID"
        }
    }
    if ([IO.File]::ReadAllText($Paths.StatePid).Trim() -cne "9040" -or
        @(Get-Process -Id 9040 -ErrorAction SilentlyContinue).Count -ne 0 -or
        (Get-ListenerCount -Port 18191) -ne 0) {
        throw "DEMO_RESUME_ATTEMPT7_STATE_RUNTIME_NOT_QUIESCENT"
    }

    $corePins = @(
        [ordered]@{ path = $coreCli; length = 49893; sha256 = "00663cebba273ea1eeb862531e744e49ac46ad16db69319999079cb51dd48483" },
        [ordered]@{ path = $matrixControlSource; length = 28283; sha256 = "4d99c125c9b884145da869f5b3e5bb990abbcdedb12f00fc60de0003e599e2bc" },
        [ordered]@{ path = (Join-Path $Paths.CoreRun "live-gateway-config.json"); length = 2826; sha256 = "7b20b12ca2cdcc9ba3f588cb1ad2d01f8e595a0a82c7acc5a7ec8e93031246db" },
        [ordered]@{ path = (Join-Path $Paths.CoreRun "packages\role_project_architect.json"); length = 1126; sha256 = "5e2d73a84f174edbad66f4dc4c3e62cd2ea87fe01b1cbe8cdb400dfae8476792" },
        [ordered]@{ path = (Join-Path $Paths.CoreRun "packages\execution_evidence_coach.json"); length = 1056; sha256 = "90dfb5de0694a6d9e02b76f0b6eb2d7546e91760b3d4fcd381cafea816e01b37" },
        [ordered]@{ path = (Join-Path $Paths.CoreRun "packages\independent_quality_reviewer.json"); length = 1352; sha256 = "7e3610f013d20bcf03598bde25fcc1fb666c301ec5ef79639b34a15d13973392" },
        [ordered]@{ path = $Paths.CoreBinding; length = 610; sha256 = "bb82a744fd64170c5dd825d979405cd80d86062577bdcc9276fc4fe11399bc32" },
        [ordered]@{ path = $Paths.PrepareStdout; length = 1500; sha256 = "b02caf94c2fdc2cc945a4e1cd943d04d0cebfdf2246005c521cf0c4a98428757" },
        [ordered]@{ path = $Paths.Baseline; length = 28494; sha256 = "bc8c4431b78df6523221caeaedbedd60b962449d51376ccda1e7a16c9d1c3e3c" }
    )
    foreach ($corePin in $corePins) {
        $corePath = Assert-RegularFile -Path ([string]$corePin.path) `
            -Reason "DEMO_RESUME_ATTEMPT7_CORE_PIN_INPUT_INVALID"
        $coreItem = Get-Item -LiteralPath $corePath -Force -ErrorAction Stop
        if ([int64]$coreItem.Length -ne [int64]$corePin.length -or
            (Get-FileHash -LiteralPath $corePath -Algorithm SHA256).Hash.ToLowerInvariant() -cne
                [string]$corePin.sha256) {
            throw "DEMO_RESUME_ATTEMPT7_CORE_PIN_INVALID"
        }
    }
    foreach ($frozen in $Baseline.frozen_file_fingerprints) {
        $current = Get-SafeFileFingerprint -Path (Join-Path $workspace ([string]$frozen.path))
        if ([string]$current.path -cne [string]$frozen.path -or
            [int64]$current.length -ne [int64]$frozen.length -or
            [string]$current.sha256 -cne [string]$frozen.sha256 -or
            [string]$current.last_write_utc -cne [string]$frozen.last_write_utc) {
            throw "DEMO_RESUME_ATTEMPT7_FROZEN_INPUT_DRIFT"
        }
    }
    [void](Assert-DemoHostRelayHelper)
    Invoke-M4Script -Path $startDemoHostRelay `
        -Arguments @("-BaselinePath", $Paths.Baseline, "-PrestartCheck") `
        -SuccessMarkers @("DEMO_HOST_RELAY_PRESTART_CHECK=PASS")
}

function Assert-ResumeInfrastructureAdmission {
    param(
        [Parameter(Mandatory = $true)]$Paths,
        [Parameter(Mandatory = $true)]$Baseline,
        [Parameter(Mandatory = $true)]$CoreBinding,
        [Parameter(Mandatory = $true)][ValidateRange(1, 7)][int]$Attempt
    )

    foreach ($port in $ports) {
        if ((Get-ListenerCount -Port $port) -ne 0) {
            throw ("DEMO_RESUME_LISTENER_PRESENT:" + $port)
        }
    }
    $blockedPaths = @(
        $Paths.HumanRequest,
        $Paths.MatrixEvents,
        $Paths.RunStdout,
        $Paths.RunStderr,
        $Paths.LivePid,
        $Paths.LiveStdout,
        $Paths.LiveStderr,
        (Join-Path $Paths.CoreRun "result.json")
    )
    if ($Attempt -eq 1) {
        $blockedPaths += @($Paths.ResumeMarker, $Paths.FailClosedMarker)
    }
    else {
        if ($Attempt -eq 2) {
            $blockedPaths += @($Paths.ResumeRecoveryMarker, $Paths.FailClosedRecoveryMarker)
            $priorResumeMarkerPath = $Paths.ResumeMarker
            $priorFailClosedMarkerPath = $Paths.FailClosedMarker
            $expectedResumeSchema = "awakening.demo.resume-infrastructure.v1"
            $expectedPriorAttempt = 0
        }
        elseif ($Attempt -eq 3) {
            $blockedPaths += @(
                $Paths.ResumeRelayRecoveryMarker,
                $Paths.FailClosedRelayRecoveryMarker
            )
            $priorResumeMarkerPath = $Paths.ResumeRecoveryMarker
            $priorFailClosedMarkerPath = $Paths.FailClosedRecoveryMarker
            $expectedResumeSchema = "awakening.demo.resume-infrastructure-recovery.v1"
            $expectedPriorAttempt = 2
        }
        elseif ($Attempt -eq 4) {
            $blockedPaths += @(
                $Paths.ResumeLogPolicyRecoveryMarker,
                $Paths.FailClosedLogPolicyRecoveryMarker
            )
            $priorResumeMarkerPath = $Paths.ResumeRelayRecoveryMarker
            $priorFailClosedMarkerPath = $Paths.FailClosedRelayRecoveryMarker
            $expectedResumeSchema = "awakening.demo.resume-infrastructure-relay-recovery.v1"
            $expectedPriorAttempt = 3
        }
        elseif ($Attempt -eq 5) {
            $blockedPaths += @(
                $Paths.ResumeWiringRecoveryMarker,
                $Paths.FailClosedWiringRecoveryMarker
            )
            $priorResumeMarkerPath = $Paths.ResumeLogPolicyRecoveryMarker
            $priorFailClosedMarkerPath = $Paths.FailClosedLogPolicyRecoveryMarker
            $expectedResumeSchema = "awakening.demo.resume-infrastructure-log-policy-recovery.v1"
            $expectedPriorAttempt = 4
        }
        elseif ($Attempt -eq 6) {
            $blockedPaths += @(
                $Paths.ResumeDemoRelayStageRecoveryMarker,
                $Paths.FailClosedDemoRelayStageRecoveryMarker
            )
            $priorResumeMarkerPath = $Paths.ResumeWiringRecoveryMarker
            $priorFailClosedMarkerPath = $Paths.FailClosedWiringRecoveryMarker
            $expectedResumeSchema = "awakening.demo.resume-infrastructure-wiring-recovery.v1"
            $expectedPriorAttempt = 5
        }
        elseif ($Attempt -eq 7) {
            $blockedPaths += @(
                $Paths.ResumeDemoRelayPrestateBoundaryRecoveryMarker,
                $Paths.FailClosedDemoRelayPrestateBoundaryRecoveryMarker
            )
            $priorResumeMarkerPath = $Paths.ResumeDemoRelayStageRecoveryMarker
            $priorFailClosedMarkerPath = $Paths.FailClosedDemoRelayStageRecoveryMarker
            $expectedResumeSchema = "awakening.demo.resume-infrastructure-demo-relay-stage-recovery.v1"
            $expectedPriorAttempt = 6
        }
        else {
            throw "DEMO_RESUME_ATTEMPT_UNREACHABLE"
        }
        $priorResumePath = Assert-RegularFile -Path $priorResumeMarkerPath `
            -Reason "DEMO_RESUME_PRIOR_MARKER_INVALID"
        $priorResume = Get-Content -LiteralPath $priorResumePath -Raw -Encoding UTF8 |
            ConvertFrom-Json
        $priorFailClosedPath = Assert-RegularFile -Path $priorFailClosedMarkerPath `
            -Reason "DEMO_RESUME_PRIOR_FAIL_CLOSED_MARKER_INVALID"
        $priorFailClosed = Get-Content -LiteralPath $priorFailClosedPath -Raw -Encoding UTF8 |
            ConvertFrom-Json
        if ([string]$priorResume.schema_version -cne $expectedResumeSchema -or
            [string]$priorResume.execution_window_id -cne $DemoRunId.ToString("D").ToLowerInvariant() -or
            [string]$priorResume.core_run_id -cne [string]$CoreBinding.run_id -or
            [string]$priorResume.demo_request_id -cne [string]$CoreBinding.demo_request_id -or
            [int]$priorResume.prepare_invocation_count -ne 0 -or
            [bool]$priorResume.provider_secret_read -or
            [bool]$priorResume.provider_called -or
            [string]$priorFailClosed.schema_version -cne "awakening.demo.fail-closed-runtime.v1" -or
            [string]$priorFailClosed.execution_window_id -cne $DemoRunId.ToString("D").ToLowerInvariant() -or
            [string]$priorFailClosed.recovery_id -cne [string]$priorResume.recovery_id -or
            [string]$priorFailClosed.fail_closed_window_id -cne [string]$priorResume.fail_closed_window_id -or
            [string]$priorFailClosed.status -cne "start-intent" -or
            [bool]$priorFailClosed.provider_configured) {
            throw "DEMO_RESUME_PRIOR_FAILURE_BINDING_INVALID"
        }
        if ($expectedPriorAttempt -ne 0 -and
            ([int]$priorResume.resume_attempt -ne $expectedPriorAttempt -or
                [int]$priorFailClosed.resume_attempt -ne $expectedPriorAttempt)) {
            throw "DEMO_RESUME_PRIOR_ATTEMPT_BINDING_INVALID"
        }
        if ($Attempt -eq 6) {
            $priorResumeHash = (Get-FileHash -LiteralPath $priorResumePath `
                -Algorithm SHA256).Hash.ToLowerInvariant()
            $priorFailClosedHash = (Get-FileHash -LiteralPath $priorFailClosedPath `
                -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($priorResumeHash -cne
                    "2572b3f258df92bf3afb59ce40e097144c10e6105393800230e3ad25f3b77e4e" -or
                $priorFailClosedHash -cne
                    "094ebad000475171450e226e338ba232762abe5b2a8771133373a1cd96c2ca78" -or
                [string]$priorResume.recovery_id -cne
                    "bcd067ac-a126-469a-aae2-54c3a74ba95b" -or
                [string]$priorResume.fail_closed_window_id -cne
                    "ec5092a8-5e2c-48eb-9070-7cd9c6e77d67" -or
                [string]$priorResume.demo_host_relay_sha256 -cne
                    "ab439763606168ed7ffa5554743aed122e9d9e51811aeb7484585c6823224260") {
                throw "DEMO_RESUME_ATTEMPT6_PRIOR_MARKER_PIN_INVALID"
            }
        }
        if ($Attempt -eq 7) {
            $priorResumeHash = (Get-FileHash -LiteralPath $priorResumePath `
                -Algorithm SHA256).Hash.ToLowerInvariant()
            $priorFailClosedHash = (Get-FileHash -LiteralPath $priorFailClosedPath `
                -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($priorResumeHash -cne
                    "acc3ce7169739a224a8b042b428b0f6b1bc90a0b118d2120ef739e7b785f5af8" -or
                $priorFailClosedHash -cne
                    "69ae9fd1cc24c3c946e40c2622ee1194521c9a8e6edfc4b7985c74fddc7bc183" -or
                [string]$priorResume.recovery_id -cne
                    "6d952254-de40-4b82-9f73-b248ec871ad3" -or
                [string]$priorResume.fail_closed_window_id -cne
                    "07197912-206b-4e17-8dae-dd77a5840d89" -or
                [string]$priorResume.demo_host_relay_sha256 -cne
                    "254069a73dc8e216a62ce7eca973950acf44bbb52abd8a71bfb9edebda8d51dc") {
                throw "DEMO_RESUME_ATTEMPT7_PRIOR_MARKER_PIN_INVALID"
            }
        }
    }
    foreach ($blockedPath in $blockedPaths) {
        if (Test-Path -LiteralPath $blockedPath) {
            throw "DEMO_RESUME_DOWNSTREAM_EVIDENCE_PRESENT"
        }
    }

    Use-DockerConfig {
        foreach ($baselineRecord in $Baseline.containers) {
            $current = Get-ContainerProjection -Name ([string]$baselineRecord.name)
            Assert-ContainerMatchesBaselineFrozenProjection -BaselineRecord $baselineRecord `
                -Current $current
            if ($current.state -cne [string]$baselineRecord.state -or
                [int64]$current.restart_count -ne [int64]$baselineRecord.restart_count) {
                throw ("DEMO_RESUME_CONTAINER_NOT_AT_BASELINE:" + [string]$current.name)
            }
            if ($current.state -ceq "exited" -and [int]$current.exit_code -ne 0) {
                throw ("DEMO_RESUME_CONTAINER_EXIT_CODE_INVALID:" + [string]$current.name)
            }
        }
    }

    $preparePath = Assert-RegularFile -Path $Paths.PrepareStdout `
        -Reason "DEMO_RESUME_PREPARE_EVIDENCE_INVALID"
    $prepareLines = [IO.File]::ReadAllLines($preparePath)
    foreach ($marker in @(
        "AUTH_DEMO_001_PREPARE=PASS",
        ("AUTH_DEMO_001_RUN_ID=" + [string]$CoreBinding.run_id),
        ("AUTH_DEMO_001_REQUEST_ID=" + [string]$CoreBinding.demo_request_id),
        ("AUTH_DEMO_001_SNAPSHOT_ID=" + [string]$CoreBinding.runtime_config_snapshot_id),
        "AUTH_DEMO_001_RESERVATION_COUNT=3",
        "AUTH_DEMO_001_PACKAGE_COUNT=3",
        ("AUTH_DEMO_001_LIVE_CONFIG_SHA256=" + [string]$CoreBinding.live_config_sha256),
        "AUTH_DEMO_001_PROVIDER_SECRET_READ=false",
        "AUTH_DEMO_001_CONTENT_ECHOED=false"
    )) {
        Assert-ExactPrepareMarker -Lines $prepareLines -Marker $marker
    }

    $packageExpectations = @(
        [ordered]@{
            file = "role_project_architect.json"
            marker = "AUTH_DEMO_001_ROLE_PROJECT_ARCHITECT_PACKAGE_SHA256="
        },
        [ordered]@{
            file = "execution_evidence_coach.json"
            marker = "AUTH_DEMO_001_EXECUTION_EVIDENCE_COACH_PACKAGE_SHA256="
        },
        [ordered]@{
            file = "independent_quality_reviewer.json"
            marker = "AUTH_DEMO_001_INDEPENDENT_QUALITY_REVIEWER_PACKAGE_SHA256="
        }
    )
    $packageDirectory = Assert-RegularDirectory `
        -Path (Join-Path $Paths.CoreRun "packages") `
        -Reason "DEMO_RESUME_PACKAGE_DIRECTORY_INVALID"
    $actualPackageNames = @(
        Get-ChildItem -LiteralPath $packageDirectory -Force -File |
            Sort-Object Name |
            ForEach-Object { $_.Name }
    )
    $expectedPackageNames = @($packageExpectations.file | Sort-Object)
    if ($actualPackageNames.Count -ne 3 -or
        [string]::Join("|", $actualPackageNames) -cne
        [string]::Join("|", $expectedPackageNames)) {
        throw "DEMO_RESUME_PACKAGE_SET_INVALID"
    }
    foreach ($expectation in $packageExpectations) {
        $packagePath = Assert-RegularFile `
            -Path (Join-Path $packageDirectory ([string]$expectation.file)) `
            -Reason "DEMO_RESUME_PACKAGE_INVALID"
        $packageHash = (Get-FileHash -LiteralPath $packagePath -Algorithm SHA256).Hash.ToLowerInvariant()
        Assert-ExactPrepareMarker -Lines $prepareLines `
            -Marker ([string]$expectation.marker + $packageHash)
    }

    $records = @()
    foreach ($line in [IO.File]::ReadAllLines($Paths.Journal)) {
        if (-not [string]::IsNullOrWhiteSpace($line)) {
            $records += ($line | ConvertFrom-Json)
        }
    }
    $prepareRecords = @($records | Where-Object {
        [string]$_.kind -ceq "prepare" -and [string]$_.status -ceq "completed"
    })
    if ($prepareRecords.Count -ne 1) {
        throw "DEMO_RESUME_PREPARE_RECORD_INVALID"
    }
    $forbiddenRecords = @($records | Where-Object {
        ([string]$_.kind -ceq "infrastructure" -and [string]$_.status -ceq "ready") -or
        [string]$_.kind -ceq "human-request" -or
        ([string]$_.kind -ceq "demo-live-gateway" -and
            [string]$_.status -ceq "started") -or
        [string]$_.kind -ceq "run-chain" -or
        ($Attempt -le 5 -and
            [string]$_.kind -ceq "resume-infrastructure-wiring-recovery") -or
        ($Attempt -le 6 -and
            [string]$_.kind -ceq "resume-infrastructure-demo-relay-stage-recovery") -or
        [string]$_.kind -ceq "resume-infrastructure-demo-relay-prestate-boundary-recovery" -or
        [bool]$_.secret_value_read -or
        [bool]$_.secret_value_hashed -or
        [bool]$_.secret_value_echoed
    })
    if ($forbiddenRecords.Count -ne 0) {
        throw "DEMO_RESUME_ALREADY_CONSUMED_OR_DOWNSTREAM_STARTED"
    }
    if ($Attempt -eq 1) {
        $anyResumeRecords = @($records | Where-Object {
            [string]$_.kind -ceq "resume-infrastructure" -or
            [string]$_.kind -ceq "resume-infrastructure-recovery" -or
            [string]$_.kind -ceq "resume-infrastructure-relay-recovery" -or
            [string]$_.kind -ceq "resume-infrastructure-log-policy-recovery" -or
            [string]$_.kind -ceq "resume-infrastructure-wiring-recovery" -or
            [string]$_.kind -ceq "resume-infrastructure-demo-relay-stage-recovery" -or
            [string]$_.kind -ceq "resume-infrastructure-demo-relay-prestate-boundary-recovery"
        })
        if ($anyResumeRecords.Count -ne 0) {
            throw "DEMO_RESUME_ALREADY_CONSUMED_OR_DOWNSTREAM_STARTED"
        }
    }
    else {
        $priorKind = if ($Attempt -eq 2) {
            "resume-infrastructure"
        }
        elseif ($Attempt -eq 3) {
            "resume-infrastructure-recovery"
        }
        elseif ($Attempt -eq 4) {
            "resume-infrastructure-relay-recovery"
        }
        elseif ($Attempt -eq 5) {
            "resume-infrastructure-log-policy-recovery"
        }
        elseif ($Attempt -eq 6) {
            "resume-infrastructure-wiring-recovery"
        }
        elseif ($Attempt -eq 7) {
            "resume-infrastructure-demo-relay-stage-recovery"
        }
        else {
            throw "DEMO_RESUME_ATTEMPT_UNREACHABLE"
        }
        $priorResumeRecords = @($records | Where-Object {
            [string]$_.kind -ceq $priorKind
        })
        $priorStarted = @($priorResumeRecords | Where-Object {
            [string]$_.status -ceq "started" -and
            [string]$_.details.recovery_id -ceq [string]$priorResume.recovery_id -and
            [int]$_.details.prepare_invocation_count -eq 0 -and
            [int]$_.details.provider_call_count -eq 0
        })
        $priorFailed = @($priorResumeRecords | Where-Object {
            [string]$_.status -ceq "failed" -and
            [string]$_.details.recovery_id -ceq [string]$priorResume.recovery_id -and
            [int]$_.details.prepare_invocation_count -eq 0 -and
            [int]$_.details.provider_call_count -eq 0
        })
        $priorCompleted = @($priorResumeRecords | Where-Object {
            [string]$_.status -ceq "completed"
        })
        if ($priorResumeRecords.Count -ne 2 -or $priorStarted.Count -ne 1 -or
            $priorFailed.Count -ne 1 -or $priorCompleted.Count -ne 0) {
            throw "DEMO_RESUME_PRIOR_FAILURE_RECORD_INVALID"
        }
        if ($Attempt -in @(4, 5) -and
            [string]$priorFailed[0].details.failure_code -cne
            "DEMO_M4_SCRIPT_FAILED:Start-M4HostRelay.ps1:M4_HOST_RELAY_LOG_POLICY_INVALID") {
            throw "DEMO_RESUME_RELAY_LOG_POLICY_FAILURE_NOT_PROVEN"
        }
        if ($Attempt -eq 6 -and
            [string]$priorFailed[0].details.failure_code -cne
            "DEMO_M4_SCRIPT_FAILED:Start-AgentTeamsDemoHostRelay.ps1:DEMO_CHILD_FAILURE_UNCLASSIFIED") {
            throw "DEMO_RESUME_ATTEMPT6_RELAY_FAILURE_NOT_PROVEN"
        }
        if ($Attempt -eq 7 -and
            [string]$priorFailed[0].details.failure_code -cne
            "DEMO_M4_SCRIPT_FAILED:Start-AgentTeamsDemoHostRelay.ps1:DEMO_HOST_RELAY_STAGE_FAILED:prestate-boundary") {
            throw "DEMO_RESUME_ATTEMPT7_RELAY_FAILURE_NOT_PROVEN"
        }
        if ($Attempt -eq 2) {
            $laterRecords = @($records | Where-Object {
                [string]$_.kind -ceq "resume-infrastructure-recovery" -or
                [string]$_.kind -ceq "resume-infrastructure-relay-recovery" -or
                [string]$_.kind -ceq "resume-infrastructure-log-policy-recovery" -or
                [string]$_.kind -ceq "resume-infrastructure-wiring-recovery" -or
                [string]$_.kind -ceq "resume-infrastructure-demo-relay-stage-recovery" -or
                [string]$_.kind -ceq "resume-infrastructure-demo-relay-prestate-boundary-recovery"
            })
            if ($laterRecords.Count -ne 0) {
                throw "DEMO_RESUME_ALREADY_CONSUMED_OR_DOWNSTREAM_STARTED"
            }
        }
        elseif ($Attempt -eq 3) {
            $laterRecords = @($records | Where-Object {
                [string]$_.kind -ceq "resume-infrastructure-relay-recovery" -or
                [string]$_.kind -ceq "resume-infrastructure-log-policy-recovery" -or
                [string]$_.kind -ceq "resume-infrastructure-wiring-recovery" -or
                [string]$_.kind -ceq "resume-infrastructure-demo-relay-stage-recovery" -or
                [string]$_.kind -ceq "resume-infrastructure-demo-relay-prestate-boundary-recovery"
            })
            if ($laterRecords.Count -ne 0) {
                throw "DEMO_RESUME_ALREADY_CONSUMED_OR_DOWNSTREAM_STARTED"
            }
        }
        elseif ($Attempt -eq 4) {
            $laterRecords = @($records | Where-Object {
                [string]$_.kind -ceq "resume-infrastructure-log-policy-recovery" -or
                [string]$_.kind -ceq "resume-infrastructure-wiring-recovery" -or
                [string]$_.kind -ceq "resume-infrastructure-demo-relay-stage-recovery" -or
                [string]$_.kind -ceq "resume-infrastructure-demo-relay-prestate-boundary-recovery"
            })
            if ($laterRecords.Count -ne 0) {
                throw "DEMO_RESUME_ALREADY_CONSUMED_OR_DOWNSTREAM_STARTED"
            }
        }
        elseif ($Attempt -eq 5) {
            $laterRecords = @($records | Where-Object {
                [string]$_.kind -ceq "resume-infrastructure-wiring-recovery" -or
                [string]$_.kind -ceq "resume-infrastructure-demo-relay-stage-recovery" -or
                [string]$_.kind -ceq "resume-infrastructure-demo-relay-prestate-boundary-recovery"
            })
            if ($laterRecords.Count -ne 0) {
                throw "DEMO_RESUME_ALREADY_CONSUMED_OR_DOWNSTREAM_STARTED"
            }
        }
        elseif ($Attempt -eq 6) {
            $laterRecords = @($records | Where-Object {
                [string]$_.kind -ceq "resume-infrastructure-demo-relay-stage-recovery" -or
                [string]$_.kind -ceq "resume-infrastructure-demo-relay-prestate-boundary-recovery"
            })
            if ($laterRecords.Count -ne 0) {
                throw "DEMO_RESUME_ALREADY_CONSUMED_OR_DOWNSTREAM_STARTED"
            }
            Assert-AttemptSixRecoveryEvidence -Paths $Paths -Baseline $Baseline `
                -Records $records
        }
        elseif ($Attempt -eq 7) {
            $laterRecords = @($records | Where-Object {
                [string]$_.kind -ceq "resume-infrastructure-demo-relay-prestate-boundary-recovery"
            })
            if ($laterRecords.Count -ne 0) {
                throw "DEMO_RESUME_ALREADY_CONSUMED_OR_DOWNSTREAM_STARTED"
            }
            Assert-AttemptSevenRecoveryEvidence -Paths $Paths -Baseline $Baseline `
                -Records $records
        }
    }
}

function Invoke-OfflineCheck {
    $required = @(
        $powershell, $python, $curl, $coreCli, $matrixControlSource,
        $startPostgres, $startController, $refreshTokens, $startFailClosed,
        $startHostRelay, $startDemoHostRelay, $workerGatewaySyncSource,
        $workerEntrypointSource,
        $startAgents, $archiveGatewayEvidence,
        (Join-Path $workspace "infra\agentteams\m4\controller.compose.yaml"),
        (Join-Path $workspace "infra\agentteams\m4\runtime\m4-host-relay.py")
    )
    foreach ($path in $required) {
        [void](Assert-RegularFile -Path $path -Reason "DEMO_OFFLINE_INPUT_INVALID")
    }
    [void](Assert-DemoHostRelayHelper)
    [void](Assert-DemoWorkerGatewaySyncHelper)
    [void](Assert-DemoWorkerEntrypointGuard)
    $tokens = $null
    $errors = $null
    [void][Management.Automation.Language.Parser]::ParseFile(
        $scriptPath,
        [ref]$tokens,
        [ref]$errors
    )
    if (@($errors).Count -ne 0) {
        throw "DEMO_POWERSHELL_PARSE_FAILED"
    }
    Write-Output "DEMO_OFFLINE_CHECK=PASS"
    Write-Output "DEMO_OFFLINE_DOCKER_CALLED=false"
    Write-Output "DEMO_OFFLINE_NETWORK_CALLED=false"
    Write-Output "DEMO_OFFLINE_PROVIDER_CALLED=false"
    Write-Output "DEMO_OFFLINE_SECRET_READ=false"
}

function Invoke-Preflight {
    $paths = Get-DemoPaths
    if ((Test-Path -LiteralPath $paths.Root) -or
        (Test-Path -LiteralPath $paths.CoreRun)) {
        throw "DEMO_OUTPUT_ROOT_ALREADY_EXISTS"
    }
    # This no-auth/no-Secret transport gate runs before the fresh core directory,
    # runtime snapshot, or reservations can be created.
    $providerTransport = Get-DemoProviderTransportBinding
    foreach ($path in @($docker, $dockerCompose, (Join-Path $dockerConfig "config.json"))) {
        [void](Assert-RegularFile -Path $path -Reason "DEMO_DOCKER_INPUT_INVALID")
    }
    $frozenEvidencePaths = @(
        (Join-Path $workspace "PROGRESS.md"),
        (Join-Path $workspace "BLOCKED.md"),
        (Join-Path $workspace "DECISIONS.md"),
        (Join-Path $workspace "tmp\m4\provider\live-gateway-config.json"),
        (Join-Path $workspace "tmp\m4\provider\real-chain-results.json"),
        (Join-Path $workspace "tmp\m4\provider\packages\role_project_architect.json"),
        (Join-Path $workspace "tmp\m4\provider\packages\execution_evidence_coach.json"),
        (Join-Path $workspace "tmp\m4\provider\packages\independent_quality_reviewer.json")
    )
    foreach ($path in @(
        (Join-Path $workspace "tmp\m4\controller.env"),
        (Join-Path $workspace ".env.m2"),
        (Join-Path $workspace ".env.m4"),
        (Join-Path $workspace "tmp\m4\state\runtime-state.json"),
        (Join-Path $workspace "tmp\m4\m4-runtime-secrets-v1\gateway-credentials.env"),
        $frozenEvidencePaths[0], $frozenEvidencePaths[1],
        $frozenEvidencePaths[2], $frozenEvidencePaths[3],
        $frozenEvidencePaths[4], $frozenEvidencePaths[5],
        $frozenEvidencePaths[6], $frozenEvidencePaths[7]
    )) {
        [void](Assert-RegularFile -Path $path -Reason "DEMO_PREFLIGHT_INPUT_INVALID")
    }
    if (Test-Path -LiteralPath $m4ProviderSecret) {
        throw "DEMO_M4_PROVIDER_SECRET_MUST_REMAIN_ABSENT"
    }

    $secretPath = Assert-RegularFile -Path $m5Secret -Reason "DEMO_M5_SECRET_METADATA_INVALID"
    $secretItem = Get-Item -LiteralPath $secretPath -Force -ErrorAction Stop
    $secretAcl = Get-Acl -LiteralPath $secretPath -ErrorAction Stop
    if (-not $secretAcl.AreAccessRulesProtected -or @($secretAcl.Access).Count -ne 4 -or
        [string]::IsNullOrWhiteSpace([string]$secretAcl.Owner)) {
        throw "DEMO_M5_SECRET_ACL_METADATA_INVALID"
    }
    $streams = @(Get-Item -LiteralPath $secretPath -Stream * -ErrorAction Stop)
    $adsCount = @($streams | Where-Object { [string]$_.Stream -cne ':$DATA' }).Count
    if ($adsCount -ne 0) {
        throw "DEMO_M5_SECRET_ADS_PRESENT"
    }
    $hardlinks = @(& fsutil.exe hardlink list $secretPath 2>$null)
    if ($LASTEXITCODE -ne 0 -or $hardlinks.Count -ne 1) {
        throw "DEMO_M5_SECRET_HARDLINK_METADATA_INVALID"
    }

    foreach ($port in $ports) {
        if ((Get-ListenerCount -Port $port) -ne 0) {
            throw ("DEMO_PREFLIGHT_PORT_IN_USE:" + $port)
        }
    }

    $containerProjection = @(Use-DockerConfig {
        $version = @(& $docker version --format "{{.Server.Version}}" 2>$null)
        if ($LASTEXITCODE -ne 0 -or $version.Count -ne 1) {
            throw "DEMO_DOCKER_ENGINE_UNAVAILABLE"
        }
        foreach ($name in $exactContainers) {
            $projection = Get-ContainerProjection -Name $name
            if ($projection.state -cne "exited" -or $projection.exit_code -ne 0 -or
                $projection.restart_policy -cne "no") {
                throw ("DEMO_PREFLIGHT_CONTAINER_NOT_FROZEN:" + $name)
            }
            Write-Output $projection
        }
    })
    if ($containerProjection.Count -ne 8) {
        throw "DEMO_PREFLIGHT_EXACT_CONTAINER_COUNT_INVALID"
    }

    $stalePidProjection = @()
    $stalePidSpecs = @(
        [ordered]@{
            path = (Join-Path $workspace "tmp\m4\gateway\gateway.pid")
            required_fragments = @(
                "awakening.model_gateway.m4.fail_closed_runtime",
                "gateway-credentials.env", "--port", "18190"
            )
        },
        [ordered]@{
            path = (Join-Path $workspace "tmp\m4\gateway\live-gateway.pid")
            required_fragments = @(
                "awakening.model_gateway.m4.live_runtime",
                "live-gateway-config.json", "--port", "18190"
            )
        },
        [ordered]@{
            path = (Join-Path $workspace "tmp\m4\state-http\state-http.pid")
            required_fragments = @(
                "awakening.adapters.m4.state_http_runtime",
                "runtime-state.json", "--port", "18191"
            )
        }
    )
    foreach ($pidSpec in $stalePidSpecs) {
        $pidPath = [string]$pidSpec.path
        if (-not (Test-Path -LiteralPath $pidPath)) {
            $stalePidProjection += [ordered]@{
                path = [IO.Path]::GetFullPath($pidPath).Substring($workspace.Length).TrimStart('\')
                present = $false
                pid = $null
                process_present = $false
                pid_reused = $false
            }
            continue
        }
        $resolvedPidPath = Assert-RegularFile -Path $pidPath `
            -Reason "DEMO_STALE_PID_FILE_INVALID"
        $pidText = [IO.File]::ReadAllText($pidPath).Trim()
        if ($pidText -notmatch '^[1-9][0-9]{0,9}$') {
            throw "DEMO_STALE_PID_VALUE_INVALID"
        }
        $recordedPid = [int]$pidText
        $processRecords = @(Get-CimInstance -ClassName Win32_Process `
            -Filter ("ProcessId = " + $recordedPid) -ErrorAction Stop)
        if ($processRecords.Count -gt 1) {
            throw "DEMO_STALE_PID_PROCESS_CARDINALITY_INVALID"
        }
        $pidReused = $false
        if ($processRecords.Count -eq 1) {
            $creationDate = $processRecords[0].CreationDate
            if ($null -eq $creationDate) {
                throw "DEMO_STALE_PID_PROCESS_IDENTITY_UNAVAILABLE"
            }
            $processCreatedUtc = ([datetime]$creationDate).ToUniversalTime()
            $pidFileWrittenUtc = (Get-Item -LiteralPath $resolvedPidPath `
                -Force -ErrorAction Stop).LastWriteTimeUtc
            if ($processCreatedUtc -gt $pidFileWrittenUtc.AddSeconds(1)) {
                # Windows has safely reused the numeric PID after the recorded
                # runtime exited.  Do not stop or otherwise touch that process.
                $pidReused = $true
            }
            else {
                $commandLine = [string]$processRecords[0].CommandLine
                if ([string]::IsNullOrWhiteSpace($commandLine)) {
                    throw "DEMO_STALE_PID_PROCESS_IDENTITY_UNAVAILABLE"
                }
                $identityMatches = $true
                foreach ($fragment in @($pidSpec.required_fragments)) {
                    if ($commandLine.IndexOf(
                        [string]$fragment,
                        [StringComparison]::OrdinalIgnoreCase
                    ) -lt 0) {
                        $identityMatches = $false
                        break
                    }
                }
                if ($identityMatches) {
                    throw "DEMO_STALE_PID_IS_ACTIVE"
                }
                throw "DEMO_STALE_PID_PROCESS_IDENTITY_AMBIGUOUS"
            }
        }
        $stalePidProjection += [ordered]@{
            path = [IO.Path]::GetFullPath($pidPath).Substring($workspace.Length).TrimStart('\')
            present = $true
            pid = $recordedPid
            process_present = $false
            pid_reused = $pidReused
        }
    }
    $frozenEvidence = @($frozenEvidencePaths | ForEach-Object {
        Get-SafeFileFingerprint -Path $_
    })

    [IO.Directory]::CreateDirectory($paths.Root) | Out-Null
    $baseline = [ordered]@{
        schema_version = "awakening.demo.preflight.v1"
        demo_run_id = $DemoRunId.ToString("D").ToLowerInvariant()
        captured_at_utc = [DateTime]::UtcNow.ToString("o")
        containers = $containerProjection
        ports = @($ports | ForEach-Object {
            [ordered]@{ port = $_; listener_count = 0 }
        })
        stale_runtime_pids = $stalePidProjection
        frozen_file_fingerprints = $frozenEvidence
        m5_secret_metadata = [ordered]@{
            present = $true
            regular = $true
            non_reparse = $true
            size_positive = ($secretItem.Length -gt 0)
            acl_protected = $true
            acl_rule_count = 4
            hardlink_count = 1
            ads_count = 0
            value_read = $false
            value_hashed = $false
        }
        m4_provider_secret_present = $false
        core_run_directory_present = $false
        provider_transport_preflight = [ordered]@{
            source = [string]$providerTransport.source
            resolved_ipv4_count = [int]$providerTransport.resolved_ipv4_count
            reachable_ipv4_count = [int]$providerTransport.reachable_ipv4_count
            binding_sha256 = [string]$providerTransport.binding_sha256
            authorization_header_sent = $false
            provider_model_request_sent = $false
            provider_secret_read = $false
            single_use_plan_claim_count = 0
            system_network_modified = $false
        }
    }
    Write-JsonCreateNew -Path $paths.Baseline -Value $baseline
    Write-JournalRecord -Paths $paths -Kind "provider-transport-preflight" `
        -Status "completed" -Details @{
            phase = "before-fresh-prepare"
            source = [string]$providerTransport.source
            hostname_id = "dashscope-aliyun-beijing"
            resolved_ipv4_count = [int]$providerTransport.resolved_ipv4_count
            reachable_ipv4_count = [int]$providerTransport.reachable_ipv4_count
            binding_sha256 = [string]$providerTransport.binding_sha256
            authorization_header_sent = $false
            provider_model_request_sent = $false
            provider_secret_read = $false
            single_use_plan_claim_count = 0
            system_network_modified = $false
        }
    Write-JournalRecord -Paths $paths -Kind "preflight" -Status "passed" `
        -Details @{ exact_container_count = 8; listener_count = 0 }
    Write-Output "DEMO_PREFLIGHT=PASS"
    Write-Output ("DEMO_RUN_ID=" + $DemoRunId.ToString("D").ToLowerInvariant())
    Write-Output "DEMO_PREFLIGHT_EXACT_CONTAINER_COUNT=8"
    Write-Output "DEMO_PREFLIGHT_LISTENER_COUNT=0"
    Write-Output "DEMO_PREFLIGHT_STALE_PID_ACTIVE_COUNT=0"
    Write-Output "DEMO_PREFLIGHT_FROZEN_FILE_COUNT=8"
    Write-Output "DEMO_PREFLIGHT_M5_SECRET_VALUE_READ=false"
    Write-Output "DEMO_PREFLIGHT_PROVIDER_TRANSPORT=PASS"
    Write-Output ("DEMO_PREFLIGHT_PROVIDER_REACHABLE_IPV4_COUNT=" +
        [int]$providerTransport.reachable_ipv4_count)
}

function Invoke-StartInfrastructure {
    $paths = Get-DemoPaths
    $baseline = Read-Baseline -Paths $paths
    if (Test-Path -LiteralPath $paths.CoreRun) {
        throw "DEMO_CORE_RUN_DIRECTORY_ALREADY_EXISTS"
    }
    $success = $false
    $coreBinding = $null
    $primaryFailure = $null
    $cleanupFailure = $null
    try {
        Invoke-M4Script -Path $startPostgres -SuccessMarkers @("M4_POSTGRES_START=PASS")
        $prepareProcess = Start-Process -FilePath $python -ArgumentList @(
            $coreCli, "prepare", "--run-dir", $paths.CoreRun
        ) -WorkingDirectory $workspace -WindowStyle Hidden -Wait -PassThru `
          -RedirectStandardOutput $paths.PrepareStdout `
          -RedirectStandardError $paths.PrepareStderr
        if ($prepareProcess.ExitCode -ne 0) {
            throw ("DEMO_PREPARE_FAILED:" + $prepareProcess.ExitCode)
        }
        $coreBinding = New-CoreBinding -Paths $paths
        Write-JournalRecord -Paths $paths -Kind "prepare" -Status "completed" `
            -Details @{
                run_id = [string]$coreBinding.run_id
                demo_request_id = [string]$coreBinding.demo_request_id
                live_config_sha256 = [string]$coreBinding.live_config_sha256
            }

        Start-DemoStateMcp -Paths $paths
        Invoke-M4Script -Path $startFailClosed `
            -Arguments @(
                "-LaunchViaCim",
                "-WindowId", $DemoRunId.ToString("D")
            ) `
            -SuccessMarkers @("M4_FAIL_CLOSED_GATEWAY_START=PASS")
        [IO.File]::WriteAllText(
            $paths.FailClosedMarker,
            "started`n",
            (New-Object Text.UTF8Encoding($false))
        )
        Invoke-M4Script -Path $startController `
            -Arguments @("-ReadyTimeoutSeconds", [string]$ReadyTimeoutSeconds) `
            -SuccessMarkers @("M4_CONTROLLER_START=PASS")
        Invoke-M4Script -Path $refreshTokens -SuccessMarkers @("M4_RUNTIME_SA_REFRESH=PASS")
        [void](Assert-DemoHostRelayHelper)
        Invoke-M4Script -Path $startDemoHostRelay `
            -Arguments @(
                "-BaselinePath", $paths.Baseline,
                "-ReadyTimeoutSeconds", "120"
            ) `
            -SuccessMarkers @("DEMO_HOST_RELAY_STATUS=passed")
        $credentialSyncApply = Invoke-DemoWorkerGatewayCredentialSync `
            -Paths $paths -Baseline $baseline -Mode "apply"
        Write-Output "DEMO_WORKER_GATEWAY_CREDENTIAL_SYNC_APPLY=PASS"
        Write-Output ("DEMO_WORKER_GATEWAY_CREDENTIAL_SYNC_CHANGED_COUNT=" +
            [int]$credentialSyncApply.changed_count)
        [void](Assert-DemoWorkerEntrypointGuard)
        Invoke-M4Script -Path $startAgents `
            -Arguments @("-ReadyTimeoutSeconds", [string]$ReadyTimeoutSeconds) `
            -SuccessMarkers @("M4_AGENTTEAMS_RUNTIME_STATUS=passed")
        $credentialSyncVerify = Invoke-DemoWorkerGatewayCredentialSync `
            -Paths $paths -Baseline $baseline -Mode "verify" `
            -ChangedCount ([int]$credentialSyncApply.changed_count)
        Write-Output "DEMO_WORKER_GATEWAY_CREDENTIAL_SYNC_VERIFY=PASS"
        Write-Output ("DEMO_WORKER_GATEWAY_CREDENTIAL_SYNC_PROBE_COUNT=" +
            [int]$credentialSyncVerify.authenticated_probe_count)

        if ((Get-ListenerCount -Port 18188) -ne 1 -or
            (Get-ListenerCount -Port 18180) -ne 1) {
            throw "DEMO_ELEMENT_OR_MATRIX_LISTENER_INVALID"
        }
        Write-JournalRecord -Paths $paths -Kind "infrastructure" -Status "ready" `
            -Details @{
                element_url = "http://127.0.0.1:18188"
                matrix_url = "http://127.0.0.1:18180"
                provider_call_count = 0
            }
        $success = $true
    }
    catch {
        $primaryFailure = $_
    }
    finally {
        if (-not $success) {
            try {
                Invoke-StopRestoreInternal -Paths $paths -Baseline $baseline
            }
            catch {
                $cleanupFailure = $_
            }
        }
    }
    if ($null -ne $primaryFailure) {
        throw $primaryFailure
    }
    if ($null -ne $cleanupFailure) {
        throw $cleanupFailure
    }
    Write-Output "DEMO_INFRASTRUCTURE_START=PASS"
    Write-Output ("DEMO_EXECUTION_WINDOW_ID=" + $DemoRunId.ToString("D").ToLowerInvariant())
    Write-Output ("DEMO_CORE_RUN_ID=" + [string]$coreBinding.run_id)
    Write-Output ("DEMO_REQUEST_ID=" + [string]$coreBinding.demo_request_id)
    Write-Output "DEMO_ELEMENT_URL=http://127.0.0.1:18188"
    Write-Output "DEMO_GATEWAY_MODE=fail_closed"
    Write-Output "DEMO_PROVIDER_CALL_COUNT=0"
}

function Invoke-ResumeInfrastructure {
    $paths = Get-DemoPaths
    $baseline = Read-Baseline -Paths $paths
    $coreBinding = Read-CoreBinding -Paths $paths
    Assert-ResumeInfrastructureAdmission -Paths $paths -Baseline $baseline `
        -CoreBinding $coreBinding -Attempt $ResumeAttempt

    $recoveryId = [guid]::NewGuid().ToString("D").ToLowerInvariant()
    $failClosedWindowId = [guid]::NewGuid().ToString("D").ToLowerInvariant()
    $resumeMarkerPath = if ($ResumeAttempt -eq 1) {
        $paths.ResumeMarker
    }
    elseif ($ResumeAttempt -eq 2) {
        $paths.ResumeRecoveryMarker
    }
    elseif ($ResumeAttempt -eq 3) {
        $paths.ResumeRelayRecoveryMarker
    }
    elseif ($ResumeAttempt -eq 4) {
        $paths.ResumeLogPolicyRecoveryMarker
    }
    elseif ($ResumeAttempt -eq 5) {
        $paths.ResumeWiringRecoveryMarker
    }
    elseif ($ResumeAttempt -eq 6) {
        $paths.ResumeDemoRelayStageRecoveryMarker
    }
    elseif ($ResumeAttempt -eq 7) {
        $paths.ResumeDemoRelayPrestateBoundaryRecoveryMarker
    }
    else {
        throw "DEMO_RESUME_ATTEMPT_UNREACHABLE"
    }
    $failClosedMarkerPath = if ($ResumeAttempt -eq 1) {
        $paths.FailClosedMarker
    }
    elseif ($ResumeAttempt -eq 2) {
        $paths.FailClosedRecoveryMarker
    }
    elseif ($ResumeAttempt -eq 3) {
        $paths.FailClosedRelayRecoveryMarker
    }
    elseif ($ResumeAttempt -eq 4) {
        $paths.FailClosedLogPolicyRecoveryMarker
    }
    elseif ($ResumeAttempt -eq 5) {
        $paths.FailClosedWiringRecoveryMarker
    }
    elseif ($ResumeAttempt -eq 6) {
        $paths.FailClosedDemoRelayStageRecoveryMarker
    }
    elseif ($ResumeAttempt -eq 7) {
        $paths.FailClosedDemoRelayPrestateBoundaryRecoveryMarker
    }
    else {
        throw "DEMO_RESUME_ATTEMPT_UNREACHABLE"
    }
    $resumeKind = if ($ResumeAttempt -eq 1) {
        "resume-infrastructure"
    }
    elseif ($ResumeAttempt -eq 2) {
        "resume-infrastructure-recovery"
    }
    elseif ($ResumeAttempt -eq 3) {
        "resume-infrastructure-relay-recovery"
    }
    elseif ($ResumeAttempt -eq 4) {
        "resume-infrastructure-log-policy-recovery"
    }
    elseif ($ResumeAttempt -eq 5) {
        "resume-infrastructure-wiring-recovery"
    }
    elseif ($ResumeAttempt -eq 6) {
        "resume-infrastructure-demo-relay-stage-recovery"
    }
    elseif ($ResumeAttempt -eq 7) {
        "resume-infrastructure-demo-relay-prestate-boundary-recovery"
    }
    else {
        throw "DEMO_RESUME_ATTEMPT_UNREACHABLE"
    }
    $resumeSchema = if ($ResumeAttempt -eq 1) {
        "awakening.demo.resume-infrastructure.v1"
    }
    elseif ($ResumeAttempt -eq 2) {
        "awakening.demo.resume-infrastructure-recovery.v1"
    }
    elseif ($ResumeAttempt -eq 3) {
        "awakening.demo.resume-infrastructure-relay-recovery.v1"
    }
    elseif ($ResumeAttempt -eq 4) {
        "awakening.demo.resume-infrastructure-log-policy-recovery.v1"
    }
    elseif ($ResumeAttempt -eq 5) {
        "awakening.demo.resume-infrastructure-wiring-recovery.v1"
    }
    elseif ($ResumeAttempt -eq 6) {
        "awakening.demo.resume-infrastructure-demo-relay-stage-recovery.v1"
    }
    elseif ($ResumeAttempt -eq 7) {
        "awakening.demo.resume-infrastructure-demo-relay-prestate-boundary-recovery.v1"
    }
    else {
        throw "DEMO_RESUME_ATTEMPT_UNREACHABLE"
    }
    $success = $false
    try {
        $resumeMarker = [ordered]@{
            schema_version = $resumeSchema
            execution_window_id = $DemoRunId.ToString("D").ToLowerInvariant()
            core_run_id = [string]$coreBinding.run_id
            demo_request_id = [string]$coreBinding.demo_request_id
            recovery_id = $recoveryId
            fail_closed_window_id = $failClosedWindowId
            resume_attempt = $ResumeAttempt
            prepare_invocation_count = 0
            demo_host_relay_sha256 = $startDemoHostRelaySha256
            provider_secret_read = $false
            provider_called = $false
        }
        if ($ResumeAttempt -eq 6) {
            $resumeMarker["prior_resume_kind"] = "resume-infrastructure-wiring-recovery"
            $resumeMarker["prior_recovery_id"] = "bcd067ac-a126-469a-aae2-54c3a74ba95b"
            $resumeMarker["prior_failure_code"] =
                "DEMO_M4_SCRIPT_FAILED:Start-AgentTeamsDemoHostRelay.ps1:DEMO_CHILD_FAILURE_UNCLASSIFIED"
            $resumeMarker["prior_lifecycle_record_count"] = 49
            $resumeMarker["prior_lifecycle_bytes"] = 17172
            $resumeMarker["prior_lifecycle_sha256"] =
                "3a108d01185e075ad70893212bead29470950e55b30d181f9091d58d3dd7b188"
            $resumeMarker["cleanup_archive_window_id"] =
                "0c6c1b97-4fa5-4de7-b2da-9ca24f30e22a"
            $resumeMarker["cleanup_archive_manifest_sha256"] =
                "30616330cf8fd76f0701a690729ddf1673fbf505ad83fc3d07d1442465e7ba71"
        }
        if ($ResumeAttempt -eq 7) {
            $resumeMarker["prior_resume_kind"] =
                "resume-infrastructure-demo-relay-stage-recovery"
            $resumeMarker["prior_recovery_id"] = "6d952254-de40-4b82-9f73-b248ec871ad3"
            $resumeMarker["prior_failure_code"] =
                "DEMO_M4_SCRIPT_FAILED:Start-AgentTeamsDemoHostRelay.ps1:DEMO_HOST_RELAY_STAGE_FAILED:prestate-boundary"
            $resumeMarker["prior_lifecycle_record_count"] = 59
            $resumeMarker["prior_lifecycle_bytes"] = 20738
            $resumeMarker["prior_lifecycle_sha256"] =
                "fc84fa3dcd8ace07f5eac5056f7777825ccd4dff5e6617b805606d0bd8520874"
            $resumeMarker["cleanup_archive_window_id"] =
                "05b4aa81-5eff-4351-a070-641c481bbe1c"
            $resumeMarker["cleanup_archive_manifest_sha256"] =
                "660377cc2181fe0b5db207dcaff11206149da9b54661ef0d7986ec6d13fce5b2"
            $resumeMarker["relay_prestart_check_passed"] = $true
        }
        Write-JsonCreateNew -Path $resumeMarkerPath -Value $resumeMarker
        Move-DemoStateRuntimeEvidenceToArchive -Paths $paths
        Write-JournalRecord -Paths $paths -Kind $resumeKind -Status "started" `
            -Details @{
                recovery_id = $recoveryId
                resume_attempt = $ResumeAttempt
                prepare_invocation_count = 0
                provider_call_count = 0
            }

        Invoke-M4Script -Path $startPostgres -SuccessMarkers @("M4_POSTGRES_START=PASS")
        Start-DemoStateMcp -Paths $paths
        Write-JsonCreateNew -Path $failClosedMarkerPath -Value ([ordered]@{
            schema_version = "awakening.demo.fail-closed-runtime.v1"
            execution_window_id = $DemoRunId.ToString("D").ToLowerInvariant()
            recovery_id = $recoveryId
            fail_closed_window_id = $failClosedWindowId
            resume_attempt = $ResumeAttempt
            status = "start-intent"
            provider_configured = $false
        })
        Invoke-M4Script -Path $startFailClosed `
            -Arguments @(
                "-LaunchViaCim",
                "-ReadyTimeoutSeconds", [string]$ReadyTimeoutSeconds,
                "-WindowId", $failClosedWindowId
            ) `
            -SuccessMarkers @("M4_FAIL_CLOSED_GATEWAY_START=PASS")
        Write-JournalRecord -Paths $paths -Kind "fail-closed-gateway" -Status "started" `
            -Details @{
                window_id = $failClosedWindowId
                provider_call_count = 0
            }
        Invoke-M4Script -Path $startController `
            -Arguments @("-ReadyTimeoutSeconds", [string]$ReadyTimeoutSeconds) `
            -SuccessMarkers @("M4_CONTROLLER_START=PASS")
        Invoke-M4Script -Path $refreshTokens -SuccessMarkers @("M4_RUNTIME_SA_REFRESH=PASS")
        [void](Assert-DemoHostRelayHelper)
        Invoke-M4Script -Path $startDemoHostRelay `
            -Arguments @(
                "-BaselinePath", $paths.Baseline,
                "-ReadyTimeoutSeconds", "120"
            ) `
            -SuccessMarkers @("DEMO_HOST_RELAY_STATUS=passed")
        Invoke-M4Script -Path $startAgents `
            -Arguments @("-ReadyTimeoutSeconds", [string]$ReadyTimeoutSeconds) `
            -SuccessMarkers @("M4_AGENTTEAMS_RUNTIME_STATUS=passed")

        if ((Get-ListenerCount -Port 18188) -ne 1 -or
            (Get-ListenerCount -Port 18180) -ne 1 -or
            (Get-ListenerCount -Port 18190) -ne 1 -or
            (Get-ListenerCount -Port 18191) -ne 1) {
            throw "DEMO_RESUME_LISTENER_SET_INVALID"
        }
        Write-JournalRecord -Paths $paths -Kind "infrastructure" -Status "ready" `
            -Details @{
                element_url = "http://127.0.0.1:18188"
                matrix_url = "http://127.0.0.1:18180"
                provider_call_count = 0
                resumed = $true
            }
        Write-JournalRecord -Paths $paths -Kind $resumeKind -Status "completed" `
            -Details @{
                recovery_id = $recoveryId
                resume_attempt = $ResumeAttempt
                prepare_invocation_count = 0
                provider_call_count = 0
            }
        $success = $true
    }
    catch {
        $failureCode = "DEMO_RESUME_FAILURE_UNCLASSIFIED"
        $failureMatch = [regex]::Match(
            [string]$_.Exception.Message,
            'DEMO_M4_SCRIPT_FAILED:[A-Za-z0-9_.-]+:(?:DEMO|M4)_[A-Za-z0-9_:.-]+'
        )
        if ($failureMatch.Success) {
            $failureCode = [string]$failureMatch.Value
        }
        Write-JournalRecord -Paths $paths -Kind $resumeKind -Status "failed" `
            -Details @{
                recovery_id = $recoveryId
                resume_attempt = $ResumeAttempt
                prepare_invocation_count = 0
                provider_call_count = 0
                failure_code = $failureCode
            }
        throw
    }
    finally {
        if (-not $success) {
            Invoke-StopRestoreInternal -Paths $paths -Baseline $baseline
        }
    }

    Write-Output "DEMO_INFRASTRUCTURE_RESUME=PASS"
    Write-Output ("DEMO_EXECUTION_WINDOW_ID=" + $DemoRunId.ToString("D").ToLowerInvariant())
    Write-Output ("DEMO_CORE_RUN_ID=" + [string]$coreBinding.run_id)
    Write-Output ("DEMO_REQUEST_ID=" + [string]$coreBinding.demo_request_id)
    Write-Output ("DEMO_RECOVERY_ID=" + $recoveryId)
    Write-Output ("DEMO_RESUME_ATTEMPT=" + $ResumeAttempt)
    Write-Output "DEMO_PREPARE_INVOCATION_COUNT=0"
    Write-Output "DEMO_ELEMENT_URL=http://127.0.0.1:18188"
    Write-Output "DEMO_GATEWAY_MODE=fail_closed"
    Write-Output "DEMO_PROVIDER_SECRET_READ=false"
    Write-Output "DEMO_PROVIDER_CALL_COUNT=0"
}

function Invoke-ResumeAdmissionCheck {
    $paths = Get-DemoPaths
    $baseline = Read-Baseline -Paths $paths
    $coreBinding = Read-CoreBinding -Paths $paths
    Assert-ResumeInfrastructureAdmission -Paths $paths -Baseline $baseline `
        -CoreBinding $coreBinding -Attempt $ResumeAttempt
    Write-Output "DEMO_RESUME_ADMISSION_CHECK=PASS"
    Write-Output ("DEMO_RESUME_ATTEMPT=" + $ResumeAttempt)
    Write-Output "DEMO_RESUME_ADMISSION_MUTATION_COUNT=0"
    Write-Output "DEMO_PROVIDER_SECRET_READ=false"
    Write-Output "DEMO_PROVIDER_CALL_COUNT=0"
}

function Invoke-DemoMatrixControl {
    param(
        [Parameter(Mandatory = $true)]$Paths,
        [Parameter(Mandatory = $true)]$Baseline,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    $source = Assert-RegularFile -Path $matrixControlSource `
        -Reason "DEMO_MATRIX_CONTROL_SOURCE_INVALID"
    foreach ($argument in $Arguments) {
        if ([string]::IsNullOrWhiteSpace($argument) -or $argument -match '\s' -or
            $argument.Length -gt 4096) {
            throw "DEMO_MATRIX_CONTROL_ARGUMENT_INVALID"
        }
    }
    $baselineManager = @($Baseline.containers | Where-Object {
        $_.name -ceq "awakening-m4-manager"
    })
    if ($baselineManager.Count -ne 1) {
        throw "DEMO_MATRIX_CONTROL_MANAGER_BASELINE_MISSING"
    }

    $previousDockerConfig = $env:DOCKER_CONFIG
    try {
        $env:DOCKER_CONFIG = $dockerConfig
        $manager = Get-ContainerProjection -Name "awakening-m4-manager"
        Assert-ContainerMatchesBaselineIdentity -BaselineRecord $baselineManager[0] `
            -Current $manager
        if ($manager.state -cne "running" -or $manager.health -cne "healthy") {
            throw "DEMO_MATRIX_CONTROL_MANAGER_NOT_READY"
        }

        $startInfo = New-Object Diagnostics.ProcessStartInfo
        $startInfo.FileName = $docker
        $startInfo.Arguments = "exec -i " + $manager.id +
            " /bin/bash -s -- " + ($Arguments -join " ")
        $startInfo.WorkingDirectory = $workspace
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        $startInfo.RedirectStandardInput = $true
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
        $process = New-Object Diagnostics.Process
        $process.StartInfo = $startInfo
        if (-not $process.Start()) {
            throw "DEMO_MATRIX_CONTROL_PROCESS_START_FAILED"
        }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $bytes = [IO.File]::ReadAllBytes($source)
        try {
            $process.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length)
            $process.StandardInput.BaseStream.Flush()
            $process.StandardInput.Close()
        }
        finally {
            [Array]::Clear($bytes, 0, $bytes.Length)
        }
        $process.WaitForExit()
        $stdout = $stdoutTask.Result
        $stderr = $stderrTask.Result
        if ($process.ExitCode -ne 0 -or
            -not [string]::IsNullOrWhiteSpace($stderr) -or
            [Text.Encoding]::UTF8.GetByteCount($stdout) -gt 131072) {
            throw "DEMO_MATRIX_CONTROL_EXECUTION_FAILED"
        }
        $lines = @($stdout -split "`r?`n" | Where-Object {
            -not [string]::IsNullOrWhiteSpace($_)
        })
        if ($lines.Count -ne 1) {
            throw "DEMO_MATRIX_CONTROL_OUTPUT_INVALID"
        }
        return ($lines[0] | ConvertFrom-Json)
    }
    finally {
        $env:DOCKER_CONFIG = $previousDockerConfig
    }
}

function Invoke-AwaitHumanRequest {
    $paths = Get-DemoPaths
    $baseline = Read-Baseline -Paths $paths
    $coreBinding = Read-CoreBinding -Paths $paths
    if ($HumanMatrixUserId -notmatch '^@[A-Za-z0-9._=-]+:matrix-m4\.local:8080$' -or
        $HumanMatrixUserId -in @(
            "@manager:matrix-m4.local:8080",
            "@role_project_architect:matrix-m4.local:8080",
            "@execution_evidence_coach:matrix-m4.local:8080",
            "@independent_quality_reviewer:matrix-m4.local:8080"
        )) {
        throw "DEMO_HUMAN_MATRIX_USER_ID_INVALID"
    }
    if (Test-Path -LiteralPath $paths.HumanRequest) {
        throw "DEMO_HUMAN_REQUEST_EVIDENCE_EXISTS"
    }

    $discovery = Invoke-DemoMatrixControl -Paths $paths -Baseline $baseline `
        -Arguments @("discover", $HumanMatrixUserId, $ControlPeerUserId)
    if ([string]$discovery.command -cne "discover" -or
        [string]::IsNullOrWhiteSpace([string]$discovery.room_id)) {
        throw "DEMO_MATRIX_CONTROL_DISCOVERY_INVALID"
    }
    $roomId = [string]$discovery.room_id
    $baselineResult = Invoke-DemoMatrixControl -Paths $paths -Baseline $baseline `
        -Arguments @("baseline", $roomId, $HumanMatrixUserId, $ControlPeerUserId)
    if ([string]$baselineResult.command -cne "baseline" -or
        [string]::IsNullOrWhiteSpace([string]$baselineResult.since)) {
        throw "DEMO_MATRIX_CONTROL_BASELINE_INVALID"
    }

    $demoRequestText = [string]$coreBinding.demo_request_id
    $demoRunText = [string]$coreBinding.run_id
    if ($DemoRequestId -ne [guid]::Empty -and
        $DemoRequestId.ToString("D").ToLowerInvariant() -cne $demoRequestText) {
        throw "DEMO_REQUEST_ID_DOES_NOT_MATCH_CORE_BINDING"
    }
    $expectedBody = "Awakening AgentTeams Demo | demo_request_id=" + $demoRequestText +
        " | demo_run_id=" + $demoRunText +
        " | fixed synthetic job package | Manager coordinates Architect, Coach, Reviewer."
    Write-Output ("DEMO_HUMAN_MESSAGE=" + $expectedBody)
    Write-Output "DEMO_HUMAN_ACTION=send_the_exact_message_in_element_now"

    $request = Invoke-DemoMatrixControl -Paths $paths -Baseline $baseline `
        -Arguments @(
            "await-human-request",
            $roomId,
            $HumanMatrixUserId,
            $ControlPeerUserId,
            [string]$baselineResult.since,
            $demoRequestText,
            $demoRunText,
            [string]$HumanRequestTimeoutSeconds
        )
    if ([string]$request.command -cne "await-human-request" -or
        [string]$request.demo_request_id -cne $demoRequestText -or
        [string]$request.demo_run_id -cne $demoRunText -or
        [string]$request.human_event_id -notmatch '^\$[A-Za-z0-9._~+:/=-]{1,255}$') {
        throw "DEMO_MATRIX_HUMAN_REQUEST_OUTPUT_INVALID"
    }
    $accepted = Invoke-DemoMatrixControl -Paths $paths -Baseline $baseline `
        -Arguments @(
            "publish-event",
            $roomId,
            $HumanMatrixUserId,
            $ControlPeerUserId,
            [string]$request.human_event_id,
            $demoRequestText,
            $demoRunText,
            "request-accepted",
            "manager",
            [string]$request.human_event_id,
            [string]$request.body_sha256
        )
    if ([string]$accepted.command -cne "publish-event" -or
        [string]$accepted.phase -cne "request-accepted" -or
        [string]$accepted.target -cne "manager" -or
        [string]$accepted.event_id -notmatch '^\$[A-Za-z0-9._~+:/=-]{1,255}$') {
        throw "DEMO_MATRIX_REQUEST_ACCEPTED_INVALID"
    }
    $humanEvidence = [ordered]@{
        schema_version = "awakening.demo.human-request.v1"
        execution_window_id = $DemoRunId.ToString("D").ToLowerInvariant()
        run_id = $demoRunText
        demo_request_id = $demoRequestText
        room_id = $roomId
        human_user_id = $HumanMatrixUserId
        control_peer_user_id = $ControlPeerUserId
        human_event_id = [string]$request.human_event_id
        body_sha256 = [string]$request.body_sha256
        membership_sha256 = [string]$request.membership_sha256
        origin_server_ts = [int64]$request.origin_server_ts
        request_accepted_event_id = [string]$accepted.event_id
        request_accepted_body_sha256 = [string]$accepted.body_sha256
    }
    Write-JsonCreateNew -Path $paths.HumanRequest -Value $humanEvidence
    Write-MatrixEventRecord -Paths $paths -Value $accepted
    Write-JournalRecord -Paths $paths -Kind "human-request" -Status "captured" `
        -Details @{
            human_event_id = [string]$request.human_event_id
            room_id = $roomId
            body_sha256 = [string]$request.body_sha256
        }
    Write-Output "DEMO_HUMAN_REQUEST_CAPTURE=PASS"
    Write-Output ("DEMO_EXECUTION_WINDOW_ID=" + $DemoRunId.ToString("D").ToLowerInvariant())
    Write-Output ("DEMO_CORE_RUN_ID=" + $demoRunText)
    Write-Output ("DEMO_REQUEST_ID=" + $demoRequestText)
    Write-Output ("DEMO_HUMAN_REQUEST_EVENT_ID=" + [string]$request.human_event_id)
    Write-Output ("DEMO_REQUEST_ACCEPTED_EVENT_ID=" + [string]$accepted.event_id)
    Write-Output "DEMO_MATRIX_CONTROL_TRANSFER=stdin_only"
    Write-Output "DEMO_MATRIX_CONTROL_CONTAINER_COPY=false"
}

function Invoke-StartLiveGateway {
    $paths = Get-DemoPaths
    [void](Read-Baseline -Paths $paths)
    $coreBinding = Read-CoreBinding -Paths $paths
    [void](Read-HumanRequestBinding -Paths $paths -CoreBinding $coreBinding)
    Move-DemoFailedLiveEvidenceForRetry -Paths $paths
    $retryOneManifest = Join-Path $paths.Recovery `
        "demo-live-start-retry-1\archive-manifest.json"
    $retryTwoManifest = Join-Path $paths.Recovery `
        "demo-live-start-retry-2\archive-manifest.json"
    $retryOnePresent = Test-Path -LiteralPath $retryOneManifest -PathType Leaf
    $retryTwoPresent = Test-Path -LiteralPath $retryTwoManifest -PathType Leaf
    $retryDirectories = @()
    if (Test-Path -LiteralPath $paths.Recovery -PathType Container) {
        $retryDirectories = @(Get-ChildItem -LiteralPath $paths.Recovery -Directory -Force |
            Where-Object { $_.Name -match '^demo-live-start-retry-' })
    }
    $expectedRetryDirectoryCount = [int]$retryOnePresent + [int]$retryTwoPresent
    if (($retryTwoPresent -and -not $retryOnePresent) -or
        $retryDirectories.Count -ne $expectedRetryDirectoryCount -or
        @($retryDirectories | Where-Object {
            $_.Name -notin @("demo-live-start-retry-1", "demo-live-start-retry-2")
        }).Count -ne 0) {
        throw "DEMO_LIVE_RETRY_ARCHIVE_TOPOLOGY_INVALID"
    }
    $retryDocuments = @()
    if ($retryOnePresent) {
        $retryDocuments += Read-DemoLiveRetryArchiveManifest -Paths $paths -RetryAttempt 1
    }
    if ($retryTwoPresent) {
        $retryDocuments += Read-DemoLiveRetryArchiveManifest -Paths $paths -RetryAttempt 2
    }
    $liveStartAttempt = $retryDocuments.Count + 1
    $gatewayArchiveWindowId = $DemoRunId.ToString("D").ToLowerInvariant()
    if ($retryDocuments.Count -ne 0) {
        $retryDocument = $retryDocuments[$retryDocuments.Count - 1]
        $gatewayArchiveWindowId =
            [string]$retryDocument.fail_closed_archive_window_id
    }
    if ($retryDocuments.Count -eq 2 -and
        [string]$retryDocuments[0].fail_closed_archive_window_id -ceq
            [string]$retryDocuments[1].fail_closed_archive_window_id) {
        throw "DEMO_LIVE_RETRY_ARCHIVE_WINDOW_REUSED"
    }
    $providerTransport = Get-DemoProviderTransportBinding
    $resolvedProviderIPv4 = @($providerTransport.reachable_ipv4)
    $providerBindingSha256 = [string]$providerTransport.binding_sha256

    Write-Output "DEMO_PROVIDER_TRANSPORT_PREFLIGHT=PASS"
    Write-Output ("DEMO_PROVIDER_TRANSPORT_RESOLVED_IPV4_COUNT=" +
        [int]$providerTransport.resolved_ipv4_count)
    Write-Output ("DEMO_PROVIDER_TRANSPORT_REACHABLE_IPV4_COUNT=" +
        [int]$providerTransport.reachable_ipv4_count)
    Write-Output "DEMO_PROVIDER_TRANSPORT_IP_PERSISTED=false"
    Write-Output "DEMO_PROVIDER_TRANSPORT_SYSTEM_NETWORK_MODIFIED=false"
    Write-JournalRecord -Paths $paths -Kind "provider-transport-preflight" `
        -Status "completed" -Details @{
            phase = "before-live-switch"
            live_start_attempt = $liveStartAttempt
            source = [string]$providerTransport.source
            hostname_id = "dashscope-aliyun-beijing"
            resolved_ipv4_count = [int]$providerTransport.resolved_ipv4_count
            reachable_ipv4_count = $resolvedProviderIPv4.Count
            binding_sha256 = $providerBindingSha256
            authorization_header_sent = $false
            provider_model_request_sent = $false
            provider_secret_read = $false
            single_use_plan_claim_count = 0
            system_network_modified = $false
        }

    try {
        Stop-M4FailClosedGateway
        Invoke-M4Script -Path $archiveGatewayEvidence -Arguments @(
            "-RuntimeKind", "FailClosed",
            "-WindowId", $gatewayArchiveWindowId,
            "-Phase", "pre-live-fail-closed"
        ) -SuccessMarkers @("M4_GATEWAY_EVIDENCE_ARCHIVE=PASS")
        Start-CoreLiveGateway -Paths $paths `
            -ResolvedProviderIPv4 $resolvedProviderIPv4 `
            -ReachableProviderIPv4Count $resolvedProviderIPv4.Count `
            -ProviderBindingSha256 $providerBindingSha256 `
            -LiveStartAttempt $liveStartAttempt
    }
    catch {
        $originalFailure = $_
        $secretReadStatus = Get-DemoLiveGatewaySecretReadStatus -Paths $paths
        Write-JournalRecord -Paths $paths -Kind "demo-live-gateway" -Status "failed" `
            -SecretValueReadStatus $secretReadStatus `
            -Details @{
                provider_secret_read_status = $secretReadStatus
                lifecycle_script_secret_read = $false
                provider_call_count = 0
                live_start_attempt = $liveStartAttempt
            }
        $recoveryFailures = @()
        if (Test-Path -LiteralPath $paths.LivePid -PathType Leaf) {
            try { Stop-CoreLiveGateway -Paths $paths } catch {
                $recoveryFailures += "demo-live-gateway"
            }
        }
        elseif ((Get-ListenerCount -Port 18190) -ne 0) {
            # Without the create-new PID binding this is an unknown listener;
            # never guess its ownership or kill it by port alone.
            $recoveryFailures += "unowned-live-listener"
        }
        try {
            if ((Get-ListenerCount -Port 18190) -eq 0) {
                Invoke-M4Script -Path $startFailClosed `
                    -Arguments @(
                        "-LaunchViaCim",
                        "-WindowId", ([guid]::NewGuid().ToString("D"))
                    ) `
                    -SuccessMarkers @("M4_FAIL_CLOSED_GATEWAY_START=PASS")
            }
            Invoke-M4Script -Path $startFailClosed `
                -Arguments @(
                    "-ValidateExisting",
                    "-WindowId", ([guid]::NewGuid().ToString("D"))
                ) `
                -SuccessMarkers @("M4_FAIL_CLOSED_GATEWAY_VALIDATE_EXISTING=PASS")
        }
        catch { $recoveryFailures += "fail-closed-gateway" }
        if ($recoveryFailures.Count -ne 0) {
            throw ("DEMO_LIVE_SWITCH_RECOVERY_FAILED:" + (($recoveryFailures | Sort-Object -Unique) -join ","))
        }
        throw $originalFailure
    }
    Write-Output "DEMO_LIVE_GATEWAY_START=PASS"
    Write-Output "DEMO_LIVE_GATEWAY_LOOPBACK=127.0.0.1:18190"
    Write-Output "DEMO_M5_SECRET_READ_BY_LIFECYCLE_SCRIPT=false"
    Write-Output "DEMO_M5_SECRET_READ_BY_GATEWAY=true"
}

function Read-HumanRequestBinding {
    param(
        [Parameter(Mandatory = $true)]$Paths,
        [Parameter(Mandatory = $true)]$CoreBinding
    )

    $path = Assert-RegularFile -Path $Paths.HumanRequest `
        -Reason "DEMO_HUMAN_REQUEST_REQUIRED"
    $human = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    $eventPattern = '^\$[A-Za-z0-9._~+:/=-]{1,255}$'
    $hashPattern = '^[0-9a-f]{64}$'
    $peerAllowed = @(
        "none",
        "@role_project_architect:matrix-m4.local:8080",
        "@execution_evidence_coach:matrix-m4.local:8080",
        "@independent_quality_reviewer:matrix-m4.local:8080"
    )
    if ([string]$human.schema_version -cne "awakening.demo.human-request.v1" -or
        [string]$human.execution_window_id -cne $DemoRunId.ToString("D").ToLowerInvariant() -or
        [string]$human.run_id -cne [string]$CoreBinding.run_id -or
        [string]$human.demo_request_id -cne [string]$CoreBinding.demo_request_id -or
        [string]$human.room_id -notmatch '^![A-Za-z0-9._~+/-]+:matrix-m4\.local:8080$' -or
        [string]$human.human_user_id -notmatch '^@[A-Za-z0-9._=-]+:matrix-m4\.local:8080$' -or
        [string]$human.control_peer_user_id -notin $peerAllowed -or
        [string]$human.human_event_id -notmatch $eventPattern -or
        [string]$human.request_accepted_event_id -notmatch $eventPattern -or
        [string]$human.body_sha256 -notmatch $hashPattern -or
        [string]$human.membership_sha256 -notmatch $hashPattern) {
        throw "DEMO_HUMAN_REQUEST_BINDING_INVALID"
    }
    return $human
}

function Publish-DemoMatrixEvent {
    param(
        [Parameter(Mandatory = $true)]$Paths,
        [Parameter(Mandatory = $true)]$Baseline,
        [Parameter(Mandatory = $true)]$CoreBinding,
        [Parameter(Mandatory = $true)]$HumanBinding,
        [Parameter(Mandatory = $true)][string]$Phase,
        [Parameter(Mandatory = $true)][string]$Target,
        [Parameter(Mandatory = $true)][string]$EvidenceEventId,
        [Parameter(Mandatory = $true)][string]$EvidenceSha256
    )

    $published = Invoke-DemoMatrixControl -Paths $Paths -Baseline $Baseline `
        -Arguments @(
            "publish-event",
            [string]$HumanBinding.room_id,
            [string]$HumanBinding.human_user_id,
            [string]$HumanBinding.control_peer_user_id,
            [string]$HumanBinding.human_event_id,
            [string]$CoreBinding.demo_request_id,
            [string]$CoreBinding.run_id,
            $Phase,
            $Target,
            $EvidenceEventId,
            $EvidenceSha256
        )
    if ([string]$published.command -cne "publish-event" -or
        [string]$published.phase -cne $Phase -or
        [string]$published.target -cne $Target -or
        [string]$published.demo_request_id -cne [string]$CoreBinding.demo_request_id -or
        [string]$published.demo_run_id -cne [string]$CoreBinding.run_id -or
        [string]$published.parent_event_id -cne [string]$HumanBinding.human_event_id -or
        [string]$published.event_id -notmatch '^\$[A-Za-z0-9._~+:/=-]{1,255}$') {
        throw "DEMO_MATRIX_PUBLISHED_EVENT_INVALID"
    }
    Write-MatrixEventRecord -Paths $Paths -Value $published
    return $published
}

function Invoke-RunChain {
    $paths = Get-DemoPaths
    $baseline = Read-Baseline -Paths $paths
    $coreBinding = Read-CoreBinding -Paths $paths
    $humanBinding = Read-HumanRequestBinding -Paths $paths -CoreBinding $coreBinding
    Assert-DemoLiveGatewayRuntimeBinding -Paths $paths
    foreach ($target in @($paths.RunStdout, $paths.RunStderr)) {
        if (Test-Path -LiteralPath $target) {
            throw "DEMO_RUN_CHAIN_EVIDENCE_EXISTS"
        }
    }
    $effectiveHumanEventId = [string]$humanBinding.human_event_id
    if (-not [string]::IsNullOrWhiteSpace($HumanRequestEventId) -and
        $HumanRequestEventId -cne $effectiveHumanEventId) {
        throw "DEMO_HUMAN_REQUEST_EVENT_ID_OVERRIDE_DENIED"
    }
    $runArguments = @(
        $coreCli, "run-chain", "--run-dir", $paths.CoreRun,
        "--human-request-event-id", $effectiveHumanEventId
    )
    $process = Start-Process -FilePath $python -ArgumentList $runArguments `
      -WorkingDirectory $workspace -WindowStyle Hidden -Wait -PassThru `
      -RedirectStandardOutput $paths.RunStdout `
      -RedirectStandardError $paths.RunStderr
    $resultPath = Join-Path $paths.CoreRun "result.json"
    if (-not (Test-Path -LiteralPath $resultPath -PathType Leaf)) {
        Write-JournalRecord -Paths $paths -Kind "run-chain" -Status "failed" `
            -Details @{ exit_code = $process.ExitCode; hidden_retry_count = 0 }
        throw ("DEMO_RUN_CHAIN_RESULT_MISSING:" + $process.ExitCode)
    }
    $resultPath = Assert-RegularFile -Path $resultPath -Reason "DEMO_RUN_CHAIN_RESULT_INVALID"
    $result = Get-Content -LiteralPath $resultPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([string]$result.authorization_id -cne "AUTH-DEMO-001" -or
        [string]$result.demo_request_id -cne [string]$coreBinding.demo_request_id -or
        [string]$result.state_binding.run_id -cne [string]$coreBinding.run_id -or
        [string]$result.human_request_event_id -cne $effectiveHumanEventId -or
        [int]$result.provider_retry_count -ne 0) {
        throw "DEMO_RUN_CHAIN_RESULT_BINDING_INVALID"
    }
    $calls = @($result.calls)
    foreach ($call in $calls) {
        $target = [string]$call.agent_identity_id
        [void](Publish-DemoMatrixEvent -Paths $paths -Baseline $baseline `
            -CoreBinding $coreBinding -HumanBinding $humanBinding `
            -Phase "worker-dispatched" -Target $target `
            -EvidenceEventId ([string]$call.delivery_id) `
            -EvidenceSha256 ([string]$call.trusted_package_sha256))
        [void](Publish-DemoMatrixEvent -Paths $paths -Baseline $baseline `
            -CoreBinding $coreBinding -HumanBinding $humanBinding `
            -Phase "worker-completed" -Target $target `
            -EvidenceEventId ([string]$call.response_event_id) `
            -EvidenceSha256 ([string]$call.output_sha256))
    }
    $resultHash = (Get-FileHash -LiteralPath $resultPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $summaryPhase = if ([string]$result.status -ceq "completed" -and
        $process.ExitCode -eq 0 -and $calls.Count -eq 3) {
        "summary-completed"
    }
    else {
        "summary-failed"
    }
    $summary = Publish-DemoMatrixEvent -Paths $paths -Baseline $baseline `
        -CoreBinding $coreBinding -HumanBinding $humanBinding `
        -Phase $summaryPhase -Target "all" -EvidenceEventId "none" `
        -EvidenceSha256 $resultHash
    if ($process.ExitCode -ne 0 -or $summaryPhase -cne "summary-completed") {
        Write-JournalRecord -Paths $paths -Kind "run-chain" -Status "failed" `
            -Details @{
                exit_code = $process.ExitCode
                hidden_retry_count = 0
                summary_event_id = [string]$summary.event_id
            }
        throw ("DEMO_RUN_CHAIN_FAILED:" + $process.ExitCode)
    }
    Write-JournalRecord -Paths $paths -Kind "run-chain" -Status "completed" `
        -Details @{
            exit_code = 0
            hidden_retry_count = 0
            projected_worker_event_count = 6
            summary_event_id = [string]$summary.event_id
            result_sha256 = $resultHash
        }
    Write-Output "DEMO_RUN_CHAIN=PASS"
    Write-Output "DEMO_HIDDEN_RETRY_COUNT=0"
    Write-Output "DEMO_MATRIX_WORKER_PROJECTION_EVENT_COUNT=6"
    Write-Output ("DEMO_MATRIX_SUMMARY_EVENT_ID=" + [string]$summary.event_id)
    Write-Output ("DEMO_RUN_CHAIN_STDOUT=" + $paths.RunStdout.Substring($workspace.Length).TrimStart('\'))
}

function Invoke-StopRestore {
    $paths = Get-DemoPaths
    $baseline = Read-Baseline -Paths $paths
    Invoke-StopRestoreInternal -Paths $paths -Baseline $baseline
    Write-Output "DEMO_STOP_RESTORE=PASS"
    Write-Output "DEMO_POSTSTATE_EXACT_CONTAINER_COUNT=8"
    Write-Output "DEMO_POSTSTATE_LISTENER_COUNT=0"
}

switch ($Action) {
    "OfflineCheck" { Invoke-OfflineCheck }
    "Preflight" { Invoke-Preflight }
    "StartInfrastructure" { Invoke-StartInfrastructure }
    "ResumeAdmissionCheck" { Invoke-ResumeAdmissionCheck }
    "ResumeInfrastructure" { Invoke-ResumeInfrastructure }
    "AwaitHumanRequest" { Invoke-AwaitHumanRequest }
    "StartLiveGateway" { Invoke-StartLiveGateway }
    "RunChain" { Invoke-RunChain }
    "StopRestore" { Invoke-StopRestore }
    default { throw "DEMO_ACTION_UNREACHABLE" }
}
