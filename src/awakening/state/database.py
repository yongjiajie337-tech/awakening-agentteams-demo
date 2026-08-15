"""PostgreSQL persistence boundary for the M2 authoritative State Service.

The module deliberately accepts and returns plain mappings.  Command/domain
types live above this layer; this file owns only database transactions,
idempotency, per-Program serialization, immutable-history inserts, and the
atomic state/event/outbox commit.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, Final
from uuid import UUID

from psycopg import Connection, OperationalError, connect
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


JsonMapping = Mapping[str, Any]
ConnectionFactory = Callable[..., Connection[dict[str, Any]]]
_UNSET: Final = object()


class StateStoreError(RuntimeError):
    """Base persistence error with a stable machine-readable reason code."""

    reason_code = "STATE_STORE_ERROR"


class DatabaseUnavailableError(StateStoreError):
    reason_code = "STATE_DATABASE_UNAVAILABLE"


class ProgramNotFoundError(StateStoreError):
    reason_code = "PROGRAM_NOT_FOUND"


class StateVersionConflictError(StateStoreError):
    reason_code = "STATE_VERSION_CONFLICT"

    def __init__(self, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"expected state_version {expected}, found {actual}")


class IdempotencyConflictError(StateStoreError):
    reason_code = "IDEMPOTENCY_KEY_REUSED"


class CommandInProgressError(StateStoreError):
    reason_code = "COMMAND_IN_PROGRESS"


class PersistenceInvariantError(StateStoreError):
    reason_code = "PERSISTENCE_INVARIANT_VIOLATION"


def _uuid(value: Any, field: str) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{field} must be a UUID") from exc


def _json(value: Any, field: str) -> Jsonb:
    if not isinstance(value, (dict, list)):
        raise ValueError(f"{field} must be a JSON object or array")
    return Jsonb(value)


def _optional_uuid(value: Any, field: str) -> UUID | None:
    return None if value is None else _uuid(value, field)


class PostgresStateStore:
    """Connection owner for the M2 State Service runtime role.

    The supplied DSN must authenticate as ``awakening_state_service`` (or a
    login explicitly granted that role).  Migration/bootstrap credentials are
    intentionally not accepted separately by this class.
    """

    def __init__(
        self,
        dsn: str,
        *,
        connect_timeout_seconds: int = 5,
        connection_factory: ConnectionFactory = connect,
        connection_options: Mapping[str, Any] | None = None,
    ) -> None:
        if not dsn or not dsn.strip():
            raise ValueError("dsn must not be empty")
        if connect_timeout_seconds < 1:
            raise ValueError("connect_timeout_seconds must be positive")
        self._dsn = dsn
        self._connection_factory = connection_factory
        self._connection_options = dict(connection_options or {})
        self._connection_options.setdefault("connect_timeout", connect_timeout_seconds)
        self._connection_options.setdefault("row_factory", dict_row)
        self._connection_options.setdefault("autocommit", False)

    def _open(self) -> Connection[dict[str, Any]]:
        try:
            return self._connection_factory(self._dsn, **self._connection_options)
        except OperationalError as exc:
            raise DatabaseUnavailableError("PostgreSQL connection failed") from exc

    @contextmanager
    def transaction(self) -> Iterator["StateTransaction"]:
        """Open one all-or-nothing State Service transaction."""

        connection = self._open()
        try:
            with connection:
                with connection.transaction():
                    yield StateTransaction(connection)
        except OperationalError as exc:
            raise DatabaseUnavailableError("PostgreSQL transaction failed") from exc
        finally:
            if not connection.closed:
                connection.close()

    def ping(self) -> None:
        with self.transaction() as transaction:
            transaction.execute_scalar("SELECT 1")

    def get_snapshot(self, program_id: str | UUID) -> dict[str, Any]:
        with self.transaction() as transaction:
            return transaction.get_snapshot(program_id)

    def get_command_receipt(
        self,
        program_id: str | UUID,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        with self.transaction() as transaction:
            return transaction.get_command_receipt(program_id, idempotency_key)

    def get_command_receipt_by_id(
        self,
        program_id: str | UUID,
        command_id: str | UUID,
    ) -> dict[str, Any] | None:
        with self.transaction() as transaction:
            return transaction.get_command_receipt_by_id(program_id, command_id)


class StateTransaction:
    """Methods available only inside ``PostgresStateStore.transaction``."""

    def __init__(self, connection: Connection[dict[str, Any]]) -> None:
        self._connection = connection

    def execute_scalar(self, statement: str, parameters: Sequence[Any] = ()) -> Any:
        with self._connection.cursor() as cursor:
            cursor.execute(statement, parameters)
            row = cursor.fetchone()
        if row is None:
            raise PersistenceInvariantError("scalar query returned no row")
        return next(iter(row.values()))

    def get_command_receipt(
        self,
        program_id: str | UUID,
        idempotency_key: str,
        *,
        for_update: bool = False,
    ) -> dict[str, Any] | None:
        suffix = " FOR UPDATE" if for_update else ""
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT command_id, program_id, idempotency_key, command_type,
                       request_hash, principal_id, source_channel, status,
                       reason_code, result, state_version_before,
                       state_version_after, created_at, updated_at
                FROM business.command_receipts
                WHERE program_id = %s AND idempotency_key = %s
                """ + suffix,
                (_uuid(program_id, "program_id"), idempotency_key),
            )
            row = cursor.fetchone()
        return dict(row) if row is not None else None

    def get_command_receipt_by_id(
        self,
        program_id: str | UUID,
        command_id: str | UUID,
    ) -> dict[str, Any] | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT command_id, program_id, idempotency_key, command_type,
                       request_hash, principal_id, source_channel, status,
                       reason_code, result, state_version_before,
                       state_version_after, created_at, updated_at
                FROM business.command_receipts
                WHERE program_id = %s AND command_id = %s
                """,
                (
                    _uuid(program_id, "program_id"),
                    _uuid(command_id, "command_id"),
                ),
            )
            row = cursor.fetchone()
        return dict(row) if row is not None else None

    def reserve_command_receipt(
        self,
        *,
        command_id: str | UUID,
        program_id: str | UUID,
        idempotency_key: str,
        command_type: str,
        request_hash: str,
        principal_id: str,
        source_channel: str,
        state_version_before: int | None,
    ) -> dict[str, Any] | None:
        """Reserve an idempotency key or return its completed prior result.

        A concurrent insert with the same unique key blocks until the other
        transaction finishes.  Reuse with different authenticated principal,
        channel, command type, or request hash is rejected deterministically.
        """

        program_uuid = _uuid(program_id, "program_id")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO business.command_receipts (
                    command_id, program_id, idempotency_key, command_type,
                    request_hash, principal_id, source_channel,
                    state_version_before
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (program_id, idempotency_key) DO NOTHING
                RETURNING command_id
                """,
                (
                    _uuid(command_id, "command_id"),
                    program_uuid,
                    idempotency_key,
                    command_type,
                    request_hash,
                    principal_id,
                    source_channel,
                    state_version_before,
                ),
            )
            inserted = cursor.fetchone()
        if inserted is not None:
            return None

        existing = self.get_command_receipt(
            program_uuid,
            idempotency_key,
            for_update=True,
        )
        if existing is None:
            raise PersistenceInvariantError("idempotency conflict row disappeared")
        if (
            existing["command_type"] != command_type
            or existing["request_hash"] != request_hash
            or existing["principal_id"] != principal_id
            or existing["source_channel"] != source_channel
        ):
            raise IdempotencyConflictError(
                "idempotency key is already bound to another request"
            )
        if existing["status"] == "processing":
            raise CommandInProgressError("command receipt is still processing")
        return existing

    def lock_program(
        self,
        program_id: str | UUID,
        expected_state_version: int | None = None,
    ) -> dict[str, Any]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT program_id, owner_principal_id, state_version,
                       active_plan_version_id, status, auto_change_policy,
                       created_at, updated_at
                FROM business.programs
                WHERE program_id = %s
                FOR UPDATE
                """,
                (_uuid(program_id, "program_id"),),
            )
            row = cursor.fetchone()
        if row is None:
            raise ProgramNotFoundError(f"program {program_id} does not exist")
        if (
            expected_state_version is not None
            and row["state_version"] != expected_state_version
        ):
            raise StateVersionConflictError(
                expected_state_version,
                row["state_version"],
            )
        return dict(row)

    def get_program_member(
        self,
        program_id: str | UUID,
        principal_id: str,
    ) -> dict[str, Any] | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT program_id, principal_id, principal_type, program_role,
                       created_at
                FROM business.program_members
                WHERE program_id = %s AND principal_id = %s
                """,
                (_uuid(program_id, "program_id"), principal_id),
            )
            row = cursor.fetchone()
        return dict(row) if row is not None else None

    def create_program(self, record: JsonMapping) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO business.programs (
                    program_id, owner_principal_id, state_version,
                    active_plan_version_id, status, auto_change_policy
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    _uuid(record["program_id"], "program_id"),
                    record["owner_principal_id"],
                    record.get("state_version", 0),
                    _optional_uuid(
                        record.get("active_plan_version_id"),
                        "active_plan_version_id",
                    ),
                    record.get("status", "active"),
                    _json(record.get("auto_change_policy", {}), "auto_change_policy"),
                ),
            )

    def add_program_member(self, record: JsonMapping) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO business.program_members (
                    program_id, principal_id, principal_type, program_role
                ) VALUES (%s, %s, %s, %s)
                """,
                (
                    _uuid(record["program_id"], "program_id"),
                    record["principal_id"],
                    record["principal_type"],
                    record["program_role"],
                ),
            )

    def insert_plan_version(self, record: JsonMapping) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO business.plan_versions (
                    plan_version_id, program_id, version_no,
                    previous_plan_version_id, base_state_version,
                    base_plan_version_id, proposal_id, human_decision_id,
                    change_risk, content, content_hash, diff_hash,
                    rolled_back_from, restored_from,
                    created_by_principal_id
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    _uuid(record["plan_version_id"], "plan_version_id"),
                    _uuid(record["program_id"], "program_id"),
                    record["version_no"],
                    _optional_uuid(
                        record.get("previous_plan_version_id"),
                        "previous_plan_version_id",
                    ),
                    record.get("base_state_version"),
                    _optional_uuid(
                        record.get("base_plan_version_id"),
                        "base_plan_version_id",
                    ),
                    _optional_uuid(record.get("proposal_id"), "proposal_id"),
                    _optional_uuid(
                        record.get("human_decision_id"),
                        "human_decision_id",
                    ),
                    record["change_risk"],
                    _json(record["content"], "content"),
                    record["content_hash"],
                    record.get("diff_hash"),
                    _optional_uuid(
                        record.get("rolled_back_from"),
                        "rolled_back_from",
                    ),
                    _optional_uuid(record.get("restored_from"), "restored_from"),
                    record["created_by_principal_id"],
                ),
            )

    def get_plan_version(
        self,
        program_id: str | UUID,
        plan_version_id: str | UUID,
    ) -> dict[str, Any] | None:
        program_uuid = _uuid(program_id, "program_id")
        plan_uuid = _uuid(plan_version_id, "plan_version_id")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM business.plan_versions
                WHERE program_id = %s AND plan_version_id = %s
                """,
                (program_uuid, plan_uuid),
            )
            plan = cursor.fetchone()
            if plan is None:
                return None
            cursor.execute(
                """
                SELECT task_id, program_id, plan_version_id, task_key,
                       position, title, task_payload, created_at
                FROM business.tasks
                WHERE program_id = %s AND plan_version_id = %s
                ORDER BY position, task_key
                """,
                (program_uuid, plan_uuid),
            )
            tasks = [dict(row) for row in cursor.fetchall()]
        return {"plan": dict(plan), "tasks": tasks}

    def insert_tasks(self, records: Sequence[JsonMapping]) -> None:
        if not records:
            raise ValueError("at least one task is required")
        parameters = [
            (
                _uuid(record["task_id"], "task_id"),
                _uuid(record["program_id"], "program_id"),
                _uuid(record["plan_version_id"], "plan_version_id"),
                record["task_key"],
                record["position"],
                record["title"],
                _json(record.get("task_payload", {}), "task_payload"),
            )
            for record in records
        ]
        with self._connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO business.tasks (
                    task_id, program_id, plan_version_id, task_key,
                    position, title, task_payload
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                parameters,
            )

    def insert_proposal(self, record: JsonMapping) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO business.state_change_proposals (
                    proposal_id, program_id, base_state_version,
                    base_plan_version_id, change_type, risk_level, status,
                    change_payload, diff, diff_hash,
                    proposed_by_principal_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    _uuid(record["proposal_id"], "proposal_id"),
                    _uuid(record["program_id"], "program_id"),
                    record["base_state_version"],
                    _uuid(record["base_plan_version_id"], "base_plan_version_id"),
                    record["change_type"],
                    record["risk_level"],
                    record["status"],
                    _json(record["change_payload"], "change_payload"),
                    _json(record["diff"], "diff"),
                    record["diff_hash"],
                    record["proposed_by_principal_id"],
                ),
            )

    def get_proposal(
        self,
        program_id: str | UUID,
        proposal_id: str | UUID,
        *,
        for_update: bool = False,
    ) -> dict[str, Any] | None:
        suffix = " FOR UPDATE" if for_update else ""
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM business.state_change_proposals
                WHERE program_id = %s AND proposal_id = %s
                """ + suffix,
                (
                    _uuid(program_id, "program_id"),
                    _uuid(proposal_id, "proposal_id"),
                ),
            )
            row = cursor.fetchone()
        return dict(row) if row is not None else None

    def update_proposal_status(
        self,
        program_id: str | UUID,
        proposal_id: str | UUID,
        *,
        expected_status: str,
        new_status: str,
    ) -> None:
        self._cas_status(
            "state_change_proposals",
            "proposal_id",
            program_id,
            proposal_id,
            expected_status,
            new_status,
        )

    def insert_approval_request(self, record: JsonMapping) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO business.approval_requests (
                    approval_request_id, program_id, proposal_id,
                    base_state_version, base_plan_version_id, diff_hash,
                    status, expires_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    _uuid(record["approval_request_id"], "approval_request_id"),
                    _uuid(record["program_id"], "program_id"),
                    _uuid(record["proposal_id"], "proposal_id"),
                    record["base_state_version"],
                    _uuid(record["base_plan_version_id"], "base_plan_version_id"),
                    record["diff_hash"],
                    record.get("status", "pending"),
                    record["expires_at"],
                ),
            )

    def get_approval_request(
        self,
        program_id: str | UUID,
        approval_request_id: str | UUID,
        *,
        for_update: bool = False,
    ) -> dict[str, Any] | None:
        suffix = " FOR UPDATE" if for_update else ""
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM business.approval_requests
                WHERE program_id = %s AND approval_request_id = %s
                """ + suffix,
                (
                    _uuid(program_id, "program_id"),
                    _uuid(approval_request_id, "approval_request_id"),
                ),
            )
            row = cursor.fetchone()
        return dict(row) if row is not None else None

    def update_approval_status(
        self,
        program_id: str | UUID,
        approval_request_id: str | UUID,
        *,
        expected_status: str,
        new_status: str,
    ) -> None:
        self._cas_status(
            "approval_requests",
            "approval_request_id",
            program_id,
            approval_request_id,
            expected_status,
            new_status,
        )

    def insert_human_decision(self, record: JsonMapping) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO business.human_decisions (
                    human_decision_id, approval_request_id, program_id,
                    proposal_id, base_state_version, base_plan_version_id,
                    diff_hash, decision, decided_by_principal_id, reason,
                    decided_at, expires_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    COALESCE(%s, clock_timestamp()), %s
                )
                """,
                (
                    _uuid(record["human_decision_id"], "human_decision_id"),
                    _uuid(record["approval_request_id"], "approval_request_id"),
                    _uuid(record["program_id"], "program_id"),
                    _uuid(record["proposal_id"], "proposal_id"),
                    record["base_state_version"],
                    _uuid(record["base_plan_version_id"], "base_plan_version_id"),
                    record["diff_hash"],
                    record["decision"],
                    record["decided_by_principal_id"],
                    record.get("reason"),
                    record.get("decided_at"),
                    record["expires_at"],
                ),
            )

    def get_human_decision(
        self,
        program_id: str | UUID,
        human_decision_id: str | UUID,
        *,
        for_update: bool = False,
    ) -> dict[str, Any] | None:
        suffix = " FOR UPDATE" if for_update else ""
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM business.human_decisions
                WHERE program_id = %s AND human_decision_id = %s
                """ + suffix,
                (
                    _uuid(program_id, "program_id"),
                    _uuid(human_decision_id, "human_decision_id"),
                ),
            )
            row = cursor.fetchone()
        return dict(row) if row is not None else None

    def commit_state_change(
        self,
        *,
        program_id: str | UUID,
        command_id: str | UUID,
        expected_state_version: int,
        result: JsonMapping,
        events: Sequence[JsonMapping],
        outbox_events: Sequence[JsonMapping],
        active_plan_version_id: str | UUID | None | object = _UNSET,
        program_status: str | object = _UNSET,
        auto_change_policy: JsonMapping | object = _UNSET,
        reason_code: str = "OK",
    ) -> int:
        """Commit aggregate state, DomainEvents, OutboxEvents and receipt.

        Domain mutations made earlier through this transaction remain
        uncommitted until the surrounding context exits.  Any failed insert or
        invariant below therefore rolls back the entire command.
        """

        if not events or not outbox_events:
            raise PersistenceInvariantError(
                "a successful state command requires DomainEvent and OutboxEvent"
            )
        if len(events) != len(outbox_events):
            raise PersistenceInvariantError(
                "each M2 DomainEvent must have one OutboxEvent"
            )

        program_uuid = _uuid(program_id, "program_id")
        command_uuid = _uuid(command_id, "command_id")
        assignments = ["state_version = state_version + 1", "updated_at = clock_timestamp()"]
        values: list[Any] = []
        if active_plan_version_id is not _UNSET:
            assignments.append("active_plan_version_id = %s")
            values.append(
                _optional_uuid(active_plan_version_id, "active_plan_version_id")
            )
        if program_status is not _UNSET:
            assignments.append("status = %s")
            values.append(program_status)
        if auto_change_policy is not _UNSET:
            assignments.append("auto_change_policy = %s")
            values.append(_json(auto_change_policy, "auto_change_policy"))
        values.extend((program_uuid, expected_state_version))

        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE business.programs
                SET {', '.join(assignments)}
                WHERE program_id = %s AND state_version = %s
                RETURNING state_version
                """,
                values,
            )
            updated = cursor.fetchone()
        if updated is None:
            current = self.lock_program(program_uuid)
            raise StateVersionConflictError(
                expected_state_version,
                current["state_version"],
            )
        new_state_version = updated["state_version"]

        event_ids: set[UUID] = set()
        for index, event in enumerate(events):
            event_id = _uuid(event["event_id"], "event_id")
            if event_id in event_ids:
                raise PersistenceInvariantError("duplicate DomainEvent ID")
            event_ids.add(event_id)
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO business.domain_events (
                        event_id, program_id, command_id, event_index,
                        event_type, state_version, payload, traceparent
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        event_id,
                        program_uuid,
                        command_uuid,
                        index,
                        event["event_type"],
                        new_state_version,
                        _json(event["payload"], "event.payload"),
                        event.get("traceparent"),
                    ),
                )

        outbox_domain_ids: set[UUID] = set()
        for event in outbox_events:
            domain_event_id = _uuid(event["domain_event_id"], "domain_event_id")
            if domain_event_id not in event_ids:
                raise PersistenceInvariantError(
                    "OutboxEvent references a DomainEvent outside this command"
                )
            if domain_event_id in outbox_domain_ids:
                raise PersistenceInvariantError(
                    "multiple OutboxEvents reference one DomainEvent"
                )
            outbox_domain_ids.add(domain_event_id)
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO business.outbox_events (
                        outbox_event_id, domain_event_id, program_id,
                        aggregate_type, aggregate_id, aggregate_version,
                        topic, payload
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        _uuid(event["outbox_event_id"], "outbox_event_id"),
                        domain_event_id,
                        program_uuid,
                        event.get("aggregate_type", "program"),
                        _uuid(event.get("aggregate_id", program_uuid), "aggregate_id"),
                        event.get("aggregate_version", new_state_version),
                        event["topic"],
                        _json(event["payload"], "outbox.payload"),
                    ),
                )

        if outbox_domain_ids != event_ids:
            raise PersistenceInvariantError(
                "not every DomainEvent has a matching OutboxEvent"
            )

        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE business.command_receipts
                SET status = 'committed', reason_code = %s, result = %s,
                    state_version_after = %s, updated_at = clock_timestamp()
                WHERE command_id = %s AND program_id = %s
                  AND status = 'processing'
                RETURNING command_id
                """,
                (
                    reason_code,
                    _json(dict(result), "command result"),
                    new_state_version,
                    command_uuid,
                    program_uuid,
                ),
            )
            receipt = cursor.fetchone()
        if receipt is None:
            raise PersistenceInvariantError(
                "command receipt was not reserved or was already finalized"
            )
        return new_state_version

    def reject_reserved_command(
        self,
        *,
        program_id: str | UUID,
        command_id: str | UUID,
        reason_code: str,
        result: JsonMapping,
        observed_state_version: int | None,
    ) -> None:
        """Finalize an admitted command rejection without changing Program state."""

        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE business.command_receipts
                SET status = 'rejected', reason_code = %s, result = %s,
                    state_version_after = %s, updated_at = clock_timestamp()
                WHERE command_id = %s AND program_id = %s
                  AND status = 'processing'
                RETURNING command_id
                """,
                (
                    reason_code,
                    _json(dict(result), "command result"),
                    observed_state_version,
                    _uuid(command_id, "command_id"),
                    _uuid(program_id, "program_id"),
                ),
            )
            row = cursor.fetchone()
        if row is None:
            raise PersistenceInvariantError(
                "command receipt was not reserved or was already finalized"
            )

    def get_snapshot(self, program_id: str | UUID) -> dict[str, Any]:
        program_uuid = _uuid(program_id, "program_id")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT program_id, owner_principal_id, state_version,
                       active_plan_version_id, status, auto_change_policy,
                       created_at, updated_at
                FROM business.programs
                WHERE program_id = %s
                """,
                (program_uuid,),
            )
            program = cursor.fetchone()
            if program is None:
                raise ProgramNotFoundError(f"program {program_id} does not exist")
            plan = None
            tasks: list[dict[str, Any]] = []
            if program["active_plan_version_id"] is not None:
                cursor.execute(
                    """
                    SELECT * FROM business.plan_versions
                    WHERE program_id = %s AND plan_version_id = %s
                    """,
                    (program_uuid, program["active_plan_version_id"]),
                )
                plan = cursor.fetchone()
                cursor.execute(
                    """
                    SELECT task_id, program_id, plan_version_id, task_key,
                           position, title, task_payload, created_at
                    FROM business.tasks
                    WHERE program_id = %s AND plan_version_id = %s
                    ORDER BY position, task_key
                    """,
                    (program_uuid, program["active_plan_version_id"]),
                )
                tasks = [dict(row) for row in cursor.fetchall()]
        return {
            "program": dict(program),
            "active_plan": dict(plan) if plan is not None else None,
            "tasks": tasks,
        }

    def _cas_status(
        self,
        table_name: str,
        id_column: str,
        program_id: str | UUID,
        object_id: str | UUID,
        expected_status: str,
        new_status: str,
    ) -> None:
        allowed = {
            ("state_change_proposals", "proposal_id"),
            ("approval_requests", "approval_request_id"),
        }
        if (table_name, id_column) not in allowed:
            raise PersistenceInvariantError("invalid status CAS target")
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE business.{table_name}
                SET status = %s, updated_at = clock_timestamp()
                WHERE program_id = %s AND {id_column} = %s AND status = %s
                RETURNING {id_column}
                """,
                (
                    new_status,
                    _uuid(program_id, "program_id"),
                    _uuid(object_id, id_column),
                    expected_status,
                ),
            )
            row = cursor.fetchone()
        if row is None:
            raise PersistenceInvariantError(
                f"{table_name} status transition did not match current state"
            )


__all__ = [
    "CommandInProgressError",
    "DatabaseUnavailableError",
    "IdempotencyConflictError",
    "PersistenceInvariantError",
    "PostgresStateStore",
    "ProgramNotFoundError",
    "StateStoreError",
    "StateTransaction",
    "StateVersionConflictError",
]
