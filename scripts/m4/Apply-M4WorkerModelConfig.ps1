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

foreach ($path in @($docker, (Join-Path $anonymousConfig "config.json"), $liveConfigPath)) {
    $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
    if ($item.PSIsContainer -or
        ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "M4_WORKER_MODEL_INPUT_INVALID"
    }
}

$liveConfig = Get-Content -Raw -Encoding UTF8 -LiteralPath $liveConfigPath | ConvertFrom-Json
if (
    $liveConfig.authorization_id -cne "AUTH-M4-001" -or
    $liveConfig.schema_version -ne 1 -or
    [string]::IsNullOrWhiteSpace([string]$liveConfig.provider.model_id) -or
    $liveConfig.provider.public_model_alias -cne $liveConfig.provider.model_id
) {
    throw "M4_WORKER_MODEL_LIVE_CONFIG_INVALID"
}
$modelId = [string]$liveConfig.provider.model_id
if ($modelId -notmatch '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$') {
    throw "M4_WORKER_MODEL_ID_INVALID"
}

$workers = @(
    [ordered]@{
        Name = "awakening-m4-worker-role-project-architect"
        Configs = @(
            [ordered]@{ Label = "root"; Path = "/root/hiclaw-fs/agents/role_project_architect/openclaw.json" },
            [ordered]@{ Label = "active"; Path = "/root/hiclaw-fs/agents/role_project_architect/.openclaw/openclaw.json" }
        )
    },
    [ordered]@{
        Name = "awakening-m4-worker-execution-evidence-coach"
        Configs = @(
            [ordered]@{ Label = "root"; Path = "/root/hiclaw-fs/agents/execution_evidence_coach/openclaw.json" },
            [ordered]@{ Label = "active"; Path = "/root/hiclaw-fs/agents/execution_evidence_coach/.openclaw/openclaw.json" }
        )
    },
    [ordered]@{
        Name = "awakening-m4-worker-independent-quality-reviewer"
        Configs = @(
            [ordered]@{ Label = "root"; Path = "/root/hiclaw-fs/agents/independent_quality_reviewer/openclaw.json" },
            [ordered]@{ Label = "active"; Path = "/root/hiclaw-fs/agents/independent_quality_reviewer/.openclaw/openclaw.json" }
        )
    }
)

$patchScript = @'
set -euo pipefail
config="$1"
approved_model="$2"
operation="$3"
case "${config}" in
  /root/hiclaw-fs/agents/*/.openclaw/openclaw.json)
    test -L "${config}"
    expected="${config%/.openclaw/openclaw.json}/openclaw.json"
    test -f "${expected}"
    test ! -L "${expected}"
    test "$(readlink -f -- "${config}")" = "$(readlink -f -- "${expected}")"
    config="$(readlink -f -- "${expected}")"
    ;;
  /root/hiclaw-fs/agents/*/openclaw.json)
    test -f "${config}"
    test ! -L "${config}"
    ;;
  *) exit 78 ;;
esac
[[ "${approved_model}" =~ ^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$ ]] || exit 78
[[ "${operation}" == "apply" || "${operation}" == "verify" ]] || exit 78

for provider_secret in HICLAW_LLM_API_KEY OPENAI_API_KEY ANTHROPIC_API_KEY DASHSCOPE_API_KEY; do
  test -z "$(printenv "${provider_secret}" 2>/dev/null || true)"
done

jq -e '
  (.agents.defaults.models | type) == "object"
  and
  (.models.providers | type) == "object"
  and ((.models.providers | keys) == ["hiclaw-gateway"])
  and (.models.providers["hiclaw-gateway"].models | type) == "array"
' "${config}" >/dev/null

current="$(jq -er '.agents.defaults.model.primary' "${config}")"
target="hiclaw-gateway/${approved_model}"
target_count="$(jq -r --arg model "${approved_model}" '[.models.providers["hiclaw-gateway"].models[] | select(.id == $model)] | length' "${config}")"
fallback_count="$(jq -r '[.models.providers["hiclaw-gateway"].models[] | select(.id == "gpt-5-mini")] | length' "${config}")"
test "${target_count}" = "0" -o "${target_count}" = "1"
test "${fallback_count}" = "0" -o "${fallback_count}" = "1"
if [[ "${operation}" == "verify" ]]; then
  test "${current}" = "${target}"
  test "${target_count}" = "1"
  test "$(jq -r '.models.providers["hiclaw-gateway"].models | length' "${config}")" = "1"
  jq -e --arg target "${target}" '(.agents.defaults.models | keys) == [$target]' "${config}" >/dev/null
  jq -e '.tools.deny == ["*"]' "${config}" >/dev/null
  printf '%s\n' verified
  exit 0
fi
if [[ "${current}" == "${target}" && "${target_count}" == "1" ]] && \
   [[ "$(jq -r '.models.providers["hiclaw-gateway"].models | length' "${config}")" == "1" ]] && \
   jq -e --arg target "${target}" '(.agents.defaults.models | keys) == [$target]' "${config}" >/dev/null && \
   jq -e '.tools.deny == ["*"]' "${config}" >/dev/null; then
  printf '%s\n' already
  exit 0
fi
test "${target_count}" = "1" -o "${fallback_count}" = "1"

tmp="${config}.awakening-m4-model.tmp"
test ! -e "${tmp}"
mode="$(stat -c '%a' "${config}")"
owner="$(stat -c '%u:%g' "${config}")"
before_other="$(jq -cS 'del(.agents.defaults.model,.agents.defaults.models,.models,.tools)' "${config}" | sha256sum | cut -d' ' -f1)"
umask 077
jq --arg model "${approved_model}" --arg target "${target}" '
  .agents.defaults.model.primary = ("hiclaw-gateway/" + $model)
  | .agents.defaults.models = {($target): {"alias": $model}}
  | .models.providers["hiclaw-gateway"].models = [
      ((.models.providers["hiclaw-gateway"].models[] | select(.id == $model)),
       (.models.providers["hiclaw-gateway"].models[] | select(.id == "gpt-5-mini")))
      | .id = $model
      | .name = $model
    ][0:1]
  | .tools = ((.tools // {}) + {"deny":["*"]})
' "${config}" > "${tmp}"
jq -e --arg target "${target}" --arg model "${approved_model}" '
  .agents.defaults.model.primary == $target
  and ((.agents.defaults.models | keys) == [$target])
  and ([.models.providers["hiclaw-gateway"].models[] | select(.id == $model)] | length) == 1
  and (.models.providers["hiclaw-gateway"].models | length) == 1
  and .tools.deny == ["*"]
' "${tmp}" >/dev/null
after_other="$(jq -cS 'del(.agents.defaults.model,.agents.defaults.models,.models,.tools)' "${tmp}" | sha256sum | cut -d' ' -f1)"
test "${before_other}" = "${after_other}"
chown "${owner}" "${tmp}"
chmod "${mode}" "${tmp}"
mv -f -- "${tmp}" "${config}"
printf '%s\n' applied
'@

$patchB64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($patchScript))
$operation = if ($VerifyOnly) { "verify" } else { "apply" }
$previousDockerConfig = $env:DOCKER_CONFIG
try {
    $env:DOCKER_CONFIG = $anonymousConfig
    foreach ($worker in $workers) {
        $state = @(& $docker inspect --format "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}" $worker.Name 2>$null)
        if ($LASTEXITCODE -ne 0 -or $state.Count -ne 1 -or $state[0] -cne "running|healthy") {
            throw ("M4_WORKER_MODEL_RUNTIME_INVALID:" + $worker.Name)
        }
        foreach ($config in $worker.Configs) {
            $command = "printf %s '" + $patchB64 + "' | base64 -d | /bin/bash -s -- '" + $config.Path + "' '" + $modelId + "' '" + $operation + "'"
            $result = @(& $docker exec $worker.Name /bin/bash -ceu $command 2>$null)
            if (
                $LASTEXITCODE -ne 0 -or
                $result.Count -ne 1 -or
                $result[0] -notin @("applied", "already", "verified")
            ) {
                throw ("M4_WORKER_MODEL_" + $operation.ToUpperInvariant() + "_FAILED:" + $worker.Name + ":" + $config.Label)
            }
            Write-Output ("M4_WORKER_MODEL_CONFIG=" + $worker.Name + "|" + $config.Label + "|" + $result[0])
        }
    }
}
finally {
    $env:DOCKER_CONFIG = $previousDockerConfig
}

Write-Output ("M4_WORKER_MODEL_ID=" + $modelId)
Write-Output ("M4_WORKER_MODEL_MODE=" + $operation)
Write-Output "M4_WORKER_MODEL_WORKER_COUNT=3"
Write-Output "M4_WORKER_MODEL_CONFIG_COUNT=6"
Write-Output "M4_WORKER_MODEL_PROVIDER_SECRET_PRESENT=false"
Write-Output "M4_WORKER_MODEL_NO_TOOL_POLICY=true"
Write-Output "M4_WORKER_MODEL_CONFIG=PASS"
