"""M3 additive State Service facade and contracts."""

from .contracts import (
    EvidenceIngestJobStatus,
    M3_CHECKER_VERSION,
    M3_FORMAT_NAME,
    M3_MAX_BYTES,
    M3_MEDIA_TYPE,
    M3_POLICY_VERSION,
    M3CommandEnvelope,
    M3CommandType,
    M3QueryType,
    TrustedEvidenceReceipt,
    TrustedEvidenceRejection,
    TrustedRequestedOutboxRef,
    derive_claim_id,
    derive_evidence_item_id,
    derive_receipt_id,
    derive_verified_object_ref,
    finalize_idempotency_key,
)
from .database import M3PostgresStateStore, M3StateTransaction
from .service import M3StateServiceFacade
from .validation import (
    build_m3_command_envelope,
    validate_m3_command_envelope,
    validate_m3_query_payload,
    validate_m3_wire_payload,
)

__all__ = (
    "EvidenceIngestJobStatus",
    "M3_CHECKER_VERSION",
    "M3_FORMAT_NAME",
    "M3_MAX_BYTES",
    "M3_MEDIA_TYPE",
    "M3_POLICY_VERSION",
    "M3CommandEnvelope",
    "M3CommandType",
    "M3QueryType",
    "M3PostgresStateStore",
    "M3StateServiceFacade",
    "M3StateTransaction",
    "TrustedEvidenceReceipt",
    "TrustedEvidenceRejection",
    "TrustedRequestedOutboxRef",
    "derive_claim_id",
    "derive_evidence_item_id",
    "derive_receipt_id",
    "derive_verified_object_ref",
    "finalize_idempotency_key",
    "build_m3_command_envelope",
    "validate_m3_command_envelope",
    "validate_m3_query_payload",
    "validate_m3_wire_payload",
)
