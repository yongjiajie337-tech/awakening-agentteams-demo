"""Fresh, in-place AgentTeams visual Demo driver.

This module is an additive adapter around the accepted M4 AgentTeams and
Gateway components.  It never reuses the historical M4 authorization or
result paths.  The three commands intentionally separate durable preparation,
Gateway serving, and the single no-retry Matrix chain::

    prepare --run-dir <fresh-direct-child-of-tmp/demo/agentteams-in-place>
    serve-gateway --mode fail-closed|live --run-dir <same-directory>
    run-chain --run-dir <same-directory>

Importing this file does not read a Secret, open a database, bind a socket,
run Docker, send Matrix messages, or contact a Provider.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from hashlib import sha256
import importlib.util
from ipaddress import IPv4Address, IPv4Network, ip_address
import json
import os
from pathlib import Path
import re
import socket
import sys
from threading import Lock
from types import ModuleType
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4


WORKSPACE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKSPACE / "src"))

from awakening.adapters.m4 import (  # noqa: E402
    GATEWAY_PRINCIPAL_ID,
    GatewayStateAuthorityAdapter,
    M4InternalStateAdapter,
    M4StateMcpAdapter,
)
from awakening.context_manifest.m4 import (  # noqa: E402
    PostgresContextManifestStore,
    PostgresInvocationReceiptStore,
)
from awakening.model_gateway.m4 import (  # noqa: E402
    M4ModelGateway,
    OpenAICompatibleProvider,
    ProviderRequest,
    ProviderResponse,
    TrustedOpenAICompatibleHttpAdapter,
    build_http_server,
)
from awakening.model_gateway.m4.fail_closed_runtime import (  # noqa: E402
    build_fail_closed_adapter,
)
from awakening.model_gateway.m4.live_runtime import (  # noqa: E402
    LiveBudgetCaps,
    LiveRuntimeConfiguration,
    LiveRuntimeConfigurationError,
    _assert_committed_preconditions,
    _build_runtime_security,
    _exact_fields,
    _load_document,
    _observability_dsn,
    _parse_parameters,
    _parse_plans,
    _parse_provider,
    _regular_file as _m4_regular_file,
    _uuid_text as _m4_uuid_text,
)
from awakening.orchestration.m4 import BoundRuntimeAuthorizer  # noqa: E402
from awakening.orchestration.m4.matrix_delegation import (  # noqa: E402
    MatrixDelegationError,
    MatrixManagerDelegationPort,
)
from awakening.state.admin import build_runtime_dsn, load_m2_env  # noqa: E402
from awakening.state.contracts import (  # noqa: E402
    PrincipalType,
    QueryType,
    TrustedPrincipal,
)
from awakening.state.m4 import (  # noqa: E402
    M4AgentIdentity,
    M4PostgresStateStore,
    M4StateServiceFacade,
    ModelBudgetRequest,
    RuntimeConfigSpec,
    TrustedRuntimeContext,
)
from awakening.state.m4.admin import load_m4_env  # noqa: E402
from awakening.state.validation import canonical_json_bytes  # noqa: E402


AUTHORIZATION_ID = "AUTH-DEMO-001"
SCHEMA_VERSION = 1
PACKAGE_KIND = "agentteams_in_place_demo_skill_input"
SKILL_VERSION = "1.0.0"

PROVIDER_ALIAS = "aliyun-model-studio-official"
MODEL_ID = "qwen3.7-flash-2026-07-15"
PROVIDER_ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1"
PROVIDER_HOSTNAME = "dashscope.aliyuncs.com"
PROVIDER_TIMEOUT_SECONDS = 60
PROVIDER_SECRET_FIELD = "AWAKENING_M5_PROVIDER_API_KEY"
PROVIDER_RESOLVED_IPV4_ENV = "AWAKENING_DEMO_PROVIDER_RESOLVED_IPV4"
PROVIDER_PROXY_ENV_FIELDS = frozenset(
    {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}
)
PROVIDER_FAKE_IP_BENCHMARK_NETWORK = IPv4Network("198.18.0.0/15")

# User-confirmed standard price, represented as micro-CNY per million tokens.
INPUT_MICROCNY_PER_MILLION = 200_000
OUTPUT_MICROCNY_PER_MILLION = 800_000

# This is the user's Demo-only reservation ceiling: up to ¥10 for each of the
# three Worker calls and ¥30 for the fresh Demo run.  It does not alter the M4
# or M5 production policy, the fixed token maxima, or the three-call boundary.
CAPS: Mapping[str, int] = {
    "max_calls": 3,
    "max_input_tokens_per_call": 64_000,
    "max_output_tokens_per_call": 1_000,
    "max_cost_microunits_per_call": 10_000_000,
    "max_total_input_tokens": 192_000,
    "max_total_output_tokens": 3_000,
    "max_total_cost_microunits": 30_000_000,
}
PARAMETERS: Mapping[str, Any] = {
    "temperature": 0.01,
    "seed": 0,
    "enable_thinking": False,
    "response_format": {"type": "json_object"},
}

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
EXCLUSIONS: Mapping[str, tuple[str, ...]] = {
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

DEMO_BASE = WORKSPACE / "tmp" / "demo" / "agentteams-in-place"
DEFAULT_FIXTURE = WORKSPACE / "tmp" / "m4" / "state" / "runtime-state.json"
DEFAULT_M2_ENV = WORKSPACE / ".env.m2"
DEFAULT_M4_ENV = WORKSPACE / ".env.m4"
DEFAULT_PROVIDER_SECRET = WORKSPACE / ".env.m5.provider"
DEFAULT_RUNTIME_CREDENTIALS = (
    WORKSPACE
    / "tmp"
    / "m4"
    / "m4-runtime-secrets-v1"
    / "gateway-credentials.env"
)
DEFAULT_DOCKER = (
    Path(os.environ.get("LOCALAPPDATA", "C:/Users/Public/AppData/Local"))
    / "Programs"
    / "DockerDesktop"
    / "resources"
    / "bin"
    / "docker.exe"
)

_PACKAGE_FIELDS = {
    "schema_version",
    "package_kind",
    "authorization_id",
    "demo_request_id",
    "agent_identity_id",
    "skill_name",
    "skill_version",
    "state_binding",
    "call_binding",
    "skill_input",
}
_PROVIDER_KEY = re.compile(r"[\x21-\x7e]{20,8192}\Z", re.ASCII)
_SAFE_CODE = re.compile(
    r"^(?:DEMO|M4)_[A-Z0-9_]+(?::[a-z0-9_]+)?$",
    re.ASCII,
)
_SAFE_RUN_DIR_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z", re.ASCII)
_EVENT_ID = re.compile(r"\$[A-Za-z0-9._~+:/=-]{1,255}\Z", re.ASCII)
_SENSITIVE_JSON = re.compile(
    r"(?:access[_ -]?token|api[_ -]?key|password|authorization[\"']?\s*:|"
    r"bearer\s+[A-Za-z0-9._~-]{12,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"HICLAW_(?:LLM_API_KEY|MANAGER_PASSWORD|MANAGER_GATEWAY_KEY)|"
    r"WORKER_(?:PASSWORD|GATEWAY_KEY|MATRIX_TOKEN))",
    re.IGNORECASE,
)

_SCRIPT_MODULES: dict[str, ModuleType] = {}


@dataclass(frozen=True, slots=True)
class DemoGatewayRuntime:
    adapter: TrustedOpenAICompatibleHttpAdapter
    provider: "ObservedProvider"
    config: LiveRuntimeConfiguration


@dataclass(frozen=True, slots=True)
class DemoProviderDnsOverride:
    restore: Callable[[], None]
    count: int
    binding_sha256: str

    def __iter__(self):
        # Preserve the existing two-item installer contract for focused tests
        # and callers while exposing the additive safe binding digest.
        yield self.restore
        yield self.count


class ObservedProvider:
    """Thread-safe, secret-free concurrency and usage projection."""

    def __init__(self, delegate: OpenAICompatibleProvider) -> None:
        self._delegate = delegate
        self._lock = Lock()
        self._inflight = 0
        self._max_inflight = 0
        self._call_count = 0

    @property
    def provider_alias(self) -> str:
        return self._delegate.provider_alias

    def invoke(self, request: ProviderRequest) -> ProviderResponse:
        with self._lock:
            if self._call_count >= 3 or self._inflight >= 3:
                raise RuntimeError("DEMO_PROVIDER_CALL_CAP_EXCEEDED")
            self._inflight += 1
            self._call_count += 1
            self._max_inflight = max(self._max_inflight, self._inflight)
            inflight = self._inflight
            call_count = self._call_count
            maximum = self._max_inflight
        print(
            "AUTH_DEMO_001_PROVIDER_CALL_BEGIN="
            f"{request.model_call_id}|call={call_count}|inflight={inflight}|"
            f"max_inflight={maximum}",
            flush=True,
        )
        try:
            response = self._delegate.invoke(request)
            print(
                "AUTH_DEMO_001_PROVIDER_CALL_SUCCEEDED="
                f"{request.model_call_id}|input_tokens={response.input_tokens}|"
                f"output_tokens={response.output_tokens}|"
                f"cost_microcny={response.cost_microunits}",
                flush=True,
            )
            return response
        except BaseException as exc:
            print(
                "AUTH_DEMO_001_PROVIDER_CALL_FAILED="
                f"{request.model_call_id}|error_type={type(exc).__name__}",
                flush=True,
            )
            raise
        finally:
            with self._lock:
                self._inflight -= 1
                remaining = self._inflight
                maximum = self._max_inflight
            print(
                "AUTH_DEMO_001_PROVIDER_CALL_END="
                f"{request.model_call_id}|inflight={remaining}|"
                f"max_inflight={maximum}",
                flush=True,
            )


def _load_script_module(name: str, path: Path) -> ModuleType:
    cached = _SCRIPT_MODULES.get(name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError("DEMO_M4_HELPER_MODULE_INVALID")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    _SCRIPT_MODULES[name] = module
    return module


def _provision_helpers() -> ModuleType:
    return _load_script_module(
        "awakening_demo_m4_provision_helpers",
        WORKSPACE / "scripts" / "m4" / "provision-provider-state.py",
    )


def _real_chain_helpers() -> ModuleType:
    return _load_script_module(
        "awakening_demo_m4_real_chain_helpers",
        WORKSPACE / "scripts" / "m4" / "run-real-chain.py",
    )


def _uuid_text(value: Any, reason: str, *, version: int | None = None) -> str:
    try:
        parsed = UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(reason) from exc
    normalized = str(parsed)
    if str(value) != normalized or (version is not None and parsed.version != version):
        raise ValueError(reason)
    return normalized


def _canonical_file_bytes(value: Mapping[str, Any]) -> bytes:
    return canonical_json_bytes(value) + b"\n"


def _read_json(path: Path, reason: str) -> tuple[bytes, Mapping[str, Any]]:
    file_path = _regular_file(path, reason)
    try:
        raw = file_path.read_bytes()
        if not raw or len(raw) > 1_048_576:
            raise ValueError(reason)
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(reason) from exc
    if not isinstance(value, Mapping):
        raise ValueError(reason)
    return raw, value


def _regular_file(path: Path, reason: str) -> Path:
    try:
        if not path.is_file() or path.is_symlink():
            raise ValueError(reason)
        return path.resolve(strict=True)
    except OSError as exc:
        raise ValueError(reason) from exc


def _candidate_path(path: Path) -> Path:
    return path if path.is_absolute() else WORKSPACE / path


def _ensure_demo_base() -> Path:
    DEMO_BASE.mkdir(parents=True, exist_ok=True)
    if DEMO_BASE.is_symlink() or not DEMO_BASE.is_dir():
        raise ValueError("DEMO_BASE_DIRECTORY_INVALID")
    return DEMO_BASE.resolve(strict=True)


def _new_run_dir(path: Path) -> Path:
    base = _ensure_demo_base()
    candidate = _candidate_path(path)
    if _SAFE_RUN_DIR_NAME.fullmatch(candidate.name) is None:
        raise ValueError("DEMO_RUN_DIRECTORY_NAME_INVALID")
    try:
        parent = candidate.parent.resolve(strict=True)
    except OSError as exc:
        raise ValueError("DEMO_RUN_DIRECTORY_PARENT_INVALID") from exc
    if parent != base or candidate.exists() or candidate.is_symlink():
        raise ValueError("DEMO_RUN_DIRECTORY_NOT_FRESH")
    candidate.mkdir(exist_ok=False)
    resolved = candidate.resolve(strict=True)
    if resolved.parent != base or resolved.is_symlink():
        raise ValueError("DEMO_RUN_DIRECTORY_INVALID")
    (resolved / "packages").mkdir(exist_ok=False)
    (resolved / "runtime").mkdir(exist_ok=False)
    return resolved


def _existing_run_dir(path: Path) -> Path:
    base = _ensure_demo_base()
    candidate = _candidate_path(path)
    if _SAFE_RUN_DIR_NAME.fullmatch(candidate.name) is None:
        raise ValueError("DEMO_RUN_DIRECTORY_NAME_INVALID")
    try:
        if candidate.is_symlink() or not candidate.is_dir():
            raise ValueError("DEMO_RUN_DIRECTORY_INVALID")
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError("DEMO_RUN_DIRECTORY_INVALID") from exc
    if resolved.parent != base:
        raise ValueError("DEMO_RUN_DIRECTORY_OUTSIDE_BASE")
    for name in ("packages", "runtime"):
        child = resolved / name
        if child.is_symlink() or not child.is_dir():
            raise ValueError("DEMO_RUN_DIRECTORY_INCOMPLETE")
    return resolved


def _atomic_create(path: Path, content: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError("DEMO_OUTPUT_ALREADY_EXISTS")
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise ValueError("DEMO_OUTPUT_DIRECTORY_INVALID")
    temporary = parent / f".{path.name}.{uuid4().hex}.tmp"
    published = False
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
        published = True
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            if not published:
                raise


def _load_fixture(path: Path) -> dict[str, Any]:
    _, value = _read_json(path, "DEMO_FIXTURE_INVALID")
    required = {
        "schema_version",
        "status",
        "program_id",
        "run_id",
        "owner_principal_id",
        "initial_plan_version_id",
        "initial_state_version",
    }
    allowed = {*required, "updated_at_utc"}
    if not required.issubset(value) or not set(value).issubset(allowed):
        raise ValueError("DEMO_FIXTURE_FIELDS_INVALID")
    if value["schema_version"] != 1 or value["status"] != "ready":
        raise ValueError("DEMO_FIXTURE_NOT_READY")
    owner = value["owner_principal_id"]
    if not isinstance(owner, str) or not owner or len(owner) > 128:
        raise ValueError("DEMO_FIXTURE_OWNER_INVALID")
    state_version = value["initial_state_version"]
    if isinstance(state_version, bool) or not isinstance(state_version, int) or state_version < 0:
        raise ValueError("DEMO_FIXTURE_STATE_VERSION_INVALID")
    return {
        "program_id": _uuid_text(value["program_id"], "DEMO_PROGRAM_ID_INVALID"),
        "source_run_id": _uuid_text(value["run_id"], "DEMO_SOURCE_RUN_ID_INVALID"),
        "owner_principal_id": owner,
        "initial_state_version": state_version,
    }


def _state_service(m2_env_path: Path) -> M4StateServiceFacade:
    return M4StateServiceFacade(
        M4PostgresStateStore(
            build_runtime_dsn(
                load_m2_env(_regular_file(m2_env_path, "DEMO_M2_ENV_INVALID"))
            )
        )
    )


def _program_snapshot(
    service: M4StateServiceFacade,
    *,
    fixture: Mapping[str, Any],
) -> Mapping[str, Any]:
    principal = TrustedPrincipal(
        principal_id=str(fixture["owner_principal_id"]),
        principal_type=PrincipalType.USER,
        scopes=("state:read",),
        program_scope=(str(fixture["program_id"]),),
        auth_context_id=f"demo-read-{AUTHORIZATION_ID}",
    )
    snapshot = service.query(
        query_type=QueryType.PROGRAM_SNAPSHOT_GET,
        program_id=str(fixture["program_id"]),
        payload={},
        trusted_principal=principal,
    )
    program = snapshot.get("program") if isinstance(snapshot, Mapping) else None
    if not isinstance(program, Mapping) or program.get("program_id") != fixture["program_id"]:
        raise ValueError("DEMO_PROGRAM_SNAPSHOT_INVALID")
    state_version = program.get("state_version")
    if (
        isinstance(state_version, bool)
        or not isinstance(state_version, int)
        or state_version < int(fixture["initial_state_version"])
    ):
        raise ValueError("DEMO_PROGRAM_STATE_VERSION_INVALID")
    return snapshot


def _gateway_principal(program_id: str, run_id: str) -> TrustedPrincipal:
    return TrustedPrincipal(
        principal_id=GATEWAY_PRINCIPAL_ID,
        principal_type=PrincipalType.SERVICE,
        scopes=("model:governance",),
        program_scope=(program_id,),
        auth_context_id=f"demo-{AUTHORIZATION_ID}-{run_id}",
    )


def _build_artifacts(
    *,
    fixture: Mapping[str, Any],
    run_id: str,
    demo_request_id: str,
    snapshot_id: str,
    state_version: int,
    reservations: Mapping[str, Mapping[str, str]],
) -> tuple[dict[str, bytes], dict[str, str]]:
    helper = _provision_helpers()
    package_bytes: dict[str, bytes] = {}
    package_hashes: dict[str, str] = {}
    plans: dict[str, Any] = {}
    for identity, skill_name, filename in WORKERS:
        binding = reservations.get(identity)
        if binding is None:
            raise ValueError("DEMO_RESERVATION_SET_INVALID")
        marker = binding["request_marker"]
        marker_hash = sha256(marker.encode("ascii")).hexdigest()
        skill_input = helper._skill_input(
            identity=identity,
            program_id=str(fixture["program_id"]),
            run_id=run_id,
            state_version=state_version,
            snapshot_id=snapshot_id,
        )
        package = {
            "schema_version": SCHEMA_VERSION,
            "package_kind": PACKAGE_KIND,
            "authorization_id": AUTHORIZATION_ID,
            "demo_request_id": demo_request_id,
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
                "request_marker_sha256": marker_hash,
            },
            "skill_input": skill_input,
        }
        raw = _canonical_file_bytes(package)
        package_bytes[f"packages/{filename}"] = raw
        package_hash = sha256(raw).hexdigest()
        package_hashes[identity] = package_hash
        plans[identity] = {
            "model_call_id": binding["model_call_id"],
            "reservation_id": binding["reservation_id"],
            "skill_name": skill_name,
            "skill_version": SKILL_VERSION,
            "request_marker": marker,
            "object_refs": [
                {
                    "object_type": "agentteams_demo_skill_input_package",
                    "object_id": f"{identity}:{binding['model_call_id']}",
                    "object_version": "1",
                    "content_sha256": package_hash,
                }
            ],
            "exclusions": list(EXCLUSIONS[identity]),
        }
    if set(reservations) != {identity for identity, _, _ in WORKERS}:
        raise ValueError("DEMO_RESERVATION_SET_INVALID")
    config = {
        "authorization_id": AUTHORIZATION_ID,
        "schema_version": SCHEMA_VERSION,
        "provider": {
            "provider_alias": PROVIDER_ALIAS,
            "endpoint": PROVIDER_ENDPOINT,
            "allowed_hostname": PROVIDER_HOSTNAME,
            "model_id": MODEL_ID,
            "public_model_alias": MODEL_ID,
            "input_microunits_per_million": INPUT_MICROCNY_PER_MILLION,
            "output_microunits_per_million": OUTPUT_MICROCNY_PER_MILLION,
            "timeout_seconds": PROVIDER_TIMEOUT_SECONDS,
        },
        "parameters": dict(PARAMETERS),
        "caps": dict(CAPS),
        "state_binding": {
            "program_id": fixture["program_id"],
            "run_id": run_id,
            "runtime_config_snapshot_id": snapshot_id,
        },
        "plans": plans,
    }
    package_bytes["live-gateway-config.json"] = _canonical_file_bytes(config)
    return package_bytes, package_hashes


def _prepare(args: argparse.Namespace) -> int:
    run_dir = _new_run_dir(args.run_dir)
    fixture = _load_fixture(_candidate_path(args.fixture_state))
    run_id = str(uuid4())
    demo_request_id = str(uuid4())
    call_ids = {
        identity: str(uuid4())
        for identity, _, _ in WORKERS
    }
    markers = {
        identity: f"m4-call:{call_ids[identity]}"
        for identity, _, _ in WORKERS
    }

    service = _state_service(_candidate_path(args.m2_env))
    before_snapshot = _program_snapshot(service, fixture=fixture)
    before_program = before_snapshot["program"]
    state_version = int(before_program["state_version"])
    gateway = _gateway_principal(str(fixture["program_id"]), run_id)
    adapter = M4InternalStateAdapter(service)
    runtime_spec = RuntimeConfigSpec(
        run_id=run_id,
        provider_alias=PROVIDER_ALIAS,
        model_id=MODEL_ID,
        parameters=PARAMETERS,
        **dict(CAPS),
    )
    snapshot = adapter.create_runtime_config_snapshot(
        program_id=str(fixture["program_id"]),
        idempotency_key=f"demo-snapshot:{run_id}",
        runtime_config=runtime_spec,
        trusted_context=gateway,
    )
    if not snapshot.committed:
        raise ValueError("DEMO_RUNTIME_CONFIG_SNAPSHOT_REJECTED")
    snapshot_id = _uuid_text(
        snapshot.result.get("snapshot_id"),
        "DEMO_RUNTIME_CONFIG_SNAPSHOT_ID_INVALID",
    )
    if snapshot.result.get("contains_provider_key") is not False:
        raise ValueError("DEMO_RUNTIME_CONFIG_SECRET_BOUNDARY_INVALID")

    reservations: dict[str, dict[str, str]] = {}
    for identity, _skill_name, _filename in WORKERS:
        call_id = call_ids[identity]
        reserved = adapter.reserve_model_budget(
            program_id=str(fixture["program_id"]),
            idempotency_key=f"demo-reserve:{call_id}",
            budget_request=ModelBudgetRequest(
                run_id=run_id,
                model_call_id=call_id,
                snapshot_id=snapshot_id,
                max_input_tokens=CAPS["max_input_tokens_per_call"],
                max_output_tokens=CAPS["max_output_tokens_per_call"],
                max_cost_microunits=CAPS["max_cost_microunits_per_call"],
            ),
            trusted_context=gateway,
        )
        if (
            not reserved.committed
            or reserved.result.get("status") != "reserved"
            or reserved.result.get("provider_call_allowed") is not True
        ):
            raise ValueError("DEMO_MODEL_BUDGET_RESERVATION_REJECTED")
        reservation_id = _uuid_text(
            reserved.result.get("reservation_id"),
            "DEMO_RESERVATION_ID_INVALID",
        )
        reservations[identity] = {
            "model_call_id": call_id,
            "reservation_id": reservation_id,
            "request_marker": markers[identity],
        }

    if len(reservations) != 3 or len(
        {item["reservation_id"] for item in reservations.values()}
    ) != 3:
        raise ValueError("DEMO_RESERVATION_CARDINALITY_INVALID")
    for identity, binding in reservations.items():
        current = service.get_model_budget_reservation(
            program_id=str(fixture["program_id"]),
            reservation_id=binding["reservation_id"],
            trusted_principal=gateway,
        )
        if any(
            current.get(field) != expected
            for field, expected in {
                "reservation_id": binding["reservation_id"],
                "program_id": fixture["program_id"],
                "run_id": run_id,
                "model_call_id": binding["model_call_id"],
                "snapshot_id": snapshot_id,
                "status": "reserved",
                "reservation_version": 1,
            }.items()
        ):
            raise ValueError(f"DEMO_RESERVATION_CURRENT_STATE_INVALID:{identity}")

    after_snapshot = _program_snapshot(service, fixture=fixture)
    after_program = after_snapshot["program"]
    if (
        int(after_program["state_version"]) != state_version
        or after_program.get("active_plan_version_id")
        != before_program.get("active_plan_version_id")
    ):
        raise ValueError("DEMO_PREPARE_CHANGED_BUSINESS_STATE")

    artifacts, package_hashes = _build_artifacts(
        fixture=fixture,
        run_id=run_id,
        demo_request_id=demo_request_id,
        snapshot_id=snapshot_id,
        state_version=state_version,
        reservations=reservations,
    )
    # Publish the config last.  Its presence is the bundle commit marker.
    for identity, _skill_name, filename in WORKERS:
        relative = f"packages/{filename}"
        _atomic_create(run_dir / relative, artifacts[relative])
    _atomic_create(
        run_dir / "live-gateway-config.json",
        artifacts["live-gateway-config.json"],
    )

    print("AUTH_DEMO_001_PREPARE=PASS")
    print(f"AUTH_DEMO_001_RUN_DIR={run_dir}")
    print(f"AUTH_DEMO_001_RUN_ID={run_id}")
    print(f"AUTH_DEMO_001_REQUEST_ID={demo_request_id}")
    print(f"AUTH_DEMO_001_SNAPSHOT_ID={snapshot_id}")
    print("AUTH_DEMO_001_RESERVATION_COUNT=3")
    print("AUTH_DEMO_001_PACKAGE_COUNT=3")
    print(
        "AUTH_DEMO_001_LIVE_CONFIG_SHA256="
        f"{sha256(artifacts['live-gateway-config.json']).hexdigest()}"
    )
    for identity, _skill_name, _filename in WORKERS:
        print(
            f"AUTH_DEMO_001_{identity.upper()}_MODEL_CALL_ID="
            f"{reservations[identity]['model_call_id']}"
        )
        print(
            f"AUTH_DEMO_001_{identity.upper()}_RESERVATION_ID="
            f"{reservations[identity]['reservation_id']}"
        )
        print(
            f"AUTH_DEMO_001_{identity.upper()}_PACKAGE_SHA256="
            f"{package_hashes[identity]}"
        )
    print("AUTH_DEMO_001_PROVIDER_SECRET_READ=false")
    print("AUTH_DEMO_001_CONTENT_ECHOED=false")
    return 0


def _parse_demo_live_caps(value: Any) -> LiveBudgetCaps:
    record = _exact_fields(
        value,
        set(CAPS),
        "DEMO_LIVE_CAP_FIELDS_INVALID",
    )
    if any(
        isinstance(record[field], bool)
        or not isinstance(record[field], int)
        or record[field] != expected
        for field, expected in CAPS.items()
    ):
        raise LiveRuntimeConfigurationError("DEMO_LIVE_CAP_VALUE_INVALID")
    return LiveBudgetCaps(**dict(CAPS))


def _load_demo_live_config(path: Path) -> LiveRuntimeConfiguration:
    config_path = _m4_regular_file(path, "DEMO_LIVE_CONFIG_FILE_INVALID")
    document = _load_document(config_path)
    if document["authorization_id"] != AUTHORIZATION_ID:
        raise LiveRuntimeConfigurationError("DEMO_LIVE_AUTHORIZATION_ID_INVALID")
    if document["schema_version"] != SCHEMA_VERSION:
        raise LiveRuntimeConfigurationError("DEMO_LIVE_CONFIG_SCHEMA_INVALID")
    provider = _parse_provider(document["provider"])
    try:
        endpoint = urlsplit(provider.endpoint)
    except (TypeError, ValueError) as exc:
        raise LiveRuntimeConfigurationError(
            "DEMO_LIVE_PROVIDER_ENDPOINT_HOST_INVALID"
        ) from exc
    if (
        endpoint.scheme != "https"
        or endpoint.netloc != PROVIDER_HOSTNAME
        or endpoint.hostname != PROVIDER_HOSTNAME
        or provider.allowed_hostname != PROVIDER_HOSTNAME
    ):
        raise LiveRuntimeConfigurationError(
            "DEMO_LIVE_PROVIDER_ENDPOINT_HOST_INVALID"
        )
    if (
        provider.input_microunits_per_million != INPUT_MICROCNY_PER_MILLION
        or provider.output_microunits_per_million != OUTPUT_MICROCNY_PER_MILLION
    ):
        raise LiveRuntimeConfigurationError("DEMO_LIVE_PROVIDER_PRICE_INVALID")
    parameters = _parse_parameters(document["parameters"])
    caps = _parse_demo_live_caps(document["caps"])
    if caps.to_dict() != dict(CAPS):
        raise LiveRuntimeConfigurationError("DEMO_LIVE_CAPS_INVALID")
    state_binding = _exact_fields(
        document["state_binding"],
        {"program_id", "run_id", "runtime_config_snapshot_id"},
        "DEMO_LIVE_STATE_BINDING_FIELDS_INVALID",
    )
    per_call_cost = (
        caps.max_input_tokens_per_call * provider.input_microunits_per_million
        + caps.max_output_tokens_per_call * provider.output_microunits_per_million
        + 999_999
    ) // 1_000_000
    total_cost = (
        caps.max_total_input_tokens * provider.input_microunits_per_million
        + caps.max_total_output_tokens * provider.output_microunits_per_million
        + 999_999
    ) // 1_000_000
    if (
        per_call_cost > caps.max_cost_microunits_per_call
        or total_cost > caps.max_total_cost_microunits
    ):
        raise LiveRuntimeConfigurationError("DEMO_LIVE_RATE_EXCEEDS_COST_CAP")
    return LiveRuntimeConfiguration(
        provider=provider,
        parameters=parameters,
        caps=caps,
        program_id=_m4_uuid_text(
            state_binding["program_id"], "DEMO_LIVE_PROGRAM_ID_INVALID"
        ),
        run_id=_m4_uuid_text(state_binding["run_id"], "DEMO_LIVE_RUN_ID_INVALID"),
        runtime_config_snapshot_id=_m4_uuid_text(
            state_binding["runtime_config_snapshot_id"],
            "DEMO_LIVE_SNAPSHOT_ID_INVALID",
        ),
        plans=_parse_plans(document["plans"]),
    )


def _parse_demo_provider_resolved_ipv4(value: str | None) -> tuple[str, ...]:
    if value is None:
        raise ValueError("DEMO_PROVIDER_RESOLVED_IPV4_REQUIRED")
    parts = value.split(",")
    if not 1 <= len(parts) <= 8:
        raise ValueError("DEMO_PROVIDER_RESOLVED_IPV4_INVALID")

    resolved: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if not part or part.strip() != part:
            raise ValueError("DEMO_PROVIDER_RESOLVED_IPV4_INVALID")
        try:
            address = ip_address(part)
        except ValueError as exc:
            raise ValueError("DEMO_PROVIDER_RESOLVED_IPV4_INVALID") from exc
        if (
            not isinstance(address, IPv4Address)
            or str(address) != part
            or address in PROVIDER_FAKE_IP_BENCHMARK_NETWORK
            or not address.is_global
            or address.is_private
            or address.is_reserved
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_unspecified
            or part in seen
        ):
            raise ValueError("DEMO_PROVIDER_RESOLVED_IPV4_INVALID")
        seen.add(part)
        resolved.append(part)
    return tuple(resolved)


def _assert_demo_provider_proxy_env_absent() -> None:
    if any(
        name.upper() in PROVIDER_PROXY_ENV_FIELDS and bool(value)
        for name, value in os.environ.items()
    ):
        raise ValueError("DEMO_PROVIDER_PROXY_ENV_FORBIDDEN")


def _install_demo_provider_dns_override(
    endpoint: str,
) -> DemoProviderDnsOverride:
    _assert_demo_provider_proxy_env_absent()
    try:
        parsed_endpoint = urlsplit(endpoint)
    except (TypeError, ValueError) as exc:
        raise ValueError("DEMO_PROVIDER_ENDPOINT_HOST_INVALID") from exc
    if (
        parsed_endpoint.scheme != "https"
        or parsed_endpoint.netloc != PROVIDER_HOSTNAME
        or parsed_endpoint.hostname != PROVIDER_HOSTNAME
    ):
        raise ValueError("DEMO_PROVIDER_ENDPOINT_HOST_INVALID")

    addresses = _parse_demo_provider_resolved_ipv4(
        os.environ.get(PROVIDER_RESOLVED_IPV4_ENV)
    )
    binding_sha256 = sha256(",".join(addresses).encode("ascii")).hexdigest()
    original_getaddrinfo = socket.getaddrinfo

    def demo_provider_getaddrinfo(
        host: str | bytes | None,
        port: Any,
        family: int = 0,
        type: int = 0,
        proto: int = 0,
        flags: int = 0,
    ) -> list[tuple[Any, ...]]:
        if host != PROVIDER_HOSTNAME:
            return original_getaddrinfo(host, port, family, type, proto, flags)

        # Resolve only the exact Provider hostname to the lifecycle-validated
        # numeric addresses.  The URL hostname remains unchanged, so HTTPS
        # certificate verification and SNI continue to use the Provider name.
        results: list[tuple[Any, ...]] = []
        last_error: socket.gaierror | None = None
        numeric_flags = flags | socket.AI_NUMERICHOST
        for address in addresses:
            try:
                results.extend(
                    original_getaddrinfo(
                        address,
                        port,
                        family,
                        type,
                        proto,
                        numeric_flags,
                    )
                )
            except socket.gaierror as exc:
                last_error = exc
        if results:
            return results
        if last_error is not None:
            raise last_error
        raise socket.gaierror(socket.EAI_NONAME, "name resolution failed")

    socket.getaddrinfo = demo_provider_getaddrinfo

    def restore() -> None:
        if socket.getaddrinfo is demo_provider_getaddrinfo:
            socket.getaddrinfo = original_getaddrinfo

    return DemoProviderDnsOverride(
        restore=restore,
        count=len(addresses),
        binding_sha256=binding_sha256,
    )


def _read_m5_provider_key(path: Path) -> str:
    candidate = _regular_file(path, "DEMO_PROVIDER_SECRET_FILE_INVALID")
    expected = _regular_file(
        DEFAULT_PROVIDER_SECRET,
        "DEMO_EXPECTED_PROVIDER_SECRET_FILE_INVALID",
    )
    if candidate != expected:
        raise ValueError("DEMO_PROVIDER_SECRET_EXACT_PATH_REQUIRED")
    try:
        lines = candidate.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError("DEMO_PROVIDER_SECRET_FILE_INVALID") from exc
    if len(lines) != 1:
        raise ValueError("DEMO_PROVIDER_SECRET_FIELDS_INVALID")
    key, separator, value = lines[0].partition("=")
    if (
        separator != "="
        or key != PROVIDER_SECRET_FIELD
        or _PROVIDER_KEY.fullmatch(value) is None
    ):
        raise ValueError("DEMO_PROVIDER_SECRET_FIELDS_INVALID")
    return value


def _build_live_gateway(
    *,
    run_dir: Path,
    provider_secret_path: Path,
    runtime_credential_path: Path,
    m2_env_path: Path,
    m4_env_path: Path,
) -> DemoGatewayRuntime:
    config = _load_demo_live_config(run_dir / "live-gateway-config.json")
    m2_values = load_m2_env(_m4_regular_file(m2_env_path, "DEMO_M2_ENV_INVALID"))
    m4_values = load_m4_env(_m4_regular_file(m4_env_path, "DEMO_M4_ENV_INVALID"))
    state_service = M4StateServiceFacade(
        M4PostgresStateStore(build_runtime_dsn(m2_values))
    )
    state_authority = GatewayStateAuthorityAdapter(
        internal_adapter=M4InternalStateAdapter(state_service),
        trusted_gateway_principal=TrustedPrincipal(
            principal_id=GATEWAY_PRINCIPAL_ID,
            principal_type=PrincipalType.SERVICE,
            scopes=("model:gateway",),
            program_scope=(config.program_id,),
            auth_context_id=f"demo-live-{AUTHORIZATION_ID}-{config.run_id}",
        ),
    )
    _assert_committed_preconditions(config, state_authority)
    credential_registry, invocation_plans = _build_runtime_security(
        config,
        runtime_credential_path,
    )
    observability_dsn = _observability_dsn(m2_values, m4_values)
    manifests = PostgresContextManifestStore(observability_dsn)
    receipts = PostgresInvocationReceiptStore(observability_dsn)

    # This is deliberately the final composition input.  All provider-free
    # config, State, reservation, and runtime-token checks have already passed.
    api_key = _read_m5_provider_key(provider_secret_path)
    print("AUTH_DEMO_001_GATEWAY_PROVIDER_SECRET_READ=true", flush=True)
    print("AUTH_DEMO_001_GATEWAY_PROVIDER_SECRET_ECHOED=false", flush=True)
    delegate = OpenAICompatibleProvider(
        provider_alias=config.provider.provider_alias,
        endpoint=config.provider.endpoint,
        api_key=api_key,
        allowed_hostname=config.provider.allowed_hostname,
        input_microunits_per_million=(
            config.provider.input_microunits_per_million
        ),
        output_microunits_per_million=(
            config.provider.output_microunits_per_million
        ),
        timeout_seconds=float(config.provider.timeout_seconds),
    )
    del api_key
    provider = ObservedProvider(delegate)

    def gateway_factory(session: Any) -> M4ModelGateway:
        return M4ModelGateway(
            state_authority=state_authority,
            manifest_store=manifests,
            invocation_receipt_store=receipts,
            provider=provider,
            runtime_authorizer=BoundRuntimeAuthorizer(session),
        )

    adapter = TrustedOpenAICompatibleHttpAdapter(
        credential_registry=credential_registry,
        invocation_plans=invocation_plans,
        gateway_factory=gateway_factory,
    )
    return DemoGatewayRuntime(adapter=adapter, provider=provider, config=config)


def _serve_gateway(args: argparse.Namespace) -> int:
    run_dir = _existing_run_dir(args.run_dir)
    host = args.host
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("DEMO_GATEWAY_LOOPBACK_REQUIRED")
    if not 1 <= args.port <= 65_535:
        raise ValueError("DEMO_GATEWAY_PORT_INVALID")
    credential_path = _candidate_path(args.runtime_credentials)
    restore_dns: Callable[[], None] | None = None
    server: Any = None
    try:
        if args.mode == "fail-closed":
            adapter = build_fail_closed_adapter(
                _regular_file(
                    credential_path,
                    "DEMO_RUNTIME_CREDENTIAL_FILE_INVALID",
                )
            )
            provider_alias = PROVIDER_ALIAS
            model_id = MODEL_ID
            secret_read = False
            plan_count = 0
            dns_override_count = 0
            print(
                "AUTH_DEMO_001_GATEWAY_PROVIDER_SECRET_READ=false",
                flush=True,
            )
            print(
                "AUTH_DEMO_001_GATEWAY_PROVIDER_SECRET_ECHOED=false",
                flush=True,
            )
        else:
            resolver_config = _load_demo_live_config(
                run_dir / "live-gateway-config.json"
            )
            dns_override = _install_demo_provider_dns_override(
                resolver_config.provider.endpoint
            )
            restore_dns, dns_override_count = dns_override
            print(
                "AUTH_DEMO_001_GATEWAY_PROVIDER_DNS_OVERRIDE_COUNT="
                f"{dns_override_count}",
                flush=True,
            )
            print(
                "AUTH_DEMO_001_GATEWAY_PROVIDER_DNS_OVERRIDE_SHA256="
                f"{dns_override.binding_sha256}",
                flush=True,
            )
            runtime = _build_live_gateway(
                run_dir=run_dir,
                provider_secret_path=_candidate_path(args.provider_secret),
                runtime_credential_path=_candidate_path(args.runtime_credentials),
                m2_env_path=_candidate_path(args.m2_env),
                m4_env_path=_candidate_path(args.m4_env),
            )
            adapter = runtime.adapter
            provider_alias = runtime.config.provider.provider_alias
            model_id = runtime.config.provider.model_id
            secret_read = True
            plan_count = len(runtime.config.plans)
        if args.mode == "fail-closed":
            print(
                "AUTH_DEMO_001_GATEWAY_PROVIDER_DNS_OVERRIDE_COUNT=0",
                flush=True,
            )

        server = build_http_server(adapter, host=host, port=args.port)
        if args.mode == "live":
            live_config_path = _regular_file(
                run_dir / "live-gateway-config.json",
                "DEMO_LIVE_CONFIG_FILE_INVALID",
            )
            ready_document = {
                "schema_version": "awakening.demo.live-gateway-ready.v1",
                "authorization_id": AUTHORIZATION_ID,
                "mode": "live",
                "run_id": runtime.config.run_id,
                "live_config_sha256": sha256(live_config_path.read_bytes()).hexdigest(),
                "host": host,
                "port": args.port,
                "provider_hostname": PROVIDER_HOSTNAME,
                "provider_dns_override_count": dns_override_count,
                "provider_dns_override_sha256": dns_override.binding_sha256,
                "provider_secret_read": True,
                "provider_secret_echoed": False,
                "single_use_plan_count": plan_count,
                "single_use_plan_claim_count": 0,
                "manager_plan_count": 0,
            }
            _atomic_create(
                run_dir / "runtime" / "demo-live-gateway.ready.json",
                _canonical_file_bytes(ready_document),
            )
        print("AUTH_DEMO_001_GATEWAY_READY=true", flush=True)
        print(f"AUTH_DEMO_001_GATEWAY_MODE={args.mode}", flush=True)
        print(
            f"AUTH_DEMO_001_GATEWAY_PROVIDER_ALIAS={provider_alias}",
            flush=True,
        )
        print(f"AUTH_DEMO_001_GATEWAY_MODEL_ID={model_id}", flush=True)
        print(
            "AUTH_DEMO_001_GATEWAY_SINGLE_USE_PLAN_COUNT="
            f"{plan_count}",
            flush=True,
        )
        print(
            "AUTH_DEMO_001_GATEWAY_SINGLE_USE_PLAN_CLAIM_COUNT=0",
            flush=True,
        )
        print("AUTH_DEMO_001_GATEWAY_MANAGER_PLAN_COUNT=0", flush=True)
        print(
            "AUTH_DEMO_001_PROVIDER_SECRET_READ="
            f"{str(secret_read).lower()}",
            flush=True,
        )
        print("AUTH_DEMO_001_GATEWAY_SECRET_ECHOED=false", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    finally:
        if server is not None:
            server.server_close()
        if restore_dns is not None:
            restore_dns()
    return 0


def _load_demo_packages(
    *,
    run_dir: Path,
    config: LiveRuntimeConfiguration,
) -> tuple[dict[str, Mapping[str, Any]], dict[str, str], int, str]:
    plans = {plan.agent_identity_id: plan for plan in config.plans}
    if set(plans) != {identity for identity, _, _ in WORKERS}:
        raise ValueError("DEMO_PLAN_SET_INVALID")
    packages: dict[str, Mapping[str, Any]] = {}
    package_hashes: dict[str, str] = {}
    state_versions: set[int] = set()
    request_ids: set[str] = set()
    helper = _real_chain_helpers()
    for identity, skill_name, filename in WORKERS:
        raw, package = _read_json(
            run_dir / "packages" / filename,
            f"DEMO_PACKAGE_INVALID:{identity}",
        )
        if set(package) != _PACKAGE_FIELDS:
            raise ValueError(f"DEMO_PACKAGE_FIELDS_INVALID:{identity}")
        state_binding = package.get("state_binding")
        call_binding = package.get("call_binding")
        skill_input = package.get("skill_input")
        if not isinstance(state_binding, Mapping) or set(state_binding) != {
            "program_id",
            "run_id",
            "program_state_version",
            "runtime_config_snapshot_id",
        }:
            raise ValueError(f"DEMO_PACKAGE_STATE_BINDING_INVALID:{identity}")
        if not isinstance(call_binding, Mapping) or set(call_binding) != {
            "model_call_id",
            "reservation_id",
            "request_marker_sha256",
        }:
            raise ValueError(f"DEMO_PACKAGE_CALL_BINDING_INVALID:{identity}")
        plan = plans[identity]
        request_id = _uuid_text(
            package.get("demo_request_id"),
            f"DEMO_REQUEST_ID_INVALID:{identity}",
            version=4,
        )
        if (
            package.get("schema_version") != SCHEMA_VERSION
            or package.get("package_kind") != PACKAGE_KIND
            or package.get("authorization_id") != AUTHORIZATION_ID
            or package.get("agent_identity_id") != identity
            or package.get("skill_name") != skill_name
            or package.get("skill_version") != SKILL_VERSION
            or state_binding.get("program_id") != config.program_id
            or state_binding.get("run_id") != config.run_id
            or state_binding.get("runtime_config_snapshot_id")
            != config.runtime_config_snapshot_id
            or call_binding.get("model_call_id") != plan.model_call_id
            or call_binding.get("reservation_id") != plan.reservation_id
            or call_binding.get("request_marker_sha256")
            != sha256(str(plan.request_marker).encode("ascii")).hexdigest()
            or not isinstance(skill_input, Mapping)
        ):
            raise ValueError(f"DEMO_PACKAGE_BINDING_INVALID:{identity}")
        state_version = state_binding.get("program_state_version")
        if isinstance(state_version, bool) or not isinstance(state_version, int) or state_version < 0:
            raise ValueError(f"DEMO_PACKAGE_STATE_VERSION_INVALID:{identity}")
        helper._validate_schema(skill_name, "input", skill_input)
        package_hash = sha256(raw).hexdigest()
        if (
            len(plan.object_refs) != 1
            or plan.object_refs[0].get("content_sha256") != package_hash
        ):
            raise ValueError(f"DEMO_PACKAGE_OBJECT_REF_INVALID:{identity}")
        packages[identity] = dict(skill_input)
        package_hashes[identity] = package_hash
        state_versions.add(state_version)
        request_ids.add(request_id)
    if len(state_versions) != 1 or len(request_ids) != 1:
        raise ValueError("DEMO_PACKAGE_SHARED_BINDING_INVALID")
    return packages, package_hashes, state_versions.pop(), request_ids.pop()


def _manager_snapshot(
    *,
    program_id: str,
    run_id: str,
    m2_env_path: Path,
) -> Mapping[str, Any]:
    service = _state_service(m2_env_path)
    adapter = M4StateMcpAdapter(service)
    context = TrustedRuntimeContext(
        principal_id="m4-runtime-principal-awakening_program_manager",
        agent_identity=M4AgentIdentity.MANAGER,
        program_role="manager",
        program_scope=(program_id,),
        run_id=run_id,
        auth_context_id=f"demo-manager-{AUTHORIZATION_ID}-{run_id}",
    )
    snapshot = adapter.get_snapshot(program_id=program_id, trusted_context=context)
    program = snapshot.get("program") if isinstance(snapshot, Mapping) else None
    if not isinstance(program, Mapping) or program.get("program_id") != program_id:
        raise ValueError("DEMO_MANAGER_SNAPSHOT_INVALID")
    return snapshot


def _worker_call(
    *,
    port: MatrixManagerDelegationPort,
    identity: str,
    skill_name: str,
    program_id: str,
    run_id: str,
    state_version: int,
    skill_input: Mapping[str, Any],
    response_timeout_seconds: int,
) -> dict[str, Any]:
    if identity == "independent_quality_reviewer":
        delivery_id = port.dispatch_reviewer_contract_smoke(
            context={
                "program_id": program_id,
                "run_id": run_id,
                "state_version": state_version,
            }
        )
    else:
        delivery_id = port.dispatch(
            target_identity=identity,
            payload={
                "program_id": program_id,
                "run_id": run_id,
                "state_version": state_version,
                "skill_name": skill_name,
                "skill_version": SKILL_VERSION,
            },
        )
    response = port.await_response(
        target_identity=identity,
        delivery_id=delivery_id,
        timeout_seconds=response_timeout_seconds,
    )
    output = _real_chain_helpers()._parse_worker_output(
        identity=identity,
        skill_name=skill_name,
        text=response.text,
        skill_input=skill_input,
    )
    output_bytes = canonical_json_bytes(output)
    if _SENSITIVE_JSON.search(output_bytes.decode("utf-8")) is not None:
        raise ValueError(f"DEMO_OUTPUT_SENSITIVE_CONTENT_BLOCKED:{identity}")
    return {
        "agent_identity_id": identity,
        "skill_name": skill_name,
        "skill_version": SKILL_VERSION,
        "delivery_id": delivery_id,
        "response_event_id": response.response_event_id,
        "output_sha256": sha256(output_bytes).hexdigest(),
        "output": output,
    }


def _safe_failure_record(exc: BaseException) -> dict[str, str]:
    record = {"error_type": type(exc).__name__}
    if isinstance(exc, MatrixDelegationError) and exc.safe_helper_code is not None:
        record["safe_code"] = exc.safe_helper_code
    elif isinstance(exc, ValueError) and len(exc.args) == 1:
        value = exc.args[0]
        if isinstance(value, str) and _SAFE_CODE.fullmatch(value) is not None:
            record["safe_code"] = value
    return record


def _run_chain(args: argparse.Namespace) -> int:
    run_dir = _existing_run_dir(args.run_dir)
    result_path = run_dir / "result.json"
    if result_path.exists() or result_path.is_symlink():
        raise ValueError("DEMO_RESULT_ALREADY_EXISTS")
    human_request_event_id: str | None = args.human_request_event_id
    if human_request_event_id is not None and _EVENT_ID.fullmatch(human_request_event_id) is None:
        raise ValueError("DEMO_HUMAN_REQUEST_EVENT_ID_INVALID")
    docker_executable = _regular_file(
        _candidate_path(args.docker_executable),
        "DEMO_DOCKER_EXECUTABLE_INVALID",
    )
    live_raw, _ = _read_json(
        run_dir / "live-gateway-config.json",
        "DEMO_LIVE_CONFIG_INVALID",
    )
    config = _load_demo_live_config(run_dir / "live-gateway-config.json")
    plans = {plan.agent_identity_id: plan for plan in config.plans}
    packages, package_hashes, state_version, demo_request_id = _load_demo_packages(
        run_dir=run_dir,
        config=config,
    )
    before_snapshot = _manager_snapshot(
        program_id=config.program_id,
        run_id=config.run_id,
        m2_env_path=_candidate_path(args.m2_env),
    )
    before_program = before_snapshot["program"]
    if int(before_program.get("state_version", -1)) != state_version:
        raise ValueError("DEMO_MANAGER_STATE_VERSION_MISMATCH")

    port = MatrixManagerDelegationPort(
        request_markers={
            identity: plans[identity].request_marker
            for identity, _, _ in WORKERS
        },
        trusted_worker_packages=packages,
        docker_executable=str(docker_executable),
        helper_timeout_seconds=20,
    )
    futures: dict[str, Future[dict[str, Any]]] = {}
    with ThreadPoolExecutor(
        max_workers=3,
        thread_name_prefix="auth-demo-001-worker",
    ) as executor:
        for identity, skill_name, _filename in WORKERS:
            futures[identity] = executor.submit(
                _worker_call,
                port=port,
                identity=identity,
                skill_name=skill_name,
                program_id=config.program_id,
                run_id=config.run_id,
                state_version=state_version,
                skill_input=packages[identity],
                response_timeout_seconds=args.response_timeout_seconds,
            )

    calls: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for sequence, (identity, _skill_name, _filename) in enumerate(WORKERS, start=1):
        try:
            worker = futures[identity].result()
        except BaseException as exc:
            failures.append(
                {
                    "agent_identity_id": identity,
                    **_safe_failure_record(exc),
                }
            )
            continue
        plan = plans[identity]
        calls.append(
            {
                "sequence": sequence,
                **worker,
                "model_call_id": plan.model_call_id,
                "reservation_id": plan.reservation_id,
                "request_marker_sha256": port.request_marker_hash_for(
                    target_identity=identity
                ),
                "trusted_package_sha256": package_hashes[identity],
            }
        )

    after_snapshot = _manager_snapshot(
        program_id=config.program_id,
        run_id=config.run_id,
        m2_env_path=_candidate_path(args.m2_env),
    )
    after_program = after_snapshot["program"]
    business_state_changed = (
        int(after_program.get("state_version", -1)) != state_version
        or after_program.get("active_plan_version_id")
        != before_program.get("active_plan_version_id")
    )
    if business_state_changed:
        failures.append(
            {
                "agent_identity_id": "awakening_program_manager",
                "error_type": "ValueError",
                "safe_code": "DEMO_UNEXPECTED_BUSINESS_STATE_CHANGE",
            }
        )

    status = "completed" if not failures and len(calls) == 3 else "failed"
    result = {
        "schema_version": SCHEMA_VERSION,
        "authorization_id": AUTHORIZATION_ID,
        "status": status,
        "demo_request_id": demo_request_id,
        "human_request_event_id": human_request_event_id,
        "live_config_sha256": sha256(live_raw).hexdigest(),
        "state_binding": {
            "program_id": config.program_id,
            "run_id": config.run_id,
            "program_state_version_before": state_version,
            "program_state_version_after": int(after_program.get("state_version", -1)),
            "active_plan_version_id_before": before_program.get("active_plan_version_id"),
            "active_plan_version_id_after": after_program.get("active_plan_version_id"),
            "runtime_config_snapshot_id": config.runtime_config_snapshot_id,
        },
        "manager_provider_call_count": 0,
        "worker_provider_call_plan_count": 3,
        "worker_dispatch_concurrency_limit": 3,
        "provider_retry_count": 0,
        "business_state_changed": business_state_changed,
        "calls": calls,
        "failures": failures,
    }
    _atomic_create(result_path, _canonical_file_bytes(result))

    print(f"AUTH_DEMO_001_RUN_CHAIN={status.upper()}")
    print(f"AUTH_DEMO_001_CALL_COUNT={len(calls)}")
    print("AUTH_DEMO_001_MANAGER_PROVIDER_CALL_COUNT=0")
    print("AUTH_DEMO_001_PROVIDER_RETRY_COUNT=0")
    print("AUTH_DEMO_001_WORKER_CONCURRENCY_LIMIT=3")
    print(
        "AUTH_DEMO_001_BUSINESS_STATE_CHANGED="
        f"{str(business_state_changed).lower()}"
    )
    print(f"AUTH_DEMO_001_RESULT_PATH={result_path}")
    print("AUTH_DEMO_001_CONTENT_ECHOED=false")
    return 0 if status == "completed" else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AUTH-DEMO-001 in-place AgentTeams fresh Demo CLI"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="commit a fresh Demo bundle")
    prepare.add_argument("--run-dir", required=True, type=Path)
    prepare.add_argument("--fixture-state", type=Path, default=DEFAULT_FIXTURE)
    prepare.add_argument("--m2-env", type=Path, default=DEFAULT_M2_ENV)
    prepare.set_defaults(handler=_prepare)

    serve = subparsers.add_parser(
        "serve-gateway",
        help="serve the fail-closed or fresh Demo Gateway",
    )
    serve.add_argument("--mode", required=True, choices=("fail-closed", "live"))
    serve.add_argument("--run-dir", required=True, type=Path)
    serve.add_argument("--provider-secret", type=Path, default=DEFAULT_PROVIDER_SECRET)
    serve.add_argument(
        "--runtime-credentials",
        type=Path,
        default=DEFAULT_RUNTIME_CREDENTIALS,
    )
    serve.add_argument("--m2-env", type=Path, default=DEFAULT_M2_ENV)
    serve.add_argument("--m4-env", type=Path, default=DEFAULT_M4_ENV)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=18190, type=int)
    serve.set_defaults(handler=_serve_gateway)

    run = subparsers.add_parser(
        "run-chain",
        help="dispatch the three Worker calls once with fixed three-way concurrency",
    )
    run.add_argument("--run-dir", required=True, type=Path)
    run.add_argument("--docker-executable", type=Path, default=DEFAULT_DOCKER)
    run.add_argument("--m2-env", type=Path, default=DEFAULT_M2_ENV)
    run.add_argument("--human-request-event-id", required=True)
    run.add_argument(
        "--response-timeout-seconds",
        type=int,
        choices=range(1, 241),
        default=240,
        metavar="1..240",
    )
    run.set_defaults(handler=_run_chain)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return int(args.handler(args))


def _report_failure(exc: BaseException) -> None:
    print(f"AUTH_DEMO_001=FAIL:{type(exc).__name__}", file=sys.stderr)
    if isinstance(exc, MatrixDelegationError) and exc.safe_helper_code is not None:
        print(
            f"AUTH_DEMO_001_FAILURE_CODE={exc.safe_helper_code}",
            file=sys.stderr,
        )
    elif isinstance(exc, ValueError) and len(exc.args) == 1:
        value = exc.args[0]
        if isinstance(value, str) and _SAFE_CODE.fullmatch(value) is not None:
            print(f"AUTH_DEMO_001_FAILURE_CODE={value}", file=sys.stderr)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BaseException as error:
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        _report_failure(error)
        raise SystemExit(1) from None
