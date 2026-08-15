#!/bin/sh
set -eu

umask 077

RUNTIME_ENV="/run/secrets/awakening-m4/runtime.env"
UPSTREAM="/opt/hiclaw/scripts/worker-entrypoint.sh"
PATCHED="/tmp/awakening-m4-worker-entrypoint.sh"
MODEL_GUARD="/tmp/awakening-m4-pin-agent-model.sh"
APPROVED_MODEL="qwen3.7-flash-2026-07-15"

if [ ! -f "${RUNTIME_ENV}" ] || [ ! -f "${UPSTREAM}" ]; then
    echo "M4_WORKER_RUNTIME_INPUT_MISSING" >&2
    exit 78
fi

set -a
# shellcheck disable=SC1090
. "${RUNTIME_ENV}"
set +a

for provider_secret in HICLAW_LLM_API_KEY OPENAI_API_KEY ANTHROPIC_API_KEY DASHSCOPE_API_KEY; do
    if [ -n "$(printenv "${provider_secret}" 2>/dev/null || true)" ]; then
        echo "M4_PROVIDER_SECRET_FORBIDDEN:${provider_secret}" >&2
        exit 78
    fi
done

if [ -S /var/run/docker.sock ]; then
    echo "M4_DOCKER_SOCKET_FORBIDDEN" >&2
    exit 78
fi

# The upstream Worker pulls the Manager-owned root openclaw.json from MinIO
# before it launches OpenClaw.  M4 therefore pins the approved model and the
# deny-all tool policy after that pull and before exec; a post-start host edit
# is too late for the process snapshot and is not accepted as runtime evidence.
cat > "${MODEL_GUARD}" <<'M4_MODEL_GUARD'
#!/bin/bash
set -euo pipefail

config="$1"
approved_model="qwen3.7-flash-2026-07-15"
target="hiclaw-gateway/${approved_model}"
gateway_key="${HICLAW_WORKER_GATEWAY_KEY:-}"

if [[ "${gateway_key}" =~ ^[A-Za-z0-9_-]{43}$ ]]; then
  : # Rotated M5 Architect/Reviewer credential format.
elif [[ "${gateway_key}" =~ ^[A-Fa-f0-9]{64}$ ]]; then
  : # Existing M4 Manager-issued Worker credential format (for example Coach).
else
  exit 78
fi

case "${config}" in
  /root/hiclaw-fs/agents/*/openclaw.json|/root/manager-workspace/openclaw.json) ;;
  *) exit 78 ;;
esac
test -f "${config}"
test ! -L "${config}"

for provider_secret in HICLAW_LLM_API_KEY OPENAI_API_KEY ANTHROPIC_API_KEY DASHSCOPE_API_KEY; do
  test -z "$(printenv "${provider_secret}" 2>/dev/null || true)"
done

jq -e '
  (.agents.defaults.model | type) == "object"
  and (.agents.defaults.models | type) == "object"
  and (.models.providers | type) == "object"
  and ((.models.providers | keys) == ["hiclaw-gateway"])
  and (.models.providers["hiclaw-gateway"].apiKey | type) == "string"
  and (.models.providers["hiclaw-gateway"].models | type) == "array"
  and (.models.providers["hiclaw-gateway"].models | length) >= 1
' "${config}" >/dev/null

tmp="${config}.awakening-m4-preexec-model.tmp"
test ! -e "${tmp}"
mode="$(stat -c '%a' "${config}")"
owner="$(stat -c '%u:%g' "${config}")"
before_other="$(jq -cS 'del(.agents.defaults.model,.agents.defaults.models,.models.providers["hiclaw-gateway"].apiKey,.models.providers["hiclaw-gateway"].models,.tools)' "${config}" | sha256sum | cut -d' ' -f1)"

umask 077
jq --rawfile gateway_key <(printf '%s' "${gateway_key}") \
  --arg model "${approved_model}" --arg target "${target}" '
  (.models.providers["hiclaw-gateway"].models
    | (map(select(.id == $model))[0]
       // map(select(.id == "gpt-5-mini"))[0]
       // .[0])) as $model_record
  | (.agents.defaults.models[$target] // {"alias": $model}) as $alias_record
  | .agents.defaults.model.primary = $target
  | .agents.defaults.models = {($target): $alias_record}
  | .models.providers["hiclaw-gateway"].apiKey = $gateway_key
  | .models.providers["hiclaw-gateway"].models = [
      ($model_record | .id = $model | .name = $model)
    ]
  | .tools = ((.tools // {}) + {"deny":["*"]})
' "${config}" > "${tmp}"

jq -e --rawfile gateway_key <(printf '%s' "${gateway_key}") \
  --arg model "${approved_model}" --arg target "${target}" '
  .agents.defaults.model.primary == $target
  and ((.agents.defaults.models | keys) == [$target])
  and .models.providers["hiclaw-gateway"].apiKey == $gateway_key
  and (.models.providers["hiclaw-gateway"].models | length) == 1
  and .models.providers["hiclaw-gateway"].models[0].id == $model
  and .models.providers["hiclaw-gateway"].models[0].name == $model
  and .tools.deny == ["*"]
' "${tmp}" >/dev/null
after_other="$(jq -cS 'del(.agents.defaults.model,.agents.defaults.models,.models.providers["hiclaw-gateway"].apiKey,.models.providers["hiclaw-gateway"].models,.tools)' "${tmp}" | sha256sum | cut -d' ' -f1)"
test "${before_other}" = "${after_other}"

chown "${owner}" "${tmp}"
chmod "${mode}" "${tmp}"
mv -f -- "${tmp}" "${config}"
printf 'M4_AGENT_MODEL_PREEXEC=PASS|%s\n' "${config}"
printf 'M4_AGENT_NO_TOOL_PREEXEC=PASS|%s\n' "${config}"
printf 'M4_AGENT_GATEWAY_CREDENTIAL_PREEXEC=PASS|%s\n' "${config}"
unset gateway_key
M4_MODEL_GUARD
chmod 700 "${MODEL_GUARD}"

# Existing protected runtime.env files predate the qwen decision.  Override
# only the non-secret model selector in process memory; do not rewrite or echo
# any credential-bearing file.
export HICLAW_DEFAULT_MODEL="${APPROVED_MODEL}"

cp "${UPSTREAM}" "${PATCHED}"
sed -i \
    -e 's/log "Matrix re-login successful (new device: ${NEW_DEVICE}, token prefix: ${NEW_TOKEN:0:10}\.\.\.)"/log "Matrix re-login successful (new device recorded, token redacted)"/' \
    -e 's/log "  Response: ${LOGIN_RESP}"/log "  Response redacted"/' \
    "${PATCHED}"

if [ "$(grep -Fc '# HOME is already set to WORKSPACE via docker run -e HOME=' "${PATCHED}")" -ne 1 ] || \
   [ "$(grep -Fc '        merge_openclaw_config /tmp/openclaw-remote.json "${WORKSPACE}/openclaw.json"' "${PATCHED}")" -ne 1 ]; then
    echo "M4_WORKER_MODEL_PREEXEC_SENTINEL_INVALID" >&2
    exit 78
fi

sed -i \
    -e '/^# HOME is already set to WORKSPACE via docker run -e HOME=/i\/bin/bash /tmp/awakening-m4-pin-agent-model.sh "${WORKSPACE}/openclaw.json"' \
    -e '/^[[:space:]]*merge_openclaw_config \/tmp\/openclaw-remote.json "${WORKSPACE}\/openclaw.json"$/a\        /bin/bash /tmp/awakening-m4-pin-agent-model.sh "${WORKSPACE}/openclaw.json"' \
    "${PATCHED}"

if grep -Eq 'token prefix:|Response: \$\{LOGIN_RESP\}' "${PATCHED}"; then
    echo "M4_WORKER_TOKEN_LOG_SANITIZATION_FAILED" >&2
    exit 78
fi
if [ "$(grep -Fc '/bin/bash /tmp/awakening-m4-pin-agent-model.sh "${WORKSPACE}/openclaw.json"' "${PATCHED}")" -ne 2 ]; then
    echo "M4_WORKER_MODEL_PREEXEC_INJECTION_FAILED" >&2
    exit 78
fi

chmod 700 "${PATCHED}"
exec /bin/bash "${PATCHED}"
