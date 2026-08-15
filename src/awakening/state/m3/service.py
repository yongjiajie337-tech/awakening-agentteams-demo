"""Single M2+M3 authoritative State Service facade."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from typing import Any
from uuid import UUID, uuid4

from psycopg import Error as PsycopgError, IntegrityError

from awakening.state.contracts import (
    CommandEnvelope,
    CommandResult,
    CommandStatus,
    PrincipalType,
    QueryType,
    ReasonCode,
    SourceChannel,
    TrustedPrincipal,
)
from awakening.state.database import (
    CommandInProgressError,
    DatabaseUnavailableError,
    IdempotencyConflictError,
    PersistenceInvariantError,
    ProgramNotFoundError,
    StateVersionConflictError,
)
from awakening.state.service import BootstrapMember, BusinessRuleError, StateService
from awakening.state.validation import ContractValidationError, canonical_json_bytes

from .contracts import (
    M3_CHECKER_VERSION,
    M3_FORMAT_NAME,
    M3_MEDIA_TYPE,
    M3_POLICY_VERSION,
    M3CommandEnvelope,
    M3CommandType,
    M3QueryType,
    TrustedEvidenceReceipt,
    TrustedEvidenceRejection,
    derive_claim_id,
    derive_evidence_item_id,
    derive_receipt_id,
    derive_verified_object_ref,
)
from .database import (
    IngestJobNotFoundError,
    IngestJobStateConflictError,
    IngestJobVersionConflictError,
    M3PostgresStateStore,
    M3StateTransaction,
)
from .validation import (
    m3_request_hash,
    validate_m3_command_envelope,
    validate_m3_query_payload,
)


_JOB_REJECTION_CODES = frozenset(
    {
        "FORMAT_NOT_ALLOWED",
        "SIZE_LIMIT_EXCEEDED",
        "UTF8_INVALID",
        "BOM_FORBIDDEN",
        "NUL_FORBIDDEN",
        "CONTROL_CHARACTER_FORBIDDEN",
        "SECRET_PATTERN_DETECTED",
        "DIRECT_PII_PATTERN_DETECTED",
        "CHECKER_ERROR",
        "VERIFIED_OBJECT_CONFLICT",
    }
)


@dataclass(frozen=True, slots=True)
class _M3Mutation:
    event_type: str
    result: Mapping[str, Any]


class M3StateServiceFacade:
    """One facade and one State Service database role for M2 plus M3."""

    def __init__(
        self,
        store: M3PostgresStateStore,
        *,
        bootstrap_members: tuple[BootstrapMember, ...] = (),
        approval_ttl: timedelta = timedelta(minutes=30),
        policy_version: str = M3_POLICY_VERSION,
        checker_version: str = M3_CHECKER_VERSION,
    ) -> None:
        if policy_version != M3_POLICY_VERSION:
            raise ValueError(f"M3 policy_version must be {M3_POLICY_VERSION!r}")
        if checker_version != M3_CHECKER_VERSION:
            raise ValueError(f"M3 checker_version must be {M3_CHECKER_VERSION!r}")
        self._store = store
        self._m2 = StateService(
            store,
            bootstrap_members=bootstrap_members,
            approval_ttl=approval_ttl,
        )
        self._policy_version = policy_version
        self._checker_version = checker_version
        self._m3_handlers: dict[
            M3CommandType,
            Callable[[M3CommandEnvelope], CommandResult],
        ] = {
            M3CommandType.INGEST_JOB_CREATE: self._create_ingest_job,
            M3CommandType.INGEST_JOB_CLAIM: self._claim_ingest_job,
            M3CommandType.INGEST_JOB_FINALIZE: self._finalize_ingest_job,
            M3CommandType.INGEST_JOB_REJECT: self._reject_ingest_job,
        }

    @property
    def registered_commands(self) -> frozenset[str]:
        return self._m2.registered_commands | frozenset(
            command.value for command in self._m3_handlers
        )

    def dispatch(self, envelope: CommandEnvelope | M3CommandEnvelope) -> CommandResult:
        """Delegate frozen M2 envelopes unchanged; execute only typed M3 envelopes here."""

        if isinstance(envelope, CommandEnvelope):
            return self._m2.dispatch(envelope)
        if not isinstance(envelope, M3CommandEnvelope):
            return self._rejected(
                str(uuid4()),
                ReasonCode.INVALID_ENVELOPE,
                "unsupported command envelope type",
            )
        try:
            validate_m3_command_envelope(envelope)
            self._assert_m3_entrypoint(envelope)
            handler = self._m3_handlers.get(envelope.command_type)
            if handler is None:
                raise BusinessRuleError(
                    ReasonCode.COMMAND_NOT_REGISTERED,
                    "command is not registered in M3",
                )
            return handler(envelope)
        except ContractValidationError as exc:
            return self._rejected(envelope.command_id, exc.reason_code, str(exc))
        except BusinessRuleError as exc:
            return self._rejected(
                envelope.command_id,
                exc.reason_code,
                exc.message,
                details=exc.details,
            )
        except IngestJobVersionConflictError as exc:
            return self._rejected(
                envelope.command_id,
                ReasonCode.VERSION_CONFLICT,
                str(exc),
                details={"expected": exc.expected, "actual": exc.actual, "subject": "ingest_job"},
            )
        except IngestJobNotFoundError as exc:
            return self._rejected(envelope.command_id, ReasonCode.NOT_FOUND, str(exc))
        except IngestJobStateConflictError as exc:
            return self._rejected(
                envelope.command_id,
                ReasonCode.STATE_TRANSITION_INVALID,
                str(exc),
            )
        except StateVersionConflictError as exc:
            return self._rejected(
                envelope.command_id,
                ReasonCode.VERSION_CONFLICT,
                str(exc),
                details={"expected": exc.expected, "actual": exc.actual, "subject": "program"},
            )
        except ProgramNotFoundError as exc:
            return self._rejected(envelope.command_id, ReasonCode.NOT_FOUND, str(exc))
        except IdempotencyConflictError as exc:
            return self._rejected(envelope.command_id, ReasonCode.IDEMPOTENCY_KEY_REUSED, str(exc))
        except CommandInProgressError as exc:
            return self._rejected(envelope.command_id, ReasonCode.CONFLICT, str(exc))
        except DatabaseUnavailableError as exc:
            return self._rejected(envelope.command_id, ReasonCode.DATABASE_UNAVAILABLE, str(exc))
        except IntegrityError as exc:
            return self._rejected(
                envelope.command_id,
                ReasonCode.CONFLICT,
                "database constraint rejected the M3 command",
                details={"sqlstate": exc.sqlstate},
            )
        except PersistenceInvariantError as exc:
            return self._rejected(envelope.command_id, ReasonCode.TRANSACTION_ABORTED, str(exc))
        except PsycopgError:
            return self._rejected(
                envelope.command_id,
                ReasonCode.TRANSACTION_ABORTED,
                "PostgreSQL aborted the authoritative M3 transaction",
            )

    def query(
        self,
        *,
        query_type: QueryType | M3QueryType | str,
        program_id: str,
        payload: Mapping[str, Any],
        trusted_principal: TrustedPrincipal,
    ) -> dict[str, Any]:
        try:
            m3_query = M3QueryType(query_type)
        except ValueError:
            return self._m2.query(
                query_type=query_type,
                program_id=program_id,
                payload=payload,
                trusted_principal=trusted_principal,
            )

        validate_m3_query_payload(m3_query, payload)
        self._m2._assert_program_scope(program_id, trusted_principal)
        with self._store.transaction() as transaction:
            transaction.get_snapshot(program_id)
            self._m2._require_member(transaction, program_id, trusted_principal)
            result = transaction.get_ingest_job_projection(program_id, payload["job_id"])
            if result is None:
                raise BusinessRuleError(ReasonCode.NOT_FOUND, "ingest job not found")
        return _jsonable(result)

    def _create_ingest_job(self, envelope: M3CommandEnvelope) -> CommandResult:
        def operation(
            transaction: M3StateTransaction,
            _program: Mapping[str, Any],
        ) -> _M3Mutation:
            payload = _plain_json(envelope.payload)
            job_id = str(uuid4())
            transaction.insert_ingest_job(
                {
                    "job_id": job_id,
                    "program_id": envelope.program_id,
                    "source_type": payload["source_type"],
                    "authorization_ref": payload["authorization_ref"],
                    "policy_version": self._policy_version,
                    "requested_by_principal_id": envelope.trusted_principal.principal_id,
                    "requested_by_principal_type": envelope.trusted_principal.principal_type.value,
                }
            )
            result = {
                "job_id": job_id,
                "status": "created",
                "ingest_job_version": 1,
                "source_type": "upload",
                "policy_version": self._policy_version,
            }
            return _M3Mutation("evidence.ingest.requested", result)

        return self._run_m3_program_command(envelope, operation)

    def _claim_ingest_job(self, envelope: M3CommandEnvelope) -> CommandResult:
        def operation(
            transaction: M3StateTransaction,
            _program: Mapping[str, Any],
        ) -> _M3Mutation:
            payload = _plain_json(envelope.payload)
            trusted_ref = envelope.trusted_outbox_ref
            if trusted_ref is None:
                raise BusinessRuleError(ReasonCode.INVALID_ENVELOPE, "trusted Outbox reference is required")
            committed = transaction.get_committed_ingest_request(
                program_id=envelope.program_id,
                outbox_event_id=trusted_ref.outbox_event_id,
                domain_event_id=trusted_ref.domain_event_id,
                job_id=payload["job_id"],
            )
            if committed is None:
                raise BusinessRuleError(
                    ReasonCode.STATE_TRANSITION_INVALID,
                    "claim is not bound to a committed evidence.ingest.requested Outbox event",
                )
            if int(committed["payload"].get("ingest_job_version", -1)) != int(
                payload["expected_ingest_job_version"]
            ):
                raise BusinessRuleError(
                    ReasonCode.VERSION_CONFLICT,
                    "Outbox job version does not match the claim request",
                )
            claim_id = derive_claim_id(payload["job_id"], trusted_ref.outbox_event_id)
            job = transaction.claim_ingest_job(
                program_id=envelope.program_id,
                job_id=payload["job_id"],
                expected_version=payload["expected_ingest_job_version"],
                claim_id=claim_id,
                claimed_by_principal_id=envelope.trusted_principal.principal_id,
                requested_outbox_event_id=trusted_ref.outbox_event_id,
            )
            result = {
                "program_id": envelope.program_id,
                "job_id": _uuid_text(job["job_id"]),
                "claim_id": claim_id,
                "status": "claimed",
                "ingest_job_version": int(job["ingest_job_version"]),
                "policy_version": job["policy_version"],
                "source_type": job["source_type"],
                "claimed_by_principal_id": job["claimed_by_principal_id"],
                "claimed_at": _jsonable(job["claimed_at"]),
            }
            return _M3Mutation("evidence.ingest_job.claimed", result)

        return self._run_m3_program_command(envelope, operation)

    def _reject_ingest_job(self, envelope: M3CommandEnvelope) -> CommandResult:
        def operation(
            transaction: M3StateTransaction,
            _program: Mapping[str, Any],
        ) -> _M3Mutation:
            payload = _plain_json(envelope.payload)
            rejection = envelope.trusted_rejection
            if rejection is None:
                raise BusinessRuleError(ReasonCode.INVALID_ENVELOPE, "trusted rejection is required")
            job = self._require_claimed_job(transaction, envelope, payload)
            self._validate_rejection(envelope, job, rejection)
            updated = transaction.reject_ingest_job(
                program_id=envelope.program_id,
                job_id=payload["job_id"],
                expected_version=payload["expected_ingest_job_version"],
                rejection_code=rejection.rejection_code,
                rejection_context={
                    "policy_version": rejection.policy_version,
                    "checker_version": rejection.checker_version,
                    "findings": list(rejection.findings),
                    "issued_by_principal_id": rejection.issued_by_principal_id,
                    "rejected_at": rejection.rejected_at.isoformat().replace("+00:00", "Z"),
                },
            )
            result = {
                "job_id": _uuid_text(updated["job_id"]),
                "status": "rejected",
                "ingest_job_version": int(updated["ingest_job_version"]),
                "rejection_code": rejection.rejection_code,
            }
            return _M3Mutation("evidence.ingest_job.rejected", result)

        return self._run_m3_program_command(envelope, operation)

    def _finalize_ingest_job(self, envelope: M3CommandEnvelope) -> CommandResult:
        def operation(
            transaction: M3StateTransaction,
            _program: Mapping[str, Any],
        ) -> _M3Mutation:
            payload = _plain_json(envelope.payload)
            receipt = envelope.trusted_receipt
            if receipt is None:
                raise BusinessRuleError(ReasonCode.INVALID_ENVELOPE, "trusted Receipt is required")
            job = self._require_claimed_job(transaction, envelope, payload)
            self._validate_receipt(envelope, job, receipt)
            item_id = derive_evidence_item_id(receipt.receipt_id)
            receipt_record = receipt.to_dict()
            receipt_record["issued_at"] = receipt.issued_at
            item_record = {
                "evidence_item_id": item_id,
                "program_id": receipt.program_id,
                "receipt_id": receipt.receipt_id,
                "job_id": receipt.job_id,
                "claim_id": receipt.claim_id,
                "verified_object_ref": receipt.verified_object_ref,
                "raw_sha256": receipt.raw_sha256,
                "byte_size": receipt.byte_size,
                "format_name": receipt.format_name,
                "media_type": receipt.media_type,
                "policy_version": receipt.policy_version,
                "checker_version": receipt.checker_version,
                "findings": list(receipt.findings),
                "source_type": job["source_type"],
                "authorization_ref": job["authorization_ref"],
                "created_by_principal_id": envelope.trusted_principal.principal_id,
            }
            _, _, created = transaction.register_receipt_and_item_if_absent(
                receipt=receipt_record,
                item=item_record,
            )
            updated = transaction.finalize_ingest_job(
                program_id=envelope.program_id,
                job_id=payload["job_id"],
                expected_version=payload["expected_ingest_job_version"],
                receipt_id=receipt.receipt_id,
                evidence_item_id=item_id,
            )
            result = {
                "job_id": _uuid_text(updated["job_id"]),
                "status": "finalized",
                "ingest_job_version": int(updated["ingest_job_version"]),
                "receipt_id": receipt.receipt_id,
                "evidence_item_id": item_id,
                "server_sha256": receipt.raw_sha256,
                "byte_size": receipt.byte_size,
                "created": created,
            }
            return _M3Mutation("evidence.item.created", result)

        return self._run_m3_program_command(envelope, operation)

    def _run_m3_program_command(
        self,
        envelope: M3CommandEnvelope,
        operation: Callable[[M3StateTransaction, Mapping[str, Any]], _M3Mutation],
    ) -> CommandResult:
        request_hash = m3_request_hash(envelope)
        with self._store.transaction() as transaction:
            self._m2._assert_program_scope(envelope.program_id, envelope.trusted_principal)
            member = self._m2._require_member(
                transaction,
                envelope.program_id,
                envelope.trusted_principal,
            )
            self._assert_m3_role(envelope, member)
            program = transaction.lock_program(envelope.program_id)
            replay = transaction.reserve_command_receipt(
                command_id=envelope.command_id,
                program_id=envelope.program_id,
                idempotency_key=envelope.idempotency_key,
                command_type=envelope.command_type.value,
                request_hash=request_hash,
                principal_id=envelope.trusted_principal.principal_id,
                source_channel=envelope.source_channel.value,
                state_version_before=int(program["state_version"]),
            )
            if replay is not None:
                return self._m2._receipt_result(replay)
            try:
                mutation = operation(transaction, program)
                event, outbox = self._m2._event_pair(
                    envelope,
                    mutation.event_type,
                    mutation.result,
                )
                state_version = transaction.commit_state_change(
                    program_id=envelope.program_id,
                    command_id=envelope.command_id,
                    expected_state_version=int(program["state_version"]),
                    result=mutation.result,
                    events=[event],
                    outbox_events=[outbox],
                )
                return self._m2._committed(
                    envelope.command_id,
                    state_version,
                    mutation.result,
                )
            except IngestJobVersionConflictError as exc:
                details = {"expected": exc.expected, "actual": exc.actual, "subject": "ingest_job"}
                transaction.reject_reserved_command(
                    program_id=envelope.program_id,
                    command_id=envelope.command_id,
                    reason_code=ReasonCode.VERSION_CONFLICT.value,
                    result={"message": str(exc), **details},
                    observed_state_version=int(program["state_version"]),
                )
                return self._rejected(
                    envelope.command_id,
                    ReasonCode.VERSION_CONFLICT,
                    str(exc),
                    state_version=int(program["state_version"]),
                    details=details,
                )
            except IngestJobNotFoundError as exc:
                return self._persist_business_rejection(
                    transaction, envelope, program, ReasonCode.NOT_FOUND, str(exc)
                )
            except IngestJobStateConflictError as exc:
                return self._persist_business_rejection(
                    transaction,
                    envelope,
                    program,
                    ReasonCode.STATE_TRANSITION_INVALID,
                    str(exc),
                )
            except BusinessRuleError as exc:
                return self._persist_business_rejection(
                    transaction,
                    envelope,
                    program,
                    exc.reason_code,
                    exc.message,
                    details=exc.details,
                )

    def _persist_business_rejection(
        self,
        transaction: M3StateTransaction,
        envelope: M3CommandEnvelope,
        program: Mapping[str, Any],
        reason_code: ReasonCode,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> CommandResult:
        result = {"message": message, **dict(details or {})}
        transaction.reject_reserved_command(
            program_id=envelope.program_id,
            command_id=envelope.command_id,
            reason_code=reason_code.value,
            result=result,
            observed_state_version=int(program["state_version"]),
        )
        return self._rejected(
            envelope.command_id,
            reason_code,
            message,
            state_version=int(program["state_version"]),
            details=details,
        )

    @staticmethod
    def _assert_m3_entrypoint(envelope: M3CommandEnvelope) -> None:
        if envelope.command_type is M3CommandType.INGEST_JOB_CREATE:
            allowed = (
                envelope.source_channel is SourceChannel.WEB
                and envelope.trusted_principal.principal_type is PrincipalType.USER
            ) or (
                envelope.source_channel is SourceChannel.MCP
                and envelope.trusted_principal.principal_type is PrincipalType.AGENT
            )
        else:
            allowed = (
                envelope.source_channel is SourceChannel.INTERNAL
                and envelope.trusted_principal.principal_type is PrincipalType.SERVICE
            )
        if not allowed:
            raise BusinessRuleError(
                ReasonCode.PRINCIPAL_NOT_ALLOWED,
                "principal type is not allowed on this M3 trusted adapter channel",
            )

    @staticmethod
    def _assert_m3_role(envelope: M3CommandEnvelope, member: Mapping[str, Any]) -> None:
        if (
            envelope.command_type is not M3CommandType.INGEST_JOB_CREATE
            and member["program_role"] != "service"
        ):
            raise BusinessRuleError(
                ReasonCode.PRINCIPAL_NOT_ALLOWED,
                "M3 internal job transitions require persistent Program role service",
            )

    @staticmethod
    def _require_claimed_job(
        transaction: M3StateTransaction,
        envelope: M3CommandEnvelope,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        job = transaction.get_ingest_job(envelope.program_id, payload["job_id"], for_update=True)
        if job is None:
            raise IngestJobNotFoundError(f"ingest job {payload['job_id']} does not exist")
        expected = int(payload["expected_ingest_job_version"])
        actual = int(job["ingest_job_version"])
        if actual != expected:
            raise IngestJobVersionConflictError(expected, actual)
        if job["status"] != "claimed":
            raise IngestJobStateConflictError(
                f"expected ingest job status claimed, found {job['status']}"
            )
        return job

    def _validate_receipt(
        self,
        envelope: M3CommandEnvelope,
        job: Mapping[str, Any],
        receipt: TrustedEvidenceReceipt,
    ) -> None:
        if receipt.program_id != envelope.program_id or receipt.job_id != _uuid_text(job["job_id"]):
            raise BusinessRuleError(ReasonCode.PROGRAM_SCOPE_DENIED, "Receipt is outside the job/Program binding")
        if receipt.claim_id != _uuid_text(job["claim_id"]):
            raise BusinessRuleError(ReasonCode.STATE_TRANSITION_INVALID, "Receipt claim binding is invalid")
        if receipt.issued_by_principal_id != envelope.trusted_principal.principal_id:
            raise BusinessRuleError(ReasonCode.PRINCIPAL_NOT_ALLOWED, "Receipt issuer is not the trusted caller")
        if receipt.policy_version != job["policy_version"] or receipt.policy_version != self._policy_version:
            raise BusinessRuleError(ReasonCode.STATE_TRANSITION_INVALID, "Receipt policy version is invalid")
        if receipt.checker_version != self._checker_version:
            raise BusinessRuleError(ReasonCode.STATE_TRANSITION_INVALID, "Receipt checker version is invalid")
        if receipt.format_name != M3_FORMAT_NAME or receipt.media_type != M3_MEDIA_TYPE:
            raise BusinessRuleError(ReasonCode.STATE_TRANSITION_INVALID, "Receipt format binding is invalid")
        if not receipt.export_safe:
            raise BusinessRuleError(ReasonCode.STATE_TRANSITION_INVALID, "Receipt is not export_safe")
        expected_ref = derive_verified_object_ref(receipt.program_id, receipt.raw_sha256)
        if receipt.verified_object_ref != expected_ref:
            raise BusinessRuleError(ReasonCode.STATE_TRANSITION_INVALID, "verified object ref is not server-derived")
        expected_receipt_id = derive_receipt_id(
            program_id=receipt.program_id,
            job_id=receipt.job_id,
            claim_id=receipt.claim_id,
            verified_object_ref=receipt.verified_object_ref,
            raw_sha256=receipt.raw_sha256,
            byte_size=receipt.byte_size,
            format_name=receipt.format_name,
            policy_version=receipt.policy_version,
            checker_version=receipt.checker_version,
        )
        if receipt.receipt_id != expected_receipt_id:
            raise BusinessRuleError(ReasonCode.STATE_TRANSITION_INVALID, "Receipt ID is not server-derived")

    def _validate_rejection(
        self,
        envelope: M3CommandEnvelope,
        job: Mapping[str, Any],
        rejection: TrustedEvidenceRejection,
    ) -> None:
        if rejection.program_id != envelope.program_id or rejection.job_id != _uuid_text(job["job_id"]):
            raise BusinessRuleError(ReasonCode.PROGRAM_SCOPE_DENIED, "rejection is outside the job/Program binding")
        if rejection.claim_id != _uuid_text(job["claim_id"]):
            raise BusinessRuleError(ReasonCode.STATE_TRANSITION_INVALID, "rejection claim binding is invalid")
        if rejection.issued_by_principal_id != envelope.trusted_principal.principal_id:
            raise BusinessRuleError(ReasonCode.PRINCIPAL_NOT_ALLOWED, "rejection issuer is not the trusted caller")
        if rejection.policy_version != job["policy_version"] or rejection.policy_version != self._policy_version:
            raise BusinessRuleError(ReasonCode.STATE_TRANSITION_INVALID, "rejection policy version is invalid")
        if rejection.checker_version != self._checker_version:
            raise BusinessRuleError(ReasonCode.STATE_TRANSITION_INVALID, "rejection checker version is invalid")
        if rejection.rejection_code not in _JOB_REJECTION_CODES:
            raise BusinessRuleError(ReasonCode.STATE_TRANSITION_INVALID, "rejection code is not registered")

    @staticmethod
    def _rejected(
        command_id: str,
        reason_code: ReasonCode,
        message: str,
        *,
        state_version: int | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> CommandResult:
        return CommandResult(
            command_id=command_id,
            status=CommandStatus.REJECTED,
            reason_code=reason_code,
            state_version=state_version,
            result={"message": message, **dict(details or {})},
        )


def _plain_json(value: Any) -> Any:
    return json.loads(canonical_json_bytes(value).decode("utf-8"))


def _uuid_text(value: Any) -> str:
    return str(value) if isinstance(value, UUID) else str(value)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return value


__all__ = ("M3StateServiceFacade",)
