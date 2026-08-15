"""Versioned M3 contracts layered on the frozen M2 State Service types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import re
from types import MappingProxyType
from typing import Any, Mapping, Sequence
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from awakening.state.contracts import SourceChannel, TrustedPrincipal


M3_POLICY_VERSION = "deterministic-text-v1"
M3_CHECKER_VERSION = "deterministic-text-v1"
M3_FORMAT_NAME = "txt"
M3_MEDIA_TYPE = "text/plain; charset=utf-8"
M3_MAX_BYTES = 65_536
M3_VERIFIED_REF_PREFIX = (
    "s3://hiclaw-storage/hiclaw/hiclaw-storage/awakening/m3/"
    "verified-evidence"
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REASON = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_CLAIM_NAMESPACE = uuid5(NAMESPACE_URL, "awakening.local/m3/evidence-claim")
_RECEIPT_NAMESPACE = uuid5(NAMESPACE_URL, "awakening.local/m3/evidence-receipt")
_ITEM_NAMESPACE = uuid5(NAMESPACE_URL, "awakening.local/m3/evidence-item")


class M3CommandType(StrEnum):
    INGEST_JOB_CREATE = "evidence.ingest_job.create"
    INGEST_JOB_CLAIM = "evidence.ingest_job.claim"
    INGEST_JOB_FINALIZE = "evidence.ingest_job.finalize"
    INGEST_JOB_REJECT = "evidence.ingest_job.reject"


class M3QueryType(StrEnum):
    INGEST_JOB_GET = "evidence.ingest_job.get"


class EvidenceIngestJobStatus(StrEnum):
    CREATED = "created"
    CLAIMED = "claimed"
    FINALIZED = "finalized"
    REJECTED = "rejected"


M3_WRITE_COMMAND_TYPES = frozenset(item.value for item in M3CommandType)
M3_QUERY_TYPES = frozenset(item.value for item in M3QueryType)


def _uuid_text(value: str, field: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{field} must be a UUID") from exc


def _utc_datetime(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_freeze_json(item) for item in value)
    return value


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
class TrustedRequestedOutboxRef:
    """Server-side reference to one committed ingest-requested Outbox row."""

    outbox_event_id: str
    domain_event_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "outbox_event_id", _uuid_text(self.outbox_event_id, "outbox_event_id"))
        object.__setattr__(self, "domain_event_id", _uuid_text(self.domain_event_id, "domain_event_id"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "outbox_event_id": self.outbox_event_id,
            "domain_event_id": self.domain_event_id,
        }


@dataclass(frozen=True, slots=True)
class TrustedEvidenceReceipt:
    """Trusted Ingestion attestation; never accepted from a public wire body."""

    receipt_id: str
    program_id: str
    job_id: str
    claim_id: str
    verified_object_ref: str
    raw_sha256: str
    byte_size: int
    format_name: str
    media_type: str
    policy_version: str
    checker_version: str
    findings: tuple[str, ...]
    export_safe: bool
    issued_by_principal_id: str
    issued_at: datetime

    def __post_init__(self) -> None:
        for field in ("receipt_id", "program_id", "job_id", "claim_id"):
            object.__setattr__(self, field, _uuid_text(getattr(self, field), field))
        if not _SHA256.fullmatch(self.raw_sha256):
            raise ValueError("raw_sha256 must be 64 lowercase hexadecimal characters")
        if not isinstance(self.byte_size, int) or isinstance(self.byte_size, bool):
            raise ValueError("byte_size must be an integer")
        if not 1 <= self.byte_size <= M3_MAX_BYTES:
            raise ValueError(f"byte_size must be between 1 and {M3_MAX_BYTES}")
        if self.format_name != M3_FORMAT_NAME:
            raise ValueError(f"format_name must be {M3_FORMAT_NAME!r}")
        if self.media_type != M3_MEDIA_TYPE:
            raise ValueError(f"media_type must be {M3_MEDIA_TYPE!r}")
        if not self.policy_version or not self.checker_version:
            raise ValueError("policy_version and checker_version must be non-empty")
        if not self.issued_by_principal_id:
            raise ValueError("issued_by_principal_id must be non-empty")
        if not isinstance(self.export_safe, bool):
            raise ValueError("export_safe must be boolean")
        findings = tuple(str(item) for item in self.findings)
        if len(set(findings)) != len(findings) or any(not item for item in findings):
            raise ValueError("findings must contain unique non-empty categories")
        object.__setattr__(self, "findings", findings)
        object.__setattr__(self, "issued_at", _utc_datetime(self.issued_at, "issued_at"))

    def to_dict(self) -> dict[str, Any]:
        return _thaw_json({
            "receipt_id": self.receipt_id,
            "program_id": self.program_id,
            "job_id": self.job_id,
            "claim_id": self.claim_id,
            "verified_object_ref": self.verified_object_ref,
            "raw_sha256": self.raw_sha256,
            "byte_size": self.byte_size,
            "format_name": self.format_name,
            "media_type": self.media_type,
            "policy_version": self.policy_version,
            "checker_version": self.checker_version,
            "findings": self.findings,
            "export_safe": self.export_safe,
            "issued_by_principal_id": self.issued_by_principal_id,
            "issued_at": self.issued_at,
        })


@dataclass(frozen=True, slots=True)
class TrustedEvidenceRejection:
    """Deterministic checker rejection supplied only by trusted Ingestion."""

    program_id: str
    job_id: str
    claim_id: str
    rejection_code: str
    policy_version: str
    checker_version: str
    findings: tuple[str, ...]
    issued_by_principal_id: str
    rejected_at: datetime

    def __post_init__(self) -> None:
        for field in ("program_id", "job_id", "claim_id"):
            object.__setattr__(self, field, _uuid_text(getattr(self, field), field))
        if not _REASON.fullmatch(self.rejection_code):
            raise ValueError("rejection_code must be a stable uppercase reason code")
        if not self.policy_version or not self.checker_version:
            raise ValueError("policy_version and checker_version must be non-empty")
        if not self.issued_by_principal_id:
            raise ValueError("issued_by_principal_id must be non-empty")
        findings = tuple(str(item) for item in self.findings)
        if len(set(findings)) != len(findings) or any(not item for item in findings):
            raise ValueError("findings must contain unique non-empty categories")
        object.__setattr__(self, "findings", findings)
        object.__setattr__(self, "rejected_at", _utc_datetime(self.rejected_at, "rejected_at"))

    def to_dict(self) -> dict[str, Any]:
        return _thaw_json({
            "program_id": self.program_id,
            "job_id": self.job_id,
            "claim_id": self.claim_id,
            "rejection_code": self.rejection_code,
            "policy_version": self.policy_version,
            "checker_version": self.checker_version,
            "findings": self.findings,
            "issued_by_principal_id": self.issued_by_principal_id,
            "rejected_at": self.rejected_at,
        })


@dataclass(frozen=True, slots=True)
class M3CommandEnvelope:
    """Internal M3 envelope with wire data separated from trusted facts."""

    command_id: str
    idempotency_key: str
    program_id: str
    command_type: M3CommandType | str
    payload: Mapping[str, Any]
    trusted_principal: TrustedPrincipal
    source_channel: SourceChannel | str
    traceparent: str | None = None
    trusted_outbox_ref: TrustedRequestedOutboxRef | None = None
    trusted_receipt: TrustedEvidenceReceipt | None = None
    trusted_rejection: TrustedEvidenceRejection | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", _uuid_text(self.command_id, "command_id"))
        object.__setattr__(self, "program_id", _uuid_text(self.program_id, "program_id"))
        object.__setattr__(self, "command_type", M3CommandType(self.command_type))
        object.__setattr__(self, "source_channel", SourceChannel(self.source_channel))
        object.__setattr__(self, "payload", _freeze_json(self.payload))

    @classmethod
    def new(cls, **values: Any) -> "M3CommandEnvelope":
        return cls(command_id=str(uuid4()), **values)

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
        if self.traceparent is not None:
            document["traceparent"] = self.traceparent
        if self.trusted_outbox_ref is not None:
            document["trusted_outbox_ref"] = self.trusted_outbox_ref.to_dict()
        if self.trusted_receipt is not None:
            document["trusted_receipt"] = self.trusted_receipt.to_dict()
        if self.trusted_rejection is not None:
            document["trusted_rejection"] = self.trusted_rejection.to_dict()
        return document


def derive_claim_id(job_id: str, outbox_event_id: str) -> str:
    return str(uuid5(_CLAIM_NAMESPACE, f"{_uuid_text(job_id, 'job_id')}|{_uuid_text(outbox_event_id, 'outbox_event_id')}"))


def derive_verified_object_ref(program_id: str, raw_sha256: str) -> str:
    program = _uuid_text(program_id, "program_id")
    if not _SHA256.fullmatch(raw_sha256):
        raise ValueError("raw_sha256 must be 64 lowercase hexadecimal characters")
    return f"{M3_VERIFIED_REF_PREFIX}/programs/{program}/sha256/{raw_sha256}.txt"


def derive_receipt_id(
    *,
    program_id: str,
    job_id: str,
    claim_id: str,
    verified_object_ref: str,
    raw_sha256: str,
    byte_size: int,
    format_name: str,
    policy_version: str,
    checker_version: str,
) -> str:
    material = "|".join(
        (
            _uuid_text(program_id, "program_id"),
            _uuid_text(job_id, "job_id"),
            _uuid_text(claim_id, "claim_id"),
            verified_object_ref,
            raw_sha256,
            str(byte_size),
            format_name,
            policy_version,
            checker_version,
        )
    )
    return str(uuid5(_RECEIPT_NAMESPACE, material))


def derive_evidence_item_id(receipt_id: str) -> str:
    return str(uuid5(_ITEM_NAMESPACE, _uuid_text(receipt_id, "receipt_id")))


def finalize_idempotency_key(receipt_id: str, expected_ingest_job_version: int) -> str:
    if expected_ingest_job_version < 1:
        raise ValueError("expected_ingest_job_version must be positive")
    return f"m3-finalize:{_uuid_text(receipt_id, 'receipt_id')}:job-v{expected_ingest_job_version}"


__all__ = (
    "EvidenceIngestJobStatus",
    "M3_CHECKER_VERSION",
    "M3_FORMAT_NAME",
    "M3_MAX_BYTES",
    "M3_MEDIA_TYPE",
    "M3_POLICY_VERSION",
    "M3_QUERY_TYPES",
    "M3_VERIFIED_REF_PREFIX",
    "M3_WRITE_COMMAND_TYPES",
    "M3CommandEnvelope",
    "M3CommandType",
    "M3QueryType",
    "TrustedEvidenceReceipt",
    "TrustedEvidenceRejection",
    "TrustedRequestedOutboxRef",
    "derive_claim_id",
    "derive_evidence_item_id",
    "derive_receipt_id",
    "derive_verified_object_ref",
    "finalize_idempotency_key",
)
