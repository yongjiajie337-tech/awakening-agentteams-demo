#!/bin/bash
set -euo pipefail

umask 077

OUTPUT_DIR="${1:-}"
KUBE_API="https://127.0.0.1:6443"
KUBE_CA="/data/hiclaw-controller/pki/ca.crt"
KUBE_ADMIN_TOKEN_FILE="/data/hiclaw-controller/admin-token"
TOKEN_AUDIENCE="hiclaw-controller"
TOKEN_TTL_SECONDS=7200
MIN_REMAINING_SECONDS=6900

case "${OUTPUT_DIR}" in
    /tmp/awakening-m4-sa-refresh-[0-9a-f]*) ;;
    *)
        echo "M4_RUNTIME_SA_REFRESH_PATH_INVALID" >&2
        exit 78
        ;;
esac

if [ -e "${OUTPUT_DIR}" ]; then
    echo "M4_RUNTIME_SA_REFRESH_TARGET_EXISTS" >&2
    exit 78
fi
for required in "${KUBE_CA}" "${KUBE_ADMIN_TOKEN_FILE}"; do
    if [ ! -s "${required}" ]; then
        echo "M4_RUNTIME_SA_REFRESH_SOURCE_MISSING" >&2
        exit 78
    fi
done

install -d -m 700 "${OUTPUT_DIR}"
success=0
cleanup_on_error() {
    if [ "${success}" -eq 1 ]; then
        return
    fi
    rm -f -- \
        "${OUTPUT_DIR}/manager.sa-token" \
        "${OUTPUT_DIR}/ROLE_PROJECT_ARCHITECT.sa-token" \
        "${OUTPUT_DIR}/EXECUTION_EVIDENCE_COACH.sa-token" \
        "${OUTPUT_DIR}/INDEPENDENT_QUALITY_REVIEWER.sa-token"
    rmdir -- "${OUTPUT_DIR}" 2>/dev/null || true
}
trap cleanup_on_error EXIT

mint_and_validate() {
    local service_account="$1" filename="$2" expected_subject="$3"
    local admin_token response token payload decoded sub aud exp now remaining

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
        echo "M4_RUNTIME_SA_REFRESH_TOKEN_INVALID:${service_account}" >&2
        exit 78
    fi

    payload="$(printf '%s' "${token}" | cut -d. -f2 | tr '_-' '/+')"
    case $((${#payload} % 4)) in
        2) payload="${payload}==" ;;
        3) payload="${payload}=" ;;
        1)
            echo "M4_RUNTIME_SA_REFRESH_PAYLOAD_INVALID:${service_account}" >&2
            exit 78
            ;;
    esac
    decoded="$(printf '%s' "${payload}" | base64 -d 2>/dev/null)"
    sub="$(printf '%s' "${decoded}" | jq -er '.sub')"
    aud="$(printf '%s' "${decoded}" | jq -er '.aud | if type == "array" then join(",") else . end')"
    exp="$(printf '%s' "${decoded}" | jq -er '.exp')"
    now="$(date +%s)"
    remaining=$((exp - now))
    if [ "${sub}" != "${expected_subject}" ] || \
       [ "${aud}" != "${TOKEN_AUDIENCE}" ] || \
       [ "${remaining}" -lt "${MIN_REMAINING_SECONDS}" ] || \
       [ "${remaining}" -gt "${TOKEN_TTL_SECONDS}" ]; then
        echo "M4_RUNTIME_SA_REFRESH_CLAIMS_INVALID:${service_account}" >&2
        exit 78
    fi

    printf '%s' "${token}" > "${OUTPUT_DIR}/${filename}"
    chmod 600 "${OUTPUT_DIR}/${filename}"
    printf 'M4_RUNTIME_SA_REFRESH_IDENTITY=%s|remaining=%s\n' \
        "${expected_subject}" "${remaining}"

    admin_token=""
    response=""
    token=""
    payload=""
    decoded=""
}

mint_and_validate \
    awakening-m4-manager \
    manager.sa-token \
    system:serviceaccount:default:awakening-m4-manager
mint_and_validate \
    awakening-m4-worker-role-project-architect \
    ROLE_PROJECT_ARCHITECT.sa-token \
    system:serviceaccount:default:awakening-m4-worker-role-project-architect
mint_and_validate \
    awakening-m4-worker-execution-evidence-coach \
    EXECUTION_EVIDENCE_COACH.sa-token \
    system:serviceaccount:default:awakening-m4-worker-execution-evidence-coach
mint_and_validate \
    awakening-m4-worker-independent-quality-reviewer \
    INDEPENDENT_QUALITY_REVIEWER.sa-token \
    system:serviceaccount:default:awakening-m4-worker-independent-quality-reviewer

success=1
printf 'M4_RUNTIME_SA_REFRESH_MINT=PASS\n'
printf 'M4_RUNTIME_SA_REFRESH_TOKEN_COUNT=4\n'
printf 'M4_RUNTIME_SA_REFRESH_TTL_SECONDS=%s\n' "${TOKEN_TTL_SECONDS}"
printf 'M4_RUNTIME_SA_REFRESH_SECRET_ECHOED=false\n'

