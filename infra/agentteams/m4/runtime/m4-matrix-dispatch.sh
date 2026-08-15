#!/usr/bin/env bash
set -euo pipefail

umask 077

readonly MATRIX_BASE_URL="http://awakening-m4-controller:6167"
readonly MATRIX_SERVER_NAME="matrix-m4.local:8080"
readonly MANAGER_MATRIX_USER_ID="@manager:${MATRIX_SERVER_NAME}"
readonly CONTROLLER_BASE_URL="http://awakening-m4-controller:8090"
readonly CONTROLLER_TOKEN_FILE="/run/secrets/awakening-m4/sa-token"
readonly MANAGER_WORKSPACE="/root/manager-workspace"
readonly DISPATCH_STATE_DIR="/run/awakening-m4/matrix-dispatch-v1"
readonly OPENCLAW_CONFIG_1="${MANAGER_WORKSPACE}/.openclaw/openclaw.json"
readonly OPENCLAW_CONFIG_2="${MANAGER_WORKSPACE}/openclaw.json"

AUTH_HEADER_FILE=""
CONTROLLER_AUTH_HEADER_FILE=""
WORK_DIR=""

cleanup() {
    local item
    AUTH_HEADER_FILE=""
    CONTROLLER_AUTH_HEADER_FILE=""
    if [[ -n "${WORK_DIR}" && "${WORK_DIR}" == "${DISPATCH_STATE_DIR}/.work."* && -d "${WORK_DIR}" ]]; then
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
    for tool in awk cat curl date grep jq sha256sum realpath install mktemp stat wc; do
        command -v "${tool}" >/dev/null 2>&1 || fail "M4_MATRIX_HELPER_DEPENDENCY_MISSING"
    done
}

resolve_single_file() {
    local required_basename="$1"
    shift
    local candidate resolved="" current existing duplicate
    local -a unique_resolved=()
    for candidate in "$@"; do
        if [[ -f "${candidate}" ]]; then
            current="$(realpath -e -- "${candidate}" 2>/dev/null)" || fail "M4_MATRIX_RUNTIME_FILE_INVALID"
            [[ "${current}" == "${MANAGER_WORKSPACE}/"* ]] || fail "M4_MATRIX_RUNTIME_FILE_OUTSIDE_WORKSPACE"
            [[ "${current##*/}" == "${required_basename}" ]] || fail "M4_MATRIX_RUNTIME_FILE_NAME_INVALID"
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
    [[ ${#unique_resolved[@]} -eq 1 ]] || fail "M4_MATRIX_RUNTIME_FILE_COUNT_INVALID"
    printf '%s' "${resolved}"
}

prepare_auth_header() {
    local config mode token
    config="$(resolve_single_file openclaw.json "${OPENCLAW_CONFIG_1}" "${OPENCLAW_CONFIG_2}")"
    mode="$(stat -c '%a' -- "${config}" 2>/dev/null)" || fail "M4_MATRIX_CONFIG_MODE_UNAVAILABLE"
    [[ "${mode}" =~ ^[0-7]{3,4}$ ]] || fail "M4_MATRIX_CONFIG_MODE_INVALID"
    if (( (8#${mode} & 8#077) != 0 )); then
        fail "M4_MATRIX_CONFIG_NOT_PRIVATE"
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
    ' "${config}" 2>/dev/null)" || fail "M4_MATRIX_TOKEN_UNAVAILABLE"
    [[ "${token}" =~ ^[A-Za-z0-9._~+/-]{20,4096}$ ]] || fail "M4_MATRIX_TOKEN_FORMAT_INVALID"
    AUTH_HEADER_FILE="${WORK_DIR}/auth-header"
    printf 'Authorization: Bearer %s\n' "${token}" > "${AUTH_HEADER_FILE}"
    chmod 600 "${AUTH_HEADER_FILE}"
    token=""
    unset token
}

prepare_controller_auth_header() {
    local owner size token
    [[ -f "${CONTROLLER_TOKEN_FILE}" && ! -L "${CONTROLLER_TOKEN_FILE}" ]] \
        || fail "M4_CONTROLLER_TOKEN_FILE_INVALID"
    [[ "$(realpath -e -- "${CONTROLLER_TOKEN_FILE}" 2>/dev/null)" == "${CONTROLLER_TOKEN_FILE}" ]] \
        || fail "M4_CONTROLLER_TOKEN_FILE_INVALID"
    owner="$(stat -c '%u:%g' -- "${CONTROLLER_TOKEN_FILE}" 2>/dev/null)" \
        || fail "M4_CONTROLLER_TOKEN_OWNER_UNAVAILABLE"
    [[ "${owner}" == "0:0" ]] || fail "M4_CONTROLLER_TOKEN_OWNER_INVALID"
    [[ ! -w "${CONTROLLER_TOKEN_FILE}" ]] || fail "M4_CONTROLLER_TOKEN_FILE_WRITABLE"
    size="$(wc -c < "${CONTROLLER_TOKEN_FILE}")"
    [[ "${size}" =~ ^[0-9]+$ && "${size}" -ge 80 && "${size}" -le 8192 ]] \
        || fail "M4_CONTROLLER_TOKEN_SIZE_INVALID"
    token="$(cat "${CONTROLLER_TOKEN_FILE}")"
    [[ "${token}" =~ ^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$ ]] \
        || fail "M4_CONTROLLER_TOKEN_FORMAT_INVALID"
    CONTROLLER_AUTH_HEADER_FILE="${WORK_DIR}/controller-auth-header"
    printf 'Authorization: Bearer %s\n' "${token}" > "${CONTROLLER_AUTH_HEADER_FILE}"
    chmod 600 "${CONTROLLER_AUTH_HEADER_FILE}"
    token=""
    unset token
}

validate_target() {
    case "$1" in
        role_project_architect|execution_evidence_coach|independent_quality_reviewer) ;;
        *) fail "M4_MATRIX_TARGET_DENIED" ;;
    esac
}

validate_request_marker() {
    [[ "$1" =~ ^m4-call:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$ ]] \
        || fail "M4_MATRIX_REQUEST_MARKER_INVALID"
}

load_worker_binding() {
    local target="$1" resource_name expected_user_id response_file status size binding room_id user_id room_uri members_file known_count
    case "${target}" in
        role_project_architect) resource_name="role-project-architect" ;;
        execution_evidence_coach) resource_name="execution-evidence-coach" ;;
        independent_quality_reviewer) resource_name="independent-quality-reviewer" ;;
        *) fail "M4_MATRIX_TARGET_DENIED" ;;
    esac
    expected_user_id="@${target}:${MATRIX_SERVER_NAME}"
    response_file="$(new_temp_file)"
    status="$(curl --silent --output "${response_file}" --write-out '%{http_code}' \
        --noproxy '*' --connect-timeout 3 --max-time 10 \
        --header "@${CONTROLLER_AUTH_HEADER_FILE}" \
        "${CONTROLLER_BASE_URL}/api/v1/workers/${resource_name}")" \
        || fail "M4_CONTROLLER_WORKER_API_UNAVAILABLE"
    [[ "${status}" == "200" ]] || fail "M4_CONTROLLER_WORKER_API_FAILED"
    size="$(wc -c < "${response_file}")"
    [[ "${size}" =~ ^[0-9]+$ && "${size}" -ge 32 && "${size}" -le 32768 ]] \
        || fail "M4_CONTROLLER_WORKER_RESPONSE_INVALID"
    binding="$(jq -cer --arg name "${resource_name}" --arg user "${expected_user_id}" '
        select(
            type == "object"
            and (keys | sort) == (["image","matrixUserID","model","name","phase","roomID","runtime","state"] | sort)
            and .name == $name
            and .phase == "Running"
            and .matrixUserID == $user
            and (.roomID | type) == "string"
        )
        | {room_id:.roomID,user_id:.matrixUserID}
    ' "${response_file}" 2>/dev/null)" || fail "M4_CONTROLLER_WORKER_RESPONSE_INVALID"
    room_id="$(jq -er '.room_id' <<<"${binding}" 2>/dev/null)" || fail "M4_MATRIX_ROOM_INVALID"
    user_id="$(jq -er '.user_id' <<<"${binding}" 2>/dev/null)" || fail "M4_MATRIX_USER_INVALID"
    [[ "${room_id}" =~ ^![A-Za-z0-9._~+/-]+:matrix-m4\.local:8080$ ]] || fail "M4_MATRIX_ROOM_INVALID"
    [[ "${user_id}" =~ ^@[A-Za-z0-9._=-]+:matrix-m4\.local:8080$ ]] || fail "M4_MATRIX_USER_INVALID"
    [[ "${user_id}" == "${expected_user_id}" ]] || fail "M4_MATRIX_USER_BINDING_MISMATCH"

    room_uri="$(uri_encode "${room_id}")"
    members_file="$(new_temp_file)"
    status="$(curl --silent --output "${members_file}" --write-out '%{http_code}' \
        --noproxy '*' --connect-timeout 3 --max-time 10 \
        --header "@${AUTH_HEADER_FILE}" \
        "${MATRIX_BASE_URL}/_matrix/client/v3/rooms/${room_uri}/joined_members")" \
        || fail "M4_MATRIX_LOCAL_API_UNAVAILABLE"
    assert_http_ok "${status}"
    size="$(wc -c < "${members_file}")"
    [[ "${size}" =~ ^[0-9]+$ && "${size}" -le 262144 ]] || fail "M4_MATRIX_JOINED_MEMBERS_INVALID"
    known_count="$(jq -er \
        --arg expected "${expected_user_id}" \
        --arg manager "${MANAGER_MATRIX_USER_ID}" \
        --arg architect "@role_project_architect:${MATRIX_SERVER_NAME}" \
        --arg coach "@execution_evidence_coach:${MATRIX_SERVER_NAME}" \
        --arg reviewer "@independent_quality_reviewer:${MATRIX_SERVER_NAME}" '
        .joined
        | select(type == "object" and has($expected) and has($manager))
        | [keys[] | select(. == $architect or . == $coach or . == $reviewer)]
        | length
    ' "${members_file}" 2>/dev/null)" || fail "M4_MATRIX_WORKER_MEMBERSHIP_INVALID"
    [[ "${known_count}" == "1" ]] || fail "M4_MATRIX_ROOM_WORKER_SCOPE_INVALID"
    jq -cn --arg room_id "${room_id}" --arg user_id "${user_id}" \
        '{room_id:$room_id,user_id:$user_id}'
}

validate_uuid() {
    [[ "$1" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$ ]] \
        || fail "M4_MATRIX_CONTEXT_UUID_INVALID"
}

validate_route_payload() {
    local target="$1" raw="$2" expected_skill
    [[ ${#raw} -le 2048 ]] || fail "M4_MATRIX_PAYLOAD_TOO_LARGE"
    case "${target}" in
        role_project_architect) expected_skill="analyze_role_gap" ;;
        execution_evidence_coach) expected_skill="coach_task_submission" ;;
        *) fail "M4_MATRIX_ROUTE_TARGET_DENIED" ;;
    esac
    jq -e --arg skill "${expected_skill}" '
        type == "object"
        and (keys | sort) == (["program_id","run_id","skill_name","skill_version","state_version"] | sort)
        and (.program_id | type) == "string"
        and (.run_id | type) == "string"
        and (.state_version | type) == "number"
        and (.state_version | floor) == .state_version
        and .state_version >= 0
        and .skill_name == $skill
        and .skill_version == "1.0.0"
    ' <<<"${raw}" >/dev/null 2>&1 || fail "M4_MATRIX_ROUTE_PAYLOAD_DENIED"
    validate_uuid "$(jq -er '.program_id' <<<"${raw}" 2>/dev/null)"
    validate_uuid "$(jq -er '.run_id' <<<"${raw}" 2>/dev/null)"
    jq -cS '{program_id,run_id,state_version}' <<<"${raw}"
}

validate_reviewer_context() {
    local raw="$1" program_id run_id
    [[ ${#raw} -le 1536 ]] || fail "M4_MATRIX_PAYLOAD_TOO_LARGE"
    jq -e '
        type == "object"
        and (keys | sort) == (["program_id","run_id","state_version"] | sort)
        and (.program_id | type) == "string"
        and (.run_id | type) == "string"
        and (.state_version | type) == "number"
        and (.state_version | floor) == .state_version
        and .state_version >= 0
    ' <<<"${raw}" >/dev/null 2>&1 || fail "M4_MATRIX_REVIEWER_CONTEXT_DENIED"
    program_id="$(jq -er '.program_id' <<<"${raw}" 2>/dev/null)"
    run_id="$(jq -er '.run_id' <<<"${raw}" 2>/dev/null)"
    validate_uuid "${program_id}"
    validate_uuid "${run_id}"
    jq -cS . <<<"${raw}"
}

validate_trusted_package() {
    local target="$1" raw="$2" context="$3" canonical
    [[ ${#raw} -le 16384 ]] || fail "M4_MATRIX_TRUSTED_PACKAGE_TOO_LARGE"
    case "${target}" in
        role_project_architect)
            jq -e --argjson context "${context}" '
                type == "object"
                and (keys | sort) == (["constraints","program_id","role_facts","state_version","user_facts"] | sort)
                and .program_id == $context.program_id
                and .state_version == $context.state_version
            ' <<<"${raw}" >/dev/null 2>&1 || fail "M4_MATRIX_TRUSTED_PACKAGE_DENIED"
            ;;
        execution_evidence_coach)
            jq -e --argjson context "${context}" '
                type == "object"
                and (keys | sort) == (["criteria","evidence_refs","program_id","state_version","task"] | sort)
                and .program_id == $context.program_id
                and .state_version == $context.state_version
            ' <<<"${raw}" >/dev/null 2>&1 || fail "M4_MATRIX_TRUSTED_PACKAGE_DENIED"
            ;;
        independent_quality_reviewer)
            jq -e '
                type == "object"
                and (keys | sort) == (["context_sha256","criteria","evidence_facts","package_id","package_kind","package_sha256","reviewer_mode","rubric_version","tools_allowed"] | sort)
                and .reviewer_mode == "contract_smoke"
                and .package_kind == "fixed_synthetic_closed_package"
                and .tools_allowed == false
            ' <<<"${raw}" >/dev/null 2>&1 || fail "M4_MATRIX_TRUSTED_PACKAGE_DENIED"
            ;;
        *) fail "M4_MATRIX_TARGET_DENIED" ;;
    esac
    canonical="$(jq -cS . <<<"${raw}" 2>/dev/null)" || fail "M4_MATRIX_TRUSTED_PACKAGE_DENIED"
    if grep -Eqi \
        '(access[_ -]?token|api[_ -]?key|password|authorization[[:space:]]*:|bearer[[:space:]]+[A-Za-z0-9._~-]{12,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|HICLAW_(LLM_API_KEY|MANAGER_PASSWORD|MANAGER_GATEWAY_KEY)|WORKER_(PASSWORD|GATEWAY_KEY|MATRIX_TOKEN)|"channels"[[:space:]]*:|"plugins"[[:space:]]*:)' \
        <<<"${canonical}"; then
        fail "M4_MATRIX_TRUSTED_PACKAGE_SENSITIVE_CONTENT_BLOCKED"
    fi
    printf '%s' "${canonical}"
}

uri_encode() {
    jq -nr --arg value "$1" '$value | @uri'
}

new_temp_file() {
    mktemp "${WORK_DIR}/temp.XXXXXX"
}

assert_http_ok() {
    [[ "$1" == "200" ]] || fail "M4_MATRIX_LOCAL_API_FAILED"
}

sync_baseline() {
    local output status filter
    output="$(new_temp_file)"
    filter='{"room":{"timeline":{"limit":1}}}'
    status="$(curl --silent --output "${output}" --write-out '%{http_code}' \
        --noproxy '*' --connect-timeout 3 --max-time 10 \
        --header "@${AUTH_HEADER_FILE}" \
        --get --data-urlencode 'timeout=0' --data-urlencode "filter=${filter}" \
        "${MATRIX_BASE_URL}/_matrix/client/v3/sync")" || fail "M4_MATRIX_LOCAL_API_UNAVAILABLE"
    assert_http_ok "${status}"
    jq -er '.next_batch | select(type == "string" and length > 0 and length <= 4096)' \
        "${output}" 2>/dev/null || fail "M4_MATRIX_SYNC_TOKEN_INVALID"
}

build_output_contract() {
    local target="$1" trusted_package="$2"
    case "${target}" in
        role_project_architect)
            jq -cn --argjson package "${trusted_package}" '
              {
                contract_name:"analyze_role_gap.output.v1.0.0",
                response_rules:{
                  json_object_only:true,
                  markdown:false,
                  wrapper:false,
                  extra_keys:false,
                  exact_top_level_keys:["proposal_type","program_id","base_state_version","gaps","unable_to_determine"]
                },
                fixed_fields:{
                  proposal_type:"gap_assessment",
                  program_id:$package.program_id,
                  base_state_version:$package.state_version
                },
                gaps:{
                  type:"array",
                  min_items:1,
                  max_items:32,
                  item_exact_keys:["gap_id","requirement","current_evidence_fact_ids","status","reason"],
                  gap_id:"1-64 characters matching ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$",
                  requirement:"use a requirement from role_facts; do not invent one",
                  current_evidence_fact_ids:"unique confirmed user_facts fact_id values only; use [] when none support the requirement",
                  status_allowed:["covered","gap","unable_to_determine"],
                  reason:"1-1000 characters based only on the trusted package"
                },
                unable_to_determine:"unique array of 0-32 strings, each 1-500 characters"
              }
            ' 2>/dev/null || fail "M4_MATRIX_OUTPUT_CONTRACT_BUILD_FAILED"
            ;;
        execution_evidence_coach)
            jq -cn --argjson package "${trusted_package}" '
              {
                contract_name:"coach_task_submission.output.v1.0.0",
                response_rules:{
                  json_object_only:true,
                  markdown:false,
                  wrapper:false,
                  extra_keys:false,
                  exact_top_level_keys:["feedback_type","program_id","base_state_version","task_id","task_version","criterion_observations","missing_evidence","revision_suggestion","certifies_completion"]
                },
                fixed_fields:{
                  feedback_type:"coach_feedback",
                  program_id:$package.program_id,
                  base_state_version:$package.state_version,
                  task_id:$package.task.task_id,
                  task_version:$package.task.task_version,
                  certifies_completion:false
                },
                criterion_observations:{
                  type:"array",
                  min_items:1,
                  max_items:32,
                  item_exact_keys:["criterion_id","evidence_item_ids","status","reason"],
                  criterion_id:"use a criterion_id from criteria",
                  evidence_item_ids:"unique evidence_refs evidence_item_id UUID values only; use [] when none support the criterion",
                  status_allowed:["supported","needs_more_evidence","unable_to_determine"],
                  reason:"1-1000 characters based only on the trusted package"
                },
                missing_evidence:"unique array of 0-32 strings, each 1-500 characters",
                revision_suggestion:"1-1000 characters; non-authoritative feedback only"
              }
            ' 2>/dev/null || fail "M4_MATRIX_OUTPUT_CONTRACT_BUILD_FAILED"
            ;;
        independent_quality_reviewer)
            jq -cn --argjson package "${trusted_package}" '
              {
                contract_name:"review_evidence_against_rubric.output.v1.0.0",
                response_rules:{
                  json_object_only:true,
                  markdown:false,
                  wrapper:false,
                  extra_keys:false,
                  exact_top_level_keys:["output_type","reviewer_mode","package_id","package_sha256","context_sha256","rubric_version","observations","missing_package_facts","business_evaluation","verified_claim_created","limitations"]
                },
                fixed_fields:{
                  output_type:"reviewer_contract_smoke",
                  reviewer_mode:"contract_smoke",
                  package_id:$package.package_id,
                  package_sha256:$package.package_sha256,
                  context_sha256:$package.context_sha256,
                  rubric_version:$package.rubric_version,
                  business_evaluation:false,
                  verified_claim_created:false,
                  limitations:["not_business_evaluation","m5_review_dispatch_gate_not_implemented"]
                },
                observations:{
                  type:"array",
                  min_items:0,
                  max_items:32,
                  item_exact_keys:["criterion_id","status","evidence_fact_ids","reason"],
                  criterion_id:"use a criterion_id from criteria",
                  status_allowed:["contract_shape_supported","missing_package_fact","unable_to_determine"],
                  evidence_fact_ids:"unique evidence_facts evidence_fact_id values only; use [] when none support the criterion",
                  reason:"1-1000 characters based only on the trusted closed package"
                },
                missing_package_facts:"unique array of 0-32 strings, each 1-500 characters",
                required_object_template:{
                  output_type:"reviewer_contract_smoke",
                  reviewer_mode:"contract_smoke",
                  package_id:$package.package_id,
                  package_sha256:$package.package_sha256,
                  context_sha256:$package.context_sha256,
                  rubric_version:$package.rubric_version,
                  observations:[],
                  missing_package_facts:[],
                  business_evaluation:false,
                  verified_claim_created:false,
                  limitations:["not_business_evaluation","m5_review_dispatch_gate_not_implemented"]
                },
                template_rules:"Return the complete object shown by required_object_template. The first character must be { and the last character must be }. Never return [], null, a wrapper object, or a code fence. You may replace observations and missing_package_facts only with contract-valid arrays grounded in the trusted package."
              }
            ' 2>/dev/null || fail "M4_MATRIX_OUTPUT_CONTRACT_BUILD_FAILED"
            ;;
        *) fail "M4_MATRIX_TARGET_DENIED" ;;
    esac
}

build_message_content() {
    local target="$1" user_id="$2" instruction="$3" request_marker="$4" context="$5" trusted_package="$6" output_file="$7"
    local output_contract
    output_contract="$(build_output_contract "${target}" "${trusted_package}")"
    if ! jq -cn \
        --arg user_id "${user_id}" \
        --arg instruction "${instruction}" \
        --arg request_marker "${request_marker}" \
        --argjson context "${context}" \
        --argjson trusted_package "${trusted_package}" \
        --argjson output_contract "${output_contract}" '
          (
            $instruction
            + "\nThe M4_REQUIRED_OUTPUT_CONTRACT below is authoritative. Return the result object that obeys it; do not return or wrap the contract object itself. Copy every fixed_fields value exactly."
            + "\nM4_REQUEST_MARKER=" + $request_marker
            + "\nM4_ID_CONTEXT=" + ($context | tojson)
            + "\nM4_TRUSTED_PACKAGE=" + ($trusted_package | tojson)
            + "\nM4_REQUIRED_OUTPUT_CONTRACT=" + ($output_contract | tojson)
          ) as $plain_payload
          | {
              msgtype: "m.text",
              body: ($user_id + " " + $plain_payload),
              format: "org.matrix.custom.html",
              formatted_body: (
                "<a href=\"https://matrix.to/#/"
                + ($user_id | @html)
                + "\">"
                + ($user_id | @html)
                + "</a> "
                + ($plain_payload | @html | gsub("\n"; "<br>"))
              ),
              "m.mentions": {user_ids: [$user_id]}
            }
        ' > "${output_file}" 2>/dev/null; then
        fail "M4_MATRIX_MESSAGE_BUILD_FAILED"
    fi
}

validate_visible_mention_content() {
    local input_file="$1" user_id="$2" event_wrapper="$3"
    [[ "${event_wrapper}" == "true" || "${event_wrapper}" == "false" ]] \
        || fail "M4_MATRIX_VISIBLE_MENTION_INVALID"
    jq -e \
        --arg user_id "${user_id}" \
        --argjson event_wrapper "${event_wrapper}" '
          (if $event_wrapper then .content else . end) as $content
          | ($content | type) == "object"
          and (($content | keys | sort) == (["body","format","formatted_body","m.mentions","msgtype"] | sort))
          and $content.msgtype == "m.text"
          and $content.format == "org.matrix.custom.html"
          and (($content.body | type) == "string")
          and ($content.body | startswith($user_id + " "))
          and (($content.formatted_body | type) == "string")
          and (
            $content.formatted_body
            | startswith(
                "<a href=\"https://matrix.to/#/"
                + $user_id
                + "\">"
                + $user_id
                + "</a> "
              )
          )
          and $content["m.mentions"] == {user_ids: [$user_id]}
        ' "${input_file}" >/dev/null 2>&1 \
        || fail "M4_MATRIX_VISIBLE_MENTION_INVALID"
}

message_builder_preflight() {
    local target output_file probe_user_id trusted_package expected_contract_name
    for target in \
        role_project_architect \
        execution_evidence_coach \
        independent_quality_reviewer; do
        case "${target}" in
            role_project_architect)
                probe_user_id="@role_project_architect:${MATRIX_SERVER_NAME}"
                trusted_package='{"program_id":"00000000-0000-4000-8000-000000000000","state_version":0,"role_facts":[],"user_facts":[]}'
                expected_contract_name='analyze_role_gap.output.v1.0.0'
                ;;
            execution_evidence_coach)
                probe_user_id="@execution_evidence_coach:${MATRIX_SERVER_NAME}"
                trusted_package='{"program_id":"00000000-0000-4000-8000-000000000000","state_version":0,"task":{"task_id":"00000000-0000-4000-8000-000000000002","task_version":1},"criteria":[],"evidence_refs":[]}'
                expected_contract_name='coach_task_submission.output.v1.0.0'
                ;;
            independent_quality_reviewer)
                probe_user_id="@independent_quality_reviewer:${MATRIX_SERVER_NAME}"
                trusted_package='{"package_id":"00000000-0000-4000-8000-000000000003","package_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","context_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","rubric_version":"synthetic-rubric-v1","criteria":[],"evidence_facts":[]}'
                expected_contract_name='review_evidence_against_rubric.output.v1.0.0'
                ;;
        esac
        output_file="$(new_temp_file)"
        build_message_content \
            "${target}" \
            "${probe_user_id}" \
            'M4 visible mention escape probe <probe>&' \
            'm4-call:00000000-0000-4000-8000-000000000000' \
            '{"program_id":"00000000-0000-4000-8000-000000000000","run_id":"00000000-0000-4000-8000-000000000001","state_version":0}' \
            "${trusted_package}" \
            "${output_file}"
        validate_visible_mention_content "${output_file}" "${probe_user_id}" false
        jq -e --arg expected_contract_name "${expected_contract_name}" '
            (.formatted_body | contains("&lt;probe&gt;&amp;"))
            and ((.formatted_body | contains("<probe>")) | not)
            and (([.formatted_body | scan("<a\\b")] | length) == 1)
            and (.body | contains("M4_REQUIRED_OUTPUT_CONTRACT="))
            and (.body | contains($expected_contract_name))
            and (.body | contains("do not return or wrap the contract object itself"))
        ' "${output_file}" >/dev/null 2>&1 \
            || fail "M4_MATRIX_VISIBLE_MENTION_INVALID"
    done
}

send_dispatch() {
    local target="$1" request_marker="$2" context="$3" trusted_package="$4" binding room_id user_id instruction body_file response_file
    local since room_uri transaction_id status event_id event_uri event_file event_status event_ts digest marker_hash metadata_tmp metadata
    validate_request_marker "${request_marker}"
    binding="$(load_worker_binding "${target}")"
    room_id="$(jq -er '.room_id' <<<"${binding}" 2>/dev/null)"
    user_id="$(jq -er '.user_id' <<<"${binding}" 2>/dev/null)"
    case "${target}" in
        role_project_architect)
            instruction="Execute only the frozen M4 analyze_role_gap v1.0.0 path using the ID-only context and trusted synthetic package below. Do not use room history, invent facts, or mutate State. Return exactly one JSON object with no Markdown, no extra text, no wrapper key, and no fields outside the authoritative contract below."
            ;;
        execution_evidence_coach)
            instruction="Execute only the frozen M4 coach_task_submission v1.0.0 path using the ID-only context and trusted synthetic package below. Do not use room history, invent facts, certify completion, or mutate State. Return exactly one JSON object with no Markdown, no extra text, no wrapper key, and no fields outside the authoritative contract below."
            ;;
        independent_quality_reviewer)
            instruction="Execute only the frozen M4 review_evidence_against_rubric v1.0.0 no-tool contract_smoke using the ID-only context and trusted closed synthetic package below. Do not use room history, call tools, create a business evaluation, or mutate State. Return exactly one complete JSON object with no Markdown, no extra text, no wrapper key, and no fields outside the authoritative contract below. The first character must be { and the last character must be }. Never return [], null, or a code fence. Copy every fixed field from required_object_template exactly; even when both variable arrays are empty, return the entire object."
            ;;
        *) fail "M4_MATRIX_TARGET_DENIED" ;;
    esac
    since="$(sync_baseline)"
    body_file="$(new_temp_file)"
    build_message_content \
        "${target}" "${user_id}" "${instruction}" "${request_marker}" \
        "${context}" "${trusted_package}" "${body_file}"
    validate_visible_mention_content "${body_file}" "${user_id}" false
    room_uri="$(uri_encode "${room_id}")"
    transaction_id="m4_$(date +%s)_$$_${RANDOM}"
    response_file="$(new_temp_file)"
    status="$(curl --silent --output "${response_file}" --write-out '%{http_code}' \
        --noproxy '*' --connect-timeout 3 --max-time 10 \
        --header "@${AUTH_HEADER_FILE}" --header 'Content-Type: application/json' \
        --request PUT --data-binary "@${body_file}" \
        "${MATRIX_BASE_URL}/_matrix/client/v3/rooms/${room_uri}/send/m.room.message/${transaction_id}")" \
        || fail "M4_MATRIX_LOCAL_API_UNAVAILABLE"
    assert_http_ok "${status}"
    event_id="$(jq -er '.event_id | select(type == "string")' "${response_file}" 2>/dev/null)" \
        || fail "M4_MATRIX_DELIVERY_ID_INVALID"
    [[ "${event_id}" =~ ^\$[A-Za-z0-9._~+:/=-]{1,255}$ ]] || fail "M4_MATRIX_DELIVERY_ID_INVALID"

    event_uri="$(uri_encode "${event_id}")"
    event_file="$(new_temp_file)"
    event_status="$(curl --silent --output "${event_file}" --write-out '%{http_code}' \
        --noproxy '*' --connect-timeout 3 --max-time 10 \
        --header "@${AUTH_HEADER_FILE}" \
        "${MATRIX_BASE_URL}/_matrix/client/v3/rooms/${room_uri}/event/${event_uri}")" \
        || fail "M4_MATRIX_LOCAL_API_UNAVAILABLE"
    assert_http_ok "${event_status}"
    validate_visible_mention_content "${event_file}" "${user_id}" true
    event_ts="$(jq -er '.origin_server_ts | select(type == "number" and . >= 0 and floor == .)' \
        "${event_file}" 2>/dev/null)" || fail "M4_MATRIX_DELIVERY_TIMESTAMP_INVALID"

    digest="$(printf '%s' "${event_id}" | sha256sum | awk '{print $1}')"
    [[ "${digest}" =~ ^[0-9a-f]{64}$ ]] || fail "M4_MATRIX_DELIVERY_DIGEST_INVALID"
    metadata="${DISPATCH_STATE_DIR}/${digest}.json"
    [[ ! -e "${metadata}" ]] || fail "M4_MATRIX_DELIVERY_ALREADY_RECORDED"
    metadata_tmp="$(new_temp_file)"
    marker_hash="$(printf '%s' "${request_marker}" | sha256sum | awk '{print $1}')"
    [[ "${marker_hash}" =~ ^[0-9a-f]{64}$ ]] || fail "M4_MATRIX_REQUEST_MARKER_HASH_INVALID"
    jq -cn \
        --arg target_identity "${target}" \
        --arg room_id "${room_id}" \
        --arg user_id "${user_id}" \
        --arg delivery_id "${event_id}" \
        --arg request_marker_sha256 "${marker_hash}" \
        --arg since "${since}" \
        --argjson sent_at_ms "${event_ts}" \
        '{target_identity:$target_identity,room_id:$room_id,user_id:$user_id,delivery_id:$delivery_id,request_marker_sha256:$request_marker_sha256,since:$since,sent_at_ms:$sent_at_ms}' \
        > "${metadata_tmp}"
    chmod 600 "${metadata_tmp}"
    mv -n -- "${metadata_tmp}" "${metadata}" || fail "M4_MATRIX_DELIVERY_RECORD_FAILED"
    printf '%s\n' "${event_id}"
}

response_is_safe() {
    local text="$1"
    [[ -n "${text//[[:space:]]/}" ]] || return 1
    [[ $(printf '%s' "${text}" | wc -c) -le 8192 ]] || return 1
    if grep -Eqi \
        '(access[_ -]?token|api[_ -]?key|password|authorization[[:space:]]*:|bearer[[:space:]]+[A-Za-z0-9._~-]{12,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|HICLAW_(LLM_API_KEY|MANAGER_PASSWORD|MANAGER_GATEWAY_KEY)|WORKER_(PASSWORD|GATEWAY_KEY|MATRIX_TOKEN)|"channels"[[:space:]]*:|"plugins"[[:space:]]*:)' \
        <<<"${text}"; then
        return 1
    fi
    return 0
}

worker_output_matches_contract() {
    local target="$1" text="$2"
    printf '%s' "${text}" | jq -Rse --arg target "${target}" '
      def exact_keys($expected):
        type == "object" and ((keys | sort) == ($expected | sort));
      def integer_at_least($minimum):
        type == "number" and floor == . and . >= $minimum;
      def bounded_string($maximum):
        type == "string" and length >= 1 and length <= $maximum;
      def uuid:
        type == "string" and test("^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$");
      def identifier:
        type == "string" and test("^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$");
      def unique_array:
        type == "array" and length == (unique | length);
      def architect_output:
        exact_keys(["proposal_type","program_id","base_state_version","gaps","unable_to_determine"])
        and .proposal_type == "gap_assessment"
        and (.program_id | uuid)
        and (.base_state_version | integer_at_least(0))
        and (.gaps | type == "array" and length >= 1 and length <= 32)
        and all(.gaps[];
          exact_keys(["gap_id","requirement","current_evidence_fact_ids","status","reason"])
          and (.gap_id | identifier)
          and (.requirement | bounded_string(1000))
          and (.current_evidence_fact_ids | type == "array" and length <= 32 and unique_array)
          and all(.current_evidence_fact_ids[]; identifier)
          and (.status == "covered" or .status == "gap" or .status == "unable_to_determine")
          and (.reason | bounded_string(1000))
        )
        and (.unable_to_determine | type == "array" and length <= 32 and unique_array)
        and all(.unable_to_determine[]; bounded_string(500));
      def coach_output:
        exact_keys(["feedback_type","program_id","base_state_version","task_id","task_version","criterion_observations","missing_evidence","revision_suggestion","certifies_completion"])
        and .feedback_type == "coach_feedback"
        and (.program_id | uuid)
        and (.base_state_version | integer_at_least(0))
        and (.task_id | uuid)
        and (.task_version | integer_at_least(1))
        and (.criterion_observations | type == "array" and length >= 1 and length <= 32)
        and all(.criterion_observations[];
          exact_keys(["criterion_id","evidence_item_ids","status","reason"])
          and (.criterion_id | identifier)
          and (.evidence_item_ids | type == "array" and length <= 32 and unique_array)
          and all(.evidence_item_ids[]; uuid)
          and (.status == "supported" or .status == "needs_more_evidence" or .status == "unable_to_determine")
          and (.reason | bounded_string(1000))
        )
        and (.missing_evidence | type == "array" and length <= 32 and unique_array)
        and all(.missing_evidence[]; bounded_string(500))
        and (.revision_suggestion | bounded_string(1000))
        and .certifies_completion == false;
      def reviewer_output:
        exact_keys(["output_type","reviewer_mode","package_id","package_sha256","context_sha256","rubric_version","observations","missing_package_facts","business_evaluation","verified_claim_created","limitations"])
        and .output_type == "reviewer_contract_smoke"
        and .reviewer_mode == "contract_smoke"
        and (.package_id | uuid)
        and (.package_sha256 | type == "string" and test("^[0-9a-f]{64}$"))
        and (.context_sha256 | type == "string" and test("^[0-9a-f]{64}$"))
        and (.rubric_version | identifier)
        and (.observations | type == "array" and length <= 32)
        and all(.observations[];
          exact_keys(["criterion_id","status","evidence_fact_ids","reason"])
          and (.criterion_id | identifier)
          and (.status == "contract_shape_supported" or .status == "missing_package_fact" or .status == "unable_to_determine")
          and (.evidence_fact_ids | type == "array" and length <= 64 and unique_array)
          and all(.evidence_fact_ids[]; identifier)
          and (.reason | bounded_string(1000))
        )
        and (.missing_package_facts | type == "array" and length <= 32 and unique_array)
        and all(.missing_package_facts[]; bounded_string(500))
        and .business_evaluation == false
        and .verified_claim_created == false
        and (.limitations | type == "array" and sort == ["m5_review_dispatch_gate_not_implemented","not_business_evaluation"]);
      (fromjson?) as $output
      | $output
      | if $target == "role_project_architect" then architect_output
        elif $target == "execution_evidence_coach" then coach_output
        elif $target == "independent_quality_reviewer" then reviewer_output
        else false
        end
    ' >/dev/null 2>&1
}

worker_contract_filter_preflight() {
    local architect coach reviewer
    architect='{"proposal_type":"gap_assessment","program_id":"10000000-0000-4000-8000-000000000001","base_state_version":1,"gaps":[{"gap_id":"gap-001","requirement":"Synthetic requirement.","current_evidence_fact_ids":[],"status":"gap","reason":"Synthetic gap."}],"unable_to_determine":[]}'
    coach='{"feedback_type":"coach_feedback","program_id":"10000000-0000-4000-8000-000000000001","base_state_version":1,"task_id":"20000000-0000-4000-8000-000000000001","task_version":1,"criterion_observations":[{"criterion_id":"criterion-001","evidence_item_ids":[],"status":"needs_more_evidence","reason":"Synthetic observation."}],"missing_evidence":[],"revision_suggestion":"Add one synthetic assertion.","certifies_completion":false}'
    reviewer='{"output_type":"reviewer_contract_smoke","reviewer_mode":"contract_smoke","package_id":"30000000-0000-4000-8000-000000000001","package_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","context_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","rubric_version":"synthetic-rubric-v1","observations":[],"missing_package_facts":[],"business_evaluation":false,"verified_claim_created":false,"limitations":["not_business_evaluation","m5_review_dispatch_gate_not_implemented"]}'
    worker_output_matches_contract role_project_architect "${architect}" || return 1
    worker_output_matches_contract execution_evidence_coach "${coach}" || return 1
    worker_output_matches_contract independent_quality_reviewer "${reviewer}" || return 1
    if worker_output_matches_contract role_project_architect 'model-pricing bootstrap failed'; then
        return 1
    fi
    return 0
}

await_response() {
    local target="$1" delivery_id="$2" timeout_seconds="$3" digest metadata
    local metadata_target room_id user_id recorded_delivery request_marker_sha256 since sent_at_ms room_uri
    local deadline now remaining wait_ms output status curl_exit local_api_failures candidates candidate_count candidate_index
    local candidate response_event_id text next_batch
    local -a valid_candidates
    validate_target "${target}"
    [[ "${delivery_id}" =~ ^\$[A-Za-z0-9._~+:/=-]{1,255}$ ]] || fail "M4_MATRIX_DELIVERY_ID_INVALID"
    [[ "${timeout_seconds}" =~ ^[0-9]+$ ]] || fail "M4_MATRIX_WAIT_TIMEOUT_INVALID"
    (( timeout_seconds >= 1 && timeout_seconds <= 240 )) || fail "M4_MATRIX_WAIT_TIMEOUT_INVALID"
    digest="$(printf '%s' "${delivery_id}" | sha256sum | awk '{print $1}')"
    metadata="${DISPATCH_STATE_DIR}/${digest}.json"
    [[ -f "${metadata}" && ! -L "${metadata}" ]] || fail "M4_MATRIX_DELIVERY_RECORD_MISSING"
    [[ "$(stat -c '%a' -- "${metadata}" 2>/dev/null)" =~ ^[46]00$ ]] || fail "M4_MATRIX_DELIVERY_RECORD_NOT_PRIVATE"
    jq -e '
        type == "object"
        and (keys | sort) == (["delivery_id","request_marker_sha256","room_id","sent_at_ms","since","target_identity","user_id"] | sort)
    ' "${metadata}" >/dev/null 2>&1 || fail "M4_MATRIX_DELIVERY_RECORD_INVALID"
    metadata_target="$(jq -er '.target_identity' "${metadata}" 2>/dev/null)"
    room_id="$(jq -er '.room_id' "${metadata}" 2>/dev/null)"
    user_id="$(jq -er '.user_id' "${metadata}" 2>/dev/null)"
    recorded_delivery="$(jq -er '.delivery_id' "${metadata}" 2>/dev/null)"
    request_marker_sha256="$(jq -er '.request_marker_sha256' "${metadata}" 2>/dev/null)"
    since="$(jq -er '.since' "${metadata}" 2>/dev/null)"
    sent_at_ms="$(jq -er '.sent_at_ms' "${metadata}" 2>/dev/null)"
    [[ "${metadata_target}" == "${target}" && "${recorded_delivery}" == "${delivery_id}" ]] \
        || fail "M4_MATRIX_DELIVERY_BINDING_MISMATCH"
    [[ "${request_marker_sha256}" =~ ^[0-9a-f]{64}$ ]] || fail "M4_MATRIX_REQUEST_MARKER_HASH_INVALID"
    [[ "${room_id}" =~ ^![A-Za-z0-9._~+/-]+:matrix-m4\.local:8080$ ]] || fail "M4_MATRIX_ROOM_INVALID"
    [[ "${user_id}" =~ ^@[A-Za-z0-9._=-]+:matrix-m4\.local:8080$ ]] || fail "M4_MATRIX_USER_INVALID"
    [[ "${since}" != *[[:space:]]* && ${#since} -le 4096 ]] || fail "M4_MATRIX_SYNC_TOKEN_INVALID"
    [[ "${sent_at_ms}" =~ ^[0-9]+$ ]] || fail "M4_MATRIX_DELIVERY_TIMESTAMP_INVALID"
    room_uri="$(uri_encode "${room_id}")"
    deadline=$(( $(date +%s) + timeout_seconds ))
    local_api_failures=0

    while true; do
        now="$(date +%s)"
        remaining=$((deadline - now))
        (( remaining > 0 )) || fail "M4_MATRIX_WORKER_RESPONSE_TIMEOUT"
        if (( remaining > 5 )); then wait_ms=5000; else wait_ms=$((remaining * 1000)); fi
        output="$(new_temp_file)"
        curl_exit=0
        status="$(curl --silent --output "${output}" --write-out '%{http_code}' \
            --noproxy '*' --connect-timeout 3 --max-time "$((remaining + 3))" \
            --header "@${AUTH_HEADER_FILE}" \
            --get --data-urlencode "since=${since}" --data-urlencode "timeout=${wait_ms}" \
            --data-urlencode 'filter={"room":{"timeline":{"limit":20}}}' \
            "${MATRIX_BASE_URL}/_matrix/client/v3/sync")" || curl_exit=$?
        if (( curl_exit != 0 )); then
            now="$(date +%s)"
            (( now < deadline )) || fail "M4_MATRIX_WORKER_RESPONSE_TIMEOUT"
            local_api_failures=$((local_api_failures + 1))
            (( local_api_failures <= 3 )) || fail "M4_MATRIX_LOCAL_API_UNAVAILABLE"
            sleep 1
            continue
        fi
        local_api_failures=0
        assert_http_ok "${status}"
        candidates="$(new_temp_file)"
        jq -ce \
            --arg room "${room_id}" \
            --arg sender "${user_id}" \
            --arg request "${delivery_id}" \
            --argjson sent_at "${sent_at_ms}" '
              [
                .rooms.join[$room].timeline.events[]?
                | select(
                    .type == "m.room.message"
                    and .sender == $sender
                    and (.origin_server_ts | type) == "number"
                    and .origin_server_ts > $sent_at
                    and (.content.msgtype // "") == "m.text"
                    and (.content.body | type) == "string"
                    and (
                      (.content."m.relates_to"."m.in_reply_to".event_id? // $request)
                      == $request
                    )
                  )
                | {response_event_id:.event_id,text:.content.body}
              ]
            ' "${output}" > "${candidates}" 2>/dev/null \
            || fail "M4_MATRIX_WORKER_RESPONSE_INVALID"
        candidate_count="$(jq -er 'length' "${candidates}" 2>/dev/null)" \
            || fail "M4_MATRIX_WORKER_RESPONSE_INVALID"
        [[ "${candidate_count}" =~ ^[0-9]+$ ]] || fail "M4_MATRIX_WORKER_RESPONSE_INVALID"
        valid_candidates=()
        for ((candidate_index = 0; candidate_index < candidate_count; candidate_index++)); do
            candidate="$(jq -cer --argjson index "${candidate_index}" '.[$index]' "${candidates}" 2>/dev/null)" \
                || fail "M4_MATRIX_WORKER_RESPONSE_INVALID"
            response_event_id="$(jq -er '.response_event_id' <<<"${candidate}" 2>/dev/null)" \
                || fail "M4_MATRIX_WORKER_RESPONSE_INVALID"
            text="$(jq -er '.text' <<<"${candidate}" 2>/dev/null)" \
                || fail "M4_MATRIX_WORKER_RESPONSE_INVALID"
            [[ "${response_event_id}" =~ ^\$[A-Za-z0-9._~+:/=-]{1,255}$ ]] \
                || fail "M4_MATRIX_WORKER_RESPONSE_INVALID"
            response_is_safe "${text}" \
                || fail "M4_MATRIX_WORKER_RESPONSE_SENSITIVE_CONTENT_BLOCKED"
            if worker_output_matches_contract "${target}" "${text}"; then
                valid_candidates+=("${candidate}")
            fi
        done
        if (( ${#valid_candidates[@]} > 1 )); then
            fail "M4_MATRIX_WORKER_RESPONSE_AMBIGUOUS"
        fi
        if (( ${#valid_candidates[@]} == 1 )); then
            candidate="${valid_candidates[0]}"
            response_event_id="$(jq -er '.response_event_id' <<<"${candidate}" 2>/dev/null)" \
                || fail "M4_MATRIX_WORKER_RESPONSE_INVALID"
            text="$(jq -er '.text' <<<"${candidate}" 2>/dev/null)" \
                || fail "M4_MATRIX_WORKER_RESPONSE_INVALID"
            jq -cn \
                --arg target_identity "${target}" \
                --arg delivery_id "${delivery_id}" \
                --arg response_event_id "${response_event_id}" \
                --arg text "${text}" \
                '{target_identity:$target_identity,delivery_id:$delivery_id,response_event_id:$response_event_id,text:$text}'
            return 0
        fi
        next_batch="$(jq -er '.next_batch | select(type == "string" and length > 0 and length <= 4096)' \
            "${output}" 2>/dev/null)" || fail "M4_MATRIX_SYNC_TOKEN_INVALID"
        since="${next_batch}"
    done
}

main() {
    local context
    require_tools
    install -d -m 700 "${DISPATCH_STATE_DIR}"
    [[ ! -L "${DISPATCH_STATE_DIR}" ]] || fail "M4_MATRIX_STATE_DIR_INVALID"
    [[ "$(realpath -e -- "${DISPATCH_STATE_DIR}" 2>/dev/null)" == "${DISPATCH_STATE_DIR}" ]] \
        || fail "M4_MATRIX_STATE_DIR_INVALID"
    [[ "$(stat -c '%a' -- "${DISPATCH_STATE_DIR}" 2>/dev/null)" == "700" ]] \
        || fail "M4_MATRIX_STATE_DIR_NOT_PRIVATE"
    WORK_DIR="$(mktemp -d "${DISPATCH_STATE_DIR}/.work.XXXXXX")"
    [[ "${WORK_DIR}" == "${DISPATCH_STATE_DIR}/.work."* ]] || fail "M4_MATRIX_WORK_DIR_INVALID"
    chmod 700 "${WORK_DIR}"
    prepare_auth_header
    prepare_controller_auth_header
    case "${1:-}" in
        preflight)
            [[ $# -eq 1 ]] || fail "M4_MATRIX_ARGUMENT_COUNT_INVALID"
            message_builder_preflight
            worker_contract_filter_preflight || fail "M4_MATRIX_WORKER_RESPONSE_CONTRACT_PREFLIGHT_FAILED"
            load_worker_binding role_project_architect >/dev/null
            load_worker_binding execution_evidence_coach >/dev/null
            load_worker_binding independent_quality_reviewer >/dev/null
            sync_baseline >/dev/null
            printf '%s\n' 'M4_MATRIX_PREFLIGHT=PASS'
            ;;
        dispatch)
            [[ $# -eq 5 ]] || fail "M4_MATRIX_ARGUMENT_COUNT_INVALID"
            validate_target "$2"
            [[ "$2" != "independent_quality_reviewer" ]] || fail "M4_MATRIX_ROUTE_TARGET_DENIED"
            validate_request_marker "$3"
            context="$(validate_route_payload "$2" "$4")"
            send_dispatch "$2" "$3" "${context}" "$(validate_trusted_package "$2" "$5" "${context}")"
            ;;
        dispatch-reviewer-contract-smoke)
            [[ $# -eq 4 ]] || fail "M4_MATRIX_ARGUMENT_COUNT_INVALID"
            validate_request_marker "$2"
            context="$(validate_reviewer_context "$3")"
            send_dispatch independent_quality_reviewer "$2" "${context}" "$(validate_trusted_package independent_quality_reviewer "$4" "${context}")"
            ;;
        await-response)
            [[ $# -eq 4 ]] || fail "M4_MATRIX_ARGUMENT_COUNT_INVALID"
            await_response "$2" "$3" "$4"
            ;;
        *) fail "M4_MATRIX_COMMAND_DENIED" ;;
    esac
}

main "$@"
