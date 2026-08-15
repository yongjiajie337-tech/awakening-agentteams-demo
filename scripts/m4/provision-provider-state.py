"""Create the authorized M4 State bindings and their key-free live bundle."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from jsonschema import Draft202012Validator, FormatChecker


WORKSPACE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKSPACE / "src"))

from awakening.adapters.m4 import M4InternalStateAdapter  # noqa: E402
from awakening.state.admin import build_runtime_dsn, load_m2_env  # noqa: E402
from awakening.state.contracts import (  # noqa: E402
    PrincipalType,
    QueryType,
    TrustedPrincipal,
)
from awakening.state.m4 import (  # noqa: E402
    M4PostgresStateStore,
    M4StateServiceFacade,
    ModelBudgetRequest,
    RuntimeConfigSpec,
)
from awakening.state.service import BusinessRuleError  # noqa: E402


CONFIG_PATH = WORKSPACE / "tmp" / "m4" / "provider" / "provider-runtime-config.json"
FIXTURE_PATH = WORKSPACE / "tmp" / "m4" / "state" / "runtime-state.json"
M2_ENV_PATH = WORKSPACE / ".env.m2"
LIVE_CONFIG_PATH = WORKSPACE / "tmp" / "m4" / "provider" / "live-gateway-config.json"
PACKAGE_DIRECTORY = WORKSPACE / "tmp" / "m4" / "provider" / "packages"
GATEWAY_PRINCIPAL_ID = "awakening-m4-model-gateway"
AUTHORIZATION_ID = "AUTH-M4-001"
AUTHORIZED_PROVIDER_ALIAS = "aliyun-model-studio-official"
AUTHORIZED_MODEL_ID = "qwen3.7-flash-2026-07-15"
AUTHORIZED_ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1"
AUTHORIZED_HOSTNAME = "dashscope.aliyuncs.com"
AUTHORIZED_TIMEOUT_SECONDS = 60
SKILL_VERSION = "1.0.0"
WORKERS = (
    ("ARCHITECT", "role_project_architect", "analyze_role_gap"),
    ("COACH", "execution_evidence_coach", "coach_task_submission"),
    (
        "REVIEWER",
        "independent_quality_reviewer",
        "review_evidence_against_rubric",
    ),
)
PACKAGE_PATHS = {
    identity: PACKAGE_DIRECTORY / f"{identity}.json"
    for _, identity, _ in WORKERS
}
EXCLUSIONS = {
    "role_project_architect": (
        "private_raw_content",
        "unverified_external_claims",
        "direct_business_write",
        "unregistered_tools",
    ),
    "execution_evidence_coach": (
        "private_raw_content",
        "material_generation",
        "direct_business_write",
        "unregistered_tools",
    ),
    "independent_quality_reviewer": (
        "private_raw_content",
        "state_or_tool_access",
        "expert_certification_claim",
        "non_contract_smoke_review",
    ),
}


def _exact_keys(document: Mapping[str, Any], expected: set[str], field: str) -> None:
    if set(document) != expected:
        raise ValueError(f"M4_PROVIDER_STATE_{field}_FIELDS_INVALID")


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"M4_PROVIDER_STATE_{field}_INVALID")
    return value


def _non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"M4_PROVIDER_STATE_{field}_INVALID")
    return value


def _uuid_text(value: Any, field: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"M4_PROVIDER_STATE_{field}_INVALID") from exc


def _read_json(path: Path, field: str) -> tuple[dict[str, Any], bytes]:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"M4_PROVIDER_STATE_{field}_PATH_INVALID")
    raw = path.read_bytes()
    document = json.loads(raw.decode("utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"M4_PROVIDER_STATE_{field}_DOCUMENT_INVALID")
    return document, raw


def _trusted_parameters(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("M4_PROVIDER_STATE_PARAMETERS_INVALID")
    _exact_keys(
        value,
        {"temperature", "seed", "enable_thinking", "response_format"},
        "PARAMETERS",
    )
    temperature = value["temperature"]
    if (
        isinstance(temperature, bool)
        or not isinstance(temperature, (int, float))
        or float(temperature) != 0.01
    ):
        raise ValueError("M4_PROVIDER_STATE_TEMPERATURE_INVALID")
    seed = value["seed"]
    if isinstance(seed, bool) or not isinstance(seed, int) or seed != 0:
        raise ValueError("M4_PROVIDER_STATE_SEED_INVALID")
    if value["enable_thinking"] is not False:
        raise ValueError("M4_PROVIDER_STATE_ENABLE_THINKING_INVALID")
    response_format = value["response_format"]
    if not isinstance(response_format, dict):
        raise ValueError("M4_PROVIDER_STATE_RESPONSE_FORMAT_INVALID")
    _exact_keys(response_format, {"type"}, "RESPONSE_FORMAT")
    if response_format["type"] != "json_object":
        raise ValueError("M4_PROVIDER_STATE_RESPONSE_FORMAT_INVALID")
    return {
        "temperature": 0.01,
        "seed": 0,
        "enable_thinking": False,
        "response_format": {"type": "json_object"},
    }


def _load_fixture() -> dict[str, Any]:
    document, _ = _read_json(FIXTURE_PATH, "FIXTURE")
    allowed = {
        "schema_version",
        "status",
        "program_id",
        "run_id",
        "owner_principal_id",
        "initial_plan_version_id",
        "initial_state_version",
        "updated_at_utc",
    }
    required = {
        "schema_version",
        "status",
        "program_id",
        "run_id",
        "owner_principal_id",
        "initial_plan_version_id",
        "initial_state_version",
    }
    if not required.issubset(document) or not set(document).issubset(allowed):
        raise ValueError("M4_PROVIDER_STATE_FIXTURE_FIELDS_INVALID")
    if document.get("schema_version") != 1 or document.get("status") != "ready":
        raise ValueError("M4_PROVIDER_STATE_FIXTURE_NOT_READY")
    owner_principal_id = document.get("owner_principal_id")
    if (
        not isinstance(owner_principal_id, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}", owner_principal_id)
    ):
        raise ValueError("M4_PROVIDER_STATE_OWNER_PRINCIPAL_ID_INVALID")
    initial_state_version = _non_negative_int(
        document.get("initial_state_version"),
        "INITIAL_STATE_VERSION",
    )
    return {
        "program_id": _uuid_text(document.get("program_id"), "PROGRAM_ID"),
        "run_id": _uuid_text(document.get("run_id"), "RUN_ID"),
        "owner_principal_id": owner_principal_id,
        "initial_state_version": initial_state_version,
    }


def _load_config(expected_run_id: str) -> tuple[dict[str, Any], str]:
    document, raw = _read_json(CONFIG_PATH, "CONFIG")
    _exact_keys(
        document,
        {
            "schema_version",
            "status",
            "authorization_id",
            "program",
            "provider",
            "pricing",
            "parameters",
            "limits",
            "plans",
        },
        "CONFIG",
    )
    if document["schema_version"] != 1 or document["status"] != "authorization_bound":
        raise ValueError("M4_PROVIDER_STATE_CONFIG_STATUS_INVALID")
    if document["authorization_id"] != AUTHORIZATION_ID:
        raise ValueError("M4_PROVIDER_STATE_AUTHORIZATION_ID_INVALID")

    program = document["program"]
    provider = document["provider"]
    pricing = document["pricing"]
    parameters = document["parameters"]
    limits = document["limits"]
    plans = document["plans"]
    if not all(
        isinstance(item, dict)
        for item in (program, provider, pricing, parameters, limits, plans)
    ):
        raise ValueError("M4_PROVIDER_STATE_CONFIG_SECTION_INVALID")
    _exact_keys(
        program,
        {"run_id", "source_fixture_run_id", "run_mode"},
        "PROGRAM",
    )
    _exact_keys(
        provider,
        {
            "alias",
            "model_id",
            "base_endpoint",
            "allowed_hostname",
            "timeout_seconds",
            "secret_file",
            "secret_field",
        },
        "PROVIDER",
    )
    _exact_keys(
        pricing,
        {
            "cost_unit",
            "input_microusd_per_million_tokens",
            "output_microusd_per_million_tokens",
        },
        "PRICING",
    )
    _trusted_parameters(parameters)
    _exact_keys(
        limits,
        {
            "max_calls",
            "max_retries",
            "max_input_tokens_per_call",
            "max_output_tokens_per_call",
            "max_cost_microusd_per_call",
            "max_total_input_tokens",
            "max_total_output_tokens",
            "max_total_cost_microusd",
        },
        "LIMITS",
    )
    _exact_keys(plans, {identity for _, identity, _ in WORKERS}, "PLANS")
    markers: set[str] = set()
    for _, identity, _ in WORKERS:
        plan = plans[identity]
        if not isinstance(plan, dict):
            raise ValueError("M4_PROVIDER_STATE_PLAN_INVALID")
        _exact_keys(plan, {"request_marker"}, "PLAN")
        marker = plan["request_marker"]
        if not isinstance(marker, str) or not marker.startswith("m4-call:"):
            raise ValueError("M4_PROVIDER_STATE_REQUEST_MARKER_INVALID")
        marker_uuid = _uuid_text(marker[len("m4-call:") :], "REQUEST_MARKER")
        if marker != f"m4-call:{marker_uuid}" or marker in markers:
            raise ValueError("M4_PROVIDER_STATE_REQUEST_MARKER_INVALID")
        markers.add(marker)

    config_run_id = _uuid_text(program["run_id"], "CONFIG_RUN_ID")
    source_fixture_run_id = _uuid_text(
        program["source_fixture_run_id"], "SOURCE_FIXTURE_RUN_ID"
    )
    if source_fixture_run_id != expected_run_id:
        raise ValueError("M4_PROVIDER_STATE_CONFIG_SOURCE_RUN_ID_MISMATCH")
    run_mode = program["run_mode"]
    if run_mode == "fixture":
        if config_run_id != source_fixture_run_id:
            raise ValueError("M4_PROVIDER_STATE_CONFIG_RUN_MODE_INVALID")
    elif run_mode == "fresh_window":
        if config_run_id == source_fixture_run_id:
            raise ValueError("M4_PROVIDER_STATE_CONFIG_FRESH_RUN_REQUIRED")
    else:
        raise ValueError("M4_PROVIDER_STATE_CONFIG_RUN_MODE_INVALID")
    alias = provider["alias"]
    model_id = provider["model_id"]
    hostname = provider["allowed_hostname"]
    endpoint = provider["base_endpoint"]
    if not isinstance(alias, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", alias):
        raise ValueError("M4_PROVIDER_STATE_PROVIDER_ALIAS_INVALID")
    if not isinstance(model_id, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}", model_id
    ):
        raise ValueError("M4_PROVIDER_STATE_MODEL_ID_INVALID")
    if not isinstance(hostname, str) or hostname != hostname.lower():
        raise ValueError("M4_PROVIDER_STATE_HOSTNAME_INVALID")
    if not isinstance(endpoint, str) or endpoint != endpoint.rstrip("/"):
        raise ValueError("M4_PROVIDER_STATE_ENDPOINT_INVALID")
    parsed = urlparse(endpoint)
    if (
        parsed.scheme != "https"
        or parsed.hostname != hostname
        or parsed.port not in (None, 443)
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path in ("", "/")
        or "//" in parsed.path
        or endpoint.strip() != endpoint
    ):
        raise ValueError("M4_PROVIDER_STATE_ENDPOINT_INVALID")
    if (
        alias != AUTHORIZED_PROVIDER_ALIAS
        or model_id != AUTHORIZED_MODEL_ID
        or endpoint != AUTHORIZED_ENDPOINT
        or hostname != AUTHORIZED_HOSTNAME
        or provider["timeout_seconds"] != AUTHORIZED_TIMEOUT_SECONDS
    ):
        raise ValueError("M4_PROVIDER_STATE_AUTHORIZED_PROVIDER_BINDING_INVALID")
    if provider["secret_file"] != ".env.m4.provider" or provider[
        "secret_field"
    ] != "AWAKENING_M4_PROVIDER_API_KEY":
        raise ValueError("M4_PROVIDER_STATE_SECRET_REFERENCE_INVALID")
    _positive_int(provider["timeout_seconds"], "TIMEOUT_SECONDS")
    if provider["timeout_seconds"] > 60:
        raise ValueError("M4_PROVIDER_STATE_TIMEOUT_SECONDS_INVALID")
    if pricing["cost_unit"] != "micro_usd":
        raise ValueError("M4_PROVIDER_STATE_COST_UNIT_INVALID")
    _positive_int(
        pricing["input_microusd_per_million_tokens"],
        "INPUT_RATE",
    )
    _positive_int(
        pricing["output_microusd_per_million_tokens"],
        "OUTPUT_RATE",
    )
    integer_limits = {
        key: _positive_int(value, key.upper())
        for key, value in limits.items()
        if key != "max_retries"
    }
    integer_limits["max_retries"] = _non_negative_int(
        limits["max_retries"], "MAX_RETRIES"
    )
    if integer_limits["max_calls"] != len(WORKERS):
        raise ValueError("M4_PROVIDER_STATE_EXACT_THREE_CALL_CAP_REQUIRED")
    if integer_limits["max_retries"] != 0:
        raise ValueError("M4_PROVIDER_STATE_DETERMINISM_BOUNDARY_INVALID")
    if (
        integer_limits["max_input_tokens_per_call"] > 64000
        or integer_limits["max_total_input_tokens"] > 192000
        or integer_limits["max_output_tokens_per_call"] > 1000
        or integer_limits["max_total_output_tokens"] > 3000
        or integer_limits["max_cost_microusd_per_call"] > 30000
        or integer_limits["max_total_cost_microusd"] > 100000
    ):
        raise ValueError("M4_PROVIDER_STATE_APPROVED_HARD_CAP_EXCEEDED")
    if (
        integer_limits["max_total_input_tokens"]
        < len(WORKERS) * integer_limits["max_input_tokens_per_call"]
        or integer_limits["max_total_output_tokens"]
        < len(WORKERS) * integer_limits["max_output_tokens_per_call"]
        or integer_limits["max_total_cost_microusd"]
        < len(WORKERS) * integer_limits["max_cost_microusd_per_call"]
    ):
        raise ValueError("M4_PROVIDER_STATE_TOTAL_CAP_BELOW_THREE_RESERVATIONS")
    return document, sha256(raw).hexdigest()


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _current_program_state_version(
    service: M4StateServiceFacade,
    fixture: Mapping[str, Any],
) -> int:
    owner = TrustedPrincipal(
        principal_id=str(fixture["owner_principal_id"]),
        principal_type=PrincipalType.USER,
        scopes=("state:read",),
        program_scope=(str(fixture["program_id"]),),
        auth_context_id=f"m4-provider-package-{AUTHORIZATION_ID}",
    )
    snapshot = service.query(
        query_type=QueryType.PROGRAM_SNAPSHOT_GET,
        program_id=str(fixture["program_id"]),
        payload={},
        trusted_principal=owner,
    )
    program = snapshot.get("program")
    if not isinstance(program, Mapping):
        raise ValueError("M4_PROVIDER_STATE_CURRENT_PROGRAM_INVALID")
    if _uuid_text(program.get("program_id"), "CURRENT_PROGRAM_ID") != fixture[
        "program_id"
    ]:
        raise ValueError("M4_PROVIDER_STATE_CURRENT_PROGRAM_ID_MISMATCH")
    state_version = _non_negative_int(
        program.get("state_version"),
        "CURRENT_STATE_VERSION",
    )
    if state_version < int(fixture["initial_state_version"]):
        raise ValueError("M4_PROVIDER_STATE_CURRENT_STATE_VERSION_REGRESSED")
    return state_version


def _derived_uuid(*parts: str) -> str:
    return str(uuid5(NAMESPACE_URL, "/".join(("awakening.local", "m4", *parts))))


def _validate_skill_input(skill_name: str, document: Mapping[str, Any]) -> None:
    schema_path = (
        WORKSPACE / "schemas" / "m4" / "skills" / f"{skill_name}.input.schema.json"
    )
    schema, _ = _read_json(schema_path, f"{skill_name.upper()}_INPUT_SCHEMA")
    errors = sorted(
        Draft202012Validator(
            schema,
            format_checker=FormatChecker(),
        ).iter_errors(document),
        key=lambda error: (
            tuple(str(item) for item in error.absolute_path),
            error.message,
        ),
    )
    if errors:
        raise ValueError(f"M4_PROVIDER_STATE_{skill_name.upper()}_INPUT_INVALID")


def _skill_input(
    *,
    identity: str,
    program_id: str,
    run_id: str,
    state_version: int,
    snapshot_id: str,
) -> dict[str, Any]:
    if identity == "role_project_architect":
        document: dict[str, Any] = {
            "program_id": program_id,
            "state_version": state_version,
            "role_facts": [
                {
                    "source_ref": "synthetic-role-source:m4-live-001",
                    "requirement": (
                        "Build one reproducible, evidence-bound Agent workflow."
                    ),
                }
            ],
            "user_facts": [
                {
                    "fact_id": "synthetic-fact-001",
                    "statement": (
                        "The synthetic candidate has authored one deterministic "
                        "Python unit test."
                    ),
                    "confirmed": True,
                }
            ],
            "constraints": {"duration_weeks": 4, "weekly_hours": 5},
        }
        skill_name = "analyze_role_gap"
    elif identity == "execution_evidence_coach":
        document = {
            "program_id": program_id,
            "state_version": state_version,
            "task": {
                "task_id": _derived_uuid(program_id, run_id, "coach-task"),
                "task_version": 1,
                "title": "Create one deterministic workflow test",
            },
            "criteria": [
                {
                    "criterion_id": "criterion-001",
                    "statement": (
                        "The test uses a fixed input and asserts the expected result."
                    ),
                }
            ],
            # Empty is intentional: this is coaching before any evidence is submitted,
            # not a fabricated persisted EvidenceItem.
            "evidence_refs": [],
        }
        skill_name = "coach_task_submission"
    elif identity == "independent_quality_reviewer":
        criteria = [
            {
                "criterion_id": "criterion-001",
                "statement": "The fixture contains one asserted expected result.",
            }
        ]
        evidence_facts = [
            {
                "evidence_fact_id": "synthetic-evidence-fact-001",
                "statement": (
                    "The closed synthetic fixture records one passing assertion."
                ),
            }
        ]
        package_id = _derived_uuid(program_id, run_id, "review-package")
        context_material = {
            "program_id": program_id,
            "run_id": run_id,
            "state_version": state_version,
            "runtime_config_snapshot_id": snapshot_id,
        }
        closed_package_material = {
            "package_id": package_id,
            "package_kind": "fixed_synthetic_closed_package",
            "rubric_version": "synthetic-rubric-v1",
            "criteria": criteria,
            "evidence_facts": evidence_facts,
            "context_sha256": _canonical_sha256(context_material),
        }
        document = {
            "reviewer_mode": "contract_smoke",
            "package_kind": "fixed_synthetic_closed_package",
            "package_id": package_id,
            "package_sha256": _canonical_sha256(closed_package_material),
            "context_sha256": closed_package_material["context_sha256"],
            "rubric_version": "synthetic-rubric-v1",
            "criteria": criteria,
            "evidence_facts": evidence_facts,
            "tools_allowed": False,
        }
        skill_name = "review_evidence_against_rubric"
    else:
        raise ValueError("M4_PROVIDER_STATE_PACKAGE_IDENTITY_INVALID")
    _validate_skill_input(skill_name, document)
    return document


def _build_live_artifacts(
    *,
    config: Mapping[str, Any],
    fixture: Mapping[str, Any],
    run_id: str,
    snapshot_id: str,
    state_version: int,
    reservations: list[Mapping[str, str]],
) -> tuple[dict[Path, bytes], dict[str, str]]:
    if len(reservations) != len(WORKERS):
        raise ValueError("M4_PROVIDER_STATE_LIVE_RESERVATION_COUNT_INVALID")
    reservation_by_identity = {
        item["identity"]: item
        for item in reservations
    }
    if set(reservation_by_identity) != {identity for _, identity, _ in WORKERS}:
        raise ValueError("M4_PROVIDER_STATE_LIVE_RESERVATION_SET_INVALID")

    package_bytes: dict[Path, bytes] = {}
    package_hashes: dict[str, str] = {}
    live_plans: dict[str, Any] = {}
    for _, identity, skill_name in WORKERS:
        binding = reservation_by_identity[identity]
        request_marker = config["plans"][identity]["request_marker"]
        if binding["request_marker_sha256"] != sha256(
            request_marker.encode("utf-8")
        ).hexdigest():
            raise ValueError("M4_PROVIDER_STATE_LIVE_MARKER_BINDING_INVALID")
        package = {
            "schema_version": 1,
            "package_kind": "m4_synthetic_live_skill_input",
            "authorization_id": AUTHORIZATION_ID,
            "agent_identity_id": identity,
            "skill_name": skill_name,
            "skill_version": SKILL_VERSION,
            "state_binding": {
                "program_id": fixture["program_id"],
                "run_id": run_id,
                "program_state_version": state_version,
                "runtime_config_snapshot_id": snapshot_id,
            },
            "call_binding": {
                "model_call_id": binding["model_call_id"],
                "reservation_id": binding["reservation_id"],
                "request_marker_sha256": binding["request_marker_sha256"],
            },
            "skill_input": _skill_input(
                identity=identity,
                program_id=str(fixture["program_id"]),
                run_id=run_id,
                state_version=state_version,
                snapshot_id=snapshot_id,
            ),
        }
        raw_package = _canonical_json_bytes(package)
        package_path = PACKAGE_PATHS[identity]
        package_bytes[package_path] = raw_package
        package_hash = sha256(raw_package).hexdigest()
        package_hashes[identity] = package_hash
        live_plans[identity] = {
            "model_call_id": binding["model_call_id"],
            "reservation_id": binding["reservation_id"],
            "skill_name": skill_name,
            "skill_version": SKILL_VERSION,
            "request_marker": request_marker,
            "object_refs": [
                {
                    "object_type": "synthetic_skill_input_package",
                    "object_id": f"{identity}:{binding['model_call_id']}",
                    "object_version": "1",
                    "content_sha256": package_hash,
                }
            ],
            "exclusions": list(EXCLUSIONS[identity]),
        }

    provider = config["provider"]
    pricing = config["pricing"]
    limits = config["limits"]
    live_config = {
        "authorization_id": AUTHORIZATION_ID,
        "schema_version": 1,
        "provider": {
            "provider_alias": provider["alias"],
            "endpoint": provider["base_endpoint"],
            "allowed_hostname": provider["allowed_hostname"],
            "model_id": provider["model_id"],
            "public_model_alias": provider["model_id"],
            "input_microunits_per_million": pricing[
                "input_microusd_per_million_tokens"
            ],
            "output_microunits_per_million": pricing[
                "output_microusd_per_million_tokens"
            ],
            "timeout_seconds": provider["timeout_seconds"],
        },
        "parameters": _trusted_parameters(config["parameters"]),
        "caps": {
            "max_calls": limits["max_calls"],
            "max_input_tokens_per_call": limits["max_input_tokens_per_call"],
            "max_output_tokens_per_call": limits["max_output_tokens_per_call"],
            "max_cost_microunits_per_call": limits[
                "max_cost_microusd_per_call"
            ],
            "max_total_input_tokens": limits["max_total_input_tokens"],
            "max_total_output_tokens": limits["max_total_output_tokens"],
            "max_total_cost_microunits": limits["max_total_cost_microusd"],
        },
        "state_binding": {
            "program_id": fixture["program_id"],
            "run_id": run_id,
            "runtime_config_snapshot_id": snapshot_id,
        },
        "plans": live_plans,
    }
    artifacts = {**package_bytes, LIVE_CONFIG_PATH: _canonical_json_bytes(live_config)}
    return artifacts, package_hashes


def _output_targets() -> tuple[Path, ...]:
    return (*PACKAGE_PATHS.values(), LIVE_CONFIG_PATH)


def _assert_output_targets_absent() -> None:
    if any(path.exists() or path.is_symlink() for path in _output_targets()):
        raise ValueError("M4_PROVIDER_STATE_LIVE_ARTIFACT_ALREADY_EXISTS")


def _atomic_create(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise OSError("M4_PROVIDER_STATE_LIVE_OUTPUT_DIRECTORY_INVALID")
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    published = False
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        # A hard-link publish is atomic and, unlike replace(), cannot overwrite.
        os.link(temporary, path)
        published = True
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            # Once the complete file is published, a leftover private temp name
            # must not turn a successful durable create into a false failure.
            if not published:
                raise


def _publish_live_artifacts(artifacts: Mapping[Path, bytes]) -> None:
    if set(artifacts) != set(_output_targets()):
        raise ValueError("M4_PROVIDER_STATE_LIVE_ARTIFACT_SET_INVALID")
    created: list[Path] = []
    try:
        # Publish the config last; its presence is the bundle commit marker.
        for path in (*PACKAGE_PATHS.values(), LIVE_CONFIG_PATH):
            _atomic_create(path, artifacts[path])
            created.append(path)
    except (OSError, ValueError) as exc:
        cleanup_complete = True
        for path in reversed(created):
            try:
                path.unlink()
            except (FileNotFoundError, OSError):
                cleanup_complete = False
        reason = "M4_PROVIDER_STATE_LIVE_ARTIFACT_WRITE_FAILED_STATE_COMMITS_RETAINED"
        if not cleanup_complete:
            reason += "_PARTIAL_ARTIFACTS_MAY_REMAIN"
        raise ValueError(reason) from exc


def _assert_current_reservations(
    *,
    service: M4StateServiceFacade,
    program_id: str,
    run_id: str,
    snapshot_id: str,
    reservations: list[dict[str, str]],
    trusted_principal: TrustedPrincipal,
) -> None:
    """Fail closed unless every live binding still names a fresh reservation."""

    for reservation in reservations:
        label = reservation["label"]
        error_code = (
            f"M4_PROVIDER_STATE_{label}_RESERVATION_CURRENT_STATE_INVALID"
        )
        try:
            current = service.get_model_budget_reservation(
                program_id=program_id,
                reservation_id=reservation["reservation_id"],
                trusted_principal=trusted_principal,
            )
        except BusinessRuleError as exc:
            raise ValueError(error_code) from exc

        expected_binding = {
            "reservation_id": reservation["reservation_id"],
            "program_id": program_id,
            "run_id": run_id,
            "model_call_id": reservation["model_call_id"],
            "snapshot_id": snapshot_id,
            "status": "reserved",
        }
        if (
            not isinstance(current, dict)
            or any(
                current.get(field) != value
                for field, value in expected_binding.items()
            )
            or type(current.get("reservation_version")) is not int
            or current.get("reservation_version") != 1
        ):
            raise ValueError(error_code)


def main() -> int:
    fixture = _load_fixture()
    config, provider_config_sha256 = _load_config(fixture["run_id"])
    window_run_id = _uuid_text(config["program"]["run_id"], "WINDOW_RUN_ID")
    _assert_output_targets_absent()
    provider = config["provider"]
    parameters = config["parameters"]
    limits = config["limits"]
    state_material = {
        "run_id": window_run_id,
        "provider_alias": provider["alias"],
        "model_id": provider["model_id"],
        "parameters": _trusted_parameters(parameters),
        "max_calls": limits["max_calls"],
        "max_input_tokens_per_call": limits["max_input_tokens_per_call"],
        "max_output_tokens_per_call": limits["max_output_tokens_per_call"],
        "max_cost_microunits_per_call": limits["max_cost_microusd_per_call"],
        "max_total_input_tokens": limits["max_total_input_tokens"],
        "max_total_output_tokens": limits["max_total_output_tokens"],
        "max_total_cost_microunits": limits["max_total_cost_microusd"],
    }
    state_material_sha256 = _canonical_sha256(state_material)

    service = M4StateServiceFacade(
        M4PostgresStateStore(build_runtime_dsn(load_m2_env(M2_ENV_PATH)))
    )
    adapter = M4InternalStateAdapter(service)
    gateway = TrustedPrincipal(
        principal_id=GATEWAY_PRINCIPAL_ID,
        principal_type=PrincipalType.SERVICE,
        scopes=("model:governance",),
        program_scope=(fixture["program_id"],),
        auth_context_id=f"m4-provider-{config['authorization_id']}",
    )
    snapshot = adapter.create_runtime_config_snapshot(
        program_id=fixture["program_id"],
        idempotency_key=(
            f"m4-provider-snapshot:{config['authorization_id']}:"
            f"{state_material_sha256[:32]}"
        ),
        runtime_config=RuntimeConfigSpec(**state_material),
        trusted_context=gateway,
    )
    if not snapshot.committed:
        raise ValueError(f"M4_PROVIDER_STATE_SNAPSHOT_REJECTED:{snapshot.reason_code.value}")
    snapshot_id = _uuid_text(snapshot.result.get("snapshot_id"), "SNAPSHOT_ID")
    snapshot_config_sha256 = snapshot.result.get("config_sha256")
    if not isinstance(snapshot_config_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", snapshot_config_sha256
    ):
        raise ValueError("M4_PROVIDER_STATE_SNAPSHOT_HASH_INVALID")
    if snapshot.result.get("contains_provider_key") is not False:
        raise ValueError("M4_PROVIDER_STATE_SNAPSHOT_SECRET_BOUNDARY_INVALID")

    reservations: list[dict[str, str]] = []
    for label, identity, skill_name in WORKERS:
        request_marker = config["plans"][identity]["request_marker"]
        model_call_id = request_marker[len("m4-call:") :]
        result = adapter.reserve_model_budget(
            program_id=fixture["program_id"],
            idempotency_key=f"m4-provider-reserve:{label.lower()}:{model_call_id}",
            budget_request=ModelBudgetRequest(
                run_id=window_run_id,
                model_call_id=model_call_id,
                snapshot_id=snapshot_id,
                max_input_tokens=limits["max_input_tokens_per_call"],
                max_output_tokens=limits["max_output_tokens_per_call"],
                max_cost_microunits=limits["max_cost_microusd_per_call"],
            ),
            trusted_context=gateway,
        )
        if not result.committed:
            raise ValueError(
                f"M4_PROVIDER_STATE_{label}_RESERVATION_REJECTED:{result.reason_code.value}"
            )
        if result.result.get("status") != "reserved" or result.result.get(
            "provider_call_allowed"
        ) is not True:
            raise ValueError(f"M4_PROVIDER_STATE_{label}_RESERVATION_NOT_ALLOWED")
        reservation_id = _uuid_text(
            result.result.get("reservation_id"),
            f"{label}_RESERVATION_ID",
        )
        reservations.append(
            {
                "label": label,
                "identity": identity,
                "skill_name": skill_name,
                "reservation_id": reservation_id,
                "model_call_id": model_call_id,
                "request_marker_sha256": sha256(
                    request_marker.encode("utf-8")
                ).hexdigest(),
                "status": "reserved",
            }
        )

    if len(reservations) != 3 or len(
        {item["reservation_id"] for item in reservations}
    ) != 3:
        raise ValueError("M4_PROVIDER_STATE_RESERVATION_CARDINALITY_INVALID")
    if len({item["model_call_id"] for item in reservations}) != 3:
        raise ValueError("M4_PROVIDER_STATE_MODEL_CALL_CARDINALITY_INVALID")

    # A reserve command can be an idempotent replay of its historical result.
    # Re-read authoritative current rows before any live artifact is published.
    _assert_current_reservations(
        service=service,
        program_id=fixture["program_id"],
        run_id=window_run_id,
        snapshot_id=snapshot_id,
        reservations=reservations,
        trusted_principal=gateway,
    )

    state_version = _current_program_state_version(service, fixture)
    artifacts, package_hashes = _build_live_artifacts(
        config=config,
        fixture=fixture,
        run_id=window_run_id,
        snapshot_id=snapshot_id,
        state_version=state_version,
        reservations=reservations,
    )
    _publish_live_artifacts(artifacts)

    print("M4_PROVIDER_STATE_SNAPSHOT_STATUS=COMMITTED")
    print(f"M4_PROVIDER_STATE_SNAPSHOT_ID={snapshot_id}")
    print(f"M4_PROVIDER_STATE_SNAPSHOT_CONFIG_SHA256={snapshot_config_sha256}")
    print(f"M4_PROVIDER_CONFIG_SHA256={provider_config_sha256}")
    for reservation in reservations:
        label = reservation["label"]
        identity = reservation["identity"]
        print(
            f"M4_PROVIDER_STATE_{label}_RESERVATION_STATUS="
            f"{reservation['status']}"
        )
        print(
            f"M4_PROVIDER_STATE_{label}_RESERVATION_ID="
            f"{reservation['reservation_id']}"
        )
        print(
            f"M4_PROVIDER_STATE_{label}_MODEL_CALL_ID="
            f"{reservation['model_call_id']}"
        )
        print(
            f"M4_PROVIDER_STATE_{label}_REQUEST_MARKER_SHA256="
            f"{reservation['request_marker_sha256']}"
        )
        print(
            f"M4_PROVIDER_STATE_{label}_PACKAGE_SHA256="
            f"{package_hashes[identity]}"
        )
    print("M4_PROVIDER_STATE_RESERVATION_COUNT=3")
    print(f"M4_PROVIDER_STATE_PROGRAM_STATE_VERSION={state_version}")
    print(
        "M4_PROVIDER_STATE_LIVE_CONFIG_SHA256="
        f"{sha256(artifacts[LIVE_CONFIG_PATH]).hexdigest()}"
    )
    print("M4_PROVIDER_STATE_LIVE_CONFIG_PATH=tmp/m4/provider/live-gateway-config.json")
    print("M4_PROVIDER_STATE_PACKAGE_COUNT=3")
    print("M4_PROVIDER_STATE_PROVIDER_SECRET_READ=false")
    print("M4_PROVIDER_STATE_CONTENT_ECHOED=false")
    print("M4_PROVIDER_STATE_PROVISION=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
