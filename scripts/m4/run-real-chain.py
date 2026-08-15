"""Run the three authorized M4 AgentTeams calls once, sequentially.

The trusted host driver supplies only server-owned bindings.  Matrix messages
are sent with the real Manager account from inside the Manager container; each
Worker receives one frozen synthetic package and one single-use Gateway marker.
No request is retried and no model output is applied to business State.
"""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from awakening.adapters.m4 import M4StateMcpAdapter
from awakening.model_gateway.m4.live_runtime import (
    LIVE_AUTHORIZATION_ID,
    LiveInvocationPlanConfiguration,
    load_live_runtime_config,
)
from awakening.orchestration.m4.matrix_delegation import (
    MatrixDelegationError,
    MatrixManagerDelegationPort,
)
from awakening.state.admin import build_runtime_dsn, load_m2_env
from awakening.state.m4 import (
    M4AgentIdentity,
    M4PostgresStateStore,
    M4StateServiceFacade,
    TrustedRuntimeContext,
)
from awakening.state.validation import canonical_json_bytes


WORKSPACE = Path(__file__).resolve().parents[2]
LIVE_CONFIG_PATH = WORKSPACE / "tmp" / "m4" / "provider" / "live-gateway-config.json"
PACKAGE_DIRECTORY = WORKSPACE / "tmp" / "m4" / "provider" / "packages"
RESULT_PATH = WORKSPACE / "tmp" / "m4" / "provider" / "real-chain-results.json"
M2_ENV_PATH = WORKSPACE / ".env.m2"

WORKERS: tuple[tuple[str, str, str], ...] = (
    (
        "role_project_architect",
        "analyze_role_gap",
        "role_project_architect.json",
    ),
    (
        "execution_evidence_coach",
        "coach_task_submission",
        "execution_evidence_coach.json",
    ),
    (
        "independent_quality_reviewer",
        "review_evidence_against_rubric",
        "independent_quality_reviewer.json",
    ),
)
_PACKAGE_FIELDS = {
    "schema_version",
    "package_kind",
    "authorization_id",
    "agent_identity_id",
    "skill_name",
    "skill_version",
    "state_binding",
    "call_binding",
    "skill_input",
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_VALUE_ERROR_CODE = re.compile(
    r"^M4_REAL_CHAIN_[A-Z0-9_]+(?::[a-z0-9_]+)?$"
)


def _regular_file(path: Path, reason: str) -> Path:
    if not path.is_file() or path.is_symlink():
        raise ValueError(reason)
    return path.resolve(strict=True)


def _read_json_bytes(path: Path, reason: str) -> tuple[bytes, Mapping[str, Any]]:
    raw = _regular_file(path, reason).read_bytes()
    if not raw or len(raw) > 1_048_576:
        raise ValueError(reason)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(reason) from exc
    if not isinstance(value, Mapping):
        raise ValueError(reason)
    return raw, value


def _schema(skill_name: str, direction: str) -> Mapping[str, Any]:
    path = (
        WORKSPACE
        / "schemas"
        / "m4"
        / "skills"
        / f"{skill_name}.{direction}.schema.json"
    )
    _, document = _read_json_bytes(path, "M4_REAL_CHAIN_SCHEMA_INVALID")
    Draft202012Validator.check_schema(document)
    return document


def _validate_schema(skill_name: str, direction: str, value: Any) -> None:
    validator = Draft202012Validator(
        _schema(skill_name, direction),
        format_checker=FormatChecker(),
    )
    if next(validator.iter_errors(value), None) is not None:
        raise ValueError(f"M4_REAL_CHAIN_{direction.upper()}_SCHEMA_INVALID:{skill_name}")


def _plan_map(plans: tuple[LiveInvocationPlanConfiguration, ...]) -> dict[str, LiveInvocationPlanConfiguration]:
    by_identity = {plan.agent_identity_id: plan for plan in plans}
    if set(by_identity) != {identity for identity, _, _ in WORKERS}:
        raise ValueError("M4_REAL_CHAIN_PLAN_SET_INVALID")
    return by_identity


def _load_packages(
    *,
    program_id: str,
    run_id: str,
    snapshot_id: str,
    plans: Mapping[str, LiveInvocationPlanConfiguration],
) -> tuple[dict[str, Mapping[str, Any]], dict[str, str], int]:
    packages: dict[str, Mapping[str, Any]] = {}
    package_hashes: dict[str, str] = {}
    state_versions: set[int] = set()
    for identity, skill_name, filename in WORKERS:
        raw, package = _read_json_bytes(
            PACKAGE_DIRECTORY / filename,
            f"M4_REAL_CHAIN_PACKAGE_INVALID:{identity}",
        )
        if set(package) != _PACKAGE_FIELDS:
            raise ValueError(f"M4_REAL_CHAIN_PACKAGE_FIELDS_INVALID:{identity}")
        plan = plans[identity]
        state_binding = package.get("state_binding")
        call_binding = package.get("call_binding")
        skill_input = package.get("skill_input")
        if not isinstance(state_binding, Mapping) or set(state_binding) != {
            "program_id",
            "run_id",
            "program_state_version",
            "runtime_config_snapshot_id",
        }:
            raise ValueError(f"M4_REAL_CHAIN_PACKAGE_STATE_BINDING_INVALID:{identity}")
        if not isinstance(call_binding, Mapping) or set(call_binding) != {
            "model_call_id",
            "reservation_id",
            "request_marker_sha256",
        }:
            raise ValueError(f"M4_REAL_CHAIN_PACKAGE_CALL_BINDING_INVALID:{identity}")
        if (
            package.get("schema_version") != 1
            or package.get("package_kind") != "m4_synthetic_live_skill_input"
            or package.get("authorization_id") != LIVE_AUTHORIZATION_ID
            or package.get("agent_identity_id") != identity
            or package.get("skill_name") != skill_name
            or package.get("skill_version") != "1.0.0"
            or state_binding.get("program_id") != program_id
            or state_binding.get("run_id") != run_id
            or state_binding.get("runtime_config_snapshot_id") != snapshot_id
            or call_binding.get("model_call_id") != plan.model_call_id
            or call_binding.get("reservation_id") != plan.reservation_id
            or call_binding.get("request_marker_sha256")
            != sha256(plan.request_marker.encode("utf-8")).hexdigest()
            or not isinstance(skill_input, Mapping)
        ):
            raise ValueError(f"M4_REAL_CHAIN_PACKAGE_BINDING_INVALID:{identity}")
        state_version = state_binding.get("program_state_version")
        if isinstance(state_version, bool) or not isinstance(state_version, int) or state_version < 0:
            raise ValueError(f"M4_REAL_CHAIN_PACKAGE_STATE_VERSION_INVALID:{identity}")
        state_versions.add(state_version)
        _validate_schema(skill_name, "input", skill_input)
        if identity != "independent_quality_reviewer" and (
            skill_input.get("program_id") != program_id
            or skill_input.get("state_version") != state_version
        ):
            raise ValueError(f"M4_REAL_CHAIN_SKILL_INPUT_BINDING_INVALID:{identity}")
        package_hash = sha256(raw).hexdigest()
        if (
            len(plan.object_refs) != 1
            or plan.object_refs[0].get("content_sha256") != package_hash
        ):
            raise ValueError(f"M4_REAL_CHAIN_OBJECT_REF_HASH_INVALID:{identity}")
        packages[identity] = dict(skill_input)
        package_hashes[identity] = package_hash
    if len(state_versions) != 1:
        raise ValueError("M4_REAL_CHAIN_PACKAGE_STATE_VERSION_SET_INVALID")
    return packages, package_hashes, state_versions.pop()


def _manager_snapshot(
    *,
    program_id: str,
    run_id: str,
) -> Mapping[str, Any]:
    service = M4StateServiceFacade(
        M4PostgresStateStore(build_runtime_dsn(load_m2_env(_regular_file(M2_ENV_PATH, "M4_REAL_CHAIN_M2_ENV_INVALID"))))
    )
    adapter = M4StateMcpAdapter(service)
    context = TrustedRuntimeContext(
        principal_id="m4-runtime-principal-awakening_program_manager",
        agent_identity=M4AgentIdentity.MANAGER,
        program_role="manager",
        program_scope=(program_id,),
        run_id=run_id,
        auth_context_id=f"m4-real-chain-manager-{run_id}",
    )
    snapshot = adapter.get_snapshot(program_id=program_id, trusted_context=context)
    program = snapshot.get("program") if isinstance(snapshot, Mapping) else None
    if not isinstance(program, Mapping) or program.get("program_id") != program_id:
        raise ValueError("M4_REAL_CHAIN_MANAGER_SNAPSHOT_INVALID")
    return snapshot


def _parse_worker_output(
    *,
    identity: str,
    skill_name: str,
    text: str,
    skill_input: Mapping[str, Any],
) -> Mapping[str, Any]:
    try:
        output = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"M4_REAL_CHAIN_OUTPUT_NOT_EXACT_JSON:{identity}") from exc
    if not isinstance(output, Mapping):
        raise ValueError(f"M4_REAL_CHAIN_OUTPUT_NOT_OBJECT:{identity}")
    _validate_schema(skill_name, "output", output)
    if identity == "role_project_architect" and (
        output.get("program_id") != skill_input.get("program_id")
        or output.get("base_state_version") != skill_input.get("state_version")
    ):
        raise ValueError("M4_REAL_CHAIN_ARCHITECT_OUTPUT_BINDING_INVALID")
    if identity == "role_project_architect":
        allowed_requirements = {
            fact["requirement"] for fact in skill_input.get("role_facts", [])
        }
        allowed_fact_ids = {
            fact["fact_id"] for fact in skill_input.get("user_facts", [])
        }
        if any(
            gap.get("requirement") not in allowed_requirements
            or not set(gap.get("current_evidence_fact_ids", [])).issubset(
                allowed_fact_ids
            )
            for gap in output.get("gaps", [])
        ):
            raise ValueError("M4_REAL_CHAIN_ARCHITECT_OUTPUT_REFERENCE_INVALID")
    if identity == "execution_evidence_coach" and (
        output.get("program_id") != skill_input.get("program_id")
        or output.get("base_state_version") != skill_input.get("state_version")
        or output.get("task_id") != skill_input.get("task", {}).get("task_id")
        or output.get("task_version") != skill_input.get("task", {}).get("task_version")
        or output.get("certifies_completion") is not False
    ):
        raise ValueError("M4_REAL_CHAIN_COACH_OUTPUT_BINDING_INVALID")
    if identity == "execution_evidence_coach":
        allowed_criterion_ids = {
            criterion["criterion_id"] for criterion in skill_input.get("criteria", [])
        }
        allowed_evidence_ids = {
            reference["evidence_item_id"]
            for reference in skill_input.get("evidence_refs", [])
        }
        if any(
            observation.get("criterion_id") not in allowed_criterion_ids
            or not set(observation.get("evidence_item_ids", [])).issubset(
                allowed_evidence_ids
            )
            for observation in output.get("criterion_observations", [])
        ):
            raise ValueError("M4_REAL_CHAIN_COACH_OUTPUT_REFERENCE_INVALID")
    if identity == "independent_quality_reviewer" and any(
        output.get(field) != skill_input.get(field)
        for field in (
            "reviewer_mode",
            "package_id",
            "package_sha256",
            "context_sha256",
            "rubric_version",
        )
    ):
        raise ValueError("M4_REAL_CHAIN_REVIEWER_OUTPUT_BINDING_INVALID")
    if identity == "independent_quality_reviewer":
        allowed_criterion_ids = {
            criterion["criterion_id"] for criterion in skill_input.get("criteria", [])
        }
        allowed_evidence_fact_ids = {
            fact["evidence_fact_id"] for fact in skill_input.get("evidence_facts", [])
        }
        if any(
            observation.get("criterion_id") not in allowed_criterion_ids
            or not set(observation.get("evidence_fact_ids", [])).issubset(
                allowed_evidence_fact_ids
            )
            for observation in output.get("observations", [])
        ):
            raise ValueError("M4_REAL_CHAIN_REVIEWER_OUTPUT_REFERENCE_INVALID")
    return output


def _atomic_create(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError("M4_REAL_CHAIN_RESULT_ALREADY_EXISTS")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise ValueError("M4_REAL_CHAIN_RESULT_DIRECTORY_INVALID")
    content = canonical_json_bytes(value) + b"\n"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _report_failure(exc: Exception) -> None:
    """Emit only the exception type and an allowlisted fixed diagnostic code."""

    print(f"M4_REAL_CHAIN=FAIL:{type(exc).__name__}", file=sys.stderr)
    if isinstance(exc, MatrixDelegationError) and exc.safe_helper_code is not None:
        print(
            f"M4_REAL_CHAIN_MATRIX_FAILURE_CODE={exc.safe_helper_code}",
            file=sys.stderr,
        )
    elif isinstance(exc, ValueError):
        safe_code = str(exc)
        if _SAFE_VALUE_ERROR_CODE.fullmatch(safe_code) is not None:
            print(f"M4_REAL_CHAIN_FAILURE_CODE={safe_code}", file=sys.stderr)


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        raise ValueError("M4_REAL_CHAIN_DOCKER_EXECUTABLE_REQUIRED")
    docker_executable = str(_regular_file(Path(sys.argv[1]), "M4_REAL_CHAIN_DOCKER_EXECUTABLE_INVALID"))
    live_raw = _regular_file(LIVE_CONFIG_PATH, "M4_REAL_CHAIN_LIVE_CONFIG_INVALID").read_bytes()
    config = load_live_runtime_config(LIVE_CONFIG_PATH)
    plans = _plan_map(config.plans)
    packages, package_hashes, state_version = _load_packages(
        program_id=config.program_id,
        run_id=config.run_id,
        snapshot_id=config.runtime_config_snapshot_id,
        plans=plans,
    )
    before_snapshot = _manager_snapshot(program_id=config.program_id, run_id=config.run_id)
    before_program = before_snapshot["program"]
    if int(before_program.get("state_version", -1)) != state_version:
        raise ValueError("M4_REAL_CHAIN_MANAGER_STATE_VERSION_MISMATCH")

    port = MatrixManagerDelegationPort(
        request_markers={identity: plans[identity].request_marker for identity, _, _ in WORKERS},
        trusted_worker_packages=packages,
        docker_executable=docker_executable,
        helper_timeout_seconds=20,
    )
    records: list[dict[str, Any]] = []
    for identity, skill_name, _ in WORKERS:
        if identity == "independent_quality_reviewer":
            delivery_id = port.dispatch_reviewer_contract_smoke(
                context={
                    "program_id": config.program_id,
                    "run_id": config.run_id,
                    "state_version": state_version,
                }
            )
        else:
            delivery_id = port.dispatch(
                target_identity=identity,
                payload={
                    "program_id": config.program_id,
                    "run_id": config.run_id,
                    "state_version": state_version,
                    "skill_name": skill_name,
                    "skill_version": "1.0.0",
                },
            )
        response = port.await_response(
            target_identity=identity,
            delivery_id=delivery_id,
            timeout_seconds=240,
        )
        output = _parse_worker_output(
            identity=identity,
            skill_name=skill_name,
            text=response.text,
            skill_input=packages[identity],
        )
        output_bytes = canonical_json_bytes(output)
        plan = plans[identity]
        records.append(
            {
                "sequence": len(records) + 1,
                "agent_identity_id": identity,
                "skill_name": skill_name,
                "skill_version": "1.0.0",
                "model_call_id": plan.model_call_id,
                "reservation_id": plan.reservation_id,
                "request_marker_sha256": port.request_marker_hash_for(
                    target_identity=identity
                ),
                "trusted_package_sha256": package_hashes[identity],
                "delivery_id": delivery_id,
                "response_event_id": response.response_event_id,
                "output_sha256": sha256(output_bytes).hexdigest(),
                "output": output,
            }
        )

    after_snapshot = _manager_snapshot(program_id=config.program_id, run_id=config.run_id)
    after_program = after_snapshot["program"]
    if (
        int(after_program.get("state_version", -1)) != state_version
        or after_program.get("active_plan_version_id")
        != before_program.get("active_plan_version_id")
    ):
        raise ValueError("M4_REAL_CHAIN_UNEXPECTED_BUSINESS_STATE_CHANGE")

    result = {
        "schema_version": 1,
        "authorization_id": LIVE_AUTHORIZATION_ID,
        "status": "completed",
        "live_config_sha256": sha256(live_raw).hexdigest(),
        "state_binding": {
            "program_id": config.program_id,
            "run_id": config.run_id,
            "program_state_version_before": state_version,
            "program_state_version_after": int(after_program["state_version"]),
            "active_plan_version_id_before": before_program.get("active_plan_version_id"),
            "active_plan_version_id_after": after_program.get("active_plan_version_id"),
            "runtime_config_snapshot_id": config.runtime_config_snapshot_id,
        },
        "manager_provider_call_count": 0,
        "worker_provider_call_plan_count": 3,
        "provider_retry_count": 0,
        "business_state_changed": False,
        "calls": records,
    }
    _atomic_create(RESULT_PATH, result)
    print("M4_REAL_CHAIN=PASS")
    print("M4_REAL_CHAIN_CALL_COUNT=3")
    print("M4_REAL_CHAIN_MANAGER_PROVIDER_CALL_COUNT=0")
    print("M4_REAL_CHAIN_PROVIDER_RETRY_COUNT=0")
    print("M4_REAL_CHAIN_OUTPUT_SCHEMA_VALID_COUNT=3")
    print("M4_REAL_CHAIN_BUSINESS_STATE_CHANGED=false")
    print(f"M4_REAL_CHAIN_LIVE_CONFIG_SHA256={result['live_config_sha256']}")
    for record in records:
        label = record["agent_identity_id"].upper()
        print(f"M4_REAL_CHAIN_{label}_MODEL_CALL_ID={record['model_call_id']}")
        print(f"M4_REAL_CHAIN_{label}_OUTPUT_SHA256={record['output_sha256']}")
    print("M4_REAL_CHAIN_RESULT_PATH=tmp/m4/provider/real-chain-results.json")
    print("M4_REAL_CHAIN_CONTENT_ECHOED=false")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        _report_failure(exc)
        raise SystemExit(1) from None
