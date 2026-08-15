#!/usr/bin/env bash
set -euo pipefail

umask 077

readonly MATRIX_BASE_URL="http://awakening-m4-controller:6167"
readonly MATRIX_SERVER_NAME="matrix-m4.local:8080"
readonly MANAGER_MATRIX_USER_ID="@manager:${MATRIX_SERVER_NAME}"
readonly MANAGER_WORKSPACE="/root/manager-workspace"
readonly OPENCLAW_CONFIG_1="${MANAGER_WORKSPACE}/.openclaw/openclaw.json"
readonly OPENCLAW_CONFIG_2="${MANAGER_WORKSPACE}/openclaw.json"
readonly STATE_DIR="/run/awakening-demo/matrix-control-v1"
readonly MAX_MATRIX_RESPONSE_BYTES=1048576

AUTH_HEADER_FILE=""
WORK_DIR=""

cleanup() {
    local item
    AUTH_HEADER_FILE=""
    if [[ -n "${WORK_DIR}" && "${WORK_DIR}" == "${STATE_DIR}/.work."* && -d "${WORK_DIR}" ]]; then
        for item in "${WORK_DIR}"/*; do
            [[ -e "${item}" && ! -d "${item}" ]] && rm -f -- "${item}"
        done
        rmdir -- "${WORK_DIR}" 2>/dev/null || true
    fi
    WORK_DIR=""
}
trap cleanup EXIT HUP INT TERM

fail() {
    printf '%s\n' "$1" >&2
    exit 78
}

require_tools() {
    local tool
    for tool in curl date grep install jq mktemp realpath sha256sum stat wc; do
        command -v "${tool}" >/dev/null 2>&1 \
            || fail "DEMO_MATRIX_DEPENDENCY_MISSING"
    done
}

new_temp_file() {
    mktemp "${WORK_DIR}/temp.XXXXXX" \
        || fail "DEMO_MATRIX_TEMP_FILE_FAILED"
}

resolve_manager_config() {
    local candidate resolved="" current existing duplicate
    local -a unique_resolved=()
    for candidate in "${OPENCLAW_CONFIG_1}" "${OPENCLAW_CONFIG_2}"; do
        if [[ -f "${candidate}" && ! -L "${candidate}" ]]; then
            current="$(realpath -e -- "${candidate}" 2>/dev/null)" \
                || fail "DEMO_MATRIX_CONFIG_INVALID"
            [[ "${current}" == "${MANAGER_WORKSPACE}/"* ]] \
                || fail "DEMO_MATRIX_CONFIG_OUTSIDE_WORKSPACE"
            [[ "${current##*/}" == "openclaw.json" ]] \
                || fail "DEMO_MATRIX_CONFIG_INVALID"
            duplicate=false
            for existing in "${unique_resolved[@]}"; do
                if [[ "${existing}" == "${current}" ]]; then
                    duplicate=true
                    break
                fi
            done
            if [[ "${duplicate}" == false ]]; then
                unique_resolved+=("${current}")
                resolved="${current}"
            fi
        fi
    done
    [[ ${#unique_resolved[@]} -eq 1 ]] \
        || fail "DEMO_MATRIX_CONFIG_COUNT_INVALID"
    printf '%s' "${resolved}"
}

prepare_auth_header() {
    local config mode token
    config="$(resolve_manager_config)"
    mode="$(stat -c '%a' -- "${config}" 2>/dev/null)" \
        || fail "DEMO_MATRIX_CONFIG_MODE_UNAVAILABLE"
    [[ "${mode}" =~ ^[0-7]{3,4}$ ]] \
        || fail "DEMO_MATRIX_CONFIG_MODE_INVALID"
    if (( (8#${mode} & 8#077) != 0 )); then
        fail "DEMO_MATRIX_CONFIG_NOT_PRIVATE"
    fi
    token="$(jq -er '
        [
          .channels.matrix.accessToken?,
          .channels.matrix.access_token?,
          .channels.matrix.token?,
          .channels.matrix.config.accessToken?,
          .channels.matrix.config.access_token?,
          .channels.matrix.accounts.default.accessToken?,
          .channels.matrix.accounts.default.access_token?,
          .plugins.entries.matrix.config.accessToken?,
          .plugins.entries.matrix.config.access_token?
        ]
        | map(select(type == "string" and length >= 20 and length <= 4096))
        | unique
        | if length == 1 then .[0] else error("token_count") end
    ' "${config}" 2>/dev/null)" || fail "DEMO_MATRIX_TOKEN_UNAVAILABLE"
    [[ "${token}" =~ ^[A-Za-z0-9._~+/-]{20,4096}$ ]] \
        || fail "DEMO_MATRIX_TOKEN_FORMAT_INVALID"
    AUTH_HEADER_FILE="${WORK_DIR}/auth-header"
    printf 'Authorization: Bearer %s\n' "${token}" > "${AUTH_HEADER_FILE}" \
        || fail "DEMO_MATRIX_AUTH_HEADER_WRITE_FAILED"
    chmod 600 "${AUTH_HEADER_FILE}" \
        || fail "DEMO_MATRIX_AUTH_HEADER_WRITE_FAILED"
    token=""
    unset token
}

validate_room_id() {
    [[ "$1" =~ ^\![A-Za-z0-9._~+/-]+:matrix-m4\.local:8080$ ]] \
        || fail "DEMO_MATRIX_ROOM_ID_INVALID"
}

validate_user_id() {
    [[ "$1" =~ ^@[A-Za-z0-9._=-]+:matrix-m4\.local:8080$ ]] \
        || fail "DEMO_MATRIX_USER_ID_INVALID"
}

validate_human_user_id() {
    local value="$1"
    validate_user_id "${value}"
    case "${value}" in
        "${MANAGER_MATRIX_USER_ID}"|\
        "@role_project_architect:${MATRIX_SERVER_NAME}"|\
        "@execution_evidence_coach:${MATRIX_SERVER_NAME}"|\
        "@independent_quality_reviewer:${MATRIX_SERVER_NAME}")
            fail "DEMO_MATRIX_HUMAN_USER_ID_DENIED"
            ;;
    esac
}

validate_peer_user_id() {
    case "$1" in
        none|\
        "@role_project_architect:${MATRIX_SERVER_NAME}"|\
        "@execution_evidence_coach:${MATRIX_SERVER_NAME}"|\
        "@independent_quality_reviewer:${MATRIX_SERVER_NAME}") ;;
        *) fail "DEMO_MATRIX_CONTROL_PEER_DENIED" ;;
    esac
}

validate_event_id() {
    [[ "$1" =~ ^\$[A-Za-z0-9._~+:/=-]{1,255}$ ]] \
        || fail "DEMO_MATRIX_EVENT_ID_INVALID"
}

validate_uuid_v4() {
    [[ "$1" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$ ]] \
        || fail "DEMO_MATRIX_UUID_INVALID"
}

validate_sha256() {
    [[ "$1" =~ ^[0-9a-f]{64}$ ]] \
        || fail "DEMO_MATRIX_SHA256_INVALID"
}

validate_sync_token() {
    [[ -n "$1" && "$1" != *[[:space:]]* && ${#1} -le 4096 ]] \
        || fail "DEMO_MATRIX_SYNC_TOKEN_INVALID"
}

uri_encode() {
    jq -nr --arg value "$1" '$value | @uri' 2>/dev/null \
        || fail "DEMO_MATRIX_URI_ENCODING_FAILED"
}

reject_sensitive_text() {
    local text="$1"
    if grep -Eqi \
        '(access[_ -]?token|api[_ -]?key|password|secret|authorization[[:space:]]*:|bearer[[:space:]]+[A-Za-z0-9._~-]{12,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|HICLAW_|WORKER_|"channels"[[:space:]]*:|"plugins"[[:space:]]*:)' \
        <<<"${text}"; then
        fail "DEMO_MATRIX_SENSITIVE_TEXT_DENIED"
    fi
}

matrix_get() {
    local path="$1" output_file="$2" status size
    status="$(curl --silent --output "${output_file}" --write-out '%{http_code}' \
        --noproxy '*' --connect-timeout 3 --max-time 10 \
        --header "@${AUTH_HEADER_FILE}" \
        "${MATRIX_BASE_URL}${path}" 2>/dev/null)" \
        || fail "DEMO_MATRIX_LOCAL_API_UNAVAILABLE"
    [[ "${status}" == "200" ]] || fail "DEMO_MATRIX_LOCAL_API_FAILED"
    size="$(wc -c < "${output_file}")"
    [[ "${size}" =~ ^[0-9]+$ && "${size}" -ge 2 && "${size}" -le ${MAX_MATRIX_RESPONSE_BYTES} ]] \
        || fail "DEMO_MATRIX_RESPONSE_SIZE_INVALID"
}

expected_members_json() {
    local human_user_id="$1" peer_user_id="$2"
    if [[ "${peer_user_id}" == "none" ]]; then
        jq -cn \
            --arg human "${human_user_id}" \
            --arg manager "${MANAGER_MATRIX_USER_ID}" \
            '[$human,$manager] | sort'
    else
        jq -cn \
            --arg human "${human_user_id}" \
            --arg manager "${MANAGER_MATRIX_USER_ID}" \
            --arg peer "${peer_user_id}" \
            '[$human,$manager,$peer] | sort'
    fi
}

load_members_json() {
    local room_id="$1" room_uri output_file
    validate_room_id "${room_id}"
    room_uri="$(uri_encode "${room_id}")"
    output_file="$(new_temp_file)"
    matrix_get "/_matrix/client/v3/rooms/${room_uri}/joined_members" "${output_file}"
    jq -cer '
        select(type == "object" and (.joined | type) == "object")
        | .joined
        | keys
        | select(length >= 2 and length <= 16)
        | sort
    ' "${output_file}" 2>/dev/null \
        || fail "DEMO_MATRIX_JOINED_MEMBERS_INVALID"
}

assert_control_room() {
    local room_id="$1" human_user_id="$2" peer_user_id="$3"
    local expected actual
    validate_room_id "${room_id}"
    validate_human_user_id "${human_user_id}"
    validate_peer_user_id "${peer_user_id}"
    expected="$(expected_members_json "${human_user_id}" "${peer_user_id}")" \
        || fail "DEMO_MATRIX_EXPECTED_MEMBERS_BUILD_FAILED"
    actual="$(load_members_json "${room_id}")"
    [[ "${actual}" == "${expected}" ]] \
        || fail "DEMO_MATRIX_CONTROL_ROOM_MEMBERSHIP_INVALID"
    printf '%s' "${actual}" | sha256sum | jq -Rr 'split(" ")[0]' 2>/dev/null \
        || fail "DEMO_MATRIX_MEMBERSHIP_HASH_FAILED"
}

build_human_body() {
    local demo_request_id="$1" demo_run_id="$2"
    validate_uuid_v4 "${demo_request_id}"
    validate_uuid_v4 "${demo_run_id}"
    printf '%s' \
        "Awakening AgentTeams Demo | demo_request_id=${demo_request_id} | demo_run_id=${demo_run_id} | fixed synthetic job package | Manager coordinates Architect, Coach, Reviewer."
}

verify_human_event() {
    local room_id="$1" human_user_id="$2" human_event_id="$3"
    local expected_body="$4" expected_timestamp="${5:-}" room_uri event_uri output_file
    validate_room_id "${room_id}"
    validate_human_user_id "${human_user_id}"
    validate_event_id "${human_event_id}"
    reject_sensitive_text "${expected_body}"
    room_uri="$(uri_encode "${room_id}")"
    event_uri="$(uri_encode "${human_event_id}")"
    output_file="$(new_temp_file)"
    matrix_get "/_matrix/client/v3/rooms/${room_uri}/event/${event_uri}" "${output_file}"
    jq -e \
        --arg event_id "${human_event_id}" \
        --arg human "${human_user_id}" \
        --arg body "${expected_body}" \
        --arg timestamp "${expected_timestamp}" '
          type == "object"
          and .event_id == $event_id
          and .type == "m.room.message"
          and .sender == $human
          and (.origin_server_ts | type) == "number"
          and .origin_server_ts >= 0
          and (.origin_server_ts | floor) == .origin_server_ts
          and (
            $timestamp == ""
            or (.origin_server_ts | tostring) == $timestamp
          )
          and (.content | type) == "object"
          and (
            ((.content | keys | sort) == (["body","msgtype"] | sort))
            or ((.content | keys | sort) == (["body","m.mentions","msgtype"] | sort))
          )
          and .content.msgtype == "m.text"
          and .content.body == $body
          and ((.content."m.mentions"? // {}) == {})
        ' "${output_file}" >/dev/null 2>&1 \
        || fail "DEMO_MATRIX_HUMAN_EVENT_READBACK_INVALID"
}

discover_control_room() {
    local human_user_id="$1" peer_user_id="$2"
    local rooms_file room_count index room_id expected actual membership_sha256
    local -a candidates=()
    validate_human_user_id "${human_user_id}"
    validate_peer_user_id "${peer_user_id}"
    expected="$(expected_members_json "${human_user_id}" "${peer_user_id}")" \
        || fail "DEMO_MATRIX_EXPECTED_MEMBERS_BUILD_FAILED"
    rooms_file="$(new_temp_file)"
    matrix_get "/_matrix/client/v3/joined_rooms" "${rooms_file}"
    room_count="$(jq -er '
        .joined_rooms
        | select(type == "array" and length >= 1 and length <= 128)
        | length
    ' "${rooms_file}" 2>/dev/null)" \
        || fail "DEMO_MATRIX_JOINED_ROOMS_INVALID"
    for ((index = 0; index < room_count; index++)); do
        room_id="$(jq -er --argjson index "${index}" '.joined_rooms[$index]' "${rooms_file}" 2>/dev/null)" \
            || fail "DEMO_MATRIX_JOINED_ROOMS_INVALID"
        validate_room_id "${room_id}"
        actual="$(load_members_json "${room_id}")"
        if [[ "${actual}" == "${expected}" ]]; then
            candidates+=("${room_id}")
        fi
    done
    [[ ${#candidates[@]} -eq 1 ]] \
        || fail "DEMO_MATRIX_CONTROL_ROOM_COUNT_INVALID"
    membership_sha256="$(printf '%s' "${expected}" | sha256sum | jq -Rr 'split(" ")[0]' 2>/dev/null)" \
        || fail "DEMO_MATRIX_MEMBERSHIP_HASH_FAILED"
    validate_sha256 "${membership_sha256}"
    jq -cn \
        --arg command "discover" \
        --arg room_id "${candidates[0]}" \
        --arg human_user_id "${human_user_id}" \
        --arg manager_user_id "${MANAGER_MATRIX_USER_ID}" \
        --arg peer_user_id "${peer_user_id}" \
        --arg membership_sha256 "${membership_sha256}" \
        '{command:$command,room_id:$room_id,human_user_id:$human_user_id,manager_user_id:$manager_user_id,peer_user_id:$peer_user_id,membership_sha256:$membership_sha256}'
}

capture_baseline() {
    local room_id="$1" human_user_id="$2" peer_user_id="$3"
    local membership_sha256 output_file status size filter since
    membership_sha256="$(assert_control_room "${room_id}" "${human_user_id}" "${peer_user_id}")"
    validate_sha256 "${membership_sha256}"
    output_file="$(new_temp_file)"
    filter='{"room":{"timeline":{"limit":1}}}'
    status="$(curl --silent --output "${output_file}" --write-out '%{http_code}' \
        --noproxy '*' --connect-timeout 3 --max-time 10 \
        --header "@${AUTH_HEADER_FILE}" \
        --get --data-urlencode 'timeout=0' --data-urlencode "filter=${filter}" \
        "${MATRIX_BASE_URL}/_matrix/client/v3/sync" 2>/dev/null)" \
        || fail "DEMO_MATRIX_LOCAL_API_UNAVAILABLE"
    [[ "${status}" == "200" ]] || fail "DEMO_MATRIX_LOCAL_API_FAILED"
    size="$(wc -c < "${output_file}")"
    [[ "${size}" =~ ^[0-9]+$ && "${size}" -ge 2 && "${size}" -le ${MAX_MATRIX_RESPONSE_BYTES} ]] \
        || fail "DEMO_MATRIX_RESPONSE_SIZE_INVALID"
    since="$(jq -er '.next_batch | select(type == "string" and length >= 1 and length <= 4096)' \
        "${output_file}" 2>/dev/null)" \
        || fail "DEMO_MATRIX_SYNC_TOKEN_INVALID"
    validate_sync_token "${since}"
    jq -cn \
        --arg command "baseline" \
        --arg room_id "${room_id}" \
        --arg human_user_id "${human_user_id}" \
        --arg since "${since}" \
        --arg membership_sha256 "${membership_sha256}" \
        '{command:$command,room_id:$room_id,human_user_id:$human_user_id,since:$since,membership_sha256:$membership_sha256}'
}

await_human_request() {
    local room_id="$1" human_user_id="$2" peer_user_id="$3" since="$4"
    local demo_request_id="$5" demo_run_id="$6" timeout_seconds="$7"
    local expected_body body_sha256 membership_sha256 deadline now remaining wait_ms
    local output_file status size candidates_file candidate_count human_event_id origin_server_ts next_batch
    validate_sync_token "${since}"
    [[ "${timeout_seconds}" =~ ^[0-9]+$ ]] \
        || fail "DEMO_MATRIX_WAIT_TIMEOUT_INVALID"
    (( timeout_seconds >= 1 && timeout_seconds <= 900 )) \
        || fail "DEMO_MATRIX_WAIT_TIMEOUT_INVALID"
    membership_sha256="$(assert_control_room "${room_id}" "${human_user_id}" "${peer_user_id}")"
    validate_sha256 "${membership_sha256}"
    expected_body="$(build_human_body "${demo_request_id}" "${demo_run_id}")"
    reject_sensitive_text "${expected_body}"
    body_sha256="$(printf '%s' "${expected_body}" | sha256sum | jq -Rr 'split(" ")[0]' 2>/dev/null)" \
        || fail "DEMO_MATRIX_BODY_HASH_FAILED"
    validate_sha256 "${body_sha256}"
    deadline=$(( $(date +%s) + timeout_seconds ))

    while true; do
        now="$(date +%s)"
        remaining=$((deadline - now))
        (( remaining > 0 )) || fail "DEMO_MATRIX_HUMAN_REQUEST_TIMEOUT"
        if (( remaining > 5 )); then wait_ms=5000; else wait_ms=$((remaining * 1000)); fi
        output_file="$(new_temp_file)"
        status="$(curl --silent --output "${output_file}" --write-out '%{http_code}' \
            --noproxy '*' --connect-timeout 3 --max-time "$((remaining + 3))" \
            --header "@${AUTH_HEADER_FILE}" \
            --get --data-urlencode "since=${since}" --data-urlencode "timeout=${wait_ms}" \
            --data-urlencode 'filter={"room":{"timeline":{"limit":50}}}' \
            "${MATRIX_BASE_URL}/_matrix/client/v3/sync" 2>/dev/null)" \
            || fail "DEMO_MATRIX_LOCAL_API_UNAVAILABLE"
        [[ "${status}" == "200" ]] || fail "DEMO_MATRIX_LOCAL_API_FAILED"
        size="$(wc -c < "${output_file}")"
        [[ "${size}" =~ ^[0-9]+$ && "${size}" -ge 2 && "${size}" -le ${MAX_MATRIX_RESPONSE_BYTES} ]] \
            || fail "DEMO_MATRIX_RESPONSE_SIZE_INVALID"
        if jq -e --arg room "${room_id}" \
            '.rooms.join[$room].timeline.limited? == true' "${output_file}" >/dev/null 2>&1; then
            fail "DEMO_MATRIX_TIMELINE_LIMITED"
        fi
        candidates_file="$(new_temp_file)"
        jq -ce \
            --arg room "${room_id}" \
            --arg human "${human_user_id}" \
            --arg body "${expected_body}" '
              [
                .rooms.join[$room].timeline.events[]?
                | select(
                    .type == "m.room.message"
                    and .sender == $human
                    and (.origin_server_ts | type) == "number"
                    and .origin_server_ts >= 0
                    and (.origin_server_ts | floor) == .origin_server_ts
                    and (.content | type) == "object"
                    and (
                      ((.content | keys | sort) == (["body","msgtype"] | sort))
                      or ((.content | keys | sort) == (["body","m.mentions","msgtype"] | sort))
                    )
                    and .content.msgtype == "m.text"
                    and .content.body == $body
                    and ((.content."m.mentions"? // {}) == {})
                  )
                | {event_id:.event_id,origin_server_ts:.origin_server_ts}
              ]
            ' "${output_file}" > "${candidates_file}" 2>/dev/null \
            || fail "DEMO_MATRIX_HUMAN_REQUEST_INVALID"
        candidate_count="$(jq -er 'length' "${candidates_file}" 2>/dev/null)" \
            || fail "DEMO_MATRIX_HUMAN_REQUEST_INVALID"
        [[ "${candidate_count}" =~ ^[0-9]+$ ]] \
            || fail "DEMO_MATRIX_HUMAN_REQUEST_INVALID"
        (( candidate_count <= 1 )) \
            || fail "DEMO_MATRIX_HUMAN_REQUEST_AMBIGUOUS"
        if (( candidate_count == 1 )); then
            human_event_id="$(jq -er '.[0].event_id' "${candidates_file}" 2>/dev/null)" \
                || fail "DEMO_MATRIX_HUMAN_REQUEST_INVALID"
            origin_server_ts="$(jq -er '.[0].origin_server_ts | tostring' "${candidates_file}" 2>/dev/null)" \
                || fail "DEMO_MATRIX_HUMAN_REQUEST_INVALID"
            validate_event_id "${human_event_id}"
            [[ "${origin_server_ts}" =~ ^[0-9]+$ ]] \
                || fail "DEMO_MATRIX_HUMAN_REQUEST_INVALID"
            verify_human_event \
                "${room_id}" "${human_user_id}" "${human_event_id}" \
                "${expected_body}" "${origin_server_ts}"
            jq -cn \
                --arg command "await-human-request" \
                --arg room_id "${room_id}" \
                --arg human_user_id "${human_user_id}" \
                --arg human_event_id "${human_event_id}" \
                --arg demo_request_id "${demo_request_id}" \
                --arg demo_run_id "${demo_run_id}" \
                --arg body_sha256 "${body_sha256}" \
                --arg membership_sha256 "${membership_sha256}" \
                --argjson origin_server_ts "${origin_server_ts}" \
                '{command:$command,room_id:$room_id,human_user_id:$human_user_id,human_event_id:$human_event_id,demo_request_id:$demo_request_id,demo_run_id:$demo_run_id,body_sha256:$body_sha256,membership_sha256:$membership_sha256,origin_server_ts:$origin_server_ts}'
            return 0
        fi
        next_batch="$(jq -er '.next_batch | select(type == "string" and length >= 1 and length <= 4096)' \
            "${output_file}" 2>/dev/null)" \
            || fail "DEMO_MATRIX_SYNC_TOKEN_INVALID"
        validate_sync_token "${next_batch}"
        since="${next_batch}"
    done
}

validate_publish_binding() {
    local phase="$1" target="$2" evidence_event_id="$3" human_event_id="$4"
    case "${phase}" in
        request-accepted)
            [[ "${target}" == "manager" && "${evidence_event_id}" == "${human_event_id}" ]] \
                || fail "DEMO_MATRIX_PUBLISH_BINDING_INVALID"
            ;;
        worker-dispatched|worker-completed)
            case "${target}" in
                role_project_architect|execution_evidence_coach|independent_quality_reviewer) ;;
                *) fail "DEMO_MATRIX_PUBLISH_BINDING_INVALID" ;;
            esac
            [[ "${evidence_event_id}" != "none" ]] \
                || fail "DEMO_MATRIX_PUBLISH_BINDING_INVALID"
            validate_event_id "${evidence_event_id}"
            ;;
        summary-completed|summary-failed)
            [[ "${target}" == "all" && "${evidence_event_id}" == "none" ]] \
                || fail "DEMO_MATRIX_PUBLISH_BINDING_INVALID"
            ;;
        runtime-stopping)
            [[ "${target}" == "manager" && "${evidence_event_id}" == "none" ]] \
                || fail "DEMO_MATRIX_PUBLISH_BINDING_INVALID"
            ;;
        *) fail "DEMO_MATRIX_PHASE_DENIED" ;;
    esac
}

build_publish_body() {
    local demo_request_id="$1" demo_run_id="$2" phase="$3" target="$4"
    local evidence_event_id="$5" evidence_sha256="$6"
    printf '%s' \
        "Awakening AgentTeams Demo | demo_request_id=${demo_request_id} | demo_run_id=${demo_run_id} | phase=${phase} | target=${target} | evidence_event_id=${evidence_event_id} | evidence_sha256=${evidence_sha256} | synthetic demo only; not M5 acceptance."
}

publish_event() {
    local room_id="$1" human_user_id="$2" peer_user_id="$3" human_event_id="$4"
    local demo_request_id="$5" demo_run_id="$6" phase="$7" target="$8"
    local evidence_event_id="$9" evidence_sha256="${10}"
    local expected_human_body membership_sha256 body body_sha256 content_file room_uri transaction_id
    local response_file status event_id event_uri event_file origin_server_ts phase_txn target_txn
    validate_event_id "${human_event_id}"
    validate_uuid_v4 "${demo_request_id}"
    validate_uuid_v4 "${demo_run_id}"
    validate_sha256 "${evidence_sha256}"
    validate_publish_binding "${phase}" "${target}" "${evidence_event_id}" "${human_event_id}"
    membership_sha256="$(assert_control_room "${room_id}" "${human_user_id}" "${peer_user_id}")"
    validate_sha256 "${membership_sha256}"
    expected_human_body="$(build_human_body "${demo_request_id}" "${demo_run_id}")"
    verify_human_event \
        "${room_id}" "${human_user_id}" "${human_event_id}" \
        "${expected_human_body}"
    body="$(build_publish_body \
        "${demo_request_id}" "${demo_run_id}" "${phase}" "${target}" \
        "${evidence_event_id}" "${evidence_sha256}")"
    [[ $(printf '%s' "${body}" | wc -c) -le 2048 ]] \
        || fail "DEMO_MATRIX_PUBLISH_BODY_TOO_LARGE"
    reject_sensitive_text "${body}"
    body_sha256="$(printf '%s' "${body}" | sha256sum | jq -Rr 'split(" ")[0]' 2>/dev/null)" \
        || fail "DEMO_MATRIX_BODY_HASH_FAILED"
    validate_sha256 "${body_sha256}"
    content_file="$(new_temp_file)"
    jq -cn \
        --arg body "${body}" \
        --arg parent "${human_event_id}" '
          {
            msgtype:"m.notice",
            body:$body,
            "m.relates_to":{"m.in_reply_to":{event_id:$parent}}
          }
        ' > "${content_file}" 2>/dev/null \
        || fail "DEMO_MATRIX_PUBLISH_CONTENT_BUILD_FAILED"
    jq -e \
        --arg body "${body}" \
        --arg parent "${human_event_id}" '
          type == "object"
          and ((keys | sort) == (["body","m.relates_to","msgtype"] | sort))
          and .msgtype == "m.notice"
          and .body == $body
          and ."m.relates_to" == {"m.in_reply_to":{event_id:$parent}}
        ' "${content_file}" >/dev/null 2>&1 \
        || fail "DEMO_MATRIX_PUBLISH_CONTENT_INVALID"
    room_uri="$(uri_encode "${room_id}")"
    phase_txn="${phase//-/_}"
    target_txn="${target//-/_}"
    transaction_id="demo_${demo_request_id//-/}_${phase_txn}_${target_txn}"
    [[ "${transaction_id}" =~ ^[A-Za-z0-9._~-]{1,255}$ ]] \
        || fail "DEMO_MATRIX_TRANSACTION_ID_INVALID"
    response_file="$(new_temp_file)"
    status="$(curl --silent --output "${response_file}" --write-out '%{http_code}' \
        --noproxy '*' --connect-timeout 3 --max-time 10 \
        --header "@${AUTH_HEADER_FILE}" --header 'Content-Type: application/json' \
        --request PUT --data-binary "@${content_file}" \
        "${MATRIX_BASE_URL}/_matrix/client/v3/rooms/${room_uri}/send/m.room.message/${transaction_id}" 2>/dev/null)" \
        || fail "DEMO_MATRIX_LOCAL_API_UNAVAILABLE"
    [[ "${status}" == "200" ]] || fail "DEMO_MATRIX_LOCAL_API_FAILED"
    event_id="$(jq -er '.event_id | select(type == "string")' "${response_file}" 2>/dev/null)" \
        || fail "DEMO_MATRIX_EVENT_ID_INVALID"
    validate_event_id "${event_id}"
    event_uri="$(uri_encode "${event_id}")"
    event_file="$(new_temp_file)"
    matrix_get "/_matrix/client/v3/rooms/${room_uri}/event/${event_uri}" "${event_file}"
    jq -e \
        --arg event_id "${event_id}" \
        --arg manager "${MANAGER_MATRIX_USER_ID}" \
        --arg body "${body}" \
        --arg parent "${human_event_id}" '
          type == "object"
          and .event_id == $event_id
          and .type == "m.room.message"
          and .sender == $manager
          and (.origin_server_ts | type) == "number"
          and .origin_server_ts >= 0
          and (.origin_server_ts | floor) == .origin_server_ts
          and (.content | type) == "object"
          and ((.content | keys | sort) == (["body","m.relates_to","msgtype"] | sort))
          and .content.msgtype == "m.notice"
          and .content.body == $body
          and .content."m.relates_to" == {"m.in_reply_to":{event_id:$parent}}
        ' "${event_file}" >/dev/null 2>&1 \
        || fail "DEMO_MATRIX_PUBLISH_READBACK_INVALID"
    origin_server_ts="$(jq -er '.origin_server_ts | tostring' "${event_file}" 2>/dev/null)" \
        || fail "DEMO_MATRIX_PUBLISH_READBACK_INVALID"
    [[ "${origin_server_ts}" =~ ^[0-9]+$ ]] \
        || fail "DEMO_MATRIX_PUBLISH_READBACK_INVALID"
    jq -cn \
        --arg command "publish-event" \
        --arg room_id "${room_id}" \
        --arg event_id "${event_id}" \
        --arg parent_event_id "${human_event_id}" \
        --arg demo_request_id "${demo_request_id}" \
        --arg demo_run_id "${demo_run_id}" \
        --arg phase "${phase}" \
        --arg target "${target}" \
        --arg evidence_event_id "${evidence_event_id}" \
        --arg evidence_sha256 "${evidence_sha256}" \
        --arg body_sha256 "${body_sha256}" \
        --arg membership_sha256 "${membership_sha256}" \
        --argjson origin_server_ts "${origin_server_ts}" \
        '{command:$command,room_id:$room_id,event_id:$event_id,parent_event_id:$parent_event_id,demo_request_id:$demo_request_id,demo_run_id:$demo_run_id,phase:$phase,target:$target,evidence_event_id:$evidence_event_id,evidence_sha256:$evidence_sha256,body_sha256:$body_sha256,membership_sha256:$membership_sha256,origin_server_ts:$origin_server_ts}'
}

main() {
    require_tools
    install -d -m 700 "${STATE_DIR}" \
        || fail "DEMO_MATRIX_STATE_DIRECTORY_INVALID"
    [[ ! -L "${STATE_DIR}" ]] \
        || fail "DEMO_MATRIX_STATE_DIRECTORY_INVALID"
    [[ "$(realpath -e -- "${STATE_DIR}" 2>/dev/null)" == "${STATE_DIR}" ]] \
        || fail "DEMO_MATRIX_STATE_DIRECTORY_INVALID"
    [[ "$(stat -c '%u:%g|%a' -- "${STATE_DIR}" 2>/dev/null)" == "0:0|700" ]] \
        || fail "DEMO_MATRIX_STATE_DIRECTORY_INVALID"
    WORK_DIR="$(mktemp -d "${STATE_DIR}/.work.XXXXXX")" \
        || fail "DEMO_MATRIX_WORK_DIRECTORY_INVALID"
    [[ "${WORK_DIR}" == "${STATE_DIR}/.work."* ]] \
        || fail "DEMO_MATRIX_WORK_DIRECTORY_INVALID"
    chmod 700 "${WORK_DIR}" \
        || fail "DEMO_MATRIX_WORK_DIRECTORY_INVALID"
    prepare_auth_header

    case "${1:-}" in
        discover)
            [[ $# -eq 3 ]] || fail "DEMO_MATRIX_ARGUMENT_COUNT_INVALID"
            discover_control_room "$2" "$3"
            ;;
        baseline)
            [[ $# -eq 4 ]] || fail "DEMO_MATRIX_ARGUMENT_COUNT_INVALID"
            capture_baseline "$2" "$3" "$4"
            ;;
        await-human-request)
            [[ $# -eq 8 ]] || fail "DEMO_MATRIX_ARGUMENT_COUNT_INVALID"
            await_human_request "$2" "$3" "$4" "$5" "$6" "$7" "$8"
            ;;
        publish-event)
            [[ $# -eq 11 ]] || fail "DEMO_MATRIX_ARGUMENT_COUNT_INVALID"
            publish_event "$2" "$3" "$4" "$5" "$6" "$7" "$8" "$9" "${10}" "${11}"
            ;;
        *) fail "DEMO_MATRIX_COMMAND_DENIED" ;;
    esac
}

main "$@"
