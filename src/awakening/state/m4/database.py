"""Additive M4 persistence on the accepted M3 State Service store."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from psycopg import OperationalError
from psycopg.types.json import Jsonb

from awakening.state.database import (
    DatabaseUnavailableError,
    PersistenceInvariantError,
    StateStoreError,
)
from awakening.state.m3.database import M3PostgresStateStore, M3StateTransaction


class RuntimeConfigNotFoundError(StateStoreError):
    reason_code = "M4_RUNTIME_CONFIG_NOT_FOUND"


class BudgetReservationNotFoundError(StateStoreError):
    reason_code = "M4_RESERVATION_NOT_FOUND"


class BudgetReservationStateError(StateStoreError):
    reason_code = "M4_RESERVATION_STATE_INVALID"


def _uuid(value: Any, field: str) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{field} must be a UUID") from exc


class M4PostgresStateStore(M3PostgresStateStore):
    """One database role and transaction boundary for M2, M3 and M4."""

    @contextmanager
    def transaction(self) -> Iterator["M4StateTransaction"]:
        connection = self._open()
        try:
            with connection:
                with connection.transaction():
                    yield M4StateTransaction(connection)
        except OperationalError as exc:
            raise DatabaseUnavailableError("PostgreSQL M4 transaction failed") from exc
        finally:
            if not connection.closed:
                connection.close()


class M4StateTransaction(M3StateTransaction):
    """M3 transaction superset for M4 model-governance aggregates."""

    def insert_runtime_config_snapshot(self, record: Mapping[str, Any]) -> dict[str, Any]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO business.runtime_config_snapshots (
                    snapshot_id, program_id, run_id, provider_alias, model_id,
                    parameters, config_sha256, max_calls,
                    max_input_tokens_per_call, max_output_tokens_per_call,
                    max_cost_microunits_per_call,
                    max_total_input_tokens, max_total_output_tokens,
                    max_total_cost_microunits, created_by_principal_id, created_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s
                )
                RETURNING *
                """,
                (
                    _uuid(record["snapshot_id"], "snapshot_id"),
                    _uuid(record["program_id"], "program_id"),
                    _uuid(record["run_id"], "run_id"),
                    record["provider_alias"],
                    record["model_id"],
                    Jsonb(dict(record["parameters"])),
                    record["config_sha256"],
                    record["max_calls"],
                    record["max_input_tokens_per_call"],
                    record["max_output_tokens_per_call"],
                    record["max_cost_microunits_per_call"],
                    record["max_total_input_tokens"],
                    record["max_total_output_tokens"],
                    record["max_total_cost_microunits"],
                    record["created_by_principal_id"],
                    record["created_at"],
                ),
            )
            row = cursor.fetchone()
        if row is None:
            raise PersistenceInvariantError("RuntimeConfigSnapshot insert returned no row")
        return dict(row)

    def get_runtime_config_snapshot(
        self,
        program_id: str | UUID,
        snapshot_id: str | UUID,
        *,
        for_update: bool = False,
    ) -> dict[str, Any] | None:
        suffix = " FOR UPDATE" if for_update else ""
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM business.runtime_config_snapshots
                WHERE program_id = %s AND snapshot_id = %s
                """ + suffix,
                (_uuid(program_id, "program_id"), _uuid(snapshot_id, "snapshot_id")),
            )
            row = cursor.fetchone()
        return dict(row) if row is not None else None

    def get_budget_reservation_by_call(
        self,
        program_id: str | UUID,
        model_call_id: str | UUID,
    ) -> dict[str, Any] | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM business.model_budget_reservations
                WHERE program_id = %s AND model_call_id = %s
                """,
                (_uuid(program_id, "program_id"), _uuid(model_call_id, "model_call_id")),
            )
            row = cursor.fetchone()
        return dict(row) if row is not None else None

    def get_budget_totals(
        self,
        *,
        program_id: str | UUID,
        run_id: str | UUID,
        snapshot_id: str | UUID,
    ) -> dict[str, int]:
        """Return committed maxima for the minimal M4 pre-call gate.

        Active reservations retain their full maxima.  A settled reservation
        uses the charged amount, releasing only the unused portion.  Refused
        attempts do not consume a call or budget.
        """

        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    count(*) FILTER (WHERE status <> 'refused')::bigint AS call_count,
                    COALESCE(sum(
                        CASE WHEN status = 'reserved' THEN max_input_tokens
                             WHEN status IN ('settled', 'conservative_settled')
                             THEN charged_input_tokens ELSE 0 END
                    ), 0)::bigint AS input_tokens,
                    COALESCE(sum(
                        CASE WHEN status = 'reserved' THEN max_output_tokens
                             WHEN status IN ('settled', 'conservative_settled')
                             THEN charged_output_tokens ELSE 0 END
                    ), 0)::bigint AS output_tokens,
                    COALESCE(sum(
                        CASE WHEN status = 'reserved' THEN max_cost_microunits
                             WHEN status IN ('settled', 'conservative_settled')
                             THEN charged_cost_microunits ELSE 0 END
                    ), 0)::bigint AS cost_microunits
                FROM business.model_budget_reservations
                WHERE program_id = %s AND run_id = %s AND snapshot_id = %s
                """,
                (
                    _uuid(program_id, "program_id"),
                    _uuid(run_id, "run_id"),
                    _uuid(snapshot_id, "snapshot_id"),
                ),
            )
            row = cursor.fetchone()
        if row is None:
            raise PersistenceInvariantError("budget aggregate query returned no row")
        return {key: int(value) for key, value in dict(row).items()}

    def get_program_budget_totals(
        self,
        *,
        program_id: str | UUID,
    ) -> dict[str, int]:
        """Return conservative AUTH-M4-002 totals across every snapshot.

        Reserved calls retain their full maxima; terminal calls use their
        charged values; refused reservations consume no policy budget.  The
        surrounding State Service transaction already holds the Program row
        lock, so concurrent reservations cannot race this aggregate.
        """

        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    count(*) FILTER (WHERE status <> 'refused')::bigint AS call_slot_count,
                    COALESCE(sum(
                        CASE WHEN status = 'reserved' THEN max_input_tokens
                             WHEN status IN ('settled', 'conservative_settled')
                             THEN charged_input_tokens ELSE 0 END
                    ), 0)::bigint AS input_tokens,
                    COALESCE(sum(
                        CASE WHEN status = 'reserved' THEN max_output_tokens
                             WHEN status IN ('settled', 'conservative_settled')
                             THEN charged_output_tokens ELSE 0 END
                    ), 0)::bigint AS output_tokens
                FROM business.model_budget_reservations
                WHERE program_id = %s
                """,
                (_uuid(program_id, "program_id"),),
            )
            row = cursor.fetchone()
        if row is None:
            raise PersistenceInvariantError(
                "program budget aggregate query returned no row"
            )
        return {key: int(value) for key, value in dict(row).items()}

    def insert_model_budget_reservation(self, record: Mapping[str, Any]) -> dict[str, Any]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO business.model_budget_reservations (
                    reservation_id, program_id, run_id, model_call_id,
                    snapshot_id, max_input_tokens, max_output_tokens,
                    max_cost_microunits, status, reservation_version,
                    refusal_reason_code, requested_by_principal_id, created_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s
                )
                RETURNING *
                """,
                (
                    _uuid(record["reservation_id"], "reservation_id"),
                    _uuid(record["program_id"], "program_id"),
                    _uuid(record["run_id"], "run_id"),
                    _uuid(record["model_call_id"], "model_call_id"),
                    _uuid(record["snapshot_id"], "snapshot_id"),
                    record["max_input_tokens"],
                    record["max_output_tokens"],
                    record["max_cost_microunits"],
                    record["status"],
                    record["reservation_version"],
                    record.get("refusal_reason_code"),
                    record["requested_by_principal_id"],
                    record["created_at"],
                ),
            )
            row = cursor.fetchone()
        if row is None:
            raise PersistenceInvariantError("ModelBudgetReservation insert returned no row")
        return dict(row)

    def get_model_budget_reservation(
        self,
        program_id: str | UUID,
        reservation_id: str | UUID,
        *,
        for_update: bool = False,
    ) -> dict[str, Any] | None:
        suffix = " FOR UPDATE" if for_update else ""
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM business.model_budget_reservations
                WHERE program_id = %s AND reservation_id = %s
                """ + suffix,
                (_uuid(program_id, "program_id"), _uuid(reservation_id, "reservation_id")),
            )
            row = cursor.fetchone()
        return dict(row) if row is not None else None

    def insert_model_usage_ledger(self, record: Mapping[str, Any]) -> dict[str, Any]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO business.model_usage_ledger (
                    usage_ledger_id, provider_usage_receipt_id, reservation_id,
                    program_id, run_id, model_call_id, snapshot_id,
                    provider_alias, provider_request_id, request_sha256,
                    response_sha256, usage_status, provider_status,
                    reported_input_tokens, reported_output_tokens,
                    reported_cost_microunits, charged_input_tokens,
                    charged_output_tokens, charged_cost_microunits,
                    over_budget, issued_by_principal_id, provider_issued_at,
                    created_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                RETURNING *
                """,
                (
                    _uuid(record["usage_ledger_id"], "usage_ledger_id"),
                    _uuid(record["provider_usage_receipt_id"], "provider_usage_receipt_id"),
                    _uuid(record["reservation_id"], "reservation_id"),
                    _uuid(record["program_id"], "program_id"),
                    _uuid(record["run_id"], "run_id"),
                    _uuid(record["model_call_id"], "model_call_id"),
                    _uuid(record["snapshot_id"], "snapshot_id"),
                    record["provider_alias"],
                    record.get("provider_request_id"),
                    record["request_sha256"],
                    record.get("response_sha256"),
                    record["usage_status"],
                    record["provider_status"],
                    record.get("reported_input_tokens"),
                    record.get("reported_output_tokens"),
                    record.get("reported_cost_microunits"),
                    record["charged_input_tokens"],
                    record["charged_output_tokens"],
                    record["charged_cost_microunits"],
                    record["over_budget"],
                    record["issued_by_principal_id"],
                    record["provider_issued_at"],
                    record["created_at"],
                ),
            )
            row = cursor.fetchone()
        if row is None:
            raise PersistenceInvariantError("ModelUsageLedger insert returned no row")
        return dict(row)

    def settle_model_budget_reservation(
        self,
        *,
        program_id: str | UUID,
        reservation_id: str | UUID,
        status: str,
        provider_usage_receipt_id: str | UUID,
        usage_ledger_id: str | UUID,
        charged_input_tokens: int,
        charged_output_tokens: int,
        charged_cost_microunits: int,
        settled_at: Any,
    ) -> dict[str, Any]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE business.model_budget_reservations
                SET status = %s,
                    reservation_version = reservation_version + 1,
                    provider_usage_receipt_id = %s,
                    usage_ledger_id = %s,
                    charged_input_tokens = %s,
                    charged_output_tokens = %s,
                    charged_cost_microunits = %s,
                    settled_at = %s,
                    updated_at = clock_timestamp()
                WHERE program_id = %s AND reservation_id = %s
                  AND status = 'reserved' AND reservation_version = 1
                RETURNING *
                """,
                (
                    status,
                    _uuid(provider_usage_receipt_id, "provider_usage_receipt_id"),
                    _uuid(usage_ledger_id, "usage_ledger_id"),
                    charged_input_tokens,
                    charged_output_tokens,
                    charged_cost_microunits,
                    settled_at,
                    _uuid(program_id, "program_id"),
                    _uuid(reservation_id, "reservation_id"),
                ),
            )
            row = cursor.fetchone()
        if row is not None:
            return dict(row)
        current = self.get_model_budget_reservation(
            program_id,
            reservation_id,
            for_update=True,
        )
        if current is None:
            raise BudgetReservationNotFoundError(
                f"budget reservation {reservation_id} does not exist"
            )
        raise BudgetReservationStateError(
            f"budget reservation is {current['status']!r}, not 'reserved'"
        )

    def commit_auxiliary_change(
        self,
        *,
        program_id: str | UUID,
        command_id: str | UUID,
        observed_state_version: int,
        result: Mapping[str, Any],
        event: Mapping[str, Any],
        outbox_event: Mapping[str, Any],
    ) -> int:
        """Commit receipt + event + Outbox without changing Program state_version."""

        program_uuid = _uuid(program_id, "program_id")
        command_uuid = _uuid(command_id, "command_id")
        event_id = _uuid(event["event_id"], "event_id")
        if _uuid(outbox_event["domain_event_id"], "domain_event_id") != event_id:
            raise PersistenceInvariantError("M4 OutboxEvent must reference its DomainEvent")

        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT state_version FROM business.programs WHERE program_id = %s",
                (program_uuid,),
            )
            program = cursor.fetchone()
        if program is None:
            raise PersistenceInvariantError("Program disappeared during M4 transaction")
        if int(program["state_version"]) != observed_state_version:
            raise PersistenceInvariantError("Program state_version changed while lock was held")

        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO business.domain_events (
                    event_id, program_id, command_id, event_index,
                    event_type, state_version, payload, traceparent
                ) VALUES (%s, %s, %s, 0, %s, %s, %s, %s)
                """,
                (
                    event_id,
                    program_uuid,
                    command_uuid,
                    event["event_type"],
                    observed_state_version,
                    Jsonb(dict(event["payload"])),
                    event.get("traceparent"),
                ),
            )
            cursor.execute(
                """
                INSERT INTO business.outbox_events (
                    outbox_event_id, domain_event_id, program_id,
                    aggregate_type, aggregate_id, aggregate_version,
                    topic, payload
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    _uuid(outbox_event["outbox_event_id"], "outbox_event_id"),
                    event_id,
                    program_uuid,
                    outbox_event["aggregate_type"],
                    _uuid(outbox_event["aggregate_id"], "aggregate_id"),
                    outbox_event["aggregate_version"],
                    outbox_event["topic"],
                    Jsonb(dict(outbox_event["payload"])),
                ),
            )
            cursor.execute(
                """
                UPDATE business.command_receipts
                SET status = 'committed', reason_code = 'OK', result = %s,
                    state_version_after = %s, updated_at = clock_timestamp()
                WHERE command_id = %s AND program_id = %s
                  AND status = 'processing'
                RETURNING command_id
                """,
                (
                    Jsonb(dict(result)),
                    observed_state_version,
                    command_uuid,
                    program_uuid,
                ),
            )
            receipt = cursor.fetchone()
        if receipt is None:
            raise PersistenceInvariantError(
                "M4 command receipt was not reserved or was already finalized"
            )
        return observed_state_version


__all__ = (
    "BudgetReservationNotFoundError",
    "BudgetReservationStateError",
    "M4PostgresStateStore",
    "M4StateTransaction",
    "RuntimeConfigNotFoundError",
)
