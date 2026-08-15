#requires -Version 5.1

[CmdletBinding()]
param(
    [ValidateRange(10, 120)]
    [int]$ReadyTimeoutSeconds = 30
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$workspace = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$docker = Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\resources\bin\docker.exe"
$relaySource = Join-Path $workspace "infra\agentteams\m4\runtime\m4-host-relay.py"
$relayTarget = "/opt/awakening/m4/m4-host-relay.py"
$relaySourceHash = "4bdbaf66910b21d530ccd00790052dc888dc69ad40543bac64773ad9b0d36a2e"
$relayName = "awakening-m4-host-relay"
$uplinkNetwork = "awakening-m4-host-uplink"
$m4Network = "awakening-m4-net"
$relayIp = "172.20.0.254"
$m4Subnet = "172.20.0.0/16"
$m4Gateway = "172.20.0.1"
$image = "higress-registry.cn-hangzhou.cr.aliyuncs.com/higress/hiclaw-embedded:v1.1.2@sha256:5486f4643a04a3a7a4dd81cd7f1d6091f9b7db3a5446bd5676f567c857910978"
$imageId = "sha256:5486f4643a04a3a7a4dd81cd7f1d6091f9b7db3a5446bd5676f567c857910978"
$readyLine = "M4_HOST_RELAY_READY=172.20.0.254:18190,172.20.0.254:18191"
$stopLine = "M4_HOST_RELAY_STOPPED=true"

function Test-M4ExactContainerExists {
    param([Parameter(Mandatory = $true)][string]$Name)

    $matches = @(& $docker container ls --all --filter ("name=^/" + $Name + "$") `
        --format "{{.Names}}" 2>$null)
    if ($LASTEXITCODE -ne 0 -or $matches.Count -gt 1) {
        throw "M4_HOST_RELAY_CONTAINER_LOOKUP_FAILED"
    }
    return $matches.Count -eq 1
}

function Test-M4ExactNetworkExists {
    param([Parameter(Mandatory = $true)][string]$Name)

    $matches = @(& $docker network ls --filter ("name=^" + $Name + "$") `
        --format "{{.Name}}" 2>$null)
    if ($LASTEXITCODE -ne 0 -or $matches.Count -gt 1) {
        throw ("M4_HOST_RELAY_NETWORK_LOOKUP_FAILED:" + $Name)
    }
    return $matches.Count -eq 1
}

function Get-M4Container {
    param([Parameter(Mandatory = $true)][string]$Name)

    $documents = @(& $docker inspect $Name 2>$null | ConvertFrom-Json)
    if ($LASTEXITCODE -ne 0 -or $documents.Count -ne 1) {
        throw "M4_HOST_RELAY_CONTAINER_INSPECT_FAILED"
    }
    return $documents[0]
}

function Get-M4Network {
    param([Parameter(Mandatory = $true)][string]$Name)

    $documents = @(& $docker network inspect $Name 2>$null | ConvertFrom-Json)
    if ($LASTEXITCODE -ne 0 -or $documents.Count -ne 1) {
        throw ("M4_HOST_RELAY_NETWORK_INSPECT_FAILED:" + $Name)
    }
    return $documents[0]
}

function Get-M4PublishedPorts {
    param([Parameter(Mandatory = $true)]$Container)

    if ($null -eq $Container.HostConfig.PortBindings) {
        return @()
    }
    return @($Container.HostConfig.PortBindings.PSObject.Properties | Where-Object {
        $null -ne $_.Value -and @($_.Value).Count -gt 0
    })
}

function Assert-M4StringArraysEqual {
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

function Get-M4DockerLogLines {
    param([Parameter(Mandatory = $true)][string]$Name)

    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $docker
    $startInfo.Arguments = "logs " + $Name
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    if (-not $process.Start()) {
        throw "M4_HOST_RELAY_LOG_CAPTURE_START_FAILED"
    }
    $standardOutput = $process.StandardOutput.ReadToEnd()
    $standardError = $process.StandardError.ReadToEnd()
    $process.WaitForExit()
    $exitCode = $process.ExitCode
    $process.Dispose()
    if ($exitCode -ne 0) {
        throw "M4_HOST_RELAY_LOG_CAPTURE_FAILED"
    }
    $combined = $standardOutput + "`n" + $standardError
    return @($combined -split "`r?`n" | Where-Object {
        -not [string]::IsNullOrWhiteSpace($_)
    })
}

function Get-M4Ipv4CidrRange {
    param([Parameter(Mandatory = $true)][string]$Cidr)

    $parts = $Cidr -split "/", 2
    $prefix = 0
    $address = $null
    if (
        $parts.Count -ne 2 -or
        -not [int]::TryParse($parts[1], [ref]$prefix) -or
        $prefix -lt 0 -or
        $prefix -gt 32 -or
        -not [System.Net.IPAddress]::TryParse($parts[0], [ref]$address) -or
        $address.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork
    ) {
        throw "M4_HOST_RELAY_IPV4_CIDR_INVALID"
    }
    $bytes = $address.GetAddressBytes()
    $value = [uint64]$bytes[0] * 16777216L +
        [uint64]$bytes[1] * 65536L +
        [uint64]$bytes[2] * 256L +
        [uint64]$bytes[3]
    $size = [uint64][Math]::Pow(2, 32 - $prefix)
    $start = [uint64]([Math]::Floor($value / $size) * $size)
    return [ordered]@{
        Start = $start
        End = $start + $size - 1L
    }
}

function Invoke-M4RelayPythonProbe {
    param([Parameter(Mandatory = $true)][string]$Script)

    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $docker
    $startInfo.Arguments = "exec -i " + $relayName + " /usr/bin/python3 -B -"
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardInput = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    if (-not $process.Start()) {
        throw "M4_HOST_RELAY_PROBE_PROCESS_START_FAILED"
    }
    $standardOutputTask = $process.StandardOutput.ReadToEndAsync()
    $standardErrorTask = $process.StandardError.ReadToEndAsync()
    $process.StandardInput.Write($Script)
    $process.StandardInput.Close()
    $process.WaitForExit()
    $standardOutput = $standardOutputTask.Result
    $standardError = $standardErrorTask.Result
    $exitCode = $process.ExitCode
    $process.Dispose()
    $output = @($standardOutput -split "`r?`n" | Where-Object {
        -not [string]::IsNullOrWhiteSpace($_)
    })
    return [ordered]@{
        ExitCode = $exitCode
        Output = $output
        StderrPresent = -not [string]::IsNullOrWhiteSpace($standardError)
    }
}

function Assert-M4RelayImmutableConfig {
    param([Parameter(Mandatory = $true)]$Container)

    $networks = @($Container.NetworkSettings.Networks.PSObject.Properties.Name | Sort-Object)
    $uplinkOnly = @($uplinkNetwork)
    $fullyConnected = @($m4Network, $uplinkNetwork) | Sort-Object
    $networkSetAllowed = $false
    if ($networks.Count -eq $uplinkOnly.Count -and
        [string]$networks[0] -ceq [string]$uplinkOnly[0]) {
        $networkSetAllowed = $true
    }
    elseif ($networks.Count -eq $fullyConnected.Count) {
        $networkSetAllowed = $true
        for ($index = 0; $index -lt $fullyConnected.Count; $index++) {
            if ([string]$networks[$index] -cne [string]$fullyConnected[$index]) {
                $networkSetAllowed = $false
            }
        }
    }

    $extraHosts = @($Container.HostConfig.ExtraHosts | Where-Object { $null -ne $_ })
    $dns = @($Container.HostConfig.Dns | Where-Object { $null -ne $_ })
    $capDrop = @($Container.HostConfig.CapDrop | Where-Object { $null -ne $_ })
    $capAdd = @($Container.HostConfig.CapAdd | Where-Object { $null -ne $_ })
    $security = @($Container.HostConfig.SecurityOpt | Where-Object { $null -ne $_ })
    $entrypoint = @($Container.Config.Entrypoint | Where-Object { $null -ne $_ })
    $command = @($Container.Config.Cmd | Where-Object { $null -ne $_ })
    $environment = @($Container.Config.Env | Where-Object { $null -ne $_ })
    $mounts = @($Container.Mounts)
    $published = @(Get-M4PublishedPorts -Container $Container)
    $tmpProperties = @()
    if ($null -ne $Container.HostConfig.Tmpfs) {
        $tmpProperties = @($Container.HostConfig.Tmpfs.PSObject.Properties)
    }
    $sysctlProperties = @()
    if ($null -ne $Container.HostConfig.Sysctls) {
        $sysctlProperties = @($Container.HostConfig.Sysctls.PSObject.Properties)
    }

    if (
        -not $networkSetAllowed -or
        $networks -notcontains $uplinkNetwork -or
        [string]$Container.Image -cne $imageId -or
        [string]$Container.Config.Image -cne $image -or
        [string]$Container.Config.User -cne "65534:65534" -or
        [string]$Container.Config.Hostname -cne $relayName -or
        [string]$Container.HostConfig.NetworkMode -cne $uplinkNetwork -or
        -not [bool]$Container.HostConfig.ReadonlyRootfs -or
        [bool]$Container.HostConfig.Privileged -or
        [bool]$Container.HostConfig.AutoRemove -or
        [int64]$Container.HostConfig.PidsLimit -ne 64 -or
        [string]$Container.HostConfig.RestartPolicy.Name -cne "no" -or
        $published.Count -ne 0 -or
        $extraHosts.Count -ne 1 -or
        [string]$extraHosts[0] -cne "host.docker.internal:host-gateway" -or
        $dns.Count -ne 1 -or
        [string]$dns[0] -cne "127.0.0.1" -or
        $sysctlProperties.Count -ne 1 -or
        [string]$sysctlProperties[0].Name -cne "net.ipv4.ip_forward" -or
        [string]$sysctlProperties[0].Value -cne "0" -or
        $capDrop.Count -ne 1 -or
        [string]$capDrop[0] -cne "ALL" -or
        $capAdd.Count -ne 0 -or
        ($security -notcontains "no-new-privileges:true" -and
            $security -notcontains "no-new-privileges") -or
        $entrypoint.Count -ne 1 -or
        [string]$entrypoint[0] -cne "/usr/bin/python3" -or
        $command.Count -ne 2 -or
        [string]$command[0] -cne "-B" -or
        [string]$command[1] -cne $relayTarget -or
        $tmpProperties.Count -ne 2 -or
        @($tmpProperties | Where-Object { $_.Name -ceq "/tmp" }).Count -ne 1 -or
        @($tmpProperties | Where-Object { $_.Name -ceq "/data" }).Count -ne 1 -or
        @($tmpProperties | Where-Object {
            [string]$_.Value -notmatch "noexec" -or
            [string]$_.Value -notmatch "nosuid" -or
            [string]$_.Value -notmatch "nodev"
        }).Count -ne 0 -or
        $mounts.Count -ne 1 -or
        [string]$mounts[0].Type -cne "bind" -or
        [bool]$mounts[0].RW -or
        [string]$mounts[0].Destination -cne $relayTarget -or
        -not ([System.IO.Path]::GetFullPath([string]$mounts[0].Source)).Equals(
            $relaySource,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "M4_HOST_RELAY_IMMUTABLE_CONFIG_INVALID"
    }
    Assert-M4StringArraysEqual -Actual $environment -Expected $imageEnvironment `
        -Reason "M4_HOST_RELAY_UNEXPECTED_ENVIRONMENT"

    if ($networks -contains $m4Network) {
        $m4Property = $Container.NetworkSettings.Networks.PSObject.Properties[$m4Network]
        $configuredAddress = ""
        if ($null -ne $m4Property) {
            if ([string]$Container.State.Status -ceq "running") {
                $configuredAddress = [string]$m4Property.Value.IPAddress
            }
            elseif ($null -ne $m4Property.Value.IPAMConfig) {
                $configuredAddress = [string]$m4Property.Value.IPAMConfig.IPv4Address
            }
        }
        if ($null -eq $m4Property -or $configuredAddress -cne $relayIp) {
            throw "M4_HOST_RELAY_STATIC_IP_MISMATCH"
        }
    }
}

if (-not (Test-Path -LiteralPath $docker -PathType Leaf)) {
    throw "M4_HOST_RELAY_DOCKER_EXECUTABLE_MISSING"
}
$relayItem = Get-Item -LiteralPath $relaySource -Force -ErrorAction Stop
if (
    $relayItem.PSIsContainer -or
    $relayItem.Length -le 0 -or
    ($relayItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
) {
    throw "M4_HOST_RELAY_SOURCE_INVALID"
}
$actualSourceHash = (Get-FileHash -LiteralPath $relaySource -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualSourceHash -cne $relaySourceHash) {
    throw "M4_HOST_RELAY_SOURCE_HASH_MISMATCH"
}

$imageDocuments = @(& $docker image inspect $image 2>$null | ConvertFrom-Json)
if (
    $LASTEXITCODE -ne 0 -or
    $imageDocuments.Count -ne 1 -or
    [string]$imageDocuments[0].Id -cne $imageId
) {
    throw "M4_HOST_RELAY_LOCAL_IMAGE_MISMATCH"
}
$imageEnvironment = @($imageDocuments[0].Config.Env | Where-Object { $null -ne $_ })
$imageEnvironmentKeys = @($imageEnvironment | ForEach-Object {
    ($_ -split "=", 2)[0]
})
$expectedImageEnvironmentKeys = @(
    "PATH",
    "DEBIAN_FRONTEND",
    "JAVA_HOME",
    "HICLAW_CONTROLLER_URL",
    "HICLAW_AUTH_TOKEN_FILE"
)
Assert-M4StringArraysEqual -Actual $imageEnvironmentKeys `
    -Expected $expectedImageEnvironmentKeys `
    -Reason "M4_HOST_RELAY_IMAGE_ENVIRONMENT_KEYS_INVALID"
if (@($imageEnvironment | Where-Object {
    $_ -match '(?i)^(?:DASHSCOPE_API_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY|HICLAW_LLM_API_KEY|HTTP_PROXY|HTTPS_PROXY|ALL_PROXY)='
}).Count -ne 0) {
    throw "M4_HOST_RELAY_IMAGE_SENSITIVE_ENVIRONMENT_PRESENT"
}

if (-not (Test-M4ExactNetworkExists -Name $m4Network)) {
    throw "M4_HOST_RELAY_M4_NETWORK_MISSING"
}
$m4Record = Get-M4Network -Name $m4Network
$m4Ipam = @($m4Record.IPAM.Config)
$m4Masquerade = [string]$m4Record.Options.'com.docker.network.bridge.enable_ip_masquerade'
if (
    $m4Record.Driver -cne "bridge" -or
    [bool]$m4Record.Internal -or
    [bool]$m4Record.EnableIPv6 -or
    $m4Ipam.Count -ne 1 -or
    [string]$m4Ipam[0].Subnet -cne $m4Subnet -or
    [string]$m4Ipam[0].Gateway -cne $m4Gateway -or
    $m4Masquerade -cne "false"
) {
    throw "M4_HOST_RELAY_M4_NETWORK_BOUNDARY_INVALID"
}
$m4Occupants = @($m4Record.Containers.PSObject.Properties | ForEach-Object { $_.Value })
$relayIpOccupants = @($m4Occupants | Where-Object {
    [string]$_.IPv4Address -eq ($relayIp + "/16")
})
if ($relayIpOccupants.Count -gt 1) {
    throw "M4_HOST_RELAY_STATIC_IP_NOT_UNIQUE"
}
if (
    $relayIpOccupants.Count -eq 1 -and
    [string]$relayIpOccupants[0].Name -cne $relayName
) {
    throw "M4_HOST_RELAY_STATIC_IP_OCCUPIED"
}

if (-not (Test-M4ExactNetworkExists -Name $uplinkNetwork)) {
    $networkId = @(& $docker network create `
        --driver bridge `
        --label "awakening.module=m4" `
        --label "awakening.purpose=host-uplink" `
        --opt "com.docker.network.bridge.enable_ip_masquerade=true" `
        $uplinkNetwork 2>$null)
    if ($LASTEXITCODE -ne 0 -or $networkId.Count -ne 1 -or $networkId[0].Length -lt 12) {
        throw "M4_HOST_RELAY_UPLINK_CREATE_FAILED"
    }
    Write-Output "M4_HOST_RELAY_UPLINK_CREATED=true"
}

$uplinkRecord = Get-M4Network -Name $uplinkNetwork
$uplinkMasquerade = [string]$uplinkRecord.Options.'com.docker.network.bridge.enable_ip_masquerade'
$uplinkIpam = @($uplinkRecord.IPAM.Config)
if (
    $uplinkRecord.Driver -cne "bridge" -or
    [bool]$uplinkRecord.Internal -or
    [bool]$uplinkRecord.EnableIPv6 -or
    [bool]$uplinkRecord.Attachable -or
    [bool]$uplinkRecord.Ingress -or
    $uplinkIpam.Count -ne 1 -or
    [string]::IsNullOrWhiteSpace([string]$uplinkIpam[0].Subnet) -or
    [string]::IsNullOrWhiteSpace([string]$uplinkIpam[0].Gateway) -or
    $uplinkMasquerade -cne "true" -or
    [string]$uplinkRecord.Labels.'awakening.module' -cne "m4" -or
    [string]$uplinkRecord.Labels.'awakening.purpose' -cne "host-uplink"
) {
    throw "M4_HOST_RELAY_UPLINK_BOUNDARY_INVALID"
}
$m4Range = Get-M4Ipv4CidrRange -Cidr $m4Subnet
$uplinkRange = Get-M4Ipv4CidrRange -Cidr ([string]$uplinkIpam[0].Subnet)
if ($m4Range.Start -le $uplinkRange.End -and $uplinkRange.Start -le $m4Range.End) {
    throw "M4_HOST_RELAY_UPLINK_SUBNET_OVERLAP"
}
$uplinkNames = @($uplinkRecord.Containers.PSObject.Properties | ForEach-Object {
    [string]$_.Value.Name
})
if (@($uplinkNames | Where-Object { $_ -cne $relayName }).Count -ne 0) {
    throw "M4_HOST_RELAY_UPLINK_NOT_DEDICATED"
}

if (-not (Test-M4ExactContainerExists -Name $relayName)) {
    $containerId = @(& $docker create `
        --name $relayName `
        --hostname $relayName `
        --network $uplinkNetwork `
        --add-host "host.docker.internal:host-gateway" `
        --dns "127.0.0.1" `
        --sysctl "net.ipv4.ip_forward=0" `
        --user "65534:65534" `
        --read-only `
        --tmpfs "/tmp:rw,noexec,nosuid,nodev,size=8m" `
        --tmpfs "/data:rw,noexec,nosuid,nodev,size=1m" `
        --security-opt "no-new-privileges:true" `
        --cap-drop "ALL" `
        --pids-limit "64" `
        --restart "no" `
        --label "awakening.module=m4" `
        --label "awakening.purpose=host-relay" `
        --mount ("type=bind,src=" + $relaySource + ",dst=" + $relayTarget + ",readonly") `
        --entrypoint "/usr/bin/python3" `
        $image `
        "-B" `
        $relayTarget 2>$null)
    if ($LASTEXITCODE -ne 0 -or $containerId.Count -ne 1 -or $containerId[0].Length -lt 12) {
        throw "M4_HOST_RELAY_CONTAINER_CREATE_FAILED"
    }
    Write-Output "M4_HOST_RELAY_CONTAINER_CREATED=true"
}

$relay = Get-M4Container -Name $relayName
Assert-M4RelayImmutableConfig -Container $relay
$relayNetworks = @($relay.NetworkSettings.Networks.PSObject.Properties.Name)
if ($relayNetworks -notcontains $m4Network) {
    if ($relay.State.Status -cne "created") {
        throw "M4_HOST_RELAY_M4_NETWORK_CONNECT_STATE_INVALID"
    }
    $connectOutput = @(& $docker network connect --ip $relayIp $m4Network $relayName 2>$null)
    if ($LASTEXITCODE -ne 0 -or $connectOutput.Count -ne 0) {
        throw "M4_HOST_RELAY_M4_NETWORK_CONNECT_FAILED"
    }
    Write-Output "M4_HOST_RELAY_M4_NETWORK_CONNECTED=true"
    $relay = Get-M4Container -Name $relayName
}

$relayIdBeforeStart = [string]$relay.Id
$startedThisInvocation = $false
try {
if ($relay.State.Status -ceq "created" -or
    ($relay.State.Status -ceq "exited" -and [int]$relay.State.ExitCode -eq 0)) {
    $startedThisInvocation = $true
    $startOutput = @(& $docker start $relayName 2>$null)
    if ($LASTEXITCODE -ne 0 -or $startOutput.Count -ne 1 -or $startOutput[0] -cne $relayName) {
        throw "M4_HOST_RELAY_CONTAINER_START_FAILED"
    }
    Write-Output "M4_HOST_RELAY_CONTAINER_STARTED=true"
}
elseif ($relay.State.Status -cne "running") {
    throw ("M4_HOST_RELAY_CONTAINER_STATE_INVALID:" + [string]$relay.State.Status)
}

$deadline = [DateTime]::UtcNow.AddSeconds($ReadyTimeoutSeconds)
do {
    Start-Sleep -Milliseconds 250
    $relay = Get-M4Container -Name $relayName
    $logLines = @(Get-M4DockerLogLines -Name $relayName)
    if ($relay.State.Status -ceq "running" -and $logLines.Count -gt 0 -and
        $logLines[$logLines.Count - 1] -ceq $readyLine) {
        break
    }
} while ([DateTime]::UtcNow -lt $deadline)
if ($relay.State.Status -cne "running" -or $logLines.Count -eq 0 -or
    $logLines[$logLines.Count - 1] -cne $readyLine) {
    throw "M4_HOST_RELAY_NOT_READY"
}

$relay = Get-M4Container -Name $relayName
$relayNetworks = @($relay.NetworkSettings.Networks.PSObject.Properties.Name | Sort-Object)
$expectedNetworks = @($m4Network, $uplinkNetwork) | Sort-Object
Assert-M4StringArraysEqual -Actual $relayNetworks -Expected $expectedNetworks `
    -Reason "M4_HOST_RELAY_NETWORK_SET_INVALID"
$relayM4Endpoint = $relay.NetworkSettings.Networks.PSObject.Properties[$m4Network].Value
if ([string]$relayM4Endpoint.IPAddress -cne $relayIp) {
    throw "M4_HOST_RELAY_STATIC_IP_MISMATCH"
}
Assert-M4RelayImmutableConfig -Container $relay

$uplinkRecord = Get-M4Network -Name $uplinkNetwork
$uplinkNames = @($uplinkRecord.Containers.PSObject.Properties | ForEach-Object {
    [string]$_.Value.Name
})
if ($uplinkNames.Count -ne 1 -or $uplinkNames[0] -cne $relayName) {
    throw "M4_HOST_RELAY_UPLINK_NOT_DEDICATED"
}

$listenerProbe = @'
import socket
expected = {18190, 18191}
observed = []
with open("/proc/net/tcp", "r", encoding="ascii") as handle:
    next(handle)
    for line in handle:
        fields = line.split()
        local, state = fields[1], fields[3]
        address_hex, port_hex = local.split(":")
        port = int(port_hex, 16)
        if state == "0A" and port in expected:
            address = socket.inet_ntoa(bytes.fromhex(address_hex)[::-1])
            observed.append((address, port))
if sorted(observed) != [("172.20.0.254", 18190), ("172.20.0.254", 18191)]:
    raise SystemExit(78)
print("172.20.0.254:18190,172.20.0.254:18191")
'@
$listenerProbeResult = Invoke-M4RelayPythonProbe -Script $listenerProbe
$listenerResult = @($listenerProbeResult.Output)
if ($listenerProbeResult.ExitCode -ne 0 -or $listenerProbeResult.StderrPresent -or
    $listenerResult.Count -ne 1 -or
    $listenerResult[0] -cne "172.20.0.254:18190,172.20.0.254:18191") {
    throw "M4_HOST_RELAY_LISTENER_SCOPE_INVALID"
}

$pythonVersionProbe = @'
import sys
print("%d.%d.%d" % sys.version_info[:3])
'@
$pythonVersionProbeResult = Invoke-M4RelayPythonProbe -Script $pythonVersionProbe
$pythonVersion = @($pythonVersionProbeResult.Output)
if ($pythonVersionProbeResult.ExitCode -ne 0 -or
    $pythonVersionProbeResult.StderrPresent -or
    $pythonVersion.Count -ne 1 -or
    $pythonVersion[0] -notmatch '^3\.[0-9]+\.[0-9]+$') {
    throw "M4_HOST_RELAY_PINNED_IMAGE_PYTHON_INVALID"
}

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
addresses = sorted({item[4][0] for item in socket.getaddrinfo("host.docker.internal", None, socket.AF_INET)})
if len(host_entries) != 1 or len(addresses) != 1 or addresses[0] != host_entries[0]:
    raise SystemExit(78)
if ipaddress.ip_address(addresses[0]) in ipaddress.ip_network("172.20.0.0/16"):
    raise SystemExit(78)
if addresses[0] in {"0.0.0.0", "127.0.0.1"}:
    raise SystemExit(78)
print(addresses[0])
'@
$resolutionProbeResult = Invoke-M4RelayPythonProbe -Script $resolutionProbe
$upstreamAddress = @($resolutionProbeResult.Output)
if ($resolutionProbeResult.ExitCode -ne 0 -or $resolutionProbeResult.StderrPresent -or
    $upstreamAddress.Count -ne 1) {
    throw "M4_HOST_RELAY_UPSTREAM_RESOLUTION_INVALID"
}

$httpProbe = @'
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
$httpProbeResult = Invoke-M4RelayPythonProbe -Script $httpProbe
$probeResult = @($httpProbeResult.Output)
if ($httpProbeResult.ExitCode -ne 0 -or $httpProbeResult.StderrPresent -or
    $probeResult.Count -ne 1 -or
    $probeResult[0] -cne "18190=401,18191=401") {
    throw "M4_HOST_RELAY_FAIL_CLOSED_PROBE_FAILED"
}

$logLines = @(Get-M4DockerLogLines -Name $relayName)
$readyCount = @($logLines | Where-Object { $_ -ceq $readyLine }).Count
$stopCount = @($logLines | Where-Object { $_ -ceq $stopLine }).Count
$unexpectedLogs = @($logLines | Where-Object {
    $_ -cne $readyLine -and $_ -cne $stopLine
})
if (
    $unexpectedLogs.Count -ne 0 -or
    $readyCount -ne ($stopCount + 1) -or
    $logLines[$logLines.Count - 1] -cne $readyLine
) {
    throw "M4_HOST_RELAY_LOG_POLICY_INVALID"
}

Write-Output "M4_HOST_RELAY_STATUS=passed"
Write-Output ("M4_HOST_RELAY_IMAGE_ID=" + $imageId)
Write-Output ("M4_HOST_RELAY_SOURCE_SHA256=" + $relaySourceHash)
Write-Output ("M4_HOST_RELAY_PYTHON_VERSION=" + $pythonVersion[0])
Write-Output ("M4_HOST_RELAY_AGENT_FACING_LISTENERS=" + $listenerResult[0])
Write-Output "M4_HOST_RELAY_UPSTREAM_ALLOWLIST=host.docker.internal:18190,host.docker.internal:18191"
Write-Output ("M4_HOST_RELAY_UPSTREAM_ADDRESS_COUNT=" + $upstreamAddress.Count)
Write-Output "M4_HOST_RELAY_NETWORK_COUNT=2"
Write-Output "M4_HOST_RELAY_UPLINK_CONTAINER_COUNT=1"
Write-Output "M4_HOST_RELAY_PUBLISHED_PORT_COUNT=0"
Write-Output "M4_HOST_RELAY_INJECTED_ENV_COUNT=0"
Write-Output "M4_HOST_RELAY_SECRET_VALUE_ENV_COUNT=0"
Write-Output "M4_HOST_RELAY_SECRET_MOUNT_COUNT=0"
Write-Output "M4_HOST_RELAY_DATA_VOLUME=false"
Write-Output "M4_HOST_RELAY_REQUEST_LOGGING=false"
Write-Output "M4_HOST_RELAY_IDLE_TIMEOUT_SECONDS=120"
Write-Output "M4_HOST_RELAY_IPV4_FORWARDING=false"
Write-Output "M4_HOST_RELAY_DNS_MODE=hosts_only"
Write-Output "M4_HOST_RELAY_FAIL_CLOSED_STATUS=18190:401,18191:401"
Write-Output "M4_HOST_RELAY_PROVIDER_CALL_COUNT=0"
}
catch {
    $originalFailure = $_
    if ($startedThisInvocation) {
        $current = Get-M4Container -Name $relayName
        if ([string]$current.Id -cne $relayIdBeforeStart) {
            throw "M4_HOST_RELAY_FAILURE_STOP_ID_MISMATCH"
        }
        if ([bool]$current.State.Running) {
            $stopOutput = @(& $docker stop --time 10 $relayName 2>$null)
            if ($LASTEXITCODE -ne 0 -or $stopOutput.Count -ne 1 -or
                $stopOutput[0] -cne $relayName) {
                $afterStop = Get-M4Container -Name $relayName
                if ([string]$afterStop.Id -cne $relayIdBeforeStart -or
                    [bool]$afterStop.State.Running) {
                    throw "M4_HOST_RELAY_FAILURE_STOP_FAILED"
                }
            }
        }
    }
    throw $originalFailure
}
