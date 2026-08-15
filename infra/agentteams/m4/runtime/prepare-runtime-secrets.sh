#!/bin/bash
set -euo pipefail

umask 077

OUTPUT_DIR="/data/m4-runtime-secrets-v1"
KUBE_API="https://127.0.0.1:6443"
KUBE_CA="/data/hiclaw-controller/pki/ca.crt"
KUBE_ADMIN_TOKEN_FILE="/data/hiclaw-controller/admin-token"
CREDENTIAL_DIR="/data/worker-creds"
TOKEN_AUDIENCE="hiclaw-controller"
TOKEN_TTL_SECONDS=7200
MANAGER_MODEL="qwen3.7-flash-2026-07-15"
WORKER_MODEL="qwen3.7-flash-2026-07-15"

if [ -e "${OUTPUT_DIR}" ]; then
    echo "M4_RUNTIME_SECRET_TARGET_ALREADY_EXISTS" >&2
    exit 78
fi
for required in "${KUBE_CA}" "${KUBE_ADMIN_TOKEN_FILE}"; do
    if [ ! -s "${required}" ]; then
        echo "M4_RUNTIME_SECRET_SOURCE_MISSING" >&2
        exit 78
    fi
done

install -d -m 700 "${OUTPUT_DIR}"

write_pair() {
    local key="$1" value="$2" output="$3"
    printf '%s=%q\n' "${key}" "${value}" >> "${output}"
}

mint_sa_token() {
    local service_account="$1" output="$2" admin_token response token
    admin_token="$(<"${KUBE_ADMIN_TOKEN_FILE}")"
    response="$(curl --fail --silent --show-error \
        --cacert "${KUBE_CA}" \
        -H "Authorization: Bearer ${admin_token}" \
        -H 'Content-Type: application/json' \
        -X POST \
        "${KUBE_API}/api/v1/namespaces/default/serviceaccounts/${service_account}/token" \
        -d '{"apiVersion":"authentication.k8s.io/v1","kind":"TokenRequest","spec":{"audiences":["hiclaw-controller"],"expirationSeconds":7200}}')"
    token="$(printf '%s' "${response}" | jq -er '.status.token')"
    if [ "${#token}" -lt 64 ]; then
        echo "M4_RUNTIME_SA_TOKEN_INVALID:${service_account}" >&2
        exit 78
    fi
    printf '%s' "${token}" > "${output}"
    chmod 600 "${output}"
    admin_token=""
    response=""
    token=""
}

load_credentials() {
    local source_file="$1"
    unset WORKER_PASSWORD WORKER_MINIO_PASSWORD WORKER_GATEWAY_KEY WORKER_MATRIX_TOKEN
    # shellcheck disable=SC1090
    source "${source_file}"
    for required_name in WORKER_PASSWORD WORKER_MINIO_PASSWORD WORKER_GATEWAY_KEY WORKER_MATRIX_TOKEN; do
        if [ -z "${!required_name:-}" ]; then
            echo "M4_RUNTIME_CREDENTIAL_MISSING:${required_name}" >&2
            exit 78
        fi
    done
}

write_common_env() {
    local output="$1" default_model="$2"
    write_pair HICLAW_RUNTIME k8s "${output}"
    write_pair HICLAW_MATRIX_DOMAIN matrix-m4.local:8080 "${output}"
    write_pair HICLAW_MATRIX_URL http://awakening-m4-controller:6167 "${output}"
    write_pair HICLAW_FS_ENDPOINT http://awakening-m4-controller:9000 "${output}"
    write_pair HICLAW_FS_BUCKET hiclaw-storage "${output}"
    write_pair HICLAW_STORAGE_PREFIX hiclaw/hiclaw-storage "${output}"
    write_pair HICLAW_CONTROLLER_URL http://awakening-m4-controller:8090 "${output}"
    write_pair HICLAW_AI_GATEWAY_URL http://host.docker.internal:18190 "${output}"
    write_pair HICLAW_DEFAULT_MODEL "${default_model}" "${output}"
    write_pair HICLAW_AUTH_TOKEN_FILE /run/secrets/awakening-m4/sa-token "${output}"
    write_pair HICLAW_MATRIX_E2EE 0 "${output}"
    write_pair HICLAW_CMS_TRACES_ENABLED false "${output}"
    write_pair HICLAW_CMS_METRICS_ENABLED false "${output}"
    write_pair OPENCLAW_DISABLE_BONJOUR 1 "${output}"
    write_pair TZ Asia/Shanghai "${output}"
}

gateway_credentials="${OUTPUT_DIR}/gateway-credentials.env"
: > "${gateway_credentials}"

load_credentials "${CREDENTIAL_DIR}/default.env"
manager_env="${OUTPUT_DIR}/manager.env"
: > "${manager_env}"
write_common_env "${manager_env}" "${MANAGER_MODEL}"
write_pair HICLAW_MANAGER_NAME default "${manager_env}"
write_pair HICLAW_MANAGER_GATEWAY_KEY "${WORKER_GATEWAY_KEY}" "${manager_env}"
write_pair HICLAW_MANAGER_PASSWORD "${WORKER_PASSWORD}" "${manager_env}"
write_pair HICLAW_FS_ACCESS_KEY default "${manager_env}"
write_pair HICLAW_FS_SECRET_KEY "${WORKER_MINIO_PASSWORD}" "${manager_env}"
write_pair HICLAW_MANAGER_RUNTIME openclaw "${manager_env}"
write_pair HICLAW_ADMIN_USER awakening-m4-admin "${manager_env}"
write_pair HICLAW_MANAGER_HEARTBEAT_INTERVAL 0m "${manager_env}"
write_pair HICLAW_MANAGER_WORKER_IDLE_TIMEOUT 720m "${manager_env}"
write_pair HICLAW_MANAGER_NOTIFY_CHANNEL admin-dm "${manager_env}"
write_pair HICLAW_EMBEDDING_MODEL '' "${manager_env}"
write_pair OPENCLAW_MDNS_HOSTNAME hiclaw-manager "${manager_env}"
write_pair HOME /root/manager-workspace "${manager_env}"
printf 'AWAKENING_PROGRAM_MANAGER_B64=%s\n' "$(printf '%s' "${WORKER_GATEWAY_KEY}" | base64 | tr -d '\n')" >> "${gateway_credentials}"
mint_sa_token awakening-m4-manager "${OUTPUT_DIR}/manager.sa-token"

write_worker() {
    local credential_name="$1" worker_name="$2" cr_name="$3" short_name="$4"
    local output="${OUTPUT_DIR}/${short_name}.env"
    load_credentials "${CREDENTIAL_DIR}/${credential_name}.env"
    : > "${output}"
    write_common_env "${output}" "${WORKER_MODEL}"
    write_pair HICLAW_WORKER_NAME "${worker_name}" "${output}"
    write_pair HICLAW_WORKER_CR_NAME "${cr_name}" "${output}"
    write_pair HICLAW_WORKER_GATEWAY_KEY "${WORKER_GATEWAY_KEY}" "${output}"
    write_pair HICLAW_WORKER_MATRIX_TOKEN "${WORKER_MATRIX_TOKEN}" "${output}"
    write_pair HICLAW_FS_ACCESS_KEY "${worker_name}" "${output}"
    write_pair HICLAW_FS_SECRET_KEY "${WORKER_MINIO_PASSWORD}" "${output}"
    write_pair HICLAW_CONSOLE_PORT 8088 "${output}"
    write_pair OPENCLAW_MDNS_HOSTNAME "hiclaw-w-${worker_name}" "${output}"
    write_pair HOME "/root/hiclaw-fs/agents/${worker_name}" "${output}"
    printf '%s_B64=%s\n' "${short_name^^}" "$(printf '%s' "${WORKER_GATEWAY_KEY}" | base64 | tr -d '\n')" >> "${gateway_credentials}"
    mint_sa_token "awakening-m4-worker-${cr_name}" "${OUTPUT_DIR}/${short_name}.sa-token"
}

write_worker role-project-architect role_project_architect role-project-architect ROLE_PROJECT_ARCHITECT
write_worker execution-evidence-coach execution_evidence_coach execution-evidence-coach EXECUTION_EVIDENCE_COACH
write_worker independent-quality-reviewer independent_quality_reviewer independent-quality-reviewer INDEPENDENT_QUALITY_REVIEWER

chmod 600 "${OUTPUT_DIR}"/*
printf 'M4_RUNTIME_SECRET_PREPARE=PASS\n'
printf 'M4_RUNTIME_IDENTITY_COUNT=4\n'
printf 'M4_RUNTIME_SA_TOKEN_TTL_SECONDS=%s\n' "${TOKEN_TTL_SECONDS}"
