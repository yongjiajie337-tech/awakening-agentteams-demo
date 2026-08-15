"""Transport-neutral contracts for the minimal M4 Model Gateway."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw(item) for item in value]
    if isinstance(value, StrEnum):
        return value.value
    return value


class GatewayReasonCode(StrEnum):
    OK = "OK"
    RUNTIME_PRINCIPAL_DENIED = "RUNTIME_PRINCIPAL_DENIED"
    SKILL_NOT_ALLOWED = "SKILL_NOT_ALLOWED"
    RUNTIME_BODY_FORBIDDEN = "RUNTIME_BODY_FORBIDDEN"
    TOOL_NOT_ALLOWED = "TOOL_NOT_ALLOWED"
    PROVIDER_INPUT_INVALID = "PROVIDER_INPUT_INVALID"
    SNAPSHOT_NOT_COMMITTED = "SNAPSHOT_NOT_COMMITTED"
    RESERVATION_NOT_COMMITTED = "RESERVATION_NOT_COMMITTED"
    CONTEXT_MANIFEST_NOT_COMMITTED = "CONTEXT_MANIFEST_NOT_COMMITTED"
    PRECALL_BINDING_MISMATCH = "PRECALL_BINDING_MISMATCH"
    PROVIDER_NOT_CONFIGURED = "PROVIDER_NOT_CONFIGURED"
    PROVIDER_TRANSPORT_FAILED = "PROVIDER_TRANSPORT_FAILED"
    USAGE_SETTLEMENT_FAILED = "USAGE_SETTLEMENT_FAILED"
    INVOCATION_RECEIPT_FAILED = "INVOCATION_RECEIPT_FAILED"


@dataclass(frozen=True, slots=True)
class ModelInvocation:
    """Server-owned invocation binding; the Agent supplies only model input."""

    program_id: str
    run_id: str
    model_call_id: str
    agent_identity_id: str
    agent_identity_version: str
    skill_name: str
    skill_version: str
    runtime_config_snapshot_id: str
    reservation_id: str
    provider_input: Mapping[str, Any]
    object_refs: tuple[Mapping[str, Any], ...] = ()
    exclusions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_input", _freeze(self.provider_input))
        object.__setattr__(
            self,
            "object_refs",
            tuple(_freeze(item) for item in self.object_refs),
        )
        object.__setattr__(self, "exclusions", tuple(self.exclusions))


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    provider_alias: str
    model_id: str
    model_call_id: str
    request_sha256: str
    input_document: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "input_document", _freeze(self.input_document))


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    provider_request_id: str
    output_document: Mapping[str, Any]
    skill_output_document: Any
    input_tokens: int
    output_tokens: int
    cost_microunits: int
    response_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "output_document", _freeze(self.output_document))
        object.__setattr__(self, "skill_output_document", _freeze(self.skill_output_document))


@dataclass(frozen=True, slots=True)
class GatewayResult:
    committed: bool
    reason_code: GatewayReasonCode
    model_call_id: str
    context_manifest_id: str | None = None
    provider_response: ProviderResponse | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", _freeze(self.details))


class ProviderPort(Protocol):
    @property
    def provider_alias(self) -> str: ...

    @property
    def call_count(self) -> int: ...

    def invoke(self, request: ProviderRequest) -> ProviderResponse: ...


class StateAuthorityPort(Protocol):
    def get_runtime_config_snapshot(
        self,
        *,
        snapshot_id: str,
        program_id: str,
        run_id: str,
    ) -> Mapping[str, Any] | None: ...

    def get_model_budget_reservation(
        self,
        *,
        reservation_id: str,
        program_id: str,
        run_id: str,
        model_call_id: str,
    ) -> Mapping[str, Any] | None: ...

    def settle_model_budget(self, receipt: Any) -> Mapping[str, Any]: ...


class RuntimeAuthorizerPort(Protocol):
    def authorize_model_call(self, invocation: ModelInvocation) -> GatewayReasonCode: ...


__all__ = (
    "GatewayReasonCode",
    "GatewayResult",
    "ModelInvocation",
    "ProviderPort",
    "ProviderRequest",
    "ProviderResponse",
    "RuntimeAuthorizerPort",
    "StateAuthorityPort",
    "thaw",
)
