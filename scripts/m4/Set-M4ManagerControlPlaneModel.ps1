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
$resourcePath = Join-Path $workspace "infra\agentteams\m4\resources\manager.yaml"
$managerImage = "higress-registry.cn-hangzhou.cr.aliyuncs.com/higress/hiclaw-manager@sha256:3a77482fb11472ab05f85ba5d60cbc0df8d66046aa9f63b9cf99f16d87852921"

foreach ($path in @($docker, (Join-Path $anonymousConfig "config.json"), $liveConfigPath, $resourcePath)) {
    $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
    if ($item.PSIsContainer -or ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "M4_MANAGER_CONTROL_MODEL_INPUT_INVALID"
    }
}

$liveConfig = Get-Content -Raw -Encoding UTF8 -LiteralPath $liveConfigPath | ConvertFrom-Json
if (
    $liveConfig.authorization_id -cne "AUTH-M4-001" -or
    $liveConfig.schema_version -ne 1 -or
    [string]$liveConfig.provider.model_id -cne "qwen3.7-flash-2026-07-15" -or
    $liveConfig.provider.public_model_alias -cne $liveConfig.provider.model_id
) {
    throw "M4_MANAGER_CONTROL_MODEL_LIVE_CONFIG_INVALID"
}
$modelId = [string]$liveConfig.provider.model_id

$resourceText = Get-Content -Raw -Encoding UTF8 -LiteralPath $resourcePath
if (@($resourceText -split "`r?`n" | Where-Object { $_ -ceq ("  model: " + $modelId) }).Count -ne 1) {
    throw "M4_MANAGER_CONTROL_MODEL_RESOURCE_FILE_INVALID"
}

$previousDockerConfig = $env:DOCKER_CONFIG
try {
    $env:DOCKER_CONFIG = $anonymousConfig

    $controllerState = @(& $docker inspect --format "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}" awakening-m4-controller 2>$null)
    if ($LASTEXITCODE -ne 0 -or $controllerState.Count -ne 1 -or $controllerState[0] -cne "running|healthy") {
        throw "M4_MANAGER_CONTROL_MODEL_CONTROLLER_NOT_READY"
    }
    if (-not $VerifyOnly) {
        $managerState = @(& $docker inspect --format "{{.State.Status}}|{{.State.Pid}}" awakening-m4-manager 2>$null)
        if ($LASTEXITCODE -ne 0 -or $managerState.Count -ne 1 -or $managerState[0] -cne "exited|0") {
            throw "M4_MANAGER_CONTROL_MODEL_CONTAINER_NOT_STOPPED"
        }
    }

    $operation = if ($VerifyOnly) { "verify" } else { "apply" }
    $bashLines = @(
        'set -euo pipefail',
        'token="$(</data/hiclaw-controller/admin-token)"',
        'auth="Authorization: Bearer ${token}"',
        'url="https://127.0.0.1:6443/apis/hiclaw.io/v1beta1/namespaces/default/managers/default"',
        ('approved_model=' + "'" + $modelId + "'"),
        ('expected_image=' + "'" + $managerImage + "'"),
        ('operation=' + "'" + $operation + "'"),
        'resource="$(curl -fsS --cacert /data/hiclaw-controller/pki/ca.crt -H "${auth}" "${url}")"',
        'printf %s "${resource}" | jq -e --arg image "${expected_image}" --arg approved "${approved_model}" ''',
        '  .metadata.name == "default"',
        '  and .spec.runtime == "openclaw"',
        '  and .spec.image == $image',
        '  and .spec.state == "Running"',
        '  and (.spec.model == "gpt-5-mini" or .spec.model == $approved)',
        ''' >/dev/null',
        'before="$(printf %s "${resource}" | jq -er ''.spec.model'')"',
        'changed=0',
        'if [ "${operation}" = apply ] && [ "${before}" != "${approved_model}" ]; then',
        '  payload="$(jq -cn --arg model "${approved_model}" ''{spec:{model:$model}}'')"',
        '  resource="$(curl -fsS --cacert /data/hiclaw-controller/pki/ca.crt -H "${auth}" -H ''Content-Type: application/merge-patch+json'' -X PATCH --data-binary "${payload}" "${url}")"',
        '  changed=1',
        'fi',
        'printf %s "${resource}" | jq -e --arg model "${approved_model}" ''.spec.model == $model'' >/dev/null',
        'after="$(printf %s "${resource}" | jq -er ''.spec.model'')"',
        'printf ''M4_MANAGER_CONTROL_MODEL_RESOURCE=default|before=%s|after=%s\n'' "${before}" "${after}"',
        'printf ''M4_MANAGER_CONTROL_MODEL_CHANGED_COUNT=%s\n'' "${changed}"',
        'token=',
        'auth='
    )
    $bashSource = [string]::Join("`n", $bashLines)
    $bashB64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($bashSource))
    $launcher = "printf %s " + $bashB64 + " | base64 -d | /bin/bash"
    $result = @(& $docker exec awakening-m4-controller /bin/bash -ceu $launcher 2>$null)
    if ($LASTEXITCODE -ne 0) {
        throw "M4_MANAGER_CONTROL_MODEL_CONTROLLER_UPDATE_FAILED"
    }
    $expected = '^M4_MANAGER_CONTROL_MODEL_RESOURCE=default\|before=(gpt-5-mini|qwen3\.7-flash-2026-07-15)\|after=qwen3\.7-flash-2026-07-15$'
    if (@($result | Where-Object { [string]$_ -cmatch $expected }).Count -ne 1) {
        throw "M4_MANAGER_CONTROL_MODEL_RESOURCE_RESULT_INVALID"
    }
    foreach ($line in $result) {
        Write-Output $line
    }
}
finally {
    $env:DOCKER_CONFIG = $previousDockerConfig
}

Write-Output ("M4_MANAGER_CONTROL_MODEL_ID=" + $modelId)
Write-Output ("M4_MANAGER_CONTROL_MODEL_MODE=" + $(if ($VerifyOnly) { "verify" } else { "apply" }))
Write-Output "M4_MANAGER_CONTROL_MODEL_SECRET_ECHOED=false"
Write-Output "M4_MANAGER_CONTROL_MODEL=PASS"
