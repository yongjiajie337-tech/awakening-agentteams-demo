#requires -Version 5.1

[CmdletBinding()]
param(
    [switch]$VerifyOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$workspace = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$docker = Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\resources\bin\docker.exe"
$anonymousConfig = Join-Path $workspace "tmp\m4\docker-config-anonymous"
$liveConfigPath = Join-Path $workspace "tmp\m4\provider\live-gateway-config.json"
$workerImage = "higress-registry.cn-hangzhou.cr.aliyuncs.com/higress/hiclaw-worker@sha256:d1078b42115ec2ea4eeaac507bc63352812291ff6e2406e813863161f074fb0b"

$workers = @(
    [ordered]@{
        ResourceName = "role-project-architect"
        WorkerName = "role_project_architect"
        ContainerName = "awakening-m4-worker-role-project-architect"
        ResourcePath = (Join-Path $workspace "infra\agentteams\m4\resources\role-project-architect.yaml")
    },
    [ordered]@{
        ResourceName = "execution-evidence-coach"
        WorkerName = "execution_evidence_coach"
        ContainerName = "awakening-m4-worker-execution-evidence-coach"
        ResourcePath = (Join-Path $workspace "infra\agentteams\m4\resources\execution-evidence-coach.yaml")
    },
    [ordered]@{
        ResourceName = "independent-quality-reviewer"
        WorkerName = "independent_quality_reviewer"
        ContainerName = "awakening-m4-worker-independent-quality-reviewer"
        ResourcePath = (Join-Path $workspace "infra\agentteams\m4\resources\independent-quality-reviewer.yaml")
    }
)

foreach ($path in @($docker, (Join-Path $anonymousConfig "config.json"), $liveConfigPath) + @($workers.ResourcePath)) {
    $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
    if ($item.PSIsContainer -or ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "M4_WORKER_CONTROL_MODEL_INPUT_INVALID"
    }
}

$liveConfig = Get-Content -Raw -Encoding UTF8 -LiteralPath $liveConfigPath | ConvertFrom-Json
if (
    $liveConfig.authorization_id -cne "AUTH-M4-001" -or
    $liveConfig.schema_version -ne 1 -or
    [string]$liveConfig.provider.model_id -cne "qwen3.7-flash-2026-07-15" -or
    $liveConfig.provider.public_model_alias -cne $liveConfig.provider.model_id
) {
    throw "M4_WORKER_CONTROL_MODEL_LIVE_CONFIG_INVALID"
}
$modelId = [string]$liveConfig.provider.model_id

foreach ($worker in $workers) {
    $resourceText = Get-Content -Raw -Encoding UTF8 -LiteralPath $worker.ResourcePath
    $expectedModelLine = "  model: " + $modelId
    if (@($resourceText -split "`r?`n" | Where-Object { $_ -ceq $expectedModelLine }).Count -ne 1) {
        throw ("M4_WORKER_CONTROL_MODEL_RESOURCE_FILE_INVALID:" + $worker.ResourceName)
    }
}

$previousDockerConfig = $env:DOCKER_CONFIG
try {
    $env:DOCKER_CONFIG = $anonymousConfig

    $controllerState = @(& $docker inspect --format "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}" awakening-m4-controller 2>$null)
    if ($LASTEXITCODE -ne 0 -or $controllerState.Count -ne 1 -or $controllerState[0] -cne "running|healthy") {
        throw "M4_WORKER_CONTROL_MODEL_CONTROLLER_NOT_READY"
    }

    if (-not $VerifyOnly) {
        foreach ($worker in $workers) {
            $state = @(& $docker inspect --format "{{.State.Status}}|{{.State.Pid}}" $worker.ContainerName 2>$null)
            if ($LASTEXITCODE -ne 0 -or $state.Count -ne 1 -or $state[0] -cne "exited|0") {
                throw ("M4_WORKER_CONTROL_MODEL_CONTAINER_NOT_STOPPED:" + $worker.ContainerName)
            }
        }
    }

    $workerRecords = @($workers | ForEach-Object {
        $_.ResourceName + "|" + $_.WorkerName
    }) -join ","
    $operation = if ($VerifyOnly) { "verify" } else { "apply" }

    $bashLines = @(
        'set -euo pipefail',
        'token="$(</data/hiclaw-controller/admin-token)"',
        'auth="Authorization: Bearer ${token}"',
        'base="https://127.0.0.1:6443/apis/hiclaw.io/v1beta1/namespaces/default/workers"',
        ('approved_model=' + "'" + $modelId + "'"),
        ('expected_image=' + "'" + $workerImage + "'"),
        ('operation=' + "'" + $operation + "'"),
        ('records=' + "'" + $workerRecords + "'"),
        'IFS="," read -r -a worker_records <<< "${records}"',
        'changed=0',
        'verified=0',
        'for record in "${worker_records[@]}"; do',
        '  resource_name="${record%%|*}"',
        '  worker_name="${record#*|}"',
        '  resource="$(curl -fsS --cacert /data/hiclaw-controller/pki/ca.crt -H "${auth}" "${base}/${resource_name}")"',
        '  printf %s "${resource}" | jq -e --arg resource "${resource_name}" --arg worker "${worker_name}" --arg image "${expected_image}" ''',
        '    .metadata.name == $resource',
        '    and .spec.workerName == $worker',
        '    and .spec.runtime == "openclaw"',
        '    and .spec.image == $image',
        '    and .spec.containerManaged == false',
        '    and .spec.state == "Running"',
        '    and (.spec.model == "gpt-5-mini" or .spec.model == $approved)',
        '  '' --arg approved "${approved_model}" >/dev/null',
        '  before="$(printf %s "${resource}" | jq -er ''.spec.model'')"',
        '  if [ "${operation}" = apply ] && [ "${before}" != "${approved_model}" ]; then',
        '    payload="$(jq -cn --arg model "${approved_model}" ''{spec:{model:$model}}'')"',
        '    resource="$(curl -fsS --cacert /data/hiclaw-controller/pki/ca.crt -H "${auth}" -H ''Content-Type: application/merge-patch+json'' -X PATCH --data-binary "${payload}" "${base}/${resource_name}")"',
        '    changed=$((changed + 1))',
        '  fi',
        '  printf %s "${resource}" | jq -e --arg model "${approved_model}" ''.spec.model == $model'' >/dev/null',
        '  after="$(printf %s "${resource}" | jq -er ''.spec.model'')"',
        '  printf ''M4_WORKER_CONTROL_MODEL_RESOURCE=%s|before=%s|after=%s\n'' "${resource_name}" "${before}" "${after}"',
        '  verified=$((verified + 1))',
        'done',
        'token=',
        'auth=',
        'printf ''M4_WORKER_CONTROL_MODEL_CHANGED_COUNT=%s\n'' "${changed}"',
        'printf ''M4_WORKER_CONTROL_MODEL_VERIFIED_COUNT=%s\n'' "${verified}"'
    )
    $bashSource = [string]::Join("`n", $bashLines)
    $bashB64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($bashSource))
    $launcher = "printf %s " + $bashB64 + " | base64 -d | /bin/bash"
    $result = @(& $docker exec awakening-m4-controller /bin/bash -ceu $launcher 2>$null)
    if ($LASTEXITCODE -ne 0) {
        throw "M4_WORKER_CONTROL_MODEL_CONTROLLER_UPDATE_FAILED"
    }

    if (@($result | Where-Object { [string]$_ -cmatch '^M4_WORKER_CONTROL_MODEL_RESOURCE=[a-z-]+\|before=(gpt-5-mini|qwen3\.7-flash-2026-07-15)\|after=qwen3\.7-flash-2026-07-15$' }).Count -ne 3) {
        throw "M4_WORKER_CONTROL_MODEL_RESOURCE_COUNT_INVALID"
    }
    if ($result -cnotcontains "M4_WORKER_CONTROL_MODEL_VERIFIED_COUNT=3") {
        throw "M4_WORKER_CONTROL_MODEL_VERIFICATION_FAILED"
    }
    foreach ($line in $result) {
        Write-Output $line
    }
}
finally {
    $env:DOCKER_CONFIG = $previousDockerConfig
}

Write-Output ("M4_WORKER_CONTROL_MODEL_ID=" + $modelId)
Write-Output ("M4_WORKER_CONTROL_MODEL_MODE=" + $(if ($VerifyOnly) { "verify" } else { "apply" }))
Write-Output "M4_WORKER_CONTROL_MODEL_SECRET_ECHOED=false"
Write-Output "M4_WORKER_CONTROL_MODEL=PASS"
