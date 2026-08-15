"""Strict local composition for the separately authorized live M4 Gateway.

This module binds the separately authorized Bailian OpenAI-compatible
transport.  It accepts an ACL-protected, key-free JSON configuration and a
separate one-field secret file.  Merely importing or loading this module never
opens a socket, reads a secret, or contacts a Provider;
:func:`build_live_runtime` performs the explicit composition.
"""

from __future__ import annotations

import argparse
import base64
from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
import re
import secrets
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from psycopg.conninfo import make_conninfo

from awakening.adapters.m4 import (
    GATEWAY_PRINCIPAL_ID,
    GatewayStateAuthorityAdapter,
    M4InternalStateAdapter,
)
from awakening.context_manifest.m4 import (
    PostgresContextManifestStore,
    PostgresInvocationReceiptStore,
)
from awakening.orchestration.m4 import (
    BoundRuntimeAuthorizer,
    RuntimeBinding,
    RuntimeCredentialRegistry,
    load_and_validate_m4_registry,
)
from awakening.state.admin import build_runtime_dsn, load_m2_env
from awakening.state.contracts import PrincipalType, TrustedPrincipal
from awakening.state.m4 import M4PostgresStateStore, M4StateServiceFacade
from awakening.state.m4.admin import (
    OBSERVABILITY_PASSWORD_FIELD,
    OBSERVABILITY_ROLE,
    load_m4_env,
)

from .gateway import M4ModelGateway
from .http_adapter import (
    RuntimeInvocationPlan,
    SingleUseRuntimeInvocationPlanRegistry,
    TrustedOpenAICompatibleHttpAdapter,
    build_http_server,
    validate_runtime_request_marker,
)
from .provider import OpenAICompatibleProvider


LIVE_CONFIG_SCHEMA_VERSION = 1
LIVE_AUTHORIZATION_ID = "AUTH-M4-001"
PROVIDER_SECRET_FIELD = "AWAKENING_M4_PROVIDER_API_KEY"
AUTHORIZED_PROVIDER_ALIAS = "aliyun-model-studio-official"
AUTHORIZED_MODEL_ID = "qwen3.7-flash-2026-07-15"
AUTHORIZED_ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1"
AUTHORIZED_HOSTNAME = "dashscope.aliyuncs.com"
AUTHORIZED_TIMEOUT_SECONDS = 60

_HOSTNAME = re.compile(
    r"^(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
_PROVIDER_ALIAS = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

_IDENTITY_CREDENTIAL_FIELDS: Mapping[str, str] = {
    "awakening_program_manager": "AWAKENING_PROGRAM_MANAGER_B64",
    "role_project_architect": "ROLE_PROJECT_ARCHITECT_B64",
    "execution_evidence_coach": "EXECUTION_EVIDENCE_COACH_B64",
    "independent_quality_reviewer": "INDEPENDENT_QUALITY_REVIEWER_B64",
}
_IDENTITY_PRINCIPALS: Mapping[str, str] = {
    identity: f"m4-runtime-principal-{identity}"
    for identity in _IDENTITY_CREDENTIAL_FIELDS
}
_REPRESENTATIVE_SKILLS: Mapping[str, str] = {
    "role_project_architect": "analyze_role_gap",
    "execution_evidence_coach": "coach_task_submission",
    "independent_quality_reviewer": "review_evidence_against_rubric",
}
_AUTH_MAXIMUM_CAPS: Mapping[str, int] = {
    "max_calls": 3,
    "max_input_tokens_per_call": 64_000,
    "max_output_tokens_per_call": 1_000,
    "max_cost_microunits_per_call": 30_000,
    "max_total_input_tokens": 192_000,
    "max_total_output_tokens": 3_000,
    "max_total_cost_microunits": 100_000,
}


class LiveRuntimeConfigurationError(ValueError):
    """Stable, secret-free live-runtime configuration rejection."""


@dataclass(frozen=True, slots=True)
class LiveProviderConfiguration:
    provider_alias: str
    endpoint: str
    allowed_hostname: str
    model_id: str
    public_model_alias: str
    input_microunits_per_million: int
    output_microunits_per_million: int
    timeout_seconds: int


@dataclass(frozen=True, slots=True)
class LiveBudgetCaps:
    max_calls: int
    max_input_tokens_per_call: int
    max_output_tokens_per_call: int
    max_cost_microunits_per_call: int
    max_total_input_tokens: int
    max_total_output_tokens: int
    max_total_cost_microunits: int

    def to_dict(self) -> dict[str, int]:
        return {
            field: int(getattr(self, field))
            for field in _AUTH_MAXIMUM_CAPS
        }


@dataclass(frozen=True, slots=True)
class LiveRuntimeParameters:
    temperature: float
    seed: int
    enable_thinking: bool
    response_format_type: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "temperature": self.temperature,
            "seed": self.seed,
            "enable_thinking": self.enable_thinking,
            "response_format": {"type": self.response_format_type},
        }


@dataclass(frozen=True, slots=True)
class LiveInvocationPlanConfiguration:
    agent_identity_id: str
    model_call_id: str
    reservation_id: str
    skill_name: str
    skill_version: str
    request_marker: str
    object_refs: tuple[Mapping[str, str], ...]
    exclusions: tuple[str, ...]

    def to_runtime_plan(self) -> RuntimeInvocationPlan:
        return RuntimeInvocationPlan(
            model_call_id=self.model_call_id,
            reservation_id=self.reservation_id,
            skill_name=self.skill_name,
            skill_version=self.skill_version,
            request_marker=self.request_marker,
            object_refs=self.object_refs,
            exclusions=self.exclusions,
        )


@dataclass(frozen=True, slots=True)
class LiveRuntimeConfiguration:
    provider: LiveProviderConfiguration
    parameters: LiveRuntimeParameters
    caps: LiveBudgetCaps
    program_id: str
    run_id: str
    runtime_config_snapshot_id: str
    plans: tuple[LiveInvocationPlanConfiguration, ...]


@dataclass(frozen=True, slots=True)
class LiveM4GatewayRuntime:
    """Composed runtime.  It exposes no API key or database credential."""

    adapter: TrustedOpenAICompatibleHttpAdapter
    provider: OpenAICompatibleProvider
    provider_alias: str
    model_id: str
    planned_model_call_ids: tuple[str, ...]


def _regular_file(path: str | Path, reason: str) -> Path:
    candidate = Path(path)
    try:
        if not candidate.is_file() or candidate.is_symlink():
            raise LiveRuntimeConfigurationError(reason)
        return candidate.resolve(strict=True)
    except OSError as exc:
        raise LiveRuntimeConfigurationError(reason) from exc


def _exact_fields(value: Any, fields: set[str], reason: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise LiveRuntimeConfigurationError(reason)
    return value


def _positive_int(value: Any, reason: str, *, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise LiveRuntimeConfigurationError(reason)
    if maximum is not None and value > maximum:
        raise LiveRuntimeConfigurationError(reason)
    return value


def _uuid_text(value: Any, reason: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise LiveRuntimeConfigurationError(reason) from exc


def _load_document(path: Path) -> Mapping[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LiveRuntimeConfigurationError("M4_LIVE_CONFIG_DOCUMENT_INVALID") from exc
    return _exact_fields(
        document,
        {
            "authorization_id",
            "schema_version",
            "provider",
            "parameters",
            "caps",
            "state_binding",
            "plans",
        },
        "M4_LIVE_CONFIG_FIELDS_INVALID",
    )


def _parse_provider(value: Any) -> LiveProviderConfiguration:
    record = _exact_fields(
        value,
        {
            "provider_alias",
            "endpoint",
            "allowed_hostname",
            "model_id",
            "public_model_alias",
            "input_microunits_per_million",
            "output_microunits_per_million",
            "timeout_seconds",
        },
        "M4_LIVE_PROVIDER_FIELDS_INVALID",
    )
    provider_alias = str(record["provider_alias"])
    endpoint = str(record["endpoint"])
    allowed_hostname = str(record["allowed_hostname"])
    model_id = str(record["model_id"])
    public_model_alias = str(record["public_model_alias"])
    if _PROVIDER_ALIAS.fullmatch(provider_alias) is None:
        raise LiveRuntimeConfigurationError("M4_LIVE_PROVIDER_ALIAS_INVALID")
    if _HOSTNAME.fullmatch(allowed_hostname) is None:
        raise LiveRuntimeConfigurationError("M4_LIVE_PROVIDER_HOSTNAME_INVALID")
    if _MODEL_ID.fullmatch(model_id) is None or public_model_alias != model_id:
        raise LiveRuntimeConfigurationError("M4_LIVE_PROVIDER_MODEL_INVALID")
    parsed = urlparse(endpoint)
    if (
        parsed.scheme != "https"
        or parsed.hostname != allowed_hostname
        or parsed.port not in (None, 443)
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not parsed.path
        or parsed.path == "/"
        or parsed.path.endswith("/")
        or "//" in parsed.path
        or endpoint.strip() != endpoint
    ):
        raise LiveRuntimeConfigurationError("M4_LIVE_PROVIDER_ENDPOINT_INVALID")
    if (
        provider_alias != AUTHORIZED_PROVIDER_ALIAS
        or model_id != AUTHORIZED_MODEL_ID
        or endpoint != AUTHORIZED_ENDPOINT
        or allowed_hostname != AUTHORIZED_HOSTNAME
        or record["timeout_seconds"] != AUTHORIZED_TIMEOUT_SECONDS
    ):
        raise LiveRuntimeConfigurationError(
            "M4_LIVE_AUTHORIZED_PROVIDER_BINDING_INVALID"
        )
    input_rate = _positive_int(
        record["input_microunits_per_million"],
        "M4_LIVE_PROVIDER_INPUT_RATE_INVALID",
    )
    output_rate = _positive_int(
        record["output_microunits_per_million"],
        "M4_LIVE_PROVIDER_OUTPUT_RATE_INVALID",
    )
    timeout = _positive_int(
        record["timeout_seconds"],
        "M4_LIVE_PROVIDER_TIMEOUT_INVALID",
        maximum=60,
    )
    return LiveProviderConfiguration(
        provider_alias=provider_alias,
        endpoint=endpoint,
        allowed_hostname=allowed_hostname,
        model_id=model_id,
        public_model_alias=public_model_alias,
        input_microunits_per_million=input_rate,
        output_microunits_per_million=output_rate,
        timeout_seconds=timeout,
    )


def _parse_parameters(value: Any) -> LiveRuntimeParameters:
    record = _exact_fields(
        value,
        {"temperature", "seed", "enable_thinking", "response_format"},
        "M4_LIVE_PARAMETER_FIELDS_INVALID",
    )
    temperature = record["temperature"]
    if (
        isinstance(temperature, bool)
        or not isinstance(temperature, (int, float))
        or float(temperature) != 0.01
    ):
        raise LiveRuntimeConfigurationError("M4_LIVE_TEMPERATURE_INVALID")
    seed = record["seed"]
    if isinstance(seed, bool) or not isinstance(seed, int) or seed != 0:
        raise LiveRuntimeConfigurationError("M4_LIVE_SEED_INVALID")
    if record["enable_thinking"] is not False:
        raise LiveRuntimeConfigurationError("M4_LIVE_ENABLE_THINKING_INVALID")
    response_format = _exact_fields(
        record["response_format"],
        {"type"},
        "M4_LIVE_RESPONSE_FORMAT_FIELDS_INVALID",
    )
    if response_format["type"] != "json_object":
        raise LiveRuntimeConfigurationError("M4_LIVE_RESPONSE_FORMAT_INVALID")
    return LiveRuntimeParameters(
        temperature=0.01,
        seed=0,
        enable_thinking=False,
        response_format_type="json_object",
    )


def _parse_caps(value: Any) -> LiveBudgetCaps:
    record = _exact_fields(
        value,
        set(_AUTH_MAXIMUM_CAPS),
        "M4_LIVE_CAP_FIELDS_INVALID",
    )
    parsed = {
        field: _positive_int(
            record[field],
            "M4_LIVE_CAP_VALUE_INVALID",
            maximum=maximum,
        )
        for field, maximum in _AUTH_MAXIMUM_CAPS.items()
    }
    if parsed["max_calls"] != len(_REPRESENTATIVE_SKILLS):
        raise LiveRuntimeConfigurationError("M4_LIVE_CALL_CAP_INVALID")
    if (
        parsed["max_total_input_tokens"]
        < parsed["max_input_tokens_per_call"] * parsed["max_calls"]
        or parsed["max_total_output_tokens"]
        < parsed["max_output_tokens_per_call"] * parsed["max_calls"]
        or parsed["max_total_cost_microunits"]
        < parsed["max_cost_microunits_per_call"] * parsed["max_calls"]
    ):
        raise LiveRuntimeConfigurationError("M4_LIVE_TOTAL_CAP_INVALID")
    return LiveBudgetCaps(**parsed)


def _parse_object_refs(value: Any) -> tuple[Mapping[str, str], ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= 8:
        raise LiveRuntimeConfigurationError("M4_LIVE_OBJECT_REFS_INVALID")
    refs: list[Mapping[str, str]] = []
    for item in value:
        record = _exact_fields(
            item,
            {"object_type", "object_id", "object_version", "content_sha256"},
            "M4_LIVE_OBJECT_REF_FIELDS_INVALID",
        )
        normalized = {key: str(record[key]) for key in record}
        if (
            any(not item_value or len(item_value) > 256 for item_value in normalized.values())
            or _SHA256.fullmatch(normalized["content_sha256"]) is None
        ):
            raise LiveRuntimeConfigurationError("M4_LIVE_OBJECT_REF_VALUE_INVALID")
        refs.append(normalized)
    return tuple(refs)


def _parse_plans(value: Any) -> tuple[LiveInvocationPlanConfiguration, ...]:
    record = _exact_fields(
        value,
        set(_REPRESENTATIVE_SKILLS),
        "M4_LIVE_PLAN_IDENTITY_SET_INVALID",
    )
    registry = load_and_validate_m4_registry()
    plans: list[LiveInvocationPlanConfiguration] = []
    call_ids: set[str] = set()
    reservation_ids: set[str] = set()
    for identity, expected_skill in _REPRESENTATIVE_SKILLS.items():
        raw = _exact_fields(
            record[identity],
            {
                "model_call_id",
                "reservation_id",
                "skill_name",
                "skill_version",
                "request_marker",
                "object_refs",
                "exclusions",
            },
            "M4_LIVE_PLAN_FIELDS_INVALID",
        )
        model_call_id = _uuid_text(
            raw["model_call_id"], "M4_LIVE_MODEL_CALL_ID_INVALID"
        )
        reservation_id = _uuid_text(
            raw["reservation_id"], "M4_LIVE_RESERVATION_ID_INVALID"
        )
        try:
            request_marker = validate_runtime_request_marker(raw["request_marker"])
        except ValueError as exc:
            raise LiveRuntimeConfigurationError(
                "M4_LIVE_REQUEST_MARKER_INVALID"
            ) from exc
        if request_marker != f"m4-call:{model_call_id}":
            raise LiveRuntimeConfigurationError("M4_LIVE_REQUEST_MARKER_MISMATCH")
        skill_name = str(raw["skill_name"])
        skill_version = str(raw["skill_version"])
        if skill_name != expected_skill:
            raise LiveRuntimeConfigurationError("M4_LIVE_PLAN_SKILL_INVALID")
        try:
            registry.assert_skill_allowed(
                agent_identity_id=identity,
                agent_identity_version=registry.identity_versions[identity],
                skill_name=skill_name,
                skill_version=skill_version,
            )
        except (KeyError, ValueError) as exc:
            raise LiveRuntimeConfigurationError("M4_LIVE_PLAN_CONTRACT_INVALID") from exc
        exclusions = raw["exclusions"]
        if (
            not isinstance(exclusions, list)
            or not 1 <= len(exclusions) <= 16
            or any(not isinstance(item, str) or not item or len(item) > 128 for item in exclusions)
            or len(set(exclusions)) != len(exclusions)
        ):
            raise LiveRuntimeConfigurationError("M4_LIVE_PLAN_EXCLUSIONS_INVALID")
        if model_call_id in call_ids or reservation_id in reservation_ids:
            raise LiveRuntimeConfigurationError("M4_LIVE_PLAN_ID_REUSED")
        call_ids.add(model_call_id)
        reservation_ids.add(reservation_id)
        plans.append(
            LiveInvocationPlanConfiguration(
                agent_identity_id=identity,
                model_call_id=model_call_id,
                reservation_id=reservation_id,
                skill_name=skill_name,
                skill_version=skill_version,
                request_marker=request_marker,
                object_refs=_parse_object_refs(raw["object_refs"]),
                exclusions=tuple(exclusions),
            )
        )
    return tuple(plans)


def load_live_runtime_config(path: str | Path) -> LiveRuntimeConfiguration:
    """Load a strict key-free runtime document without reading any secret file."""

    document = _load_document(_regular_file(path, "M4_LIVE_CONFIG_FILE_INVALID"))
    if document["authorization_id"] != LIVE_AUTHORIZATION_ID:
        raise LiveRuntimeConfigurationError("M4_LIVE_AUTHORIZATION_ID_INVALID")
    if document["schema_version"] != LIVE_CONFIG_SCHEMA_VERSION:
        raise LiveRuntimeConfigurationError("M4_LIVE_CONFIG_SCHEMA_INVALID")
    provider = _parse_provider(document["provider"])
    parameters = _parse_parameters(document["parameters"])
    caps = _parse_caps(document["caps"])
    state_binding = _exact_fields(
        document["state_binding"],
        {"program_id", "run_id", "runtime_config_snapshot_id"},
        "M4_LIVE_STATE_BINDING_FIELDS_INVALID",
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
        raise LiveRuntimeConfigurationError("M4_LIVE_RATE_EXCEEDS_COST_CAP")
    return LiveRuntimeConfiguration(
        provider=provider,
        parameters=parameters,
        caps=caps,
        program_id=_uuid_text(state_binding["program_id"], "M4_LIVE_PROGRAM_ID_INVALID"),
        run_id=_uuid_text(state_binding["run_id"], "M4_LIVE_RUN_ID_INVALID"),
        runtime_config_snapshot_id=_uuid_text(
            state_binding["runtime_config_snapshot_id"],
            "M4_LIVE_SNAPSHOT_ID_INVALID",
        ),
        plans=_parse_plans(document["plans"]),
    )


def _read_one_field_secret(path: str | Path) -> str:
    secret_path = _regular_file(path, "M4_LIVE_PROVIDER_SECRET_FILE_INVALID")
    try:
        lines = [
            line
            for line in secret_path.read_text(encoding="utf-8-sig").splitlines()
            if line and not line.lstrip().startswith("#")
        ]
    except (OSError, UnicodeError) as exc:
        raise LiveRuntimeConfigurationError("M4_LIVE_PROVIDER_SECRET_FILE_INVALID") from exc
    if len(lines) != 1:
        raise LiveRuntimeConfigurationError("M4_LIVE_PROVIDER_SECRET_FIELDS_INVALID")
    key, separator, value = lines[0].partition("=")
    if separator != "=" or key != PROVIDER_SECRET_FIELD:
        raise LiveRuntimeConfigurationError("M4_LIVE_PROVIDER_SECRET_FIELDS_INVALID")
    if len(value) < 20 or any(character.isspace() for character in value):
        raise LiveRuntimeConfigurationError("M4_LIVE_PROVIDER_SECRET_VALUE_INVALID")
    return value


def _read_runtime_tokens(path: str | Path) -> dict[str, str]:
    credential_path = _regular_file(path, "M4_LIVE_CREDENTIAL_FILE_INVALID")
    try:
        lines = credential_path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as exc:
        raise LiveRuntimeConfigurationError("M4_LIVE_CREDENTIAL_FILE_INVALID") from exc
    encoded: dict[str, str] = {}
    for raw_line in lines:
        if not raw_line or raw_line.lstrip().startswith("#"):
            continue
        key, separator, value = raw_line.partition("=")
        if separator != "=" or key in encoded:
            raise LiveRuntimeConfigurationError("M4_LIVE_CREDENTIAL_FIELDS_INVALID")
        encoded[key] = value
    if set(encoded) != set(_IDENTITY_CREDENTIAL_FIELDS.values()):
        raise LiveRuntimeConfigurationError("M4_LIVE_CREDENTIAL_IDENTITY_SET_INVALID")
    tokens: dict[str, str] = {}
    for identity, field in _IDENTITY_CREDENTIAL_FIELDS.items():
        try:
            token = base64.b64decode(encoded[field], validate=True).decode("utf-8")
        except (ValueError, UnicodeError) as exc:
            raise LiveRuntimeConfigurationError("M4_LIVE_CREDENTIAL_ENCODING_INVALID") from exc
        if len(token) < 32 or any(character.isspace() for character in token):
            raise LiveRuntimeConfigurationError("M4_LIVE_CREDENTIAL_VALUE_INVALID")
        tokens[identity] = token
    return tokens


def _build_runtime_security(
    config: LiveRuntimeConfiguration,
    credential_path: str | Path,
) -> tuple[RuntimeCredentialRegistry, SingleUseRuntimeInvocationPlanRegistry]:
    tokens = _read_runtime_tokens(credential_path)
    registry = load_and_validate_m4_registry()
    pepper = secrets.token_bytes(32)
    empty = RuntimeCredentialRegistry(pepper=pepper, bindings_by_fingerprint={})
    bindings: dict[str, RuntimeBinding] = {}
    fingerprints: dict[str, str] = {}
    for identity, token in tokens.items():
        fingerprint = empty.fingerprint(token)
        fingerprints[identity] = fingerprint
        bindings[fingerprint] = RuntimeBinding(
            credential_id=f"m4-runtime-{identity}",
            agent_identity_id=identity,
            agent_identity_version=registry.identity_versions[identity],
            trusted_principal=TrustedPrincipal(
                principal_id=_IDENTITY_PRINCIPALS[identity],
                principal_type=PrincipalType.AGENT,
                scopes=("model:invoke",),
                program_scope=(config.program_id,),
                auth_context_id=f"m4-live-runtime-{identity}",
            ),
            program_id=config.program_id,
            run_id=config.run_id,
            runtime_config_snapshot_id=config.runtime_config_snapshot_id,
            public_model_alias=config.provider.public_model_alias,
            allowed_skill_versions={
                skill: registry.skill_versions[skill]
                for skill in registry.identity_skills[identity]
            },
            allowed_tools=(),
            reviewer_mode=(
                "contract_smoke"
                if identity == "independent_quality_reviewer"
                else None
            ),
        )
    tokens.clear()
    plans = {
        fingerprints[plan.agent_identity_id]: plan.to_runtime_plan()
        for plan in config.plans
    }
    return (
        RuntimeCredentialRegistry(
            pepper=pepper,
            bindings_by_fingerprint=bindings,
        ),
        SingleUseRuntimeInvocationPlanRegistry(plans),
    )


def _observability_dsn(m2_values: Mapping[str, str], m4_values: Mapping[str, str]) -> str:
    return make_conninfo(
        host=m2_values["AWAKENING_M2_DB_HOST"],
        port=int(m2_values["AWAKENING_M2_DB_PORT"]),
        user=OBSERVABILITY_ROLE,
        password=m4_values[OBSERVABILITY_PASSWORD_FIELD],
        dbname=m2_values["AWAKENING_M2_DB_NAME"],
        connect_timeout=5,
    )


def _assert_committed_preconditions(
    config: LiveRuntimeConfiguration,
    state_authority: GatewayStateAuthorityAdapter,
) -> None:
    snapshot = state_authority.get_runtime_config_snapshot(
        snapshot_id=config.runtime_config_snapshot_id,
        program_id=config.program_id,
        run_id=config.run_id,
    )
    if snapshot is None:
        raise LiveRuntimeConfigurationError("M4_LIVE_SNAPSHOT_NOT_COMMITTED")
    expected_snapshot: Mapping[str, Any] = {
        "snapshot_id": config.runtime_config_snapshot_id,
        "program_id": config.program_id,
        "run_id": config.run_id,
        "provider_alias": config.provider.provider_alias,
        "model_id": config.provider.model_id,
        "parameters": config.parameters.to_dict(),
        **config.caps.to_dict(),
    }
    if any(snapshot.get(key) != expected for key, expected in expected_snapshot.items()):
        raise LiveRuntimeConfigurationError("M4_LIVE_SNAPSHOT_BINDING_MISMATCH")
    for plan in config.plans:
        reservation = state_authority.get_model_budget_reservation(
            reservation_id=plan.reservation_id,
            program_id=config.program_id,
            run_id=config.run_id,
            model_call_id=plan.model_call_id,
        )
        expected_reservation: Mapping[str, Any] = {
            "reservation_id": plan.reservation_id,
            "program_id": config.program_id,
            "run_id": config.run_id,
            "model_call_id": plan.model_call_id,
            "snapshot_id": config.runtime_config_snapshot_id,
            "max_input_tokens": config.caps.max_input_tokens_per_call,
            "max_output_tokens": config.caps.max_output_tokens_per_call,
            "max_cost_microunits": config.caps.max_cost_microunits_per_call,
            "status": "reserved",
        }
        if reservation is None or any(
            reservation.get(key) != expected
            for key, expected in expected_reservation.items()
        ):
            raise LiveRuntimeConfigurationError("M4_LIVE_RESERVATION_BINDING_MISMATCH")


def build_live_runtime(
    *,
    config_path: str | Path,
    provider_secret_path: str | Path,
    runtime_credential_path: str | Path,
    m2_env_path: str | Path,
    m4_env_path: str | Path,
) -> LiveM4GatewayRuntime:
    """Compose the real Gateway without starting a listener or making a call."""

    config_file = _regular_file(config_path, "M4_LIVE_CONFIG_FILE_INVALID")
    secret_file = _regular_file(
        provider_secret_path, "M4_LIVE_PROVIDER_SECRET_FILE_INVALID"
    )
    if config_file == secret_file:
        raise LiveRuntimeConfigurationError("M4_LIVE_CONFIG_SECRET_PATH_COLLISION")
    config = load_live_runtime_config(config_file)
    m2_values = load_m2_env(
        _regular_file(m2_env_path, "M4_LIVE_M2_ENV_FILE_INVALID")
    )
    m4_values = load_m4_env(
        _regular_file(m4_env_path, "M4_LIVE_M4_ENV_FILE_INVALID")
    )
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
            auth_context_id=f"m4-live-gateway-{config.run_id}",
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

    api_key = _read_one_field_secret(secret_file)
    provider = OpenAICompatibleProvider(
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
    return LiveM4GatewayRuntime(
        adapter=adapter,
        provider=provider,
        provider_alias=config.provider.provider_alias,
        model_id=config.provider.model_id,
        planned_model_call_ids=tuple(plan.model_call_id for plan in config.plans),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--provider-secret", required=True, type=Path)
    parser.add_argument("--runtime-credentials", required=True, type=Path)
    parser.add_argument("--m2-env", required=True, type=Path)
    parser.add_argument("--m4-env", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=18190, type=int)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        raise LiveRuntimeConfigurationError("M4_LIVE_GATEWAY_LOOPBACK_REQUIRED")
    if not 1 <= args.port <= 65_535:
        raise LiveRuntimeConfigurationError("M4_LIVE_GATEWAY_PORT_INVALID")
    runtime = build_live_runtime(
        config_path=args.config,
        provider_secret_path=args.provider_secret,
        runtime_credential_path=args.runtime_credentials,
        m2_env_path=args.m2_env,
        m4_env_path=args.m4_env,
    )
    server = build_http_server(runtime.adapter, host=args.host, port=args.port)
    print("M4_LIVE_GATEWAY_READY=true", flush=True)
    print(f"M4_LIVE_GATEWAY_PROVIDER_ALIAS={runtime.provider_alias}", flush=True)
    print(f"M4_LIVE_GATEWAY_MODEL_ID={runtime.model_id}", flush=True)
    print(
        f"M4_LIVE_GATEWAY_SINGLE_USE_PLAN_COUNT={len(runtime.planned_model_call_ids)}",
        flush=True,
    )
    print("M4_LIVE_GATEWAY_MANAGER_PLAN_COUNT=0", flush=True)
    print("M4_LIVE_GATEWAY_SECRET_ECHOED=false", flush=True)
    server.serve_forever()
    return 0


__all__ = (
    "LIVE_AUTHORIZATION_ID",
    "LIVE_CONFIG_SCHEMA_VERSION",
    "LiveBudgetCaps",
    "LiveInvocationPlanConfiguration",
    "LiveM4GatewayRuntime",
    "LiveProviderConfiguration",
    "LiveRuntimeParameters",
    "LiveRuntimeConfiguration",
    "LiveRuntimeConfigurationError",
    "PROVIDER_SECRET_FIELD",
    "build_live_runtime",
    "load_live_runtime_config",
)


if __name__ == "__main__":
    raise SystemExit(main())
