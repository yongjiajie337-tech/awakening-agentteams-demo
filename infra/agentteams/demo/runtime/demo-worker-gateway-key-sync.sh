#!/bin/bash
set -euo pipefail

fail() {
  printf 'DEMO_WORKER_GATEWAY_SYNC_%s\n' "$1" >&2
  exit 78
}

if [ "$#" -ne 2 ]; then
  fail ARGUMENTS_INVALID
fi

command_name="$1"
role="$2"
case "${command_name}" in
  inspect|apply|probe) ;;
  *) fail COMMAND_INVALID ;;
esac
case "${role}" in
  role_project_architect|independent_quality_reviewer) ;;
  *) fail ROLE_INVALID ;;
esac

config="/root/hiclaw-fs/agents/${role}/openclaw.json"
active="/root/hiclaw-fs/agents/${role}/.openclaw/openclaw.json"
runtime_env=/run/secrets/awakening-m4/runtime.env
tmp="${config}.awakening-demo-gateway-key-sync.tmp"

test -f "${config}" && test ! -L "${config}" || fail CONFIG_INVALID
test -L "${active}" || fail ACTIVE_CONFIG_LINK_INVALID
[ "$(readlink -f -- "${active}" 2>/dev/null || true)" = "${config}" ] || \
  fail ACTIVE_CONFIG_LINK_INVALID
test -f "${runtime_env}" && test ! -L "${runtime_env}" || fail RUNTIME_ENV_INVALID
test ! -e "${tmp}" || fail TEMP_TARGET_EXISTS

key_count="$(grep -c '^HICLAW_WORKER_GATEWAY_KEY=' "${runtime_env}" 2>/dev/null || true)"
[ "${key_count}" = 1 ] || fail RUNTIME_KEY_FIELD_INVALID
runtime_key="$(sed -n 's/^HICLAW_WORKER_GATEWAY_KEY=//p' "${runtime_env}")"
[[ "${runtime_key}" =~ ^[A-Za-z0-9_-]{43}$ ]] || fail RUNTIME_KEY_VALUE_INVALID

jq -e '
  (.models.providers["hiclaw-gateway"] | type) == "object"
  and (.models.providers["hiclaw-gateway"].apiKey | type) == "string"
' "${config}" >/dev/null 2>&1 || fail CONFIG_SCHEMA_INVALID
config_key="$(jq -er '.models.providers["hiclaw-gateway"].apiKey' "${config}" 2>/dev/null)" || \
  fail CONFIG_KEY_INVALID

matches=false
if [ "${config_key}" = "${runtime_key}" ]; then
  matches=true
fi

if [ "${command_name}" = inspect ]; then
  printf 'DEMO_WORKER_GATEWAY_SYNC=PASS\n'
  printf 'DEMO_WORKER_GATEWAY_SYNC_COMMAND=inspect\n'
  printf 'DEMO_WORKER_GATEWAY_SYNC_ROLE=%s\n' "${role}"
  printf 'DEMO_WORKER_GATEWAY_SYNC_MATCH=%s\n' "${matches}"
  printf 'DEMO_WORKER_GATEWAY_SYNC_SECRET_READ=true\n'
  printf 'DEMO_WORKER_GATEWAY_SYNC_SECRET_HASHED=false\n'
  printf 'DEMO_WORKER_GATEWAY_SYNC_SECRET_ECHOED=false\n'
  unset runtime_key config_key
elif [ "${command_name}" = apply ]; then
  mode="$(stat -c '%a' -- "${config}" 2>/dev/null)" || fail CONFIG_METADATA_INVALID
  owner="$(stat -c '%u:%g' -- "${config}" 2>/dev/null)" || fail CONFIG_METADATA_INVALID
  changed=false
  if [ "${matches}" != true ]; then
    umask 077
    trap 'rm -f -- "${tmp}" 2>/dev/null || true' EXIT
    jq --rawfile replacement <(printf '%s' "${runtime_key}") \
      '.models.providers["hiclaw-gateway"].apiKey = $replacement' \
      "${config}" > "${tmp}" 2>/dev/null || fail CONFIG_UPDATE_INVALID
    jq -e --rawfile replacement <(printf '%s' "${runtime_key}") '
      .models.providers["hiclaw-gateway"].apiKey == $replacement
    ' "${tmp}" >/dev/null 2>&1 || fail CONFIG_UPDATE_INVALID
    jq -e -s '
      ((.[0] | .models.providers["hiclaw-gateway"].apiKey = "__AWAKENING_DEMO_SENTINEL__")
       ==
       (.[1] | .models.providers["hiclaw-gateway"].apiKey = "__AWAKENING_DEMO_SENTINEL__"))
    ' "${config}" "${tmp}" >/dev/null 2>&1 || fail CONFIG_NON_TARGET_DRIFT
    chown "${owner}" "${tmp}" 2>/dev/null || fail CONFIG_METADATA_APPLY_INVALID
    chmod "${mode}" "${tmp}" 2>/dev/null || fail CONFIG_METADATA_APPLY_INVALID
    mv -f -- "${tmp}" "${config}" 2>/dev/null || fail CONFIG_PUBLISH_INVALID
    changed=true
  fi
  test -f "${config}" && test ! -L "${config}" || fail CONFIG_POSTVERIFY_INVALID
  post_mode="$(stat -c '%a' -- "${config}" 2>/dev/null)" || fail CONFIG_METADATA_POSTVERIFY_INVALID
  post_owner="$(stat -c '%u:%g' -- "${config}" 2>/dev/null)" || fail CONFIG_METADATA_POSTVERIFY_INVALID
  [ "${post_mode}" = "${mode}" ] && [ "${post_owner}" = "${owner}" ] || \
    fail CONFIG_METADATA_POSTVERIFY_INVALID
  [ "$(readlink -f -- "${active}" 2>/dev/null || true)" = "${config}" ] || \
    fail ACTIVE_CONFIG_POSTVERIFY_INVALID
  post_key="$(jq -er '.models.providers["hiclaw-gateway"].apiKey' "${active}" 2>/dev/null)" || \
    fail CONFIG_POSTVERIFY_INVALID
  [ "${post_key}" = "${runtime_key}" ] || fail CONFIG_POSTVERIFY_INVALID
  printf 'DEMO_WORKER_GATEWAY_SYNC=PASS\n'
  printf 'DEMO_WORKER_GATEWAY_SYNC_COMMAND=apply\n'
  printf 'DEMO_WORKER_GATEWAY_SYNC_ROLE=%s\n' "${role}"
  printf 'DEMO_WORKER_GATEWAY_SYNC_CHANGED=%s\n' "${changed}"
  printf 'DEMO_WORKER_GATEWAY_SYNC_SECRET_READ=true\n'
  printf 'DEMO_WORKER_GATEWAY_SYNC_SECRET_HASHED=false\n'
  printf 'DEMO_WORKER_GATEWAY_SYNC_SECRET_ECHOED=false\n'
  unset runtime_key config_key post_key mode owner post_mode post_owner
else
  [ "${matches}" = true ] || fail CONFIG_RUNTIME_BINDING_INVALID
  probe_body='{"model":"qwen3.7-flash-2026-07-15","messages":[{"role":"user","content":"demo fixed credential probe"}]}'
  response="$(curl -q --config <(
      printf 'header = "Authorization: Bearer %s"\n' "${config_key}"
    ) --connect-timeout 3 --max-time 10 -sS \
    -H 'Content-Type: application/json' --data-binary "${probe_body}" \
    -w '|%{http_code}' \
    http://host.docker.internal:18190/v1/chat/completions 2>/dev/null)" || \
    fail PROBE_TRANSPORT_INVALID
  status="${response##*|}"
  body="${response%|*}"
  reason="$(printf '%s' "${body}" | jq -er '.error.code' 2>/dev/null)" || \
    fail PROBE_RESPONSE_INVALID
  [ "${status}" = 403 ] && [ "${reason}" = CALL_PLAN_UNAVAILABLE ] || \
    fail PROBE_BOUNDARY_INVALID
  printf 'DEMO_WORKER_GATEWAY_SYNC=PASS\n'
  printf 'DEMO_WORKER_GATEWAY_SYNC_COMMAND=probe\n'
  printf 'DEMO_WORKER_GATEWAY_SYNC_ROLE=%s\n' "${role}"
  printf 'DEMO_WORKER_GATEWAY_SYNC_GATEWAY_AUTH=PASS\n'
  printf 'DEMO_WORKER_GATEWAY_SYNC_PROVIDER_CALL_COUNT=0\n'
  printf 'DEMO_WORKER_GATEWAY_SYNC_SECRET_READ=true\n'
  printf 'DEMO_WORKER_GATEWAY_SYNC_SECRET_HASHED=false\n'
  printf 'DEMO_WORKER_GATEWAY_SYNC_SECRET_ECHOED=false\n'
  unset runtime_key config_key response body reason
fi
