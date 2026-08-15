"""Authoritative M2 state contracts and deterministic validation."""

from .contracts import (
    CommandEnvelope,
    CommandResult,
    CommandStatus,
    CommandType,
    PrincipalType,
    QueryType,
    ReasonCode,
    SourceChannel,
    TrustedPrincipal,
)
from .validation import (
    ContractValidationError,
    build_command_envelope,
    canonical_json_bytes,
    canonical_sha256,
    validate_command_envelope,
    validate_command_result,
    validate_query_payload,
    validate_wire_payload,
)
from .service import BootstrapMember, BusinessRuleError, StateService

__all__ = (
    "BootstrapMember",
    "BusinessRuleError",
    "CommandEnvelope",
    "CommandResult",
    "CommandStatus",
    "CommandType",
    "ContractValidationError",
    "PrincipalType",
    "QueryType",
    "ReasonCode",
    "SourceChannel",
    "StateService",
    "TrustedPrincipal",
    "build_command_envelope",
    "canonical_json_bytes",
    "canonical_sha256",
    "validate_command_envelope",
    "validate_command_result",
    "validate_query_payload",
    "validate_wire_payload",
)
