#!/bin/sh
set -eu

umask 077

RUNTIME_ENV="/run/secrets/awakening-m4/runtime.env"
UPSTREAM="/opt/hiclaw/scripts/init/start-manager-agent.sh"
PATCHED="/tmp/awakening-m4-manager-entrypoint.sh"
MODEL_GUARD="/tmp/awakening-m4-pin-agent-model.sh"
APPROVED_MODEL="qwen3.7-flash-2026-07-15"

if [ ! -f "${RUNTIME_ENV}" ] || [ ! -f "${UPSTREAM}" ]; then
    echo "M4_MANAGER_RUNTIME_INPUT_MISSING" >&2
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

cat > "${MODEL_GUARD}" <<'M4_MODEL_GUARD'
#!/bin/bash
set -euo pipefail

config="$1"
approved_model="qwen3.7-flash-2026-07-15"
target="hiclaw-gateway/${approved_model}"
test "${config}" = "/root/manager-workspace/openclaw.json"
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
  and (.models.providers["hiclaw-gateway"].models | type) == "array"
  and (.models.providers["hiclaw-gateway"].models | length) >= 1
' "${config}" >/dev/null

tmp="${config}.awakening-m4-preexec-model.tmp"
test ! -e "${tmp}"
mode="$(stat -c '%a' "${config}")"
owner="$(stat -c '%u:%g' "${config}")"
before_other="$(jq -cS 'del(.agents.defaults.model,.agents.defaults.models,.models.providers["hiclaw-gateway"].models)' "${config}" | sha256sum | cut -d' ' -f1)"

umask 077
jq --arg model "${approved_model}" --arg target "${target}" '
  (.models.providers["hiclaw-gateway"].models
    | (map(select(.id == $model))[0]
       // map(select(.id == "gpt-5-mini"))[0]
       // .[0])) as $model_record
  | (.agents.defaults.models[$target] // {"alias": $model}) as $alias_record
  | .agents.defaults.model.primary = $target
  | .agents.defaults.models = {($target): $alias_record}
  | .models.providers["hiclaw-gateway"].models = [
      ($model_record | .id = $model | .name = $model)
    ]
' "${config}" > "${tmp}"

jq -e --arg model "${approved_model}" --arg target "${target}" '
  .agents.defaults.model.primary == $target
  and ((.agents.defaults.models | keys) == [$target])
  and (.models.providers["hiclaw-gateway"].models | length) == 1
  and .models.providers["hiclaw-gateway"].models[0].id == $model
  and .models.providers["hiclaw-gateway"].models[0].name == $model
' "${tmp}" >/dev/null
after_other="$(jq -cS 'del(.agents.defaults.model,.agents.defaults.models,.models.providers["hiclaw-gateway"].models)' "${tmp}" | sha256sum | cut -d' ' -f1)"
test "${before_other}" = "${after_other}"

chown "${owner}" "${tmp}"
chmod "${mode}" "${tmp}"
mv -f -- "${tmp}" "${config}"
printf 'M4_AGENT_MODEL_PREEXEC=PASS|%s\n' "${config}"
M4_MODEL_GUARD
chmod 700 "${MODEL_GUARD}"
export HICLAW_DEFAULT_MODEL="${APPROVED_MODEL}"

cp "${UPSTREAM}" "${PATCHED}"
sed -i \
    -e 's/log "ERROR: Login response was: ${_LOGIN_RESPONSE}"/log "ERROR: Matrix login failed (response redacted)"/' \
    -e 's/log "Matrix login response: ${_LOGIN_RESPONSE}"/log "Matrix login response redacted"/' \
    -e 's/log "Manager Matrix token obtained (token prefix: ${MANAGER_TOKEN:0:10}\.\.\.)"/log "Manager Matrix token obtained (redacted)"/' \
    -e 's/log "ERROR: Matrix token was not written correctly to openclaw.json (got: ${_written_token})"/log "ERROR: Matrix token was not written correctly to openclaw.json (value redacted)"/' \
    -e 's/log "Matrix token written to openclaw.json (prefix: ${_written_token:0:10}\.\.\.)"/log "Matrix token written to openclaw.json (redacted)"/' \
    -e 's/log "Matrix token written from template (prefix: ${_written_token:0:10}\.\.\.)"/log "Matrix token written from template (redacted)"/' \
    "${PATCHED}"

if [ "$(grep -Fc 'log "Starting Manager Agent (${MANAGER_RUNTIME})..."' "${PATCHED}")" -ne 1 ] || \
   [ "$(grep -Fc 'mc cp "${HICLAW_STORAGE_PREFIX}/manager/openclaw.json" /root/manager-workspace/openclaw.json 2>/dev/null || true' "${PATCHED}")" -ne 2 ]; then
    echo "M4_MANAGER_MODEL_PREEXEC_SENTINEL_INVALID" >&2
    exit 78
fi

sed -i \
    -e '/^[[:space:]]*log "Starting Manager Agent (${MANAGER_RUNTIME})\.\.\."$/i\/bin/bash /tmp/awakening-m4-pin-agent-model.sh /root/manager-workspace/openclaw.json' \
    -e '/^[[:space:]]*mc cp "${HICLAW_STORAGE_PREFIX}\/manager\/openclaw.json" \/root\/manager-workspace\/openclaw.json 2>\/dev\/null || true$/a\            /bin/bash /tmp/awakening-m4-pin-agent-model.sh /root/manager-workspace/openclaw.json' \
    "${PATCHED}"

if grep -Eq 'token prefix:|Login response was:|Matrix login response: \$\{_LOGIN_RESPONSE\}|got: \$\{_written_token\}|prefix: \$\{_written_token' "${PATCHED}"; then
    echo "M4_MANAGER_TOKEN_LOG_SANITIZATION_FAILED" >&2
    exit 78
fi
if [ "$(grep -Fc '/bin/bash /tmp/awakening-m4-pin-agent-model.sh /root/manager-workspace/openclaw.json' "${PATCHED}")" -ne 3 ]; then
    echo "M4_MANAGER_MODEL_PREEXEC_INJECTION_FAILED" >&2
    exit 78
fi

chmod 700 "${PATCHED}"
exec /bin/bash "${PATCHED}"
