[CmdletBinding()]
param(
    [int]$ReadyTimeoutSeconds = 720
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$workspace = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$docker = Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\resources\bin\docker.exe"
$secretRoot = Join-Path $workspace "tmp\m4\m4-runtime-secrets-v1"
$managerWrapper = Join-Path $workspace "infra\agentteams\m4\runtime\manager-entrypoint.sh"
$workerWrapper = Join-Path $workspace "infra\agentteams\m4\runtime\worker-entrypoint.sh"
$matrixDispatchHelper = Join-Path $workspace "infra\agentteams\m4\runtime\m4-matrix-dispatch.sh"
$workerModelConfigScript = Join-Path $PSScriptRoot "Apply-M4WorkerModelConfig.ps1"
$managerControlModelScript = Join-Path $PSScriptRoot "Set-M4ManagerControlPlaneModel.ps1"
$workerControlModelScript = Join-Path $PSScriptRoot "Set-M4WorkerControlPlaneModel.ps1"
$liveConfigPath = Join-Path $workspace "tmp\m4\provider\live-gateway-config.json"
$matrixDispatchTarget = "/root/manager-workspace/config/m4-matrix-dispatch.sh"
$relayName = "awakening-m4-host-relay"
$relayIp = "172.20.0.254"
$relayHostEntry = "host.docker.internal:" + $relayIp
$relayImageId = "sha256:5486f4643a04a3a7a4dd81cd7f1d6091f9b7db3a5446bd5676f567c857910978"
$managerImage = "higress-registry.cn-hangzhou.cr.aliyuncs.com/higress/hiclaw-manager@sha256:3a77482fb11472ab05f85ba5d60cbc0df8d66046aa9f63b9cf99f16d87852921"
$workerImage = "higress-registry.cn-hangzhou.cr.aliyuncs.com/higress/hiclaw-worker@sha256:d1078b42115ec2ea4eeaac507bc63352812291ff6e2406e813863161f074fb0b"
$expectedManagerId = "sha256:3a77482fb11472ab05f85ba5d60cbc0df8d66046aa9f63b9cf99f16d87852921"
$expectedWorkerId = "sha256:d1078b42115ec2ea4eeaac507bc63352812291ff6e2406e813863161f074fb0b"

if (-not (Test-Path -LiteralPath $docker -PathType Leaf)) {
    throw "M4_DOCKER_EXECUTABLE_MISSING"
}
foreach ($path in @($managerWrapper, $workerWrapper, $workerModelConfigScript, $managerControlModelScript, $workerControlModelScript, $liveConfigPath)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "M4_AGENT_ENTRYPOINT_WRAPPER_MISSING"
    }
}
$liveConfig = Get-Content -Raw -Encoding UTF8 -LiteralPath $liveConfigPath | ConvertFrom-Json
if (
    $liveConfig.authorization_id -cne "AUTH-M4-001" -or
    $liveConfig.schema_version -ne 1 -or
    [string]$liveConfig.provider.model_id -cne "qwen3.7-flash-2026-07-15" -or
    $liveConfig.provider.public_model_alias -cne $liveConfig.provider.model_id
) {
    throw "M4_AGENT_WORKER_MODEL_BINDING_INVALID"
}
$approvedWorkerModel = [string]$liveConfig.provider.model_id
$matrixDispatchItem = Get-Item -LiteralPath $matrixDispatchHelper -Force -ErrorAction Stop
if (
    $matrixDispatchItem.PSIsContainer -or
    $matrixDispatchItem.Length -le 0 -or
    ($matrixDispatchItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
) {
    throw "M4_MATRIX_HELPER_SOURCE_INVALID"
}

$agents = @(
    [ordered]@{
        Name = "awakening-m4-manager"
        Kind = "manager"
        Image = $managerImage
        ImageId = $expectedManagerId
        Env = (Join-Path $secretRoot "manager.env")
        Token = (Join-Path $secretRoot "manager.sa-token")
        Wrapper = $managerWrapper
        WorkspaceVolume = "awakening-m4-manager-workspace:/root/manager-workspace"
        ExtraVolume = "awakening-m4-manager-fs:/root/hiclaw-fs"
        McporterSource = (Join-Path $secretRoot "state-mcporter-manager")
        McporterDestination = "/root/manager-workspace/config"
        McporterSourceType = "Container"
        McporterWorkspacePath = "/root/manager-workspace/config/mcporter.json"
        ServiceAccountSubject = "system:serviceaccount:default:awakening-m4-manager"
    },
    [ordered]@{
        Name = "awakening-m4-worker-role-project-architect"
        Kind = "worker"
        Image = $workerImage
        ImageId = $expectedWorkerId
        Env = (Join-Path $secretRoot "ROLE_PROJECT_ARCHITECT.env")
        Token = (Join-Path $secretRoot "ROLE_PROJECT_ARCHITECT.sa-token")
        Wrapper = $workerWrapper
        WorkspaceVolume = "awakening-m4-worker-role-project-architect-fs:/root/hiclaw-fs"
        ExtraVolume = $null
        McporterSource = (Join-Path $secretRoot "state-mcporter-architect\mcporter.json")
        McporterDestination = "/root/hiclaw-fs/agents/role_project_architect/config/mcporter.json"
        McporterSourceType = "Leaf"
        McporterWorkspacePath = "/root/hiclaw-fs/agents/role_project_architect/config/mcporter.json"
        ServiceAccountSubject = "system:serviceaccount:default:awakening-m4-worker-role-project-architect"
    },
    [ordered]@{
        Name = "awakening-m4-worker-execution-evidence-coach"
        Kind = "worker"
        Image = $workerImage
        ImageId = $expectedWorkerId
        Env = (Join-Path $secretRoot "EXECUTION_EVIDENCE_COACH.env")
        Token = (Join-Path $secretRoot "EXECUTION_EVIDENCE_COACH.sa-token")
        Wrapper = $workerWrapper
        WorkspaceVolume = "awakening-m4-worker-execution-evidence-coach-fs:/root/hiclaw-fs"
        ExtraVolume = $null
        McporterSource = $null
        McporterDestination = $null
        McporterSourceType = $null
        McporterWorkspacePath = $null
        ServiceAccountSubject = "system:serviceaccount:default:awakening-m4-worker-execution-evidence-coach"
    },
    [ordered]@{
        Name = "awakening-m4-worker-independent-quality-reviewer"
        Kind = "worker"
        Image = $workerImage
        ImageId = $expectedWorkerId
        Env = (Join-Path $secretRoot "INDEPENDENT_QUALITY_REVIEWER.env")
        Token = (Join-Path $secretRoot "INDEPENDENT_QUALITY_REVIEWER.sa-token")
        Wrapper = $workerWrapper
        WorkspaceVolume = "awakening-m4-worker-independent-quality-reviewer-fs:/root/hiclaw-fs"
        ExtraVolume = $null
        McporterSource = $null
        McporterDestination = $null
        McporterSourceType = $null
        McporterWorkspacePath = $null
        ServiceAccountSubject = "system:serviceaccount:default:awakening-m4-worker-independent-quality-reviewer"
    }
)
$excludedMcporterTargets = @(
    [ordered]@{
        Name = "awakening-m4-worker-execution-evidence-coach"
        Role = "coach"
        Path = "/root/hiclaw-fs/agents/execution_evidence_coach/config/mcporter.json"
    },
    [ordered]@{
        Name = "awakening-m4-worker-independent-quality-reviewer"
        Role = "reviewer"
        Path = "/root/hiclaw-fs/agents/independent_quality_reviewer/config/mcporter.json"
    }
)
$replacementRequirements = @()

$controllerState = @(& $docker inspect --format "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}" awakening-m4-controller 2>$null)
if ($LASTEXITCODE -ne 0 -or $controllerState.Count -ne 1 -or $controllerState[0] -cne "running|healthy") {
    throw "M4_CONTROLLER_NOT_READY_FOR_AGENTS"
}

$relayDetails = @(& $docker inspect $relayName 2>$null | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0 -or $relayDetails.Count -ne 1) {
    throw "M4_HOST_RELAY_NOT_READY_FOR_AGENTS"
}
$relayRecord = $relayDetails[0]
$relayNetworks = @($relayRecord.NetworkSettings.Networks.PSObject.Properties.Name)
$relayM4Property = $relayRecord.NetworkSettings.Networks.PSObject.Properties[
    "awakening-m4-net"
]
if (
    [string]$relayRecord.Image -cne $relayImageId -or
    [string]$relayRecord.State.Status -cne "running" -or
    $relayNetworks.Count -ne 2 -or
    $relayNetworks -notcontains "awakening-m4-net" -or
    $relayNetworks -notcontains "awakening-m4-host-uplink" -or
    $null -eq $relayM4Property -or
    [string]$relayM4Property.Value.IPAddress -cne $relayIp -or
    @($relayRecord.HostConfig.PortBindings.PSObject.Properties | Where-Object {
        $null -ne $_.Value -and @($_.Value).Count -gt 0
    }).Count -ne 0 -or
    $null -ne ($relayRecord.Mounts | Where-Object {
        $_.Destination -ceq "/var/run/docker.sock"
    })
) {
    throw "M4_HOST_RELAY_BOUNDARY_INVALID_FOR_AGENTS"
}

try {
    $gatewayRequest = [System.Net.HttpWebRequest]::Create("http://127.0.0.1:18190/v1/chat/completions")
    $gatewayRequest.Method = "POST"
    $gatewayRequest.ContentType = "application/json"
    $gatewayRequest.ContentLength = 2
    $gatewayStream = $gatewayRequest.GetRequestStream()
    $gatewayPayload = [System.Text.Encoding]::UTF8.GetBytes("{}")
    $gatewayStream.Write($gatewayPayload, 0, $gatewayPayload.Length)
    $gatewayStream.Dispose()
    $gatewayResponse = $gatewayRequest.GetResponse()
    $gatewayLocalStatus = [int]$gatewayResponse.StatusCode
    $gatewayResponse.Dispose()
}
catch [System.Net.WebException] {
    if ($null -eq $_.Exception.Response) {
        throw "M4_FAIL_CLOSED_GATEWAY_NOT_REACHABLE_ON_HOST"
    }
    $gatewayLocalStatus = [int]$_.Exception.Response.StatusCode
    $_.Exception.Response.Dispose()
}
if ($gatewayLocalStatus -ne 401) {
    throw ("M4_FAIL_CLOSED_GATEWAY_HOST_STATUS_INVALID:" + $gatewayLocalStatus)
}

foreach ($agent in $agents) {
    foreach ($secretPath in @($agent.Env, $agent.Token)) {
        $item = Get-Item -LiteralPath $secretPath -Force -ErrorAction Stop
        if ($item.Length -le 0 -or ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
            throw ("M4_AGENT_SECRET_FILE_INVALID:" + $agent.Name)
        }
    }
    if ($null -ne $agent.McporterSource) {
        if (-not (Test-Path -LiteralPath $agent.McporterSource -PathType $agent.McporterSourceType)) {
            throw ("M4_AGENT_STATE_MCPORTER_SOURCE_MISSING:" + $agent.Name)
        }
        $mcporterItem = Get-Item -LiteralPath $agent.McporterSource -Force
        if (($mcporterItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw ("M4_AGENT_STATE_MCPORTER_SOURCE_REPARSE_POINT:" + $agent.Name)
        }
        if ($agent.McporterSourceType -ceq "Leaf" -and $mcporterItem.Length -le 0) {
            throw ("M4_AGENT_STATE_MCPORTER_SOURCE_EMPTY:" + $agent.Name)
        }
        if ($agent.McporterSourceType -ceq "Container") {
            $mcporterChildren = @(Get-ChildItem -LiteralPath $agent.McporterSource -Force)
            if (
                $mcporterChildren.Count -ne 1 -or
                $mcporterChildren[0].Name -cne "mcporter.json" -or
                $mcporterChildren[0].PSIsContainer -or
                $mcporterChildren[0].Length -le 0 -or
                ($mcporterChildren[0].Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
            ) {
                throw ("M4_AGENT_STATE_MCPORTER_DIRECTORY_CONTENT_INVALID:" + $agent.Name)
            }
        }
    }
    $existing = @(& $docker container ls --all --filter ("name=^/" + $agent.Name + "$") --format "{{.Names}}")
    if ($LASTEXITCODE -ne 0) {
        throw ("M4_AGENT_CONTAINER_EXISTENCE_CHECK_FAILED:" + $agent.Name)
    }
    if ($existing.Count -gt 1) {
        throw ("M4_AGENT_CONTAINER_NAME_NOT_UNIQUE:" + $agent.Name)
    }
    $agent["Exists"] = $existing.Count -eq 1
    if ($agent.Exists) {
        $existingDetails = @(& $docker inspect $agent.Name | ConvertFrom-Json)
        if ($LASTEXITCODE -ne 0 -or $existingDetails.Count -ne 1) {
            throw ("M4_AGENT_EXISTING_CONTAINER_INSPECT_FAILED:" + $agent.Name)
        }
        $existingContainer = $existingDetails[0]
        if (
            $existingContainer.Image -cne $agent.ImageId -or
            $existingContainer.HostConfig.NetworkMode -cne "awakening-m4-net" -or
            $existingContainer.HostConfig.Privileged -or
            $existingContainer.Config.Entrypoint[0] -cne "/opt/awakening/m4/entrypoint.sh" -or
            $null -ne ($existingContainer.Mounts | Where-Object { $_.Destination -ceq "/var/run/docker.sock" })
        ) {
            throw ("M4_AGENT_EXISTING_CONTAINER_BOUNDARY_MISMATCH:" + $agent.Name)
        }
        if (
            $existingContainer.State.Status -notin @("running", "exited") -or
            ($existingContainer.State.Status -ceq "exited" -and [int]$existingContainer.State.ExitCode -ne 0)
        ) {
            throw ("M4_AGENT_EXISTING_CONTAINER_STATE_INVALID:" + $agent.Name)
        }
        $agent["ExistingState"] = [string]$existingContainer.State.Status
        $existingPublished = @($existingContainer.HostConfig.PortBindings.PSObject.Properties | Where-Object {
            $null -ne $_.Value -and @($_.Value).Count -gt 0
        })
        $existingNetworks = @($existingContainer.NetworkSettings.Networks.PSObject.Properties.Name)
        $existingCapDrop = @($existingContainer.HostConfig.CapDrop)
        $existingCapAdd = @($existingContainer.HostConfig.CapAdd | Where-Object {
            $null -ne $_
        })
        $existingSecurity = @($existingContainer.HostConfig.SecurityOpt)
        if (
            $existingPublished.Count -ne 0 -or
            $existingNetworks.Count -ne 1 -or
            $existingNetworks[0] -cne "awakening-m4-net" -or
            $existingCapDrop.Count -ne 1 -or
            $existingCapDrop[0] -cne "ALL" -or
            $existingCapAdd.Count -ne 0 -or
            ($existingSecurity -notcontains "no-new-privileges:true" -and $existingSecurity -notcontains "no-new-privileges") -or
            [int64]$existingContainer.HostConfig.PidsLimit -ne 512 -or
            $existingContainer.HostConfig.RestartPolicy.Name -cne "no"
        ) {
            throw ("M4_AGENT_EXISTING_CONTAINER_HARDENING_MISMATCH:" + $agent.Name)
        }

        $existingExtraHosts = @($existingContainer.HostConfig.ExtraHosts | Where-Object {
            $null -ne $_
        })
        if (
            $existingExtraHosts.Count -ne 1 -or
            [string]$existingExtraHosts[0] -cne $relayHostEntry
        ) {
            $replacementRequirements += [ordered]@{
                Name = $agent.Name
                Reason = "HOST_RELAY_MAPPING_MISMATCH"
                Destination = $relayHostEntry
            }
        }

        $expectedBindMounts = [ordered]@{
            "/opt/awakening/m4/entrypoint.sh" = $agent.Wrapper
            "/run/secrets/awakening-m4/runtime.env" = $agent.Env
            "/run/secrets/awakening-m4/sa-token" = $agent.Token
        }
        if ($null -ne $agent.McporterSource) {
            $existingMcporterMount = @($existingContainer.Mounts | Where-Object {
                $_.Destination -ceq $agent.McporterDestination
            })
            if ($existingMcporterMount.Count -eq 0) {
                if ($null -eq $agent.McporterWorkspacePath) {
                    $replacementRequirements += [ordered]@{
                        Name = $agent.Name
                        Reason = "STATE_MCPORTER_BIND_MISSING"
                        Destination = $agent.McporterDestination
                    }
                }
            }
            else {
                if ($null -ne $agent.McporterWorkspacePath) {
                    throw ("M4_AGENT_STATE_MCPORTER_UNEXPECTED_BIND:" + $agent.Name)
                }
                $expectedBindMounts[$agent.McporterDestination] = $agent.McporterSource
            }
        }
        $volumeParts = $agent.WorkspaceVolume -split ":", 2
        $expectedVolumeMounts = [ordered]@{ $volumeParts[1] = $volumeParts[0] }
        if ($null -ne $agent.ExtraVolume) {
            $extraParts = $agent.ExtraVolume -split ":", 2
            $expectedVolumeMounts[$extraParts[1]] = $extraParts[0]
        }
        if ($existingContainer.Mounts.Count -ne ($expectedBindMounts.Count + $expectedVolumeMounts.Count)) {
            throw ("M4_AGENT_EXISTING_CONTAINER_MOUNT_COUNT_MISMATCH:" + $agent.Name)
        }
        foreach ($destination in $expectedBindMounts.Keys) {
            $mount = @($existingContainer.Mounts | Where-Object { $_.Destination -ceq $destination })
            if (
                $mount.Count -ne 1 -or
                $mount[0].Type -cne "bind" -or
                $mount[0].RW -or
                -not ([System.IO.Path]::GetFullPath([string]$mount[0].Source)).Equals(
                    [System.IO.Path]::GetFullPath([string]$expectedBindMounts[$destination]),
                    [System.StringComparison]::OrdinalIgnoreCase
                )
            ) {
                throw ("M4_AGENT_EXISTING_CONTAINER_BIND_MOUNT_MISMATCH:" + $agent.Name + ":" + $destination)
            }
        }
        foreach ($destination in $expectedVolumeMounts.Keys) {
            $mount = @($existingContainer.Mounts | Where-Object { $_.Destination -ceq $destination })
            if (
                $mount.Count -ne 1 -or
                $mount[0].Type -cne "volume" -or
                -not $mount[0].RW -or
                $mount[0].Name -cne $expectedVolumeMounts[$destination]
            ) {
                throw ("M4_AGENT_EXISTING_CONTAINER_VOLUME_MOUNT_MISMATCH:" + $agent.Name + ":" + $destination)
            }
        }
    }
    $actualImageId = @(& $docker image inspect --format "{{.Id}}" $agent.Image 2>$null)
    if ($LASTEXITCODE -ne 0 -or $actualImageId.Count -ne 1 -or $actualImageId[0] -cne $agent.ImageId) {
        throw ("M4_AGENT_IMAGE_ID_MISMATCH:" + $agent.Name)
    }
}

if ($replacementRequirements.Count -gt 0) {
    foreach ($requirement in $replacementRequirements) {
        Write-Output (
            "M4_AGENT_EXACT_REPLACEMENT_REQUIRED=" + $requirement.Name +
            "|reason=" + $requirement.Reason +
            "|destination=" + $requirement.Destination
        )
    }
    throw (
        "M4_AGENT_EXACT_REPLACEMENT_REQUIRED:" +
        ((@($replacementRequirements | ForEach-Object { $_.Name })) -join ",") +
        ":STATE_MCPORTER_BIND_MISSING"
    )
}

$allExistingExited = @($agents | Where-Object {
    $_.Exists -and $_.ExistingState -ceq "exited"
}).Count -eq 4
$allExistingRunning = @($agents | Where-Object {
    $_.Exists -and $_.ExistingState -ceq "running"
}).Count -eq 4
if (-not $allExistingExited -and -not $allExistingRunning) {
    throw "M4_AGENT_CONTROL_MODEL_LIFECYCLE_MIXED_OR_MISSING"
}

if ($allExistingExited) {
    $managerControlOutput = @(& $managerControlModelScript)
    $workerControlOutput = @(& $workerControlModelScript)
    $controlMode = "apply"
}
else {
    $managerControlOutput = @(& $managerControlModelScript -VerifyOnly)
    $workerControlOutput = @(& $workerControlModelScript -VerifyOnly)
    $controlMode = "verify"
}
if (
    $managerControlOutput -cnotcontains "M4_MANAGER_CONTROL_MODEL=PASS" -or
    $workerControlOutput -cnotcontains "M4_WORKER_CONTROL_MODEL=PASS"
) {
    throw "M4_AGENT_CONTROL_MODEL_PRESTART_FAILED"
}

foreach ($agent in $agents) {
    if ($agent.Exists) {
        if ($agent.ExistingState -ceq "exited") {
            $null = @(& $docker start $agent.Name 2>$null)
            if ($LASTEXITCODE -ne 0) {
                throw ("M4_AGENT_EXISTING_CONTAINER_START_FAILED:" + $agent.Name)
            }
            Write-Output ("M4_AGENT_CONTAINER_STARTED=" + $agent.Name)
        }
        else {
            Write-Output ("M4_AGENT_CONTAINER_REUSED=" + $agent.Name)
        }
        continue
    }
    $arguments = @(
        "run", "--detach",
        "--name", $agent.Name,
        "--network", "awakening-m4-net",
        "--add-host", $relayHostEntry,
        "--restart", "no",
        "--security-opt", "no-new-privileges:true",
        "--cap-drop", "ALL",
        "--pids-limit", "512",
        "--health-cmd", "grep -q -E ':496F .* 0A ' /proc/net/tcp /proc/net/tcp6",
        "--health-interval", "5s",
        "--health-timeout", "4s",
        "--health-retries", "36",
        "--health-start-period", "45s",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m",
        "--mount", ("type=bind,src=" + $agent.Wrapper + ",dst=/opt/awakening/m4/entrypoint.sh,readonly"),
        "--mount", ("type=bind,src=" + $agent.Env + ",dst=/run/secrets/awakening-m4/runtime.env,readonly"),
        "--mount", ("type=bind,src=" + $agent.Token + ",dst=/run/secrets/awakening-m4/sa-token,readonly"),
        "--volume", $agent.WorkspaceVolume,
        "--entrypoint", "/opt/awakening/m4/entrypoint.sh"
    )
    if ($null -ne $agent.ExtraVolume) {
        $arguments += @("--volume", $agent.ExtraVolume)
    }
    if ($null -ne $agent.McporterSource -and $null -eq $agent.McporterWorkspacePath) {
        $arguments += @(
            "--mount",
            ("type=bind,src=" + $agent.McporterSource + ",dst=" + $agent.McporterDestination + ",readonly")
        )
    }
    if ($agent.Kind -ceq "manager") {
        $arguments += @("--tmpfs", "/data:rw,nosuid,nodev,size=16m")
    }
    $arguments += $agent.Image
    $containerId = @(& $docker @arguments)
    if ($LASTEXITCODE -ne 0 -or $containerId.Count -ne 1 -or $containerId[0].Length -lt 12) {
        throw ("M4_AGENT_CONTAINER_START_FAILED:" + $agent.Name)
    }
    Write-Output ("M4_AGENT_CONTAINER_CREATED=" + $agent.Name)
}

$deadline = [DateTime]::UtcNow.AddSeconds($ReadyTimeoutSeconds)
do {
    $allReady = $true
    foreach ($agent in $agents) {
        $state = @(& $docker inspect --format "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}|{{.RestartCount}}" $agent.Name 2>$null)
        if ($LASTEXITCODE -ne 0 -or $state.Count -ne 1 -or $state[0] -cne "running|healthy|0") {
            $allReady = $false
            continue
        }
        $previousErrorAction = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $readyLog = @(& $docker logs $agent.Name 2>&1 | Select-String -SimpleMatch "[gateway] ready (" -Quiet)
        $logExit = $LASTEXITCODE
        & $docker exec $agent.Name grep -q -E ":496F .* 0A " /proc/net/tcp /proc/net/tcp6 2>$null
        $listenerExit = $LASTEXITCODE
        $ErrorActionPreference = $previousErrorAction
        if ($logExit -ne 0 -or $readyLog.Count -ne 1 -or -not [bool]$readyLog[0] -or $listenerExit -ne 0) {
            $allReady = $false
        }
    }
    if ($allReady) {
        break
    }
    Start-Sleep -Seconds 3
} while ([DateTime]::UtcNow -lt $deadline)

if (-not $allReady) {
    $summary = @()
    foreach ($agent in $agents) {
        $summary += @(& $docker inspect --format "{{.Name}}={{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}/restart={{.RestartCount}}/exit={{.State.ExitCode}}" $agent.Name 2>$null)
    }
    throw ("M4_AGENT_TOPOLOGY_NOT_READY:" + ($summary -join ";"))
}

$workerModelOutput = @(& $workerModelConfigScript)
$requiredWorkerModelMarkers = @(
    ("M4_WORKER_MODEL_ID=" + $approvedWorkerModel),
    "M4_WORKER_MODEL_MODE=apply",
    "M4_WORKER_MODEL_WORKER_COUNT=3",
    "M4_WORKER_MODEL_CONFIG_COUNT=6",
    "M4_WORKER_MODEL_PROVIDER_SECRET_PRESENT=false",
    "M4_WORKER_MODEL_NO_TOOL_POLICY=true",
    "M4_WORKER_MODEL_CONFIG=PASS"
)
foreach ($marker in $requiredWorkerModelMarkers) {
    if ($workerModelOutput -cnotcontains $marker) {
        throw ("M4_AGENT_WORKER_MODEL_APPLY_MARKER_MISSING:" + ($marker -split "=", 2)[0])
    }
}
if (@($workerModelOutput | Where-Object { [string]$_ -cmatch '^M4_WORKER_MODEL_CONFIG=awakening-m4-worker-[a-z-]+\|(root|active)\|(applied|already)$' }).Count -ne 6) {
    throw "M4_AGENT_WORKER_MODEL_APPLY_COUNT_INVALID"
}

$managerModelProjectionScript = @'
set -euo pipefail
config=/root/manager-workspace/openclaw.json
approved=qwen3.7-flash-2026-07-15
target="hiclaw-gateway/${approved}"
test -f "${config}"
test ! -L "${config}"
jq -e --arg model "${approved}" --arg target "${target}" '
  .agents.defaults.model.primary == $target
  and ((.agents.defaults.models | keys) == [$target])
  and ((.models.providers | keys) == ["hiclaw-gateway"])
  and (.models.providers["hiclaw-gateway"].models | length) == 1
  and .models.providers["hiclaw-gateway"].models[0].id == $model
  and .models.providers["hiclaw-gateway"].models[0].name == $model
' "${config}" >/dev/null
printf 'qwen3.7-flash-2026-07-15|1|1\n'
'@
$managerModelProjectionB64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($managerModelProjectionScript))
$managerModelProjectionLauncher = "printf %s " + $managerModelProjectionB64 + " | base64 -d | /bin/bash"
$managerModelProjection = @(& $docker exec awakening-m4-manager /bin/bash -ceu $managerModelProjectionLauncher 2>$null)
if (
    $LASTEXITCODE -ne 0 -or
    $managerModelProjection.Count -ne 1 -or
    $managerModelProjection[0] -cne "qwen3.7-flash-2026-07-15|1|1"
) {
    throw "M4_AGENT_MANAGER_MODEL_BINDING_INVALID"
}

$matrixKindProbe = 'if [ -L "$1" ] || [ ! -f "$1" ]; then exit 78; fi'
$null = @(& $docker exec awakening-m4-manager /bin/sh -ceu $matrixKindProbe -- $matrixDispatchTarget 2>$null)
if ($LASTEXITCODE -ne 0) {
    throw "M4_AGENTTEAMS_MATRIX_HELPER_PATH_INVALID"
}

$matrixMetadata = @(& $docker exec awakening-m4-manager stat -c "%u:%g|%a|%s" -- $matrixDispatchTarget 2>$null)
if ($LASTEXITCODE -ne 0 -or $matrixMetadata.Count -ne 1) {
    throw "M4_AGENTTEAMS_MATRIX_HELPER_METADATA_FAILED"
}
$matrixMetadataParts = [string]$matrixMetadata[0] -split "\|", 3
$matrixSize = [int64]0
if (
    $matrixMetadataParts.Count -ne 3 -or
    $matrixMetadataParts[0] -cne "0:0" -or
    $matrixMetadataParts[1] -cne "600" -or
    [int64]::TryParse($matrixMetadataParts[2], [ref]$matrixSize) -eq $false -or
    $matrixSize -le 0
) {
    throw "M4_AGENTTEAMS_MATRIX_HELPER_METADATA_INVALID"
}

$matrixSourceHash = (Get-FileHash -LiteralPath $matrixDispatchHelper -Algorithm SHA256).Hash.ToLowerInvariant()
$matrixRemoteHashLine = @(& $docker exec awakening-m4-manager sha256sum -- $matrixDispatchTarget 2>$null)
if ($LASTEXITCODE -ne 0 -or $matrixRemoteHashLine.Count -ne 1 -or $matrixRemoteHashLine[0] -notmatch '^([0-9a-fA-F]{64})\s+') {
    throw "M4_AGENTTEAMS_MATRIX_HELPER_HASH_FAILED"
}
if ($Matches[1].ToLowerInvariant() -cne $matrixSourceHash) {
    throw "M4_AGENTTEAMS_MATRIX_HELPER_HASH_MISMATCH"
}

$null = @(& $docker exec awakening-m4-manager /bin/bash -n -- $matrixDispatchTarget 2>$null)
if ($LASTEXITCODE -ne 0) {
    throw "M4_AGENTTEAMS_MATRIX_HELPER_SYNTAX_INVALID"
}

$matrixPreflight = @(& $docker exec awakening-m4-manager /bin/bash -- $matrixDispatchTarget preflight 2>$null)
if (
    $LASTEXITCODE -ne 0 -or
    $matrixPreflight.Count -ne 1 -or
    $matrixPreflight[0] -cne "M4_MATRIX_PREFLIGHT=PASS"
) {
    throw "M4_AGENTTEAMS_MATRIX_PREFLIGHT_FAILED"
}

$workspaceMcporterHashMatchCount = 0
foreach ($agent in @($agents | Where-Object { $null -ne $_.McporterWorkspacePath })) {
    $sourceFile = if ($agent.McporterSourceType -ceq "Container") {
        Join-Path $agent.McporterSource "mcporter.json"
    }
    else {
        $agent.McporterSource
    }
    $sourceHash = (Get-FileHash -LiteralPath $sourceFile -Algorithm SHA256).Hash.ToLowerInvariant()

    $kindProbe = 'if [ -L "$1" ] || [ ! -f "$1" ]; then exit 78; fi'
    $null = @(& $docker exec $agent.Name /bin/sh -ceu $kindProbe -- $agent.McporterWorkspacePath 2>$null)
    if ($LASTEXITCODE -ne 0) {
        throw ("M4_AGENT_STATE_MCPORTER_WORKSPACE_PATH_INVALID:" + $agent.Name)
    }

    $metadata = @(& $docker exec $agent.Name stat -c "%u:%g|%a|%s" -- $agent.McporterWorkspacePath 2>$null)
    if ($LASTEXITCODE -ne 0 -or $metadata.Count -ne 1) {
        throw ("M4_AGENT_STATE_MCPORTER_WORKSPACE_METADATA_FAILED:" + $agent.Name)
    }
    $metadataParts = [string]$metadata[0] -split "\|", 3
    $metadataSize = [int64]0
    if (
        $metadataParts.Count -ne 3 -or
        $metadataParts[0] -cne "0:0" -or
        $metadataParts[1] -cne "600" -or
        [int64]::TryParse($metadataParts[2], [ref]$metadataSize) -eq $false -or
        $metadataSize -le 0
    ) {
        throw ("M4_AGENT_STATE_MCPORTER_WORKSPACE_METADATA_INVALID:" + $agent.Name)
    }

    $remoteHashLine = @(& $docker exec $agent.Name sha256sum -- $agent.McporterWorkspacePath 2>$null)
    if ($LASTEXITCODE -ne 0 -or $remoteHashLine.Count -ne 1 -or $remoteHashLine[0] -notmatch '^([0-9a-fA-F]{64})\s+') {
        throw ("M4_AGENT_STATE_MCPORTER_WORKSPACE_HASH_FAILED:" + $agent.Name)
    }
    if ($Matches[1].ToLowerInvariant() -cne $sourceHash) {
        throw ("M4_AGENT_STATE_MCPORTER_WORKSPACE_HASH_MISMATCH:" + $agent.Name)
    }
    $workspaceMcporterHashMatchCount += 1
}

$excludedMcporterCleanCount = 0
foreach ($target in $excludedMcporterTargets) {
    $absenceProbe = 'if [ -e "$1" ] || [ -L "$1" ]; then exit 42; fi'
    $null = @(& $docker exec $target.Name /bin/sh -ceu $absenceProbe -- $target.Path 2>$null)
    if ($LASTEXITCODE -eq 42) {
        throw ("M4_AGENT_STATE_MCPORTER_EXCLUDED_AGENT_HAS_CONFIG:" + $target.Role)
    }
    if ($LASTEXITCODE -ne 0) {
        throw ("M4_AGENT_STATE_MCPORTER_EXCLUDED_AGENT_PROBE_FAILED:" + $target.Role)
    }
    $excludedMcporterCleanCount += 1
}

if ($workspaceMcporterHashMatchCount -ne 2 -or $excludedMcporterCleanCount -ne 2) {
    throw "M4_AGENT_STATE_MCPORTER_SCOPE_COUNT_INVALID"
}

foreach ($agent in $agents) {
    $gatewayStatus = @(& $docker exec $agent.Name curl --connect-timeout 3 --max-time 10 -sS -o /dev/null -w "%{http_code}" -X POST -H "Content-Type: application/json" --data "{}" http://host.docker.internal:18190/v1/chat/completions 2>$null)
    if ($LASTEXITCODE -ne 0 -or $gatewayStatus.Count -ne 1 -or $gatewayStatus[0] -cne "401") {
        throw ("M4_FAIL_CLOSED_GATEWAY_NOT_REACHABLE_FROM_AGENT:" + $agent.Name + ":" + ($gatewayStatus -join ","))
    }
}

$gatewayProbeDocument = [ordered]@{
    model = $approvedWorkerModel
    messages = @(
        [ordered]@{ role = "user"; content = "m4 fixed credential probe" }
    )
} | ConvertTo-Json -Compress
$gatewayProbeB64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($gatewayProbeDocument))
$gatewayProbeTemplate = '. /run/secrets/awakening-m4/runtime.env; token="${HICLAW_MANAGER_GATEWAY_KEY:-${HICLAW_WORKER_GATEWAY_KEY:-}}"; [ "${#token}" -ge 32 ]; response="$(printf %s __BODY_B64__ | base64 -d | curl --connect-timeout 3 --max-time 10 -sS -H "Authorization: Bearer ${token}" -H "Content-Type: application/json" --data-binary @- -w "|%{http_code}" http://host.docker.internal:18190/v1/chat/completions)"; status="${response##*|}"; body="${response%|*}"; reason="$(printf %s "${body}" | jq -er .error.code)"; [ "${status}" = 403 ]; [ "${reason}" = CALL_PLAN_UNAVAILABLE ]; printf "%s|%s\n" "${status}" "${reason}"'
$gatewayProbeScript = $gatewayProbeTemplate.Replace("__BODY_B64__", $gatewayProbeB64)
$gatewayProbeScriptB64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($gatewayProbeScript))
$gatewayProbeLauncher = "printf %s " + $gatewayProbeScriptB64 + " | base64 -d | /bin/bash"
foreach ($agent in $agents) {
    $authenticated = @(& $docker exec $agent.Name /bin/bash -ceu $gatewayProbeLauncher 2>$null)
    if ($LASTEXITCODE -ne 0 -or $authenticated.Count -ne 1 -or $authenticated[0] -cne "403|CALL_PLAN_UNAVAILABLE") {
        throw ("M4_GATEWAY_TRUSTED_CREDENTIAL_PROBE_FAILED:" + $agent.Name)
    }
}

$saProbeSource = @'
token="$(cat /run/secrets/awakening-m4/sa-token)"
[ "${#token}" -ge 64 ]
payload="$(printf %s "${token}" | cut -d. -f2 | tr "_-" "/+")"
case $((${#payload} % 4)) in
  2) payload="${payload}==" ;;
  3) payload="${payload}=" ;;
  1) exit 78 ;;
esac
claims="$(printf %s "${payload}" | base64 -d)"
printf %s "${claims}" | jq -e --arg expected "${M4_EXPECTED_SUB}" '.sub == $expected and ((.aud == "hiclaw-controller") or ((.aud | type) == "array" and (.aud | index("hiclaw-controller") != null)))' >/dev/null
sub="$(printf %s "${claims}" | jq -er .sub)"
aud="$(printf %s "${claims}" | jq -er '.aud | if type == "array" then join(",") else . end')"
exp="$(printf %s "${claims}" | jq -er .exp)"
remaining=$((exp - $(date +%s)))
[ "${remaining}" -ge 300 ]
printf "%s|%s|%s|%s\n" "${sub}" "${aud}" "${exp}" "${remaining}"
'@
$saProbeB64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($saProbeSource))
$saProbeScript = "printf %s " + $saProbeB64 + " | base64 -d | /bin/bash"
foreach ($agent in $agents) {
    $claims = @(& $docker exec -e ("M4_EXPECTED_SUB=" + $agent.ServiceAccountSubject) $agent.Name /bin/bash -ceu $saProbeScript 2>$null)
    if ($LASTEXITCODE -ne 0 -or $claims.Count -ne 1) {
        throw ("M4_AGENT_SA_TOKEN_CLAIMS_INVALID:" + $agent.Name)
    }
    $parts = [string]$claims[0] -split "\|", 4
    if ($parts.Count -ne 4 -or $parts[0] -cne $agent.ServiceAccountSubject -or $parts[1] -cne "hiclaw-controller") {
        throw ("M4_AGENT_SA_TOKEN_IDENTITY_MISMATCH:" + $agent.Name)
    }
    $agent["SaTokenExpiry"] = $parts[2]
    $agent["SaTokenRemaining"] = $parts[3]
}

$totalPublishedPorts = 0
foreach ($agent in $agents) {
    $details = @(& $docker inspect $agent.Name | ConvertFrom-Json)
    if ($LASTEXITCODE -ne 0 -or $details.Count -ne 1) {
        throw ("M4_AGENT_INSPECT_FAILED:" + $agent.Name)
    }
    $container = $details[0]
    if ($container.Image -cne $agent.ImageId) {
        throw ("M4_AGENT_LIVE_IMAGE_ID_MISMATCH:" + $agent.Name)
    }
    if ($container.HostConfig.NetworkMode -cne "awakening-m4-net") {
        throw ("M4_AGENT_NETWORK_MISMATCH:" + $agent.Name)
    }
    $networks = @($container.NetworkSettings.Networks.PSObject.Properties.Name)
    $published = @($container.HostConfig.PortBindings.PSObject.Properties | Where-Object {
        $null -ne $_.Value -and @($_.Value).Count -gt 0
    })
    $totalPublishedPorts += $published.Count
    $capDrop = @($container.HostConfig.CapDrop)
    $security = @($container.HostConfig.SecurityOpt)
    $extraHosts = @($container.HostConfig.ExtraHosts | Where-Object { $null -ne $_ })
    if (
        $networks.Count -ne 1 -or
        $networks[0] -cne "awakening-m4-net" -or
        $published.Count -ne 0 -or
        $capDrop.Count -ne 1 -or
        $capDrop[0] -cne "ALL" -or
        @($container.HostConfig.CapAdd | Where-Object { $null -ne $_ }).Count -ne 0 -or
        ($security -notcontains "no-new-privileges:true" -and $security -notcontains "no-new-privileges") -or
        [int64]$container.HostConfig.PidsLimit -ne 512 -or
        $container.HostConfig.RestartPolicy.Name -cne "no" -or
        $extraHosts.Count -ne 1 -or
        [string]$extraHosts[0] -cne $relayHostEntry
    ) {
        throw ("M4_AGENT_LIVE_HARDENING_FAILED:" + $agent.Name)
    }
    if ($container.HostConfig.Privileged -or $null -ne ($container.Mounts | Where-Object { $_.Destination -ceq "/var/run/docker.sock" })) {
        throw ("M4_AGENT_PRIVILEGE_BOUNDARY_FAILED:" + $agent.Name)
    }
    $providerKeys = @($container.Config.Env | ForEach-Object { ($_ -split "=", 2)[0] } | Where-Object { $_ -in @("HICLAW_LLM_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DASHSCOPE_API_KEY") })
    if ($providerKeys.Count -ne 0) {
        throw ("M4_AGENT_PROVIDER_SECRET_ENV_PRESENT:" + $agent.Name)
    }

    $resolutionLines = @(& $docker exec $agent.Name getent ahostsv4 host.docker.internal 2>$null)
    $resolutionExit = $LASTEXITCODE
    $resolvedAddresses = @($resolutionLines | ForEach-Object {
        if ($_ -match '^((?:[0-9]{1,3}\.){3}[0-9]{1,3})\s+') {
            $Matches[1]
        }
    } | Sort-Object -Unique)
    if (
        $resolutionExit -ne 0 -or
        $resolvedAddresses.Count -ne 1 -or
        [string]$resolvedAddresses[0] -cne $relayIp
    ) {
        throw ("M4_AGENT_HOST_RELAY_RESOLUTION_INVALID:" + $agent.Name)
    }
}

if ($totalPublishedPorts -ne 0) {
    throw "M4_AGENTTEAMS_PUBLISHED_PORTS_PRESENT"
}

Write-Output "M4_AGENTTEAMS_RUNTIME_STATUS=passed"
Write-Output "M4_AGENTTEAMS_WORKER_COUNT=3"
Write-Output "M4_AGENTTEAMS_CONTAINER_COUNT=4"
Write-Output "M4_AGENTTEAMS_RUNTIME_READY_SIGNAL=gateway_log_plus_tcp_listener"
Write-Output "M4_AGENTTEAMS_NETWORK=awakening-m4-net"
Write-Output "M4_AGENTTEAMS_HOST_RELAY_MAPPING=host.docker.internal:172.20.0.254"
Write-Output "M4_AGENTTEAMS_DIRECT_HOST_GATEWAY_MAPPING=false"
Write-Output "M4_AGENTTEAMS_HOST_PORT_COUNT=0"
Write-Output "M4_AGENTTEAMS_DOCKER_SOCKET=false"
Write-Output "M4_AGENTTEAMS_PRIVILEGED=false"
Write-Output "M4_AGENTTEAMS_PROVIDER_KEY_PRESENT=false"
Write-Output "M4_AGENTTEAMS_GATEWAY_CREDENTIAL_COUNT=4"
Write-Output "M4_AGENTTEAMS_GATEWAY_AUTHENTICATED_STATUS=403"
Write-Output "M4_AGENTTEAMS_GATEWAY_REASON=CALL_PLAN_UNAVAILABLE"
Write-Output "M4_AGENTTEAMS_PROVIDER_CONFIGURED=false"
Write-Output ("M4_AGENTTEAMS_CONTROL_MODEL_MODE=" + $controlMode)
Write-Output "M4_AGENTTEAMS_MANAGER_MODEL_ID=qwen3.7-flash-2026-07-15"
Write-Output "M4_AGENTTEAMS_MANAGER_MODEL_BINDING=passed"
Write-Output ("M4_AGENTTEAMS_WORKER_MODEL_ID=" + $approvedWorkerModel)
Write-Output "M4_AGENTTEAMS_WORKER_MODEL_CONFIG_COUNT=6"
Write-Output "M4_AGENTTEAMS_WORKER_MODEL_BINDING=passed"
Write-Output "M4_AGENTTEAMS_STATE_MCPORTER_CONFIG_COUNT=2"
Write-Output "M4_AGENTTEAMS_STATE_MCPORTER_MANAGER=true"
Write-Output "M4_AGENTTEAMS_STATE_MCPORTER_ARCHITECT=true"
Write-Output "M4_AGENTTEAMS_STATE_MCPORTER_COACH=false"
Write-Output "M4_AGENTTEAMS_STATE_MCPORTER_REVIEWER=false"
Write-Output "M4_AGENTTEAMS_STATE_MCPORTER_INSTALL_MODE=workspace_0600"
Write-Output "M4_AGENTTEAMS_STATE_MCPORTER_HASH_MATCH=true"
Write-Output "M4_AGENTTEAMS_MATRIX_HELPER_PRESENT=true"
Write-Output "M4_AGENTTEAMS_MATRIX_HELPER_HASH_MATCH=true"
Write-Output "M4_AGENTTEAMS_MATRIX_HELPER_OWNER=0:0"
Write-Output "M4_AGENTTEAMS_MATRIX_HELPER_MODE=600"
Write-Output "M4_AGENTTEAMS_MATRIX_HELPER_BASH_N=passed"
Write-Output "M4_AGENTTEAMS_MATRIX_PREFLIGHT=passed"
foreach ($agent in $agents) {
    Write-Output ("M4_AGENTTEAMS_SA_CLAIMS=" + $agent.Name + "|" + $agent.ServiceAccountSubject + "|hiclaw-controller|exp=" + $agent.SaTokenExpiry + "|remaining=" + $agent.SaTokenRemaining)
}
