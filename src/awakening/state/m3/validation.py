"""Strict M3 wire/internal validation without changing the frozen M2 registry."""

from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker

from awakening.state.contracts import ReasonCode, SourceChannel, TrustedPrincipal
from awakening.state.validation import (
    ContractValidationError,
    canonical_sha256,
    find_forbidden_identity_fields,
)

from .contracts import (
    M3CommandEnvelope,
    M3CommandType,
    M3QueryType,
    TrustedEvidenceReceipt,
    TrustedEvidenceRejection,
    TrustedRequestedOutboxRef,
)


SCHEMA_ROOT = Path(__file__).resolve().parents[4] / "schemas" / "m3"

M3_COMMAND_PAYLOAD_SCHEMAS = {
    M3CommandType.INGEST_JOB_CREATE.value: "evidence.ingest_job.create.payload.schema.json",
    M3CommandType.INGEST_JOB_CLAIM.value: "evidence.ingest_job.claim.payload.schema.json",
    M3CommandType.INGEST_JOB_FINALIZE.value: "evidence.ingest_job.finalize.payload.schema.json",
    M3CommandType.INGEST_JOB_REJECT.value: "evidence.ingest_job.reject.payload.schema.json",
}
M3_QUERY_PAYLOAD_SCHEMAS = {
    M3QueryType.INGEST_JOB_GET.value: "evidence.ingest_job.get.query.schema.json",
}

# Exact normalized keys.  ``authorization_ref`` and ``job_id`` are deliberate
# public fields and therefore are not present here.
FORBIDDEN_SERVER_FIELDS = frozenset(
    {
        "program_id",
        "program_scope",
        "uri",
        "object_uri",
        "object_ref",
        "verified_object_ref",
        "object_key",
        "hash",
        "sha256",
        "raw_sha256",
        "receipt",
        "receipt_id",
        "claim_id",
        "outbox_event_id",
        "domain_event_id",
        "scan_result",
        "check_result",
        "checker_result",
        "findings",
        "export_safe",
    }
)


def _normalise_field_name(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _find_server_fields(value: Any, path: tuple[str | int, ...] = ()) -> tuple[tuple[str | int, ...], ...]:
    matches: list[tuple[str | int, ...]] = []
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key)
            item_path = (*path, key)
            if _normalise_field_name(key) in FORBIDDEN_SERVER_FIELDS:
                matches.append(item_path)
            matches.extend(_find_server_fields(item, item_path))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            matches.extend(_find_server_fields(item, (*path, index)))
    return tuple(matches)


def reject_server_owned_fields(payload: Mapping[str, Any]) -> None:
    identity = find_forbidden_identity_fields(payload)
    server = _find_server_fields(payload)
    matches = identity or server
    if matches:
        raise ContractValidationError(
            ReasonCode.IDENTITY_FIELD_FORBIDDEN,
            "identity, Program scope, object provenance and Receipt are server-owned",
            matches[0],
        )


@lru_cache(maxsize=None)
def load_m3_schema(filename: str) -> Mapping[str, Any]:
    path = (SCHEMA_ROOT / filename).resolve()
    if path.parent != SCHEMA_ROOT.resolve():
        raise ValueError(f"schema path escapes M3 root: {filename!r}")
    with path.open("r", encoding="utf-8") as handle:
        schema = json.load(handle)
    Draft202012Validator.check_schema(schema)
    return schema


@lru_cache(maxsize=None)
def _validator(filename: str) -> Draft202012Validator:
    return Draft202012Validator(load_m3_schema(filename), format_checker=FormatChecker())


def _schema_error(filename: str, document: Any, reason_code: ReasonCode) -> None:
    errors = sorted(
        _validator(filename).iter_errors(document),
        key=lambda error: (tuple(str(item) for item in error.absolute_path), error.message),
    )
    if errors:
        first = errors[0]
        raise ContractValidationError(reason_code, first.message, tuple(first.absolute_path))


def validate_m3_wire_payload(command_type: M3CommandType | str, payload: Mapping[str, Any]) -> None:
    value = command_type.value if isinstance(command_type, M3CommandType) else str(command_type)
    filename = M3_COMMAND_PAYLOAD_SCHEMAS.get(value)
    if filename is None:
        raise ContractValidationError(
            ReasonCode.COMMAND_NOT_REGISTERED,
            f"unregistered M3 command_type {value!r}",
            ("command_type",),
        )
    if not isinstance(payload, Mapping):
        raise ContractValidationError(ReasonCode.SCHEMA_INVALID, "payload must be an object", ("payload",))
    reject_server_owned_fields(payload)
    _schema_error(filename, dict(payload), ReasonCode.SCHEMA_INVALID)


def validate_m3_query_payload(query_type: M3QueryType | str, payload: Mapping[str, Any]) -> None:
    value = query_type.value if isinstance(query_type, M3QueryType) else str(query_type)
    filename = M3_QUERY_PAYLOAD_SCHEMAS.get(value)
    if filename is None:
        raise ContractValidationError(
            ReasonCode.COMMAND_NOT_REGISTERED,
            f"unregistered M3 query_type {value!r}",
            ("query_type",),
        )
    if not isinstance(payload, Mapping):
        raise ContractValidationError(ReasonCode.SCHEMA_INVALID, "query payload must be an object", ("payload",))
    reject_server_owned_fields(payload)
    _schema_error(filename, dict(payload), ReasonCode.SCHEMA_INVALID)


def validate_m3_command_envelope(envelope: M3CommandEnvelope | Mapping[str, Any]) -> None:
    document = envelope.to_dict() if isinstance(envelope, M3CommandEnvelope) else dict(envelope)
    _schema_error("command-envelope.schema.json", document, ReasonCode.INVALID_ENVELOPE)
    validate_m3_wire_payload(str(document["command_type"]), document["payload"])


def build_m3_command_envelope(
    *,
    idempotency_key: str,
    program_id: str,
    command_type: M3CommandType | str,
    payload: Mapping[str, Any],
    trusted_principal: TrustedPrincipal,
    source_channel: SourceChannel | str,
    trusted_outbox_ref: TrustedRequestedOutboxRef | None = None,
    trusted_receipt: TrustedEvidenceReceipt | None = None,
    trusted_rejection: TrustedEvidenceRejection | None = None,
    traceparent: str | None = None,
) -> M3CommandEnvelope:
    validate_m3_wire_payload(command_type, payload)
    envelope = M3CommandEnvelope.new(
        idempotency_key=idempotency_key,
        program_id=program_id,
        command_type=command_type,
        payload=payload,
        trusted_principal=trusted_principal,
        source_channel=source_channel,
        trusted_outbox_ref=trusted_outbox_ref,
        trusted_receipt=trusted_receipt,
        trusted_rejection=trusted_rejection,
        traceparent=traceparent,
    )
    validate_m3_command_envelope(envelope)
    return envelope


def m3_request_hash(envelope: M3CommandEnvelope) -> str:
    document = envelope.to_dict()
    document.pop("command_id", None)
    document.pop("traceparent", None)
    return canonical_sha256(document)


__all__ = (
    "FORBIDDEN_SERVER_FIELDS",
    "M3_COMMAND_PAYLOAD_SCHEMAS",
    "M3_QUERY_PAYLOAD_SCHEMAS",
    "build_m3_command_envelope",
    "load_m3_schema",
    "m3_request_hash",
    "reject_server_owned_fields",
    "validate_m3_command_envelope",
    "validate_m3_query_payload",
    "validate_m3_wire_payload",
)
