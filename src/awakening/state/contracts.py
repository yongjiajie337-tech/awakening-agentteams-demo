"""M2 transport-neutral contracts for authoritative state commands.

The wire body never contains a principal or command identifier.  Adapters
validate the wire payload and then call :func:`build_command_envelope` from
``awakening.state.validation`` with a server-derived ``TrustedPrincipal``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4


class PrincipalType(StrEnum):
    """Canonical principal kinds produced by trusted adapters."""

    USER = "user"
    AGENT = "agent"
    SERVICE = "service"
    SYSTEM = "system"


class SourceChannel(StrEnum):
    """Trusted adapter channel, never accepted from the command body."""

    WEB = "web"
    MCP = "mcp"
    INTERNAL = "internal"


class CommandType(StrEnum):
    """The five M2 business-writing commands."""

    PROGRAM_CREATE = "program.create"
    STATE_PROPOSAL_SUBMIT = "state.proposal.submit"
    HUMAN_DECISION_RECORD = "human_decision.record"
    STATE_PROPOSAL_APPLY = "state.proposal.apply"
    APPROVAL_EXPIRE = "approval.expire"


class QueryType(StrEnum):
    """Read-only M2 query contracts; queries never use ``CommandEnvelope``."""

    PROGRAM_SNAPSHOT_GET = "program.snapshot.get"
    APPROVAL_GET = "approval.get"
    DECISION_GET = "decision.get"
    COMMAND_STATUS_GET = "command.status.get"


class CommandStatus(StrEnum):
    """Terminal status of a deterministic command attempt."""

    COMMITTED = "COMMITTED"
    REJECTED = "REJECTED"


class ReasonCode(StrEnum):
    """Frozen, intentionally small M2 machine-readable reason taxonomy."""

    OK = "OK"
    INVALID_ENVELOPE = "INVALID_ENVELOPE"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    IDENTITY_FIELD_FORBIDDEN = "IDENTITY_FIELD_FORBIDDEN"
    COMMAND_NOT_REGISTERED = "COMMAND_NOT_REGISTERED"
    PRINCIPAL_NOT_ALLOWED = "PRINCIPAL_NOT_ALLOWED"
    PROGRAM_SCOPE_DENIED = "PROGRAM_SCOPE_DENIED"
    NOT_FOUND = "NOT_FOUND"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
    STATE_TRANSITION_INVALID = "STATE_TRANSITION_INVALID"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    DECISION_INVALID = "DECISION_INVALID"
    DECISION_REPLAYED = "DECISION_REPLAYED"
    CONFLICT = "CONFLICT"
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
    TRANSACTION_ABORTED = "TRANSACTION_ABORTED"


WRITE_COMMAND_TYPES = frozenset(item.value for item in CommandType)
QUERY_TYPES = frozenset(item.value for item in QueryType)
REASON_CODES = frozenset(item.value for item in ReasonCode)


def _freeze_json(value: Any) -> Any:
    """Copy a JSON-shaped value into immutable containers."""

    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    """Return ordinary dict/list containers suitable for JSON and schemas."""

    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    if isinstance(value, StrEnum):
        return value.value
    return value


@dataclass(frozen=True, slots=True)
class TrustedPrincipal:
    """A principal derived from a trusted server/runtime context.

    This type is an internal value object.  It is deliberately not accepted by
    any wire payload schema.
    """

    principal_id: str
    principal_type: PrincipalType | str
    scopes: tuple[str, ...] = ()
    program_scope: tuple[str, ...] = ()
    auth_context_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "principal_type", PrincipalType(self.principal_type))
        object.__setattr__(self, "scopes", tuple(self.scopes))
        object.__setattr__(self, "program_scope", tuple(self.program_scope))

    def to_dict(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "principal_id": self.principal_id,
            "principal_type": self.principal_type.value,
            "scopes": list(self.scopes),
            "program_scope": list(self.program_scope),
        }
        if self.auth_context_id is not None:
            document["auth_context_id"] = self.auth_context_id
        return document


@dataclass(frozen=True, slots=True)
class CommandEnvelope:
    """Internal envelope built by an adapter after wire validation/authentication."""

    command_id: str
    idempotency_key: str
    program_id: str
    command_type: CommandType | str
    payload: Mapping[str, Any]
    trusted_principal: TrustedPrincipal
    source_channel: SourceChannel | str
    expected_state_version: int | None = None
    base_plan_version_id: str | None = None
    traceparent: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_type", CommandType(self.command_type))
        object.__setattr__(self, "source_channel", SourceChannel(self.source_channel))
        object.__setattr__(self, "payload", _freeze_json(self.payload))

    @classmethod
    def new(
        cls,
        *,
        idempotency_key: str,
        program_id: str,
        command_type: CommandType | str,
        payload: Mapping[str, Any],
        trusted_principal: TrustedPrincipal,
        source_channel: SourceChannel | str,
        expected_state_version: int | None = None,
        base_plan_version_id: str | None = None,
        traceparent: str | None = None,
    ) -> "CommandEnvelope":
        """Create an envelope with a server-generated command identifier."""

        return cls(
            command_id=str(uuid4()),
            idempotency_key=idempotency_key,
            program_id=program_id,
            command_type=command_type,
            payload=payload,
            trusted_principal=trusted_principal,
            source_channel=source_channel,
            expected_state_version=expected_state_version,
            base_plan_version_id=base_plan_version_id,
            traceparent=traceparent,
        )

    def to_dict(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "command_id": self.command_id,
            "idempotency_key": self.idempotency_key,
            "program_id": self.program_id,
            "command_type": self.command_type.value,
            "payload": _thaw_json(self.payload),
            "trusted_principal": self.trusted_principal.to_dict(),
            "source_channel": self.source_channel.value,
        }
        if self.expected_state_version is not None:
            document["expected_state_version"] = self.expected_state_version
        if self.base_plan_version_id is not None:
            document["base_plan_version_id"] = self.base_plan_version_id
        if self.traceparent is not None:
            document["traceparent"] = self.traceparent
        return document


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Transport-neutral result returned by the authoritative command handler."""

    command_id: str
    status: CommandStatus | str
    reason_code: ReasonCode | str
    state_version: int | None = None
    result: Mapping[str, Any] = field(default_factory=dict)
    replayed: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", CommandStatus(self.status))
        object.__setattr__(self, "reason_code", ReasonCode(self.reason_code))
        object.__setattr__(self, "result", _freeze_json(self.result))

    @property
    def committed(self) -> bool:
        return self.status is CommandStatus.COMMITTED

    def to_dict(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "command_id": self.command_id,
            "status": self.status.value,
            "reason_code": self.reason_code.value,
            "result": _thaw_json(self.result),
            "replayed": self.replayed,
        }
        if self.state_version is not None:
            document["state_version"] = self.state_version
        return document


__all__ = (
    "CommandEnvelope",
    "CommandResult",
    "CommandStatus",
    "CommandType",
    "PrincipalType",
    "QUERY_TYPES",
    "QueryType",
    "REASON_CODES",
    "ReasonCode",
    "SourceChannel",
    "TrustedPrincipal",
    "WRITE_COMMAND_TYPES",
)
