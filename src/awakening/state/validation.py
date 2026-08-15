"""Strict Draft 2020-12 validation and canonical JSON helpers for M2."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker

from .contracts import (
    CommandEnvelope,
    CommandResult,
    CommandType,
    QueryType,
    ReasonCode,
    SourceChannel,
    TrustedPrincipal,
)


SCHEMA_ROOT = Path(__file__).resolve().parents[3] / "schemas" / "m2"

COMMAND_PAYLOAD_SCHEMAS = {
    CommandType.PROGRAM_CREATE.value: "program.create.payload.schema.json",
    CommandType.STATE_PROPOSAL_SUBMIT.value: "state.proposal.submit.payload.schema.json",
    CommandType.HUMAN_DECISION_RECORD.value: "human_decision.record.payload.schema.json",
    CommandType.STATE_PROPOSAL_APPLY.value: "state.proposal.apply.payload.schema.json",
    CommandType.APPROVAL_EXPIRE.value: "approval.expire.payload.schema.json",
}

QUERY_PAYLOAD_SCHEMAS = {
    QueryType.PROGRAM_SNAPSHOT_GET.value: "program.snapshot.get.query.schema.json",
    QueryType.APPROVAL_GET.value: "approval.get.query.schema.json",
    QueryType.DECISION_GET.value: "decision.get.query.schema.json",
    QueryType.COMMAND_STATUS_GET.value: "command.status.get.query.schema.json",
}

# Exact field names only: ``target_role`` is a business field and remains valid.
FORBIDDEN_IDENTITY_FIELDS = frozenset(
    {
        "agent_id",
        "agent_identity_id",
        "auth_context",
        "auth_context_id",
        "authenticated_principal",
        "authenticated_user",
        "authorization",
        "internal_identity",
        "jwt",
        "principal",
        "principal_id",
        "principal_type",
        "program_scope",
        "role",
        "roles",
        "scope",
        "scopes",
        "session_id",
        "token",
        "trusted_principal",
        "user_id",
    }
)


@dataclass(frozen=True, slots=True)
class ContractValidationError(ValueError):
    """Deterministic validation failure with a machine-readable reason code."""

    reason_code: ReasonCode
    message: str
    path: tuple[str | int, ...] = ()

    def __str__(self) -> str:
        location = "$." + ".".join(str(item) for item in self.path) if self.path else "$"
        return f"{self.reason_code.value} at {location}: {self.message}"


def _normalise_field_name(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def find_forbidden_identity_fields(
    value: Any,
    *,
    path: tuple[str | int, ...] = (),
) -> tuple[tuple[str | int, ...], ...]:
    """Return every nested path that attempts to carry caller identity."""

    matches: list[tuple[str | int, ...]] = []
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key)
            item_path = (*path, key)
            if _normalise_field_name(key) in FORBIDDEN_IDENTITY_FIELDS:
                matches.append(item_path)
            matches.extend(find_forbidden_identity_fields(item, path=item_path))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            matches.extend(find_forbidden_identity_fields(item, path=(*path, index)))
    return tuple(matches)


def reject_identity_fields(payload: Mapping[str, Any]) -> None:
    """Fail closed before Schema validation when payload identity is asserted."""

    matches = find_forbidden_identity_fields(payload)
    if matches:
        raise ContractValidationError(
            ReasonCode.IDENTITY_FIELD_FORBIDDEN,
            "identity is derived from trusted adapter context and is forbidden in payload",
            matches[0],
        )


@lru_cache(maxsize=None)
def load_schema(filename: str) -> Mapping[str, Any]:
    """Load and meta-validate one repository-owned Draft 2020-12 schema."""

    path = (SCHEMA_ROOT / filename).resolve()
    if path.parent != SCHEMA_ROOT.resolve():
        raise ValueError(f"schema path escapes M2 root: {filename!r}")
    with path.open("r", encoding="utf-8") as handle:
        schema = json.load(handle)
    Draft202012Validator.check_schema(schema)
    return schema


@lru_cache(maxsize=None)
def _validator(filename: str) -> Draft202012Validator:
    return Draft202012Validator(load_schema(filename), format_checker=FormatChecker())


def _schema_error(filename: str, document: Any, reason_code: ReasonCode) -> None:
    errors = sorted(
        _validator(filename).iter_errors(document),
        key=lambda error: (tuple(str(item) for item in error.absolute_path), error.message),
    )
    if not errors:
        return
    first = errors[0]
    raise ContractValidationError(
        reason_code,
        first.message,
        tuple(first.absolute_path),
    )


def _command_type_value(command_type: CommandType | str) -> str:
    return command_type.value if isinstance(command_type, CommandType) else str(command_type)


def _query_type_value(query_type: QueryType | str) -> str:
    return query_type.value if isinstance(query_type, QueryType) else str(query_type)


def validate_wire_payload(
    command_type: CommandType | str,
    payload: Mapping[str, Any],
) -> None:
    """Validate an untrusted command body before an envelope is constructed."""

    value = _command_type_value(command_type)
    filename = COMMAND_PAYLOAD_SCHEMAS.get(value)
    if filename is None:
        raise ContractValidationError(
            ReasonCode.COMMAND_NOT_REGISTERED,
            f"unregistered command_type {value!r}",
            ("command_type",),
        )
    if not isinstance(payload, Mapping):
        raise ContractValidationError(
            ReasonCode.SCHEMA_INVALID,
            "payload must be an object",
            ("payload",),
        )
    reject_identity_fields(payload)
    document = _json_compatible(payload)
    _schema_error(filename, document, ReasonCode.SCHEMA_INVALID)
    _validate_command_semantics(value, document)


def validate_query_payload(
    query_type: QueryType | str,
    payload: Mapping[str, Any],
) -> None:
    """Validate one of the four strict read-only query bodies."""

    value = _query_type_value(query_type)
    filename = QUERY_PAYLOAD_SCHEMAS.get(value)
    if filename is None:
        raise ContractValidationError(
            ReasonCode.COMMAND_NOT_REGISTERED,
            f"unregistered query_type {value!r}",
            ("query_type",),
        )
    if not isinstance(payload, Mapping):
        raise ContractValidationError(
            ReasonCode.SCHEMA_INVALID,
            "query payload must be an object",
            ("payload",),
        )
    reject_identity_fields(payload)
    _schema_error(filename, _json_compatible(payload), ReasonCode.SCHEMA_INVALID)


def validate_command_envelope(envelope: CommandEnvelope | Mapping[str, Any]) -> None:
    """Validate an internal envelope and its registered wire payload."""

    document = envelope.to_dict() if isinstance(envelope, CommandEnvelope) else _json_compatible(envelope)
    if not isinstance(document, Mapping):
        raise ContractValidationError(
            ReasonCode.INVALID_ENVELOPE,
            "command envelope must be an object",
        )
    _schema_error("command-envelope.schema.json", document, ReasonCode.INVALID_ENVELOPE)
    validate_wire_payload(str(document["command_type"]), document["payload"])


def validate_command_result(result: CommandResult | Mapping[str, Any]) -> None:
    document = result.to_dict() if isinstance(result, CommandResult) else _json_compatible(result)
    _schema_error("command-result.schema.json", document, ReasonCode.SCHEMA_INVALID)


def build_command_envelope(
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
) -> CommandEnvelope:
    """Validate wire data and build a server-owned internal command envelope."""

    validate_wire_payload(command_type, payload)
    envelope = CommandEnvelope.new(
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
    validate_command_envelope(envelope)
    return envelope


def _validate_command_semantics(command_type: str, payload: Mapping[str, Any]) -> None:
    if command_type != CommandType.PROGRAM_CREATE.value:
        return
    tasks = payload["plan"]["tasks"]
    task_keys = [task["task_key"] for task in tasks]
    task_orders = [task["order"] for task in tasks]
    if len(set(task_keys)) != len(task_keys):
        raise ContractValidationError(
            ReasonCode.SCHEMA_INVALID,
            "plan task_key values must be unique",
            ("plan", "tasks"),
        )
    if len(set(task_orders)) != len(task_orders):
        raise ContractValidationError(
            ReasonCode.SCHEMA_INVALID,
            "plan task order values must be unique",
            ("plan", "tasks"),
        )


def _json_compatible(value: Any, *, path: tuple[str | int, ...] = ()) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        document: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"JSON object key at {path!r} must be a string")
            document[key] = _json_compatible(item, path=(*path, key))
        return document
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_compatible(item, path=(*path, index)) for index, item in enumerate(value)]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"non-finite JSON number at {path!r}")
        return value
    raise TypeError(f"unsupported JSON value at {path!r}: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    """Return stable UTF-8 JSON bytes for hashing and idempotency comparison."""

    if hasattr(value, "to_dict") and callable(value.to_dict):
        value = value.to_dict()
    document = _json_compatible(value)
    return json.dumps(
        document,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    """Return the lowercase SHA-256 hex digest of canonical JSON bytes."""

    return sha256(canonical_json_bytes(value)).hexdigest()


__all__ = (
    "COMMAND_PAYLOAD_SCHEMAS",
    "ContractValidationError",
    "FORBIDDEN_IDENTITY_FIELDS",
    "QUERY_PAYLOAD_SCHEMAS",
    "build_command_envelope",
    "canonical_json_bytes",
    "canonical_sha256",
    "find_forbidden_identity_fields",
    "load_schema",
    "reject_identity_fields",
    "validate_command_envelope",
    "validate_command_result",
    "validate_query_payload",
    "validate_wire_payload",
)
