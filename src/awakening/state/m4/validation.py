"""Strict M4 validation for State MCP calls and trusted runtime records."""

from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker

from awakening.state.contracts import PrincipalType, ReasonCode, SourceChannel
from awakening.state.validation import (
    ContractValidationError,
    canonical_sha256,
    find_forbidden_identity_fields,
)

from .contracts import (
    M4CommandEnvelope,
    M4CommandType,
    M4StateMcpMethod,
    TrustedProviderUsageReceipt,
)


SCHEMA_ROOT = Path(__file__).resolve().parents[4] / "schemas" / "m4"

STATE_MCP_SCHEMAS = {
    M4StateMcpMethod.GET_SNAPSHOT.value: "state-mcp/get-snapshot.params.schema.json",
    M4StateMcpMethod.SUBMIT_PROPOSAL.value: "state-mcp/submit-proposal.params.schema.json",
    M4StateMcpMethod.GET_COMMAND_STATUS.value: "state-mcp/get-command-status.params.schema.json",
    M4StateMcpMethod.APPLY_AUTHORIZED_CHANGE.value: (
        "state-mcp/apply-authorized-change.params.schema.json"
    ),
}

_FORBIDDEN_MCP_ASSERTIONS = frozenset(
    {
        "approved",
        "approval_token",
        "authorization_token",
        "decision_token",
        "diff",
        "patch",
        "risk",
        "risk_level",
        "trusted_runtime_context",
    }
)

_FORBIDDEN_SECRET_FIELDS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "access_token",
        "accesstoken",
        "bearer_token",
        "bearertoken",
        "client_secret",
        "clientsecret",
        "credential",
        "credentials",
        "key",
        "password",
        "private_key",
        "privatekey",
        "provider_key",
        "providerkey",
        "secret",
        "token",
    }
)


def _normalise_field_name(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _find_named_fields(
    value: Any,
    forbidden: frozenset[str],
    path: tuple[str | int, ...] = (),
) -> tuple[tuple[str | int, ...], ...]:
    matches: list[tuple[str | int, ...]] = []
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key)
            item_path = (*path, key)
            if _normalise_field_name(key) in forbidden:
                matches.append(item_path)
            matches.extend(_find_named_fields(item, forbidden, item_path))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            matches.extend(_find_named_fields(item, forbidden, (*path, index)))
    return tuple(matches)


def reject_mcp_assertions(params: Mapping[str, Any]) -> None:
    identity = find_forbidden_identity_fields(params)
    assertions = _find_named_fields(params, _FORBIDDEN_MCP_ASSERTIONS)
    matches = identity or assertions
    if matches:
        raise ContractValidationError(
            ReasonCode.IDENTITY_FIELD_FORBIDDEN,
            "identity, scope, approval, risk and Patch facts are server-owned",
            matches[0],
        )


def reject_runtime_secrets(value: Mapping[str, Any]) -> None:
    matches = _find_named_fields(value, _FORBIDDEN_SECRET_FIELDS)
    if matches:
        raise ContractValidationError(
            ReasonCode.SCHEMA_INVALID,
            "RuntimeConfigSnapshot cannot contain Provider credentials or secrets",
            matches[0],
        )


@lru_cache(maxsize=None)
def load_m4_schema(filename: str) -> Mapping[str, Any]:
    path = (SCHEMA_ROOT / filename).resolve()
    if SCHEMA_ROOT.resolve() not in path.parents:
        raise ValueError(f"schema path escapes M4 root: {filename!r}")
    with path.open("r", encoding="utf-8") as handle:
        schema = json.load(handle)
    Draft202012Validator.check_schema(schema)
    return schema


@lru_cache(maxsize=None)
def _validator(filename: str) -> Draft202012Validator:
    return Draft202012Validator(load_m4_schema(filename), format_checker=FormatChecker())


def _schema_error(filename: str, document: Any, reason_code: ReasonCode) -> None:
    errors = sorted(
        _validator(filename).iter_errors(document),
        key=lambda error: (tuple(str(item) for item in error.absolute_path), error.message),
    )
    if errors:
        first = errors[0]
        raise ContractValidationError(reason_code, first.message, tuple(first.absolute_path))


def validate_state_mcp_call(
    method: M4StateMcpMethod | str,
    params: Mapping[str, Any],
) -> None:
    try:
        method_value = M4StateMcpMethod(method).value
    except ValueError as exc:
        raise ContractValidationError(
            ReasonCode.COMMAND_NOT_REGISTERED,
            f"unregistered M4 State MCP method {method!r}",
            ("method",),
        ) from exc
    if not isinstance(params, Mapping):
        raise ContractValidationError(
            ReasonCode.SCHEMA_INVALID,
            "State MCP parameters must be an object",
            ("params",),
        )
    reject_mcp_assertions(params)
    _schema_error(STATE_MCP_SCHEMAS[method_value], dict(params), ReasonCode.SCHEMA_INVALID)


def validate_runtime_config_snapshot(record: Mapping[str, Any]) -> None:
    reject_runtime_secrets(record.get("parameters", {}))
    _schema_error(
        "runtime-config-snapshot.schema.json",
        dict(record),
        ReasonCode.SCHEMA_INVALID,
    )


def validate_model_budget_reservation(record: Mapping[str, Any]) -> None:
    _schema_error(
        "model-budget-reservation.schema.json",
        dict(record),
        ReasonCode.SCHEMA_INVALID,
    )


def validate_provider_usage_receipt(
    receipt: TrustedProviderUsageReceipt | Mapping[str, Any],
) -> None:
    document = receipt.to_dict() if isinstance(receipt, TrustedProviderUsageReceipt) else dict(receipt)
    _schema_error(
        "provider-usage-receipt.schema.json",
        document,
        ReasonCode.SCHEMA_INVALID,
    )


def validate_m4_command_envelope(envelope: M4CommandEnvelope) -> None:
    if not isinstance(envelope, M4CommandEnvelope):
        raise ContractValidationError(
            ReasonCode.INVALID_ENVELOPE,
            "M4 internal command requires M4CommandEnvelope",
        )
    if envelope.source_channel is not SourceChannel.INTERNAL:
        raise ContractValidationError(
            ReasonCode.PRINCIPAL_NOT_ALLOWED,
            "M4 model-governance commands are internal-only",
            ("source_channel",),
        )
    if envelope.trusted_principal.principal_type is not PrincipalType.SERVICE:
        raise ContractValidationError(
            ReasonCode.PRINCIPAL_NOT_ALLOWED,
            "M4 model-governance commands require a trusted service principal",
            ("trusted_principal", "principal_type"),
        )
    fields = {
        M4CommandType.RUNTIME_CONFIG_SNAPSHOT_CREATE: envelope.runtime_config,
        M4CommandType.MODEL_BUDGET_RESERVE: envelope.budget_request,
        M4CommandType.MODEL_BUDGET_SETTLE: envelope.usage_receipt,
    }
    if fields[envelope.command_type] is None or sum(value is not None for value in fields.values()) != 1:
        raise ContractValidationError(
            ReasonCode.INVALID_ENVELOPE,
            "exactly the trusted payload matching command_type is required",
            ("trusted_payload",),
        )
    if envelope.runtime_config is not None:
        reject_runtime_secrets(envelope.runtime_config.to_dict()["parameters"])
    if envelope.usage_receipt is not None:
        validate_provider_usage_receipt(envelope.usage_receipt)
        if envelope.usage_receipt.program_id != envelope.program_id:
            raise ContractValidationError(
                ReasonCode.INVALID_ENVELOPE,
                "ProviderUsageReceipt Program does not match the envelope",
                ("trusted_payload", "program_id"),
            )


def m4_request_hash(envelope: M4CommandEnvelope) -> str:
    return canonical_sha256(
        {
            "program_id": envelope.program_id,
            "command_type": envelope.command_type.value,
            "source_channel": envelope.source_channel.value,
            "trusted_principal": envelope.trusted_principal.to_dict(),
            "trusted_payload": envelope.trusted_payload_dict(),
        }
    )


__all__ = (
    "SCHEMA_ROOT",
    "STATE_MCP_SCHEMAS",
    "load_m4_schema",
    "m4_request_hash",
    "reject_mcp_assertions",
    "reject_runtime_secrets",
    "validate_m4_command_envelope",
    "validate_model_budget_reservation",
    "validate_provider_usage_receipt",
    "validate_runtime_config_snapshot",
    "validate_state_mcp_call",
)
