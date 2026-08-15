"""Additive M3 persistence methods using the frozen M2 State Service role."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from psycopg import OperationalError
from psycopg.types.json import Jsonb

from awakening.state.database import (
    DatabaseUnavailableError,
    PersistenceInvariantError,
    PostgresStateStore,
    StateStoreError,
    StateTransaction,
)


class IngestJobNotFoundError(StateStoreError):
    reason_code = "INGEST_JOB_NOT_FOUND"


class IngestJobVersionConflictError(StateStoreError):
    reason_code = "INGEST_JOB_VERSION_CONFLICT"

    def __init__(self, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"expected ingest_job_version {expected}, found {actual}")


class IngestJobStateConflictError(StateStoreError):
    reason_code = "INGEST_JOB_STATE_CONFLICT"


def _uuid(value: Any, field: str) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{field} must be a UUID") from exc


class M3PostgresStateStore(PostgresStateStore):
    """One store/role for frozen M2 handlers and additive M3 handlers."""

    @contextmanager
    def transaction(self) -> Iterator["M3StateTransaction"]:
        connection = self._open()
        try:
            with connection:
                with connection.transaction():
                    yield M3StateTransaction(connection)
        except OperationalError as exc:
            raise DatabaseUnavailableError("PostgreSQL M3 transaction failed") from exc
        finally:
            if not connection.closed:
                connection.close()


class M3StateTransaction(StateTransaction):
    """M2 transaction superset; all methods use the same psycopg transaction."""

    def insert_ingest_job(self, record: Mapping[str, Any]) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO business.evidence_ingest_jobs (
                    job_id, program_id, source_type, authorization_ref,
                    policy_version, requested_by_principal_id,
                    requested_by_principal_type, status, ingest_job_version
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'created', 1)
                """,
                (
                    _uuid(record["job_id"], "job_id"),
                    _uuid(record["program_id"], "program_id"),
                    record["source_type"],
                    record["authorization_ref"],
                    record["policy_version"],
                    record["requested_by_principal_id"],
                    record["requested_by_principal_type"],
                ),
            )

    def get_ingest_job(
        self,
        program_id: str | UUID,
        job_id: str | UUID,
        *,
        for_update: bool = False,
    ) -> dict[str, Any] | None:
        suffix = " FOR UPDATE" if for_update else ""
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM business.evidence_ingest_jobs
                WHERE program_id = %s AND job_id = %s
                """ + suffix,
                (_uuid(program_id, "program_id"), _uuid(job_id, "job_id")),
            )
            row = cursor.fetchone()
        return dict(row) if row is not None else None

    def get_committed_ingest_request(
        self,
        *,
        program_id: str | UUID,
        outbox_event_id: str | UUID,
        domain_event_id: str | UUID,
        job_id: str | UUID,
    ) -> dict[str, Any] | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT o.outbox_event_id, o.domain_event_id, o.program_id,
                       o.aggregate_version, o.topic, o.payload,
                       d.traceparent, o.created_at
                FROM business.outbox_events AS o
                JOIN business.domain_events AS d
                  ON d.event_id = o.domain_event_id
                 AND d.program_id = o.program_id
                WHERE o.outbox_event_id = %s
                  AND o.domain_event_id = %s
                  AND o.program_id = %s
                  AND o.topic = 'evidence.ingest.requested'
                  AND d.event_type = 'evidence.ingest.requested'
                  AND o.payload ->> 'job_id' = %s
                """,
                (
                    _uuid(outbox_event_id, "outbox_event_id"),
                    _uuid(domain_event_id, "domain_event_id"),
                    _uuid(program_id, "program_id"),
                    str(_uuid(job_id, "job_id")),
                ),
            )
            row = cursor.fetchone()
        return dict(row) if row is not None else None

    def claim_ingest_job(
        self,
        *,
        program_id: str | UUID,
        job_id: str | UUID,
        expected_version: int,
        claim_id: str | UUID,
        claimed_by_principal_id: str,
        requested_outbox_event_id: str | UUID,
    ) -> dict[str, Any]:
        return self._transition_job(
            program_id=program_id,
            job_id=job_id,
            expected_version=expected_version,
            expected_status="created",
            statement="""
                UPDATE business.evidence_ingest_jobs
                SET status = 'claimed', ingest_job_version = ingest_job_version + 1,
                    claim_id = %s, claimed_by_principal_id = %s,
                    requested_outbox_event_id = %s,
                    claimed_at = clock_timestamp(), updated_at = clock_timestamp()
                WHERE program_id = %s AND job_id = %s
                  AND status = 'created' AND ingest_job_version = %s
                RETURNING *
            """,
            parameters=(
                _uuid(claim_id, "claim_id"),
                claimed_by_principal_id,
                _uuid(requested_outbox_event_id, "requested_outbox_event_id"),
            ),
        )

    def reject_ingest_job(
        self,
        *,
        program_id: str | UUID,
        job_id: str | UUID,
        expected_version: int,
        rejection_code: str,
        rejection_context: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self._transition_job(
            program_id=program_id,
            job_id=job_id,
            expected_version=expected_version,
            expected_status="claimed",
            statement="""
                UPDATE business.evidence_ingest_jobs
                SET status = 'rejected', ingest_job_version = ingest_job_version + 1,
                    rejection_code = %s, rejection_context = %s,
                    completed_at = clock_timestamp(), updated_at = clock_timestamp()
                WHERE program_id = %s AND job_id = %s
                  AND status = 'claimed' AND ingest_job_version = %s
                RETURNING *
            """,
            parameters=(rejection_code, Jsonb(dict(rejection_context))),
        )

    def finalize_ingest_job(
        self,
        *,
        program_id: str | UUID,
        job_id: str | UUID,
        expected_version: int,
        receipt_id: str | UUID,
        evidence_item_id: str | UUID,
    ) -> dict[str, Any]:
        return self._transition_job(
            program_id=program_id,
            job_id=job_id,
            expected_version=expected_version,
            expected_status="claimed",
            statement="""
                UPDATE business.evidence_ingest_jobs
                SET status = 'finalized', ingest_job_version = ingest_job_version + 1,
                    receipt_id = %s, evidence_item_id = %s,
                    completed_at = clock_timestamp(), updated_at = clock_timestamp()
                WHERE program_id = %s AND job_id = %s
                  AND status = 'claimed' AND ingest_job_version = %s
                RETURNING *
            """,
            parameters=(
                _uuid(receipt_id, "receipt_id"),
                _uuid(evidence_item_id, "evidence_item_id"),
            ),
        )

    def get_receipt_by_job(
        self,
        program_id: str | UUID,
        job_id: str | UUID,
    ) -> dict[str, Any] | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM business.evidence_ingestion_receipts
                WHERE program_id = %s AND job_id = %s
                """,
                (_uuid(program_id, "program_id"), _uuid(job_id, "job_id")),
            )
            row = cursor.fetchone()
        return dict(row) if row is not None else None

    def get_evidence_item_by_receipt(
        self,
        program_id: str | UUID,
        receipt_id: str | UUID,
    ) -> dict[str, Any] | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM business.evidence_items
                WHERE program_id = %s AND receipt_id = %s
                """,
                (_uuid(program_id, "program_id"), _uuid(receipt_id, "receipt_id")),
            )
            row = cursor.fetchone()
        return dict(row) if row is not None else None

    def register_receipt_and_item_if_absent(
        self,
        *,
        receipt: Mapping[str, Any],
        item: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], bool]:
        existing_receipt = self.get_receipt_by_job(receipt["program_id"], receipt["job_id"])
        if existing_receipt is not None:
            self._assert_receipt_matches(existing_receipt, receipt)
            existing_item = self.get_evidence_item_by_receipt(
                receipt["program_id"], receipt["receipt_id"]
            )
            if existing_item is None:
                raise PersistenceInvariantError("Receipt exists without its EvidenceItem")
            self._assert_item_matches(existing_item, item)
            return existing_receipt, existing_item, False

        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO business.evidence_ingestion_receipts (
                    receipt_id, program_id, job_id, claim_id,
                    verified_object_ref, raw_sha256, byte_size, format_name,
                    media_type, policy_version, checker_version, findings,
                    export_safe, issued_by_principal_id, issued_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s
                ) RETURNING *
                """,
                (
                    _uuid(receipt["receipt_id"], "receipt_id"),
                    _uuid(receipt["program_id"], "program_id"),
                    _uuid(receipt["job_id"], "job_id"),
                    _uuid(receipt["claim_id"], "claim_id"),
                    receipt["verified_object_ref"],
                    receipt["raw_sha256"],
                    receipt["byte_size"],
                    receipt["format_name"],
                    receipt["media_type"],
                    receipt["policy_version"],
                    receipt["checker_version"],
                    Jsonb(list(receipt["findings"])),
                    receipt["export_safe"],
                    receipt["issued_by_principal_id"],
                    receipt["issued_at"],
                ),
            )
            receipt_row = dict(cursor.fetchone())
            cursor.execute(
                """
                INSERT INTO business.evidence_items (
                    evidence_item_id, program_id, receipt_id, job_id, claim_id,
                    verified_object_ref, raw_sha256, byte_size, format_name,
                    media_type, policy_version, checker_version, findings,
                    source_type, authorization_ref, created_by_principal_id
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s
                ) RETURNING *
                """,
                (
                    _uuid(item["evidence_item_id"], "evidence_item_id"),
                    _uuid(item["program_id"], "program_id"),
                    _uuid(item["receipt_id"], "receipt_id"),
                    _uuid(item["job_id"], "job_id"),
                    _uuid(item["claim_id"], "claim_id"),
                    item["verified_object_ref"],
                    item["raw_sha256"],
                    item["byte_size"],
                    item["format_name"],
                    item["media_type"],
                    item["policy_version"],
                    item["checker_version"],
                    Jsonb(list(item["findings"])),
                    item["source_type"],
                    item["authorization_ref"],
                    item["created_by_principal_id"],
                ),
            )
            item_row = dict(cursor.fetchone())
        return receipt_row, item_row, True

    def get_ingest_job_projection(
        self,
        program_id: str | UUID,
        job_id: str | UUID,
    ) -> dict[str, Any] | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT j.job_id, j.program_id, j.source_type, j.policy_version,
                       j.status, j.ingest_job_version, j.claim_id,
                       j.claimed_by_principal_id, j.claimed_at,
                       j.receipt_id, j.evidence_item_id, j.rejection_code,
                       r.raw_sha256 AS server_sha256, r.byte_size,
                       r.format_name, r.media_type, r.checker_version,
                       r.findings, r.export_safe,
                       j.created_at, j.updated_at, j.completed_at
                FROM business.evidence_ingest_jobs AS j
                LEFT JOIN business.evidence_ingestion_receipts AS r
                  ON r.program_id = j.program_id AND r.receipt_id = j.receipt_id
                WHERE j.program_id = %s AND j.job_id = %s
                """,
                (_uuid(program_id, "program_id"), _uuid(job_id, "job_id")),
            )
            row = cursor.fetchone()
        return dict(row) if row is not None else None

    def _transition_job(
        self,
        *,
        program_id: str | UUID,
        job_id: str | UUID,
        expected_version: int,
        expected_status: str,
        statement: str,
        parameters: tuple[Any, ...],
    ) -> dict[str, Any]:
        program_uuid = _uuid(program_id, "program_id")
        job_uuid = _uuid(job_id, "job_id")
        with self._connection.cursor() as cursor:
            cursor.execute(
                statement,
                (*parameters, program_uuid, job_uuid, expected_version),
            )
            row = cursor.fetchone()
        if row is not None:
            return dict(row)
        current = self.get_ingest_job(program_uuid, job_uuid, for_update=True)
        if current is None:
            raise IngestJobNotFoundError(f"ingest job {job_id} does not exist")
        if int(current["ingest_job_version"]) != expected_version:
            raise IngestJobVersionConflictError(
                expected_version,
                int(current["ingest_job_version"]),
            )
        raise IngestJobStateConflictError(
            f"expected ingest job status {expected_status}, found {current['status']}"
        )

    @staticmethod
    def _assert_receipt_matches(existing: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
        fields = (
            "receipt_id", "program_id", "job_id", "claim_id",
            "verified_object_ref", "raw_sha256", "byte_size", "format_name",
            "media_type", "policy_version", "checker_version", "findings",
            "export_safe", "issued_by_principal_id",
        )
        if any(_normal(existing[field]) != _normal(expected[field]) for field in fields):
            raise PersistenceInvariantError("existing Receipt does not match trusted Receipt")

    @staticmethod
    def _assert_item_matches(existing: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
        fields = (
            "evidence_item_id", "program_id", "receipt_id", "job_id", "claim_id",
            "verified_object_ref", "raw_sha256", "byte_size", "format_name",
            "media_type", "policy_version", "checker_version", "findings",
            "source_type", "authorization_ref", "created_by_principal_id",
        )
        if any(_normal(existing[field]) != _normal(expected[field]) for field in fields):
            raise PersistenceInvariantError("existing EvidenceItem does not match trusted Receipt")


def _normal(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, list):
        return tuple(value)
    return value


__all__ = (
    "IngestJobNotFoundError",
    "IngestJobStateConflictError",
    "IngestJobVersionConflictError",
    "M3PostgresStateStore",
    "M3StateTransaction",
)
