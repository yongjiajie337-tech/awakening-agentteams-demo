"""Typed M4 contracts layered on the accepted M3 State Service facade.

M4 keeps runtime identity and model-accounting facts out of wire payloads.  The
objects in this module are constructed by trusted runtime or Gateway adapters;
there is deliberately no ``from_request`` helper.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import math
import re
from types import MappingProxyType
from typing import Any, Mapping, Sequence
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from awakening.state.contracts import PrincipalType, SourceChannel, TrustedPrincipal


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROVIDER_ALIAS = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_IDEMPOTENCY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_TRACEPARENT = re.compile(r"^[0-9a-f]{2}-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$")
_AUTHORIZED_PROVIDER_ALIAS = "aliyun-model-studio-official"
_AUTHORIZED_MODEL_ID = "qwen3.7-flash-2026-07-15"
_RUNTIME_PARAMETER_FIELDS = frozenset(
    {"temperature", "seed", "enable_thinking", "response_format"}
)
_LEDGER_NAMESPACE = uuid5(NAMESPACE_URL, "awakening.local/m4/model-usage-ledger")
_USAGE_RECEIPT_NAMESPACE = uuid5(
    NAMESPACE_URL,
    "awakening.local/m4/provider-usage-receipt",
)


class M4AgentIdentity(StrEnum):
    MANAGER = "awakening_program_manager"
    ARCHITECT = "role_project_architect"
    COACH = "execution_evidence_coach"
    REVIEWER = "independent_quality_reviewer"


class M4StateMcpMethod(StrEnum):
    GET_SNAPSHOT = "get_snapshot"
    SUBMIT_PROPOSAL = "submit_proposal"
    GET_COMMAND_STATUS = "get_command_status"
    APPLY_AUTHORIZED_CHANGE = "apply_authorized_change"


class M4CommandType(StrEnum):
    RUNTIME_CONFIG_SNAPSHOT_CREATE = "runtime_config.snapshot.create"
    MODEL_BUDGET_RESERVE = "model_budget.reserve"
    MODEL_BUDGET_SETTLE = "model_budget.settle"


class M4QueryType(StrEnum):
    RUNTIME_CONFIG_SNAPSHOT_GET = "runtime_config.snapshot.get"
    MODEL_BUDGET_RESERVATION_GET = "model_budget.reservation.get"


class M4ReasonCode(StrEnum):
    APPLY_DISABLED = "M4_APPLY_DISABLED"
    METHOD_NOT_ALLOWED = "M4_METHOD_NOT_ALLOWED"
    RUNTIME_CONTEXT_INVALID = "M4_RUNTIME_CONTEXT_INVALID"
    RUNTIME_CONFIG_INVALID = "M4_RUNTIME_CONFIG_INVALID"
    RUNTIME_CONFIG_NOT_FOUND = "M4_RUNTIME_CONFIG_NOT_FOUND"
    BUDGET_REFUSED = "M4_BUDGET_REFUSED"
    RESERVATION_NOT_FOUND = "M4_RESERVATION_NOT_FOUND"
    RESERVATION_STATE_INVALID = "M4_RESERVATION_STATE_INVALID"
    PROVIDER_USAGE_INVALID = "M4_PROVIDER_USAGE_INVALID"


class ProviderUsageStatus(StrEnum):
    REPORTED = "reported"
    UNKNOWN = "unknown"


class ProviderCallStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


M4_WRITE_COMMAND_TYPES = frozenset(item.value for item in M4CommandType)
M4_QUERY_TYPES = frozenset(item.value for item in M4QueryType)


_IDENTITY_ROLE: Mapping[M4AgentIdentity, str | None] = MappingProxyType(
    {
        M4AgentIdentity.MANAGER: "manager",
        M4AgentIdentity.ARCHITECT: "architect",
        M4AgentIdentity.COACH: "coach",
        M4AgentIdentity.REVIEWER: None,
    }
)

M4_STATE_MCP_METHOD_MATRIX: Mapping[M4AgentIdentity, frozenset[M4StateMcpMethod]] = (
    MappingProxyType(
        {
            M4AgentIdentity.MANAGER: frozenset(
                {
                    M4StateMcpMethod.GET_SNAPSHOT,
                    M4StateMcpMethod.GET_COMMAND_STATUS,
                    M4StateMcpMethod.APPLY_AUTHORIZED_CHANGE,
                }
            ),
            M4AgentIdentity.ARCHITECT: frozenset(
                {
                    M4StateMcpMethod.SUBMIT_PROPOSAL,
                    M4StateMcpMethod.GET_COMMAND_STATUS,
                }
            ),
            M4AgentIdentity.COACH: frozenset(),
            M4AgentIdentity.REVIEWER: frozenset(),
        }
    )
)


def _uuid_text(value: str, field: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{field} must be a UUID") from exc


def _positive_int(value: int, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _non_negative_int(value: int, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _utc_datetime(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _freeze_json(value: Any, path: tuple[str | int, ...] = ()) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_json(item, (*path, str(key))) for key, item in value.items()}
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_freeze_json(item, (*path, index)) for index, item in enumerate(value))
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError(f"value at {path!r} is not finite JSON data")


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return value


@dataclass(frozen=True, slots=True)
class TrustedRuntimeContext:
    """Server-issued per-Agent runtime identity for one M4 run.

    ``agent_identity`` and ``program_role`` must match the frozen M4 identity
    registry.  The adapter also asks State Service to re-check persistent
    Program membership before returning data or accepting a Proposal.
    """

    principal_id: str
    agent_identity: M4AgentIdentity | str
    program_role: str | None
    program_scope: tuple[str, ...]
    run_id: str
    auth_context_id: str
    scopes: tuple[str, ...] = ("state:mcp",)

    def __post_init__(self) -> None:
        identity = M4AgentIdentity(self.agent_identity)
        expected_role = _IDENTITY_ROLE[identity]
        if self.program_role != expected_role:
            raise ValueError(
                f"program_role must be {expected_role!r} for {identity.value!r}"
            )
        if not self.principal_id or len(self.principal_id) > 255:
            raise ValueError("principal_id must be 1..255 characters")
        if not self.auth_context_id or len(self.auth_context_id) > 256:
            raise ValueError("auth_context_id must be 1..256 characters")
        scope = tuple(_uuid_text(item, "program_scope item") for item in self.program_scope)
        if not scope or len(set(scope)) != len(scope):
            raise ValueError("program_scope must contain unique Program UUIDs")
        scopes = tuple(str(item) for item in self.scopes)
        if not scopes or len(set(scopes)) != len(scopes) or any(not item for item in scopes):
            raise ValueError("scopes must contain unique non-empty values")
        object.__setattr__(self, "agent_identity", identity)
        object.__setattr__(self, "program_scope", scope)
        object.__setattr__(self, "run_id", _uuid_text(self.run_id, "run_id"))
        object.__setattr__(self, "scopes", scopes)

    def allows(self, method: M4StateMcpMethod | str) -> bool:
        return M4StateMcpMethod(method) in M4_STATE_MCP_METHOD_MATRIX[self.agent_identity]

    def to_trusted_principal(self) -> TrustedPrincipal:
        return TrustedPrincipal(
            principal_id=self.principal_id,
            principal_type=PrincipalType.AGENT,
            scopes=self.scopes,
            program_scope=self.program_scope,
            auth_context_id=self.auth_context_id,
        )

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "principal_id": self.principal_id,
            "agent_identity": self.agent_identity.value,
            "program_role": self.program_role,
            "program_scope": list(self.program_scope),
            "run_id": self.run_id,
            "auth_context_id": self.auth_context_id,
            "scopes": list(self.scopes),
        }


@dataclass(frozen=True, slots=True)
class RuntimeConfigSpec:
    """Trusted, key-free configuration used to create an immutable snapshot."""

    run_id: str
    provider_alias: str
    model_id: str
    parameters: Mapping[str, Any]
    max_calls: int
    max_input_tokens_per_call: int
    max_output_tokens_per_call: int
    max_cost_microunits_per_call: int
    max_total_input_tokens: int
    max_total_output_tokens: int
    max_total_cost_microunits: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _uuid_text(self.run_id, "run_id"))
        if (
            not _PROVIDER_ALIAS.fullmatch(self.provider_alias)
            or self.provider_alias != _AUTHORIZED_PROVIDER_ALIAS
        ):
            raise ValueError("provider_alias is not the authorized M4 provider")
        if self.model_id != _AUTHORIZED_MODEL_ID:
            raise ValueError("model_id is not the authorized fixed snapshot")
        parameters = _freeze_json(self.parameters)
        if not isinstance(parameters, Mapping):
            raise ValueError("parameters must be an object")
        if set(parameters) != _RUNTIME_PARAMETER_FIELDS:
            raise ValueError("parameters must contain the exact trusted M4 fields")
        temperature = parameters["temperature"]
        if (
            isinstance(temperature, bool)
            or not isinstance(temperature, (int, float))
            or float(temperature) != 0.01
        ):
            raise ValueError("parameters.temperature must equal 0.01")
        seed = parameters["seed"]
        if isinstance(seed, bool) or not isinstance(seed, int) or seed != 0:
            raise ValueError("parameters.seed must equal integer zero")
        if parameters["enable_thinking"] is not False:
            raise ValueError("parameters.enable_thinking must be false")
        response_format = parameters["response_format"]
        if (
            not isinstance(response_format, Mapping)
            or set(response_format) != {"type"}
            or response_format["type"] != "json_object"
        ):
            raise ValueError(
                "parameters.response_format must equal type=json_object"
            )
        object.__setattr__(self, "parameters", parameters)
        for field in (
            "max_calls",
            "max_input_tokens_per_call",
            "max_output_tokens_per_call",
            "max_cost_microunits_per_call",
            "max_total_input_tokens",
            "max_total_output_tokens",
            "max_total_cost_microunits",
        ):
            _positive_int(getattr(self, field), field)
        if self.max_total_input_tokens < self.max_input_tokens_per_call:
            raise ValueError("max_total_input_tokens cannot be below the per-call cap")
        if self.max_total_output_tokens < self.max_output_tokens_per_call:
            raise ValueError("max_total_output_tokens cannot be below the per-call cap")
        if self.max_total_cost_microunits < self.max_cost_microunits_per_call:
            raise ValueError("max_total_cost_microunits cannot be below the per-call cap")

    def to_dict(self) -> dict[str, Any]:
        return _thaw_json(
            {
                "run_id": self.run_id,
                "provider_alias": self.provider_alias,
                "model_id": self.model_id,
                "parameters": self.parameters,
                "max_calls": self.max_calls,
                "max_input_tokens_per_call": self.max_input_tokens_per_call,
                "max_output_tokens_per_call": self.max_output_tokens_per_call,
                "max_cost_microunits_per_call": self.max_cost_microunits_per_call,
                "max_total_input_tokens": self.max_total_input_tokens,
                "max_total_output_tokens": self.max_total_output_tokens,
                "max_total_cost_microunits": self.max_total_cost_microunits,
            }
        )


@dataclass(frozen=True, slots=True)
class ModelBudgetRequest:
    """Trusted request for one pre-call reservation."""

    run_id: str
    model_call_id: str
    snapshot_id: str
    max_input_tokens: int
    max_output_tokens: int
    max_cost_microunits: int

    def __post_init__(self) -> None:
        for field in ("run_id", "model_call_id", "snapshot_id"):
            object.__setattr__(self, field, _uuid_text(getattr(self, field), field))
        for field in ("max_input_tokens", "max_output_tokens", "max_cost_microunits"):
            _positive_int(getattr(self, field), field)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "model_call_id": self.model_call_id,
            "snapshot_id": self.snapshot_id,
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "max_cost_microunits": self.max_cost_microunits,
        }


@dataclass(frozen=True, slots=True)
class TrustedProviderUsageReceipt:
    """Gateway-issued usage fact; never accepted from an Agent or MCP body."""

    receipt_id: str
    program_id: str
    run_id: str
    model_call_id: str
    snapshot_id: str
    reservation_id: str
    provider_alias: str
    request_sha256: str
    usage_status: ProviderUsageStatus | str
    provider_status: ProviderCallStatus | str
    issued_by_principal_id: str
    issued_at: datetime
    provider_request_id: str | None = None
    response_sha256: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_microunits: int | None = None

    def __post_init__(self) -> None:
        for field in (
            "receipt_id",
            "program_id",
            "run_id",
            "model_call_id",
            "snapshot_id",
            "reservation_id",
        ):
            object.__setattr__(self, field, _uuid_text(getattr(self, field), field))
        if not _PROVIDER_ALIAS.fullmatch(self.provider_alias):
            raise ValueError("provider_alias must be a lowercase stable alias")
        if not _SHA256.fullmatch(self.request_sha256):
            raise ValueError("request_sha256 must be 64 lowercase hexadecimal characters")
        if self.response_sha256 is not None and not _SHA256.fullmatch(self.response_sha256):
            raise ValueError("response_sha256 must be 64 lowercase hexadecimal characters")
        if self.provider_request_id is not None and not _IDENTIFIER.fullmatch(
            self.provider_request_id
        ):
            raise ValueError("provider_request_id is not a stable identifier")
        usage_status = ProviderUsageStatus(self.usage_status)
        provider_status = ProviderCallStatus(self.provider_status)
        reported_values = (self.input_tokens, self.output_tokens, self.cost_microunits)
        if usage_status is ProviderUsageStatus.REPORTED:
            if any(value is None for value in reported_values):
                raise ValueError("reported usage requires token and cost values")
            for field in ("input_tokens", "output_tokens", "cost_microunits"):
                _non_negative_int(getattr(self, field), field)
        elif any(value is not None for value in reported_values):
            raise ValueError("unknown usage cannot assert partial token or cost values")
        if provider_status is ProviderCallStatus.SUCCEEDED and self.response_sha256 is None:
            raise ValueError("a succeeded Provider call requires response_sha256")
        if provider_status is ProviderCallStatus.SUCCEEDED and self.provider_request_id is None:
            raise ValueError("a succeeded Provider call requires provider_request_id")
        if not self.issued_by_principal_id or len(self.issued_by_principal_id) > 255:
            raise ValueError("issued_by_principal_id must be 1..255 characters")
        object.__setattr__(self, "usage_status", usage_status)
        object.__setattr__(self, "provider_status", provider_status)
        object.__setattr__(self, "issued_at", _utc_datetime(self.issued_at, "issued_at"))

    def to_dict(self) -> dict[str, Any]:
        return _thaw_json(
            {
                "receipt_id": self.receipt_id,
                "program_id": self.program_id,
                "run_id": self.run_id,
                "model_call_id": self.model_call_id,
                "snapshot_id": self.snapshot_id,
                "reservation_id": self.reservation_id,
                "provider_alias": self.provider_alias,
                "provider_request_id": self.provider_request_id,
                "request_sha256": self.request_sha256,
                "response_sha256": self.response_sha256,
                "usage_status": self.usage_status,
                "provider_status": self.provider_status,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "cost_microunits": self.cost_microunits,
                "issued_by_principal_id": self.issued_by_principal_id,
                "issued_at": self.issued_at,
            }
        )


@dataclass(frozen=True, slots=True)
class M4CommandEnvelope:
    """Internal-only envelope for M4 model-governance commands."""

    command_id: str
    idempotency_key: str
    program_id: str
    command_type: M4CommandType | str
    trusted_principal: TrustedPrincipal
    source_channel: SourceChannel | str
    runtime_config: RuntimeConfigSpec | None = None
    budget_request: ModelBudgetRequest | None = None
    usage_receipt: TrustedProviderUsageReceipt | None = None
    traceparent: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", _uuid_text(self.command_id, "command_id"))
        object.__setattr__(self, "program_id", _uuid_text(self.program_id, "program_id"))
        object.__setattr__(self, "command_type", M4CommandType(self.command_type))
        object.__setattr__(self, "source_channel", SourceChannel(self.source_channel))
        if not _IDEMPOTENCY.fullmatch(self.idempotency_key):
            raise ValueError("idempotency_key must be a stable 1..128 character key")
        if self.traceparent is not None and not _TRACEPARENT.fullmatch(self.traceparent):
            raise ValueError("traceparent must use W3C traceparent shape")

    @classmethod
    def new(cls, **values: Any) -> "M4CommandEnvelope":
        return cls(command_id=str(uuid4()), **values)

    def trusted_payload_dict(self) -> dict[str, Any]:
        if self.command_type is M4CommandType.RUNTIME_CONFIG_SNAPSHOT_CREATE:
            return self.runtime_config.to_dict() if self.runtime_config is not None else {}
        if self.command_type is M4CommandType.MODEL_BUDGET_RESERVE:
            return self.budget_request.to_dict() if self.budget_request is not None else {}
        return self.usage_receipt.to_dict() if self.usage_receipt is not None else {}

    def to_dict(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "command_id": self.command_id,
            "idempotency_key": self.idempotency_key,
            "program_id": self.program_id,
            "command_type": self.command_type.value,
            "trusted_principal": self.trusted_principal.to_dict(),
            "source_channel": self.source_channel.value,
            "trusted_payload": self.trusted_payload_dict(),
        }
        if self.traceparent is not None:
            document["traceparent"] = self.traceparent
        return document


def derive_usage_ledger_id(receipt_id: str) -> str:
    return str(uuid5(_LEDGER_NAMESPACE, _uuid_text(receipt_id, "receipt_id")))


def derive_provider_usage_receipt_id(
    *,
    program_id: str,
    run_id: str,
    model_call_id: str,
    reservation_id: str,
    request_sha256: str,
) -> str:
    """Derive one receipt ID before the Provider call for safe retry/settlement."""

    if not _SHA256.fullmatch(request_sha256):
        raise ValueError("request_sha256 must be 64 lowercase hexadecimal characters")
    material = "|".join(
        (
            _uuid_text(program_id, "program_id"),
            _uuid_text(run_id, "run_id"),
            _uuid_text(model_call_id, "model_call_id"),
            _uuid_text(reservation_id, "reservation_id"),
            request_sha256,
        )
    )
    return str(uuid5(_USAGE_RECEIPT_NAMESPACE, material))


def settlement_idempotency_key(receipt_id: str) -> str:
    return f"m4-settle:{_uuid_text(receipt_id, 'receipt_id')}"


__all__ = (
    "M4AgentIdentity",
    "M4CommandEnvelope",
    "M4CommandType",
    "M4ReasonCode",
    "M4QueryType",
    "M4StateMcpMethod",
    "M4_STATE_MCP_METHOD_MATRIX",
    "M4_WRITE_COMMAND_TYPES",
    "M4_QUERY_TYPES",
    "ModelBudgetRequest",
    "ProviderCallStatus",
    "ProviderUsageStatus",
    "RuntimeConfigSpec",
    "TrustedProviderUsageReceipt",
    "TrustedRuntimeContext",
    "derive_provider_usage_receipt_id",
    "derive_usage_ledger_id",
    "settlement_idempotency_key",
)
