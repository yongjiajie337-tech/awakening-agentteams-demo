#requires -Version 5.1

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BaselinePath,

    [ValidateRange(10, 300)]
    [int]$ReadyTimeoutSeconds = 120,

    [switch]$PrestartCheck
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$workspace = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$demoBaselineRoot = [IO.Path]::GetFullPath(
    (Join-Path $workspace "tmp\demo\agentteams-in-place")
)
$docker = Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\resources\bin\docker.exe"
$relayName = "awakening-m4-host-relay"
$relaySource = [IO.Path]::GetFullPath(
    (Join-Path $workspace "infra\agentteams\m4\runtime\m4-host-relay.py")
)
$relayTarget = "/opt/awakening/m4/m4-host-relay.py"
$relaySourceHash = "4bdbaf66910b21d530ccd00790052dc888dc69ad40543bac64773ad9b0d36a2e"
$relayImage = "higress-registry.cn-hangzhou.cr.aliyuncs.com/higress/hiclaw-embedded:v1.1.2@sha256:5486f4643a04a3a7a4dd81cd7f1d6091f9b7db3a5446bd5676f567c857910978"
$relayImageId = "sha256:5486f4643a04a3a7a4dd81cd7f1d6091f9b7db3a5446bd5676f567c857910978"
$m4Network = "awakening-m4-net"
$uplinkNetwork = "awakening-m4-host-uplink"
$relayIp = "172.20.0.254"
$readyLine = "M4_HOST_RELAY_READY=172.20.0.254:18190,172.20.0.254:18191"
$stopLine = "M4_HOST_RELAY_STOPPED=true"

function Assert-DemoStringArraysEqual {
    param(
        [Parameter(Mandatory = $true)][object[]]$Actual,
        [Parameter(Mandatory = $true)][object[]]$Expected,
        [Parameter(Mandatory = $true)][string]$Reason
    )

    if ($Actual.Count -ne $Expected.Count) {
        throw $Reason
    }
    for ($index = 0; $index -lt $Expected.Count; $index++) {
        if ([string]$Actual[$index] -cne [string]$Expected[$index]) {
            throw $Reason
        }
    }
}

function Assert-DemoRegularFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Reason
    )

    $candidate = [IO.Path]::GetFullPath($Path)
    try {
        $resolved = [IO.Path]::GetFullPath(
            (Resolve-Path -LiteralPath $candidate -ErrorAction Stop).ProviderPath
        )
        $item = Get-Item -LiteralPath $resolved -Force -ErrorAction Stop
    }
    catch {
        throw $Reason
    }
    if (-not [StringComparer]::OrdinalIgnoreCase.Equals($candidate, $resolved) -or
        $item.PSIsContainer -or $item.Length -le 0 -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw $Reason
    }
    return $resolved
}

function Read-DemoRelayBaseline {
    param([Parameter(Mandatory = $true)][string]$Path)

    $resolved = Assert-DemoRegularFile -Path $Path `
        -Reason "DEMO_HOST_RELAY_BASELINE_FILE_INVALID"
    $rootPrefix = $demoBaselineRoot.TrimEnd("\") + "\"
    if (-not $resolved.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetFileName($resolved) -cne "preflight-baseline.json") {
        throw "DEMO_HOST_RELAY_BASELINE_SCOPE_INVALID"
    }
    try {
        $baseline = Get-Content -LiteralPath $resolved -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        throw "DEMO_HOST_RELAY_BASELINE_JSON_INVALID"
    }

    $demoRunId = [guid]::Empty
    try {
        $schemaValid = [string]$baseline.schema_version -ceq "awakening.demo.preflight.v1"
        $idValid = [guid]::TryParse([string]$baseline.demo_run_id, [ref]$demoRunId)
        $containerCount = @($baseline.containers).Count
    }
    catch {
        throw "DEMO_HOST_RELAY_BASELINE_SCHEMA_INVALID"
    }
    if (-not $schemaValid -or -not $idValid -or $demoRunId -eq [guid]::Empty -or
        $containerCount -ne 8) {
        throw "DEMO_HOST_RELAY_BASELINE_SCHEMA_INVALID"
    }
    $expectedDirectory = ".preflight-" + $demoRunId.ToString("D").ToLowerInvariant()
    $directory = [IO.DirectoryInfo]([IO.Path]::GetDirectoryName($resolved))
    if ($directory.Name -cne $expectedDirectory -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals(
            [IO.Path]::GetFullPath($directory.Parent.FullName),
            $demoBaselineRoot
        )) {
        throw "DEMO_HOST_RELAY_BASELINE_DIRECTORY_BINDING_INVALID"
    }

    $relayRecords = @($baseline.containers | Where-Object {
        [string]$_.name -ceq $relayName
    })
    if ($relayRecords.Count -ne 1) {
        throw "DEMO_HOST_RELAY_BASELINE_RECORD_INVALID"
    }
    $record = $relayRecords[0]
    try {
        $baselineNetworks = @($record.networks | ForEach-Object { [string]$_ } | Sort-Object)
        $baselineMounts = @($record.mounts)
        $baselinePublishedPorts = @($record.published_ports)
        $recordId = [string]$record.id
        $recordImageId = [string]$record.image_id
        $recordState = [string]$record.state
        $recordExitCode = [int]$record.exit_code
        $recordRestartCount = [int]$record.restart_count
        $recordRestartPolicy = [string]$record.restart_policy
        $recordNetworkMode = [string]$record.network_mode
    }
    catch {
        throw "DEMO_HOST_RELAY_BASELINE_RECORD_INVALID"
    }
    $fixedNetworks = @($m4Network, $uplinkNetwork) | Sort-Object
    Assert-DemoStringArraysEqual -Actual $baselineNetworks -Expected $fixedNetworks `
        -Reason "DEMO_HOST_RELAY_BASELINE_NETWORK_SET_INVALID"
    if ($recordId -notmatch '^[0-9a-f]{64}$' -or
        $recordImageId -cne $relayImageId -or
        $recordState -cne "exited" -or $recordExitCode -ne 0 -or
        $recordRestartCount -ne 0 -or $recordRestartPolicy -cne "no" -or
        $recordNetworkMode -cne $uplinkNetwork -or
        $baselinePublishedPorts.Count -ne 0 -or $baselineMounts.Count -ne 1) {
        throw "DEMO_HOST_RELAY_BASELINE_BOUNDARY_INVALID"
    }

    $baselineMount = $baselineMounts[0]
    try {
        $baselineMountSource = [IO.Path]::GetFullPath([string]$baselineMount.source)
        $baselineMountType = [string]$baselineMount.type
        $baselineMountName = [string]$baselineMount.name
        $baselineMountDestination = [string]$baselineMount.destination
        $baselineMountRw = [bool]$baselineMount.rw
    }
    catch {
        throw "DEMO_HOST_RELAY_BASELINE_MOUNT_INVALID"
    }
    if ($baselineMountType -cne "bind" -or $baselineMountName -cne "" -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals(
            $baselineMountSource,
            $relaySource
        ) -or
        $baselineMountDestination -cne $relayTarget -or $baselineMountRw) {
        throw "DEMO_HOST_RELAY_BASELINE_MOUNT_INVALID"
    }

    return [pscustomobject]@{
        Path = $resolved
        DemoRunId = $demoRunId.ToString("D").ToLowerInvariant()
        Record = $record
        ContainerId = $recordId
        Networks = $baselineNetworks
        Mounts = $baselineMounts
    }
}

function Get-DemoRelayContainer {
    param([Parameter(Mandatory = $true)][string]$ContainerId)

    $documents = @(& $docker inspect $ContainerId 2>$null | ConvertFrom-Json)
    if ($LASTEXITCODE -ne 0 -or $documents.Count -ne 1) {
        throw "DEMO_HOST_RELAY_CONTAINER_INSPECT_FAILED"
    }
    $container = $documents[0]
    if ([string]$container.Id -cne $ContainerId -or
        [string]$container.Name -cne ("/" + $relayName)) {
        throw "DEMO_HOST_RELAY_CONTAINER_ID_BINDING_INVALID"
    }
    return $container
}

function Get-DemoPublishedPorts {
    param([Parameter(Mandatory = $true)]$Container)

    if ($null -eq $Container.HostConfig.PortBindings) {
        return @()
    }
    return @($Container.HostConfig.PortBindings.PSObject.Properties | Where-Object {
        $null -ne $_.Value -and @($_.Value).Count -gt 0
    })
}

function Assert-DemoMountsMatchBaseline {
    param(
        [Parameter(Mandatory = $true)][object[]]$Actual,
        [Parameter(Mandatory = $true)][object[]]$Baseline
    )

    $actualKeys = @($Actual | ForEach-Object {
        [string]$_.Destination
    } | Sort-Object)
    $baselineKeys = @($Baseline | ForEach-Object {
        [string]$_.destination
    } | Sort-Object)
    if ($Actual.Count -ne $Baseline.Count -or
        @($actualKeys | Sort-Object -Unique).Count -ne $actualKeys.Count -or
        @($baselineKeys | Sort-Object -Unique).Count -ne $baselineKeys.Count -or
        [string]::Join("|", $actualKeys) -cne [string]::Join("|", $baselineKeys)) {
        throw "DEMO_HOST_RELAY_MOUNT_SET_CHANGED"
    }
    foreach ($actualMount in $Actual) {
        $baselineMatches = @($Baseline | Where-Object {
            [string]$_.destination -ceq [string]$actualMount.Destination
        })
        if ($baselineMatches.Count -ne 1) {
            throw "DEMO_HOST_RELAY_MOUNT_SET_CHANGED"
        }
        $baselineMount = $baselineMatches[0]
        $actualNameProperty = $actualMount.PSObject.Properties["Name"]
        $actualName = if ($null -eq $actualNameProperty) {
            ""
        }
        else {
            [string]$actualNameProperty.Value
        }
        $actualSource = [IO.Path]::GetFullPath([string]$actualMount.Source)
        $baselineSource = [IO.Path]::GetFullPath([string]$baselineMount.source)
        if ([string]$actualMount.Type -cne [string]$baselineMount.type -or
            $actualName -cne [string]$baselineMount.name -or
            -not [StringComparer]::OrdinalIgnoreCase.Equals($actualSource, $baselineSource) -or
            [string]$actualMount.Destination -cne [string]$baselineMount.destination -or
            [bool]$actualMount.RW -ne [bool]$baselineMount.rw) {
            throw "DEMO_HOST_RELAY_MOUNT_SET_CHANGED"
        }
    }
}

function Assert-DemoRelayBoundary {
    param(
        [Parameter(Mandatory = $true)]$Container,
        [Parameter(Mandatory = $true)]$Baseline,
        [Parameter(Mandatory = $true)][ValidateSet("exited", "running")][string]$ExpectedState,
        [Parameter(Mandatory = $true)][object[]]$ImageEnvironment
    )

    if ([string]$Container.Id -cne [string]$Baseline.ContainerId -or
        [string]$Container.Image -cne $relayImageId -or
        [string]$Container.Config.Image -cne $relayImage -or
        [string]$Container.HostConfig.RestartPolicy.Name -cne "no" -or
        [int]$Container.RestartCount -ne 0 -or
        [string]$Container.HostConfig.NetworkMode -cne $uplinkNetwork -or
        [bool]$Container.HostConfig.AutoRemove -or
        [bool]$Container.HostConfig.Privileged) {
        throw "DEMO_HOST_RELAY_IMMUTABLE_BOUNDARY_CHANGED"
    }
    if ($ExpectedState -ceq "exited") {
        if ([string]$Container.State.Status -cne "exited" -or
            [bool]$Container.State.Running -or [bool]$Container.State.Restarting -or
            [int]$Container.State.ExitCode -ne 0) {
            throw "DEMO_HOST_RELAY_PRESTATE_INVALID"
        }
    }
    elseif ([string]$Container.State.Status -cne "running" -or
        -not [bool]$Container.State.Running -or [bool]$Container.State.Restarting) {
        throw "DEMO_HOST_RELAY_RUNNING_STATE_INVALID"
    }

    $actualNetworks = @(
        $Container.NetworkSettings.Networks.PSObject.Properties.Name | Sort-Object
    )
    Assert-DemoStringArraysEqual -Actual $actualNetworks -Expected $Baseline.Networks `
        -Reason "DEMO_HOST_RELAY_NETWORK_SET_CHANGED"
    if ($actualNetworks.Count -ne 2) {
        throw "DEMO_HOST_RELAY_NETWORK_COUNT_INVALID"
    }
    $m4Endpoint = $Container.NetworkSettings.Networks.PSObject.Properties[$m4Network]
    if ($null -eq $m4Endpoint) {
        throw "DEMO_HOST_RELAY_STATIC_IP_INVALID"
    }
    $configuredIp = ""
    if ($ExpectedState -ceq "running") {
        $configuredIp = [string]$m4Endpoint.Value.IPAddress
    }
    elseif ($null -ne $m4Endpoint.Value.IPAMConfig) {
        $configuredIp = [string]$m4Endpoint.Value.IPAMConfig.IPv4Address
    }
    if ($configuredIp -cne $relayIp) {
        throw "DEMO_HOST_RELAY_STATIC_IP_INVALID"
    }

    $actualMounts = @($Container.Mounts)
    Assert-DemoMountsMatchBaseline -Actual $actualMounts -Baseline $Baseline.Mounts
    if (@(Get-DemoPublishedPorts -Container $Container).Count -ne 0) {
        throw "DEMO_HOST_RELAY_PUBLISHED_PORT_PRESENT"
    }

    $environment = @($Container.Config.Env | Where-Object { $null -ne $_ })
    Assert-DemoStringArraysEqual -Actual $environment -Expected $ImageEnvironment `
        -Reason "DEMO_HOST_RELAY_INJECTED_ENVIRONMENT_PRESENT"
    $providerEnvironment = @($environment | Where-Object {
        $_ -match '(?i)^(?:DASHSCOPE_API_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY|HICLAW_LLM_API_KEY|AZURE_OPENAI_API_KEY|GOOGLE_API_KEY|GEMINI_API_KEY|HTTP_PROXY|HTTPS_PROXY|ALL_PROXY|NO_PROXY)='
    })
    if ($providerEnvironment.Count -ne 0) {
        throw "DEMO_HOST_RELAY_PROVIDER_ENVIRONMENT_PRESENT"
    }
    $providerMounts = @($actualMounts | Where-Object {
        [string]$_.Source -match '(?i)(?:provider|secret|credential|\.env(?:\.|$))' -or
        [string]$_.Destination -match '(?i)(?:provider|secret|credential|\.env(?:\.|$))'
    })
    if ($providerMounts.Count -ne 0) {
        throw "DEMO_HOST_RELAY_PROVIDER_MOUNT_PRESENT"
    }
}

function Get-DemoDockerLogLines {
    param([Parameter(Mandatory = $true)][string]$ContainerId)

    $startInfo = New-Object Diagnostics.ProcessStartInfo
    $startInfo.FileName = $docker
    $startInfo.Arguments = "logs " + $ContainerId
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $startInfo
    if (-not $process.Start()) {
        throw "DEMO_HOST_RELAY_LOG_CAPTURE_START_FAILED"
    }
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $process.WaitForExit()
    $stdout = $stdoutTask.Result
    $stderr = $stderrTask.Result
    $exitCode = $process.ExitCode
    $process.Dispose()
    if ($exitCode -ne 0) {
        throw "DEMO_HOST_RELAY_LOG_CAPTURE_FAILED"
    }
    return @(($stdout + "`n" + $stderr) -split "`r?`n" | Where-Object {
        -not [string]::IsNullOrWhiteSpace($_)
    })
}

function Test-DemoReadySuffix {
    param(
        [Parameter(Mandatory = $true)][object[]]$Before,
        [Parameter(Mandatory = $true)][object[]]$After
    )

    if ($After.Count -ne ($Before.Count + 1)) {
        return $false
    }
    for ($index = 0; $index -lt $Before.Count; $index++) {
        if ([string]$After[$index] -cne [string]$Before[$index]) {
            return $false
        }
    }
    return [string]$After[$After.Count - 1] -ceq $readyLine
}

function Invoke-DemoRelayPythonProbe {
    param(
        [Parameter(Mandatory = $true)][string]$ContainerId,
        [Parameter(Mandatory = $true)][string]$Script
    )

    $startInfo = New-Object Diagnostics.ProcessStartInfo
    $startInfo.FileName = $docker
    $startInfo.Arguments = "exec -i " + $ContainerId + " /usr/bin/python3 -B -"
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardInput = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $startInfo
    if (-not $process.Start()) {
        throw "DEMO_HOST_RELAY_PROBE_START_FAILED"
    }
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $process.StandardInput.Write($Script)
    $process.StandardInput.Close()
    $process.WaitForExit()
    $stdout = $stdoutTask.Result
    $stderr = $stderrTask.Result
    $exitCode = $process.ExitCode
    $process.Dispose()
    return [pscustomobject]@{
        ExitCode = $exitCode
        Output = @($stdout -split "`r?`n" | Where-Object {
            -not [string]::IsNullOrWhiteSpace($_)
        })
        StderrPresent = -not [string]::IsNullOrWhiteSpace($stderr)
    }
}

function Stop-DemoRelayAfterFailure {
    param([Parameter(Mandatory = $true)][string]$ContainerId)

    $current = Get-DemoRelayContainer -ContainerId $ContainerId
    if ([bool]$current.State.Running) {
        $stopOutput = @(& $docker stop --time 10 $ContainerId 2>$null)
        if ($LASTEXITCODE -ne 0 -or $stopOutput.Count -ne 1) {
            throw "DEMO_HOST_RELAY_FAILURE_STOP_FAILED"
        }
    }
    $afterStop = Get-DemoRelayContainer -ContainerId $ContainerId
    if ([string]$afterStop.State.Status -cne "exited" -or
        [bool]$afterStop.State.Running -or [bool]$afterStop.State.Restarting -or
        [int]$afterStop.State.ExitCode -ne 0 -or
        [string]$afterStop.HostConfig.RestartPolicy.Name -cne "no" -or
        [int]$afterStop.RestartCount -ne 0) {
        throw "DEMO_HOST_RELAY_FAILURE_RESTORE_INVALID"
    }
}

$demoStage = "bootstrap"
$allowedDemoStages = @(
    "bootstrap",
    "baseline",
    "image-binding",
    "prestate-boundary",
    "historical-logs",
    "prestart-check",
    "container-start",
    "ready-wait",
    "running-boundary",
    "listener-probe",
    "resolution-probe",
    "upstream-probe",
    "log-suffix",
    "success-output"
)
$startAttempted = $false
try {
$demoStage = "bootstrap"
if (-not (Test-Path -LiteralPath $docker -PathType Leaf)) {
    throw "DEMO_HOST_RELAY_DOCKER_EXECUTABLE_MISSING"
}
$relaySource = Assert-DemoRegularFile -Path $relaySource `
    -Reason "DEMO_HOST_RELAY_SOURCE_INVALID"
if ((Get-FileHash -LiteralPath $relaySource -Algorithm SHA256).Hash.ToLowerInvariant() -cne
    $relaySourceHash) {
    throw "DEMO_HOST_RELAY_SOURCE_HASH_MISMATCH"
}

$demoStage = "baseline"
$baseline = Read-DemoRelayBaseline -Path $BaselinePath
$demoStage = "image-binding"
$imageDocuments = @(& $docker image inspect $relayImageId 2>$null | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0 -or $imageDocuments.Count -ne 1 -or
    [string]$imageDocuments[0].Id -cne $relayImageId) {
    throw "DEMO_HOST_RELAY_IMAGE_BINDING_INVALID"
}
$imageEnvironment = @($imageDocuments[0].Config.Env | Where-Object { $null -ne $_ })
if (@($imageEnvironment | Where-Object {
    $_ -match '(?i)^(?:DASHSCOPE_API_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY|HICLAW_LLM_API_KEY|AZURE_OPENAI_API_KEY|GOOGLE_API_KEY|GEMINI_API_KEY|HTTP_PROXY|HTTPS_PROXY|ALL_PROXY|NO_PROXY)='
}).Count -ne 0) {
    throw "DEMO_HOST_RELAY_IMAGE_PROVIDER_ENVIRONMENT_PRESENT"
}

$demoStage = "prestate-boundary"
$relay = Get-DemoRelayContainer -ContainerId $baseline.ContainerId
Assert-DemoRelayBoundary -Container $relay -Baseline $baseline `
    -ExpectedState "exited" -ImageEnvironment $imageEnvironment
$demoStage = "historical-logs"
$baselineLogs = @(Get-DemoDockerLogLines -ContainerId $baseline.ContainerId)
$unexpectedBaselineLogs = @($baselineLogs | Where-Object {
    $_ -cne $readyLine -and $_ -cne $stopLine
})
if ($unexpectedBaselineLogs.Count -ne 0) {
    throw "DEMO_HOST_RELAY_HISTORICAL_LOG_NOT_ALLOWLISTED"
}
$baselineReadyCount = @($baselineLogs | Where-Object { $_ -ceq $readyLine }).Count
$baselineStopCount = @($baselineLogs | Where-Object { $_ -ceq $stopLine }).Count

if ($PrestartCheck) {
    $demoStage = "prestart-check"
    Write-Output "DEMO_HOST_RELAY_PRESTART_CHECK=PASS"
    Write-Output ("DEMO_HOST_RELAY_DEMO_RUN_ID=" + $baseline.DemoRunId)
    Write-Output "DEMO_HOST_RELAY_CONTAINER_STARTED=false"
    Write-Output "DEMO_HOST_RELAY_SECRET_VALUE_READ=false"
    Write-Output "DEMO_HOST_RELAY_PROVIDER_CALL_COUNT=0"
    return
}

$demoStage = "container-start"
    $startAttempted = $true
    $startOutput = @(& $docker start $baseline.ContainerId 2>$null)
    if ($LASTEXITCODE -ne 0 -or $startOutput.Count -ne 1) {
        throw "DEMO_HOST_RELAY_CONTAINER_START_FAILED"
    }

$demoStage = "ready-wait"
    $deadline = [DateTime]::UtcNow.AddSeconds($ReadyTimeoutSeconds)
    $currentLogs = @()
    do {
        Start-Sleep -Milliseconds 250
        $relay = Get-DemoRelayContainer -ContainerId $baseline.ContainerId
        $currentLogs = @(Get-DemoDockerLogLines -ContainerId $baseline.ContainerId)
        if ([string]$relay.State.Status -ceq "running" -and
            (Test-DemoReadySuffix -Before $baselineLogs -After $currentLogs)) {
            break
        }
    } while ([DateTime]::UtcNow -lt $deadline)
    if ([string]$relay.State.Status -cne "running" -or
        -not (Test-DemoReadySuffix -Before $baselineLogs -After $currentLogs)) {
        throw "DEMO_HOST_RELAY_NOT_READY"
    }

$demoStage = "running-boundary"
    Assert-DemoRelayBoundary -Container $relay -Baseline $baseline `
        -ExpectedState "running" -ImageEnvironment $imageEnvironment

$demoStage = "listener-probe"
    $listenerProbe = @'
import socket
expected = [("172.20.0.254", 18190), ("172.20.0.254", 18191)]
observed = []
with open("/proc/net/tcp", "r", encoding="ascii") as handle:
    next(handle)
    for line in handle:
        fields = line.split()
        address_hex, port_hex = fields[1].split(":")
        port = int(port_hex, 16)
        if fields[3] == "0A" and port in {18190, 18191}:
            address = socket.inet_ntoa(bytes.fromhex(address_hex)[::-1])
            observed.append((address, port))
if sorted(observed) != expected:
    raise SystemExit(78)
print("172.20.0.254:18190,172.20.0.254:18191")
'@
    $listenerResult = Invoke-DemoRelayPythonProbe `
        -ContainerId $baseline.ContainerId -Script $listenerProbe
    if ($listenerResult.ExitCode -ne 0 -or $listenerResult.StderrPresent -or
        $listenerResult.Output.Count -ne 1 -or
        [string]$listenerResult.Output[0] -cne
        "172.20.0.254:18190,172.20.0.254:18191") {
        throw "DEMO_HOST_RELAY_LISTENER_SCOPE_INVALID"
    }

$demoStage = "resolution-probe"
    $resolutionProbe = @'
import ipaddress
import socket
host_entries = []
with open("/etc/hosts", "r", encoding="utf-8") as handle:
    for line in handle:
        fields = line.split()
        if len(fields) >= 2 and "host.docker.internal" in fields[1:]:
            try:
                candidate = ipaddress.ip_address(fields[0])
            except ValueError:
                continue
            if candidate.version == 4:
                host_entries.append(str(candidate))
addresses = sorted({item[4][0] for item in socket.getaddrinfo(
    "host.docker.internal", None, socket.AF_INET
)})
if len(host_entries) != 1 or len(addresses) != 1 or addresses[0] != host_entries[0]:
    raise SystemExit(78)
address = ipaddress.ip_address(addresses[0])
if address in ipaddress.ip_network("172.20.0.0/16") or addresses[0] in {"0.0.0.0", "127.0.0.1"}:
    raise SystemExit(78)
print("resolved")
'@
    $resolutionResult = Invoke-DemoRelayPythonProbe `
        -ContainerId $baseline.ContainerId -Script $resolutionProbe
    if ($resolutionResult.ExitCode -ne 0 -or $resolutionResult.StderrPresent -or
        $resolutionResult.Output.Count -ne 1 -or
        [string]$resolutionResult.Output[0] -cne "resolved") {
        throw "DEMO_HOST_RELAY_UPSTREAM_RESOLUTION_INVALID"
    }

$demoStage = "upstream-probe"
    $upstreamProbe = @'
import http.client
checks = ((18190, "/v1/chat/completions"), (18191, "/mcp"))
statuses = []
for port, path in checks:
    connection = http.client.HTTPConnection("172.20.0.254", port, timeout=5)
    connection.request("POST", path, body=b"{}", headers={"Content-Type": "application/json"})
    response = connection.getresponse()
    statuses.append((port, response.status))
    response.read()
    connection.close()
if statuses != [(18190, 401), (18191, 401)]:
    raise SystemExit(78)
print("18190=401,18191=401")
'@
    $upstreamResult = Invoke-DemoRelayPythonProbe `
        -ContainerId $baseline.ContainerId -Script $upstreamProbe
    if ($upstreamResult.ExitCode -ne 0 -or $upstreamResult.StderrPresent -or
        $upstreamResult.Output.Count -ne 1 -or
        [string]$upstreamResult.Output[0] -cne "18190=401,18191=401") {
        throw "DEMO_HOST_RELAY_UPSTREAM_FAIL_CLOSED_INVALID"
    }

$demoStage = "log-suffix"
    $finalLogs = @(Get-DemoDockerLogLines -ContainerId $baseline.ContainerId)
    $finalReadyCount = @($finalLogs | Where-Object { $_ -ceq $readyLine }).Count
    $finalStopCount = @($finalLogs | Where-Object { $_ -ceq $stopLine }).Count
    $unexpectedFinalLogs = @($finalLogs | Where-Object {
        $_ -cne $readyLine -and $_ -cne $stopLine
    })
    if ($unexpectedFinalLogs.Count -ne 0 -or
        -not (Test-DemoReadySuffix -Before $baselineLogs -After $finalLogs) -or
        $finalReadyCount -ne ($baselineReadyCount + 1) -or
        $finalStopCount -ne $baselineStopCount -or
        [string]$finalLogs[$finalLogs.Count - 1] -cne $readyLine) {
        throw "DEMO_HOST_RELAY_LOG_SUFFIX_INVALID"
    }

$demoStage = "success-output"
    Write-Output "DEMO_HOST_RELAY_STATUS=passed"
    Write-Output ("DEMO_HOST_RELAY_DEMO_RUN_ID=" + $baseline.DemoRunId)
    Write-Output ("DEMO_HOST_RELAY_BASELINE_READY_COUNT=" + $baselineReadyCount)
    Write-Output ("DEMO_HOST_RELAY_BASELINE_STOP_COUNT=" + $baselineStopCount)
    Write-Output "DEMO_HOST_RELAY_SUFFIX_READY_COUNT=1"
    Write-Output "DEMO_HOST_RELAY_SUFFIX_STOP_COUNT=0"
    Write-Output "DEMO_HOST_RELAY_LAST_LOG=READY"
    Write-Output "DEMO_HOST_RELAY_NETWORK_COUNT=2"
    Write-Output "DEMO_HOST_RELAY_STATIC_IP=172.20.0.254"
    Write-Output "DEMO_HOST_RELAY_AGENT_FACING_LISTENERS=172.20.0.254:18190,172.20.0.254:18191"
    Write-Output "DEMO_HOST_RELAY_UPSTREAM_RESOLUTION=passed"
    Write-Output "DEMO_HOST_RELAY_UPSTREAM_FAIL_CLOSED=18190:401,18191:401"
    Write-Output "DEMO_HOST_RELAY_PUBLISHED_PORT_COUNT=0"
    Write-Output "DEMO_HOST_RELAY_INJECTED_ENV_COUNT=0"
    Write-Output "DEMO_HOST_RELAY_PROVIDER_ENV_COUNT=0"
    Write-Output "DEMO_HOST_RELAY_PROVIDER_MOUNT_COUNT=0"
    Write-Output "DEMO_HOST_RELAY_SECRET_VALUE_READ=false"
    Write-Output "DEMO_HOST_RELAY_PROVIDER_CALL_COUNT=0"
}
catch {
    $originalFailure = $_
    if ($startAttempted) {
        try {
            Stop-DemoRelayAfterFailure -ContainerId $baseline.ContainerId
        }
        catch {
            throw "DEMO_HOST_RELAY_FAILURE_RESTORE_FAILED"
        }
    }
    $fixedFailure = [regex]::Match(
        [string]$originalFailure.Exception.Message,
        '\bDEMO_HOST_RELAY_[A-Z0-9_]+'
    )
    if ($fixedFailure.Success) {
        throw [string]$fixedFailure.Value
    }
    if ($allowedDemoStages -cnotcontains $demoStage) {
        throw "DEMO_HOST_RELAY_STAGE_INVALID"
    }
    throw ("DEMO_HOST_RELAY_STAGE_FAILED:" + $demoStage)
}
