"""Authoritative M2+M3+M4 State Service facade."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from psycopg import Error as PsycopgError, IntegrityError

from awakening.state.contracts import (
    CommandEnvelope,
    CommandResult,
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
)
from awakening.state.m3 import (
    M3_CHECKER_VERSION,
    M3_POLICY_VERSION,
    M3CommandEnvelope,
    M3StateServiceFacade,
)
from awakening.state.service import BootstrapMember, BusinessRuleError
from awakening.state.validation import ContractValidationError, canonical_sha256

from .contracts import (
    M4CommandEnvelope,
    M4CommandType,
    M4ReasonCode,
    M4StateMcpMethod,
    ProviderUsageStatus,
    TrustedRuntimeContext,
    derive_usage_ledger_id,
)
from .database import (
    BudgetReservationNotFoundError,
    BudgetReservationStateError,
    M4PostgresStateStore,
    M4StateTransaction,
)
from .validation import (
    m4_request_hash,
    validate_m4_command_envelope,
    validate_model_budget_reservation,
    validate_runtime_config_snapshot,
)


_APPLY_REJECTION_NAMESPACE = uuid5(
    NAMESPACE_URL,
    "awakening.local/m4/apply-disabled",
)

# DEC-M4-006 supersedes only AUTH-M4-002's original nine reservation slots.
# State Service still owns a finite cross-snapshot boundary: a 300-slot
# anti-loop fuse plus the exact user-confirmed ¥10 hard cost gate at
# ¥0.2 / ¥0.8 per million tokens.  The separate micro-USD strategy ledger is
# not reused or relabelled as RMB.
_M4_MULTI_CALL_POLICY_ID = "DEC-M4-006"
_DEC_M4_006_PROGRAM_CAPS: Mapping[str, int] = {
    "reservation_slot_count": 300,
    "input_tokens": 50_000_000,
    "output_tokens": 12_500_000,
    "cost_microcny": 10_000_000,
}
_M4_INPUT_MICROCNY_PER_MILLION = 200_000
_M4_OUTPUT_MICROCNY_PER_MILLION = 800_000


def _program_provider_policy_violations(
    *,
    program_totals: Mapping[str, int],
    requested_input_tokens: int,
    requested_output_tokens: int,
) -> tuple[str, ...]:
    """Apply the finite cross-snapshot multi-call policy owned by State."""

    violations: list[str] = []
    prospective_slots = int(program_totals["call_slot_count"]) + 1
    prospective_input = int(program_totals["input_tokens"]) + requested_input_tokens
    prospective_output = int(program_totals["output_tokens"]) + requested_output_tokens
    if prospective_slots > _DEC_M4_006_PROGRAM_CAPS["reservation_slot_count"]:
        violations.append("PROGRAM_TOTAL_CALL_CAP")
    if prospective_input > _DEC_M4_006_PROGRAM_CAPS["input_tokens"]:
        violations.append("PROGRAM_TOTAL_INPUT_CAP")
    if prospective_output > _DEC_M4_006_PROGRAM_CAPS["output_tokens"]:
        violations.append("PROGRAM_TOTAL_OUTPUT_CAP")
    prospective_cost_microcny_numerator = (
        prospective_input * _M4_INPUT_MICROCNY_PER_MILLION
        + prospective_output * _M4_OUTPUT_MICROCNY_PER_MILLION
    )
    if (
        prospective_cost_microcny_numerator
        > _DEC_M4_006_PROGRAM_CAPS["cost_microcny"] * 1_000_000
    ):
        violations.append("PROGRAM_RMB_COST_CAP")
    return tuple(violations)


@dataclass(frozen=True, slots=True)
class _M4Mutation:
    event_type: str
    aggregate_type: str
    aggregate_id: str
    aggregate_version: int
    result: Mapping[str, Any]


class M4StateServiceFacade:
    """One facade and one State Service database role for M2 through M4."""

    def __init__(
        self,
        store: M4PostgresStateStore,
        *,
        bootstrap_members: tuple[BootstrapMember, ...] = (),
        approval_ttl: timedelta = timedelta(minutes=30),
        policy_version: str = M3_POLICY_VERSION,
        checker_version: str = M3_CHECKER_VERSION,
    ) -> None:
        self._store = store
        self._m3 = M3StateServiceFacade(
            store,
            bootstrap_members=bootstrap_members,
            approval_ttl=approval_ttl,
            policy_version=policy_version,
            checker_version=checker_version,
        )
        # M4 is an additive facade.  The accepted M2 object remains owned by
        # the accepted M3 facade and all M2/M3 envelopes are delegated intact.
        self._m2 = self._m3._m2
        self._m4_handlers: dict[
            M4CommandType,
            Callable[[M4CommandEnvelope], CommandResult],
        ] = {
            M4CommandType.RUNTIME_CONFIG_SNAPSHOT_CREATE: (
                self._create_runtime_config_snapshot
            ),
            M4CommandType.MODEL_BUDGET_RESERVE: self._reserve_model_budget,
            M4CommandType.MODEL_BUDGET_SETTLE: self._settle_model_budget,
        }

    @property
    def registered_commands(self) -> frozenset[str]:
        return self._m3.registered_commands | frozenset(
            command.value for command in self._m4_handlers
        )

    def dispatch(
        self,
        envelope: CommandEnvelope | M3CommandEnvelope | M4CommandEnvelope,
    ) -> CommandResult:
        """Delegate frozen envelopes and handle only typed M4 internal commands."""

        if isinstance(envelope, (CommandEnvelope, M3CommandEnvelope)):
            return self._m3.dispatch(envelope)
        if not isinstance(envelope, M4CommandEnvelope):
            return self._m2._rejected(
                str(uuid4()),
                ReasonCode.INVALID_ENVELOPE,
                "unsupported command envelope type",
            )
        try:
            validate_m4_command_envelope(envelope)
            handler = self._m4_handlers.get(envelope.command_type)
            if handler is None:
                raise BusinessRuleError(
                    ReasonCode.COMMAND_NOT_REGISTERED,
                    "command is not registered in M4",
                )
            return handler(envelope)
        except ContractValidationError as exc:
            return self._m2._rejected(envelope.command_id, exc.reason_code, str(exc))
        except BusinessRuleError as exc:
            return self._m2._rejected(
                envelope.command_id,
                exc.reason_code,
                exc.message,
                details=exc.details,
            )
        except ProgramNotFoundError as exc:
            return self._m2._rejected(envelope.command_id, ReasonCode.NOT_FOUND, str(exc))
        except IdempotencyConflictError as exc:
            return self._m2._rejected(
                envelope.command_id,
                ReasonCode.IDEMPOTENCY_KEY_REUSED,
                str(exc),
            )
        except CommandInProgressError as exc:
            return self._m2._rejected(envelope.command_id, ReasonCode.CONFLICT, str(exc))
        except DatabaseUnavailableError as exc:
            return self._m2._rejected(
                envelope.command_id,
                ReasonCode.DATABASE_UNAVAILABLE,
                str(exc),
            )
        except IntegrityError as exc:
            return self._m2._rejected(
                envelope.command_id,
                ReasonCode.CONFLICT,
                "database constraint rejected the M4 command",
                details={"sqlstate": exc.sqlstate},
            )
        except PersistenceInvariantError as exc:
            return self._m2._rejected(
                envelope.command_id,
                ReasonCode.TRANSACTION_ABORTED,
                str(exc),
            )
        except PsycopgError:
            return self._m2._rejected(
                envelope.command_id,
                ReasonCode.TRANSACTION_ABORTED,
                "PostgreSQL aborted the authoritative M4 transaction",
            )

    def query(
        self,
        *,
        query_type: QueryType | str,
        program_id: str,
        payload: Mapping[str, Any],
        trusted_principal: TrustedPrincipal,
    ) -> dict[str, Any]:
        return self._m3.query(
            query_type=query_type,
            program_id=program_id,
            payload=payload,
            trusted_principal=trusted_principal,
        )

    def get_runtime_config_snapshot(
        self,
        *,
        program_id: str,
        snapshot_id: str,
        trusted_principal: TrustedPrincipal,
    ) -> dict[str, Any]:
        """Return one key-free snapshot through the State Service boundary."""

        with self._store.transaction() as transaction:
            self._authorize_internal_service_read(
                transaction,
                program_id,
                trusted_principal,
            )
            row = transaction.get_runtime_config_snapshot(program_id, snapshot_id)
            if row is None:
                raise BusinessRuleError(
                    ReasonCode.NOT_FOUND,
                    "RuntimeConfigSnapshot was not found",
                    details={
                        "m4_reason_code": M4ReasonCode.RUNTIME_CONFIG_NOT_FOUND.value,
                    },
                )
        return _jsonable(row)

    def get_model_budget_reservation(
        self,
        *,
        program_id: str,
        reservation_id: str,
        trusted_principal: TrustedPrincipal,
    ) -> dict[str, Any]:
        """Return a reservation and its exact snapshot/model-call binding."""

        with self._store.transaction() as transaction:
            self._authorize_internal_service_read(
                transaction,
                program_id,
                trusted_principal,
            )
            row = transaction.get_model_budget_reservation(program_id, reservation_id)
            if row is None:
                raise BusinessRuleError(
                    ReasonCode.NOT_FOUND,
                    "ModelBudgetReservation was not found",
                    details={
                        "m4_reason_code": M4ReasonCode.RESERVATION_NOT_FOUND.value,
                    },
                )
        return _jsonable(row)

    def authorize_runtime_method(
        self,
        *,
        method: M4StateMcpMethod | str,
        program_id: str,
        trusted_context: TrustedRuntimeContext,
    ) -> TrustedPrincipal:
        """Re-check method, Program scope and persistent role inside State Service."""

        method_value = M4StateMcpMethod(method)
        principal = self._authorize_runtime_context_without_read(
            method=method_value,
            program_id=program_id,
            trusted_context=trusted_context,
        )
        with self._store.transaction() as transaction:
            transaction.get_snapshot(program_id)
            member = self._m2._require_member(transaction, program_id, principal)
            if member["program_role"] != trusted_context.program_role:
                raise BusinessRuleError(
                    ReasonCode.PRINCIPAL_NOT_ALLOWED,
                    "server runtime role does not match persistent Program membership",
                    details={
                        "m4_reason_code": M4ReasonCode.RUNTIME_CONTEXT_INVALID.value,
                    },
                )
        return principal

    def authorize_disabled_apply_context(
        self,
        *,
        program_id: str,
        trusted_context: TrustedRuntimeContext,
    ) -> TrustedPrincipal:
        """Authorize only the no-side-effect M4 apply refusal path."""

        return self._authorize_runtime_context_without_read(
            method=M4StateMcpMethod.APPLY_AUTHORIZED_CHANGE,
            program_id=program_id,
            trusted_context=trusted_context,
        )

    def _authorize_runtime_context_without_read(
        self,
        *,
        method: M4StateMcpMethod,
        program_id: str,
        trusted_context: TrustedRuntimeContext,
    ) -> TrustedPrincipal:
        if not trusted_context.allows(method):
            raise BusinessRuleError(
                ReasonCode.PRINCIPAL_NOT_ALLOWED,
                "M4 Agent identity is not allowed to call this State MCP method",
                details={
                    "m4_reason_code": M4ReasonCode.METHOD_NOT_ALLOWED.value,
                    "method": method.value,
                    "agent_identity": trusted_context.agent_identity.value,
                },
            )
        principal = trusted_context.to_trusted_principal()
        self._m2._assert_program_scope(program_id, principal)
        return principal

    def _authorize_internal_service_read(
        self,
        transaction: M4StateTransaction,
        program_id: str,
        trusted_principal: TrustedPrincipal,
    ) -> None:
        if trusted_principal.principal_type is not PrincipalType.SERVICE:
            raise BusinessRuleError(
                ReasonCode.PRINCIPAL_NOT_ALLOWED,
                "M4 runtime queries require a trusted service principal",
            )
        self._m2._assert_program_scope(program_id, trusted_principal)
        transaction.get_snapshot(program_id)
        member = self._m2._require_member(
            transaction,
            program_id,
            trusted_principal,
        )
        if member["program_role"] != "service":
            raise BusinessRuleError(
                ReasonCode.PRINCIPAL_NOT_ALLOWED,
                "M4 runtime queries require persistent Program role service",
            )

    @staticmethod
    def reject_m4_apply(
        *,
        program_id: str,
        proposal_id: str,
        expected_state_version: int,
        idempotency_key: str,
        human_decision_id: str | None,
    ) -> CommandResult:
        """M4 never delegates apply to the accepted M2 low-risk path."""

        material = "|".join(
            (
                program_id,
                proposal_id,
                str(expected_state_version),
                idempotency_key,
                human_decision_id or "",
            )
        )
        return CommandResult(
            command_id=str(uuid5(_APPLY_REJECTION_NAMESPACE, material)),
            status="REJECTED",
            reason_code=ReasonCode.APPROVAL_REQUIRED,
            result={
                "message": "M4 does not activate a Proposal or create plan V2",
                "m4_reason_code": M4ReasonCode.APPLY_DISABLED.value,
                "delegated": False,
            },
        )

    def _create_runtime_config_snapshot(
        self,
        envelope: M4CommandEnvelope,
    ) -> CommandResult:
        def operation(
            transaction: M4StateTransaction,
            _program: Mapping[str, Any],
        ) -> _M4Mutation:
            spec = envelope.runtime_config
            if spec is None:
                raise BusinessRuleError(
                    ReasonCode.INVALID_ENVELOPE,
                    "trusted RuntimeConfigSpec is required",
                )
            now = transaction.execute_scalar("SELECT clock_timestamp()")
            snapshot_id = str(uuid4())
            config_material = {
                "program_id": envelope.program_id,
                **spec.to_dict(),
            }
            record = {
                "snapshot_id": snapshot_id,
                **config_material,
                "config_sha256": canonical_sha256(config_material),
                "created_by_principal_id": envelope.trusted_principal.principal_id,
                "created_at": _iso(now),
            }
            validate_runtime_config_snapshot(record)
            database_record = {**record, "created_at": now}
            transaction.insert_runtime_config_snapshot(database_record)
            result = {
                "snapshot_id": snapshot_id,
                "program_id": envelope.program_id,
                "run_id": spec.run_id,
                "provider_alias": spec.provider_alias,
                "model_id": spec.model_id,
                "config_sha256": record["config_sha256"],
                "created_at": record["created_at"],
                "contains_provider_key": False,
            }
            return _M4Mutation(
                event_type="runtime_config.snapshot.created",
                aggregate_type="runtime_config_snapshot",
                aggregate_id=snapshot_id,
                aggregate_version=1,
                result=result,
            )

        return self._run_m4_program_command(envelope, operation)

    def _reserve_model_budget(self, envelope: M4CommandEnvelope) -> CommandResult:
        def operation(
            transaction: M4StateTransaction,
            _program: Mapping[str, Any],
        ) -> _M4Mutation:
            request = envelope.budget_request
            if request is None:
                raise BusinessRuleError(
                    ReasonCode.INVALID_ENVELOPE,
                    "trusted ModelBudgetRequest is required",
                )
            snapshot = transaction.get_runtime_config_snapshot(
                envelope.program_id,
                request.snapshot_id,
            )
            if snapshot is None:
                raise BusinessRuleError(
                    ReasonCode.NOT_FOUND,
                    "RuntimeConfigSnapshot was not found",
                    details={
                        "m4_reason_code": M4ReasonCode.RUNTIME_CONFIG_NOT_FOUND.value,
                    },
                )
            if _uuid_text(snapshot["run_id"]) != request.run_id:
                raise BusinessRuleError(
                    ReasonCode.SCHEMA_INVALID,
                    "budget request run_id does not match RuntimeConfigSnapshot",
                    details={"m4_reason_code": M4ReasonCode.RUNTIME_CONFIG_INVALID.value},
                )
            existing = transaction.get_budget_reservation_by_call(
                envelope.program_id,
                request.model_call_id,
            )
            if existing is not None:
                raise BusinessRuleError(
                    ReasonCode.CONFLICT,
                    "model_call_id is already bound to another reservation command",
                    details={"reservation_id": _uuid_text(existing["reservation_id"])},
                )
            totals = transaction.get_budget_totals(
                program_id=envelope.program_id,
                run_id=request.run_id,
                snapshot_id=request.snapshot_id,
            )
            program_totals = transaction.get_program_budget_totals(
                program_id=envelope.program_id,
            )
            violations: list[str] = []
            if request.max_input_tokens > int(snapshot["max_input_tokens_per_call"]):
                violations.append("PER_CALL_INPUT_CAP")
            if request.max_output_tokens > int(snapshot["max_output_tokens_per_call"]):
                violations.append("PER_CALL_OUTPUT_CAP")
            if request.max_cost_microunits > int(
                snapshot["max_cost_microunits_per_call"]
            ):
                violations.append("PER_CALL_COST_CAP")
            if totals["call_count"] + 1 > int(snapshot["max_calls"]):
                violations.append("TOTAL_CALL_CAP")
            if totals["input_tokens"] + request.max_input_tokens > int(
                snapshot["max_total_input_tokens"]
            ):
                violations.append("TOTAL_INPUT_CAP")
            if totals["output_tokens"] + request.max_output_tokens > int(
                snapshot["max_total_output_tokens"]
            ):
                violations.append("TOTAL_OUTPUT_CAP")
            if totals["cost_microunits"] + request.max_cost_microunits > int(
                snapshot["max_total_cost_microunits"]
            ):
                violations.append("TOTAL_COST_CAP")
            violations.extend(
                _program_provider_policy_violations(
                    program_totals=program_totals,
                    requested_input_tokens=request.max_input_tokens,
                    requested_output_tokens=request.max_output_tokens,
                )
            )

            now = transaction.execute_scalar("SELECT clock_timestamp()")
            reservation_id = str(uuid4())
            status = "refused" if violations else "reserved"
            refusal_code = M4ReasonCode.BUDGET_REFUSED.value if violations else None
            record = {
                "reservation_id": reservation_id,
                "program_id": envelope.program_id,
                **request.to_dict(),
                "status": status,
                "reservation_version": 1,
                "refusal_reason_code": refusal_code,
                "provider_usage_receipt_id": None,
                "usage_ledger_id": None,
                "charged_input_tokens": None,
                "charged_output_tokens": None,
                "charged_cost_microunits": None,
                "requested_by_principal_id": envelope.trusted_principal.principal_id,
                "created_at": _iso(now),
                "settled_at": None,
            }
            validate_model_budget_reservation(record)
            transaction.insert_model_budget_reservation({**record, "created_at": now})
            result = {
                "reservation_id": reservation_id,
                "program_id": envelope.program_id,
                "run_id": request.run_id,
                "model_call_id": request.model_call_id,
                "snapshot_id": request.snapshot_id,
                "status": status,
                "reservation_version": 1,
                "provider_call_allowed": status == "reserved",
                "m4_reason_code": refusal_code,
                "violations": violations,
            }
            return _M4Mutation(
                event_type=f"model_budget.reservation.{status}",
                aggregate_type="model_budget_reservation",
                aggregate_id=reservation_id,
                aggregate_version=1,
                result=result,
            )

        return self._run_m4_program_command(envelope, operation)

    def _settle_model_budget(self, envelope: M4CommandEnvelope) -> CommandResult:
        def operation(
            transaction: M4StateTransaction,
            _program: Mapping[str, Any],
        ) -> _M4Mutation:
            receipt = envelope.usage_receipt
            if receipt is None:
                raise BusinessRuleError(
                    ReasonCode.INVALID_ENVELOPE,
                    "trusted ProviderUsageReceipt is required",
                )
            if (
                receipt.issued_by_principal_id
                != envelope.trusted_principal.principal_id
            ):
                raise BusinessRuleError(
                    ReasonCode.PRINCIPAL_NOT_ALLOWED,
                    "ProviderUsageReceipt issuer does not match the trusted Gateway principal",
                    details={
                        "m4_reason_code": M4ReasonCode.PROVIDER_USAGE_INVALID.value,
                    },
                )
            reservation = transaction.get_model_budget_reservation(
                envelope.program_id,
                receipt.reservation_id,
                for_update=True,
            )
            if reservation is None:
                raise BudgetReservationNotFoundError(
                    f"budget reservation {receipt.reservation_id} does not exist"
                )
            if reservation["status"] != "reserved":
                raise BudgetReservationStateError(
                    f"budget reservation is {reservation['status']!r}, not 'reserved'"
                )
            snapshot = transaction.get_runtime_config_snapshot(
                envelope.program_id,
                receipt.snapshot_id,
            )
            if snapshot is None:
                raise BusinessRuleError(
                    ReasonCode.NOT_FOUND,
                    "RuntimeConfigSnapshot was not found during settlement",
                    details={
                        "m4_reason_code": M4ReasonCode.RUNTIME_CONFIG_NOT_FOUND.value,
                    },
                )
            expected_bindings = {
                "program_id": envelope.program_id,
                "run_id": _uuid_text(reservation["run_id"]),
                "model_call_id": _uuid_text(reservation["model_call_id"]),
                "snapshot_id": _uuid_text(reservation["snapshot_id"]),
                "reservation_id": _uuid_text(reservation["reservation_id"]),
                "provider_alias": snapshot["provider_alias"],
            }
            actual_bindings = {
                "program_id": receipt.program_id,
                "run_id": receipt.run_id,
                "model_call_id": receipt.model_call_id,
                "snapshot_id": receipt.snapshot_id,
                "reservation_id": receipt.reservation_id,
                "provider_alias": receipt.provider_alias,
            }
            if actual_bindings != expected_bindings:
                raise BusinessRuleError(
                    ReasonCode.SCHEMA_INVALID,
                    "ProviderUsageReceipt is not bound to the reserved model call",
                    details={"m4_reason_code": M4ReasonCode.PROVIDER_USAGE_INVALID.value},
                )

            reported = receipt.usage_status is ProviderUsageStatus.REPORTED
            charged_input = (
                int(receipt.input_tokens)
                if reported
                else int(reservation["max_input_tokens"])
            )
            charged_output = (
                int(receipt.output_tokens)
                if reported
                else int(reservation["max_output_tokens"])
            )
            charged_cost = (
                int(receipt.cost_microunits)
                if reported
                else int(reservation["max_cost_microunits"])
            )
            over_budget = (
                charged_input > int(reservation["max_input_tokens"])
                or charged_output > int(reservation["max_output_tokens"])
                or charged_cost > int(reservation["max_cost_microunits"])
            )
            settlement_status = "settled" if reported else "conservative_settled"
            usage_ledger_id = derive_usage_ledger_id(receipt.receipt_id)
            now = transaction.execute_scalar("SELECT clock_timestamp()")
            ledger_record = {
                "usage_ledger_id": usage_ledger_id,
                "provider_usage_receipt_id": receipt.receipt_id,
                "reservation_id": receipt.reservation_id,
                "program_id": receipt.program_id,
                "run_id": receipt.run_id,
                "model_call_id": receipt.model_call_id,
                "snapshot_id": receipt.snapshot_id,
                "provider_alias": receipt.provider_alias,
                "provider_request_id": receipt.provider_request_id,
                "request_sha256": receipt.request_sha256,
                "response_sha256": receipt.response_sha256,
                "usage_status": receipt.usage_status.value,
                "provider_status": receipt.provider_status.value,
                "reported_input_tokens": receipt.input_tokens,
                "reported_output_tokens": receipt.output_tokens,
                "reported_cost_microunits": receipt.cost_microunits,
                "charged_input_tokens": charged_input,
                "charged_output_tokens": charged_output,
                "charged_cost_microunits": charged_cost,
                "over_budget": over_budget,
                "issued_by_principal_id": receipt.issued_by_principal_id,
                "provider_issued_at": receipt.issued_at,
                "created_at": now,
            }
            transaction.insert_model_usage_ledger(ledger_record)
            updated = transaction.settle_model_budget_reservation(
                program_id=envelope.program_id,
                reservation_id=receipt.reservation_id,
                status=settlement_status,
                provider_usage_receipt_id=receipt.receipt_id,
                usage_ledger_id=usage_ledger_id,
                charged_input_tokens=charged_input,
                charged_output_tokens=charged_output,
                charged_cost_microunits=charged_cost,
                settled_at=now,
            )
            reservation_record = {
                "reservation_id": _uuid_text(updated["reservation_id"]),
                "program_id": _uuid_text(updated["program_id"]),
                "run_id": _uuid_text(updated["run_id"]),
                "model_call_id": _uuid_text(updated["model_call_id"]),
                "snapshot_id": _uuid_text(updated["snapshot_id"]),
                "max_input_tokens": int(updated["max_input_tokens"]),
                "max_output_tokens": int(updated["max_output_tokens"]),
                "max_cost_microunits": int(updated["max_cost_microunits"]),
                "status": updated["status"],
                "reservation_version": int(updated["reservation_version"]),
                "refusal_reason_code": updated["refusal_reason_code"],
                "provider_usage_receipt_id": _uuid_text(
                    updated["provider_usage_receipt_id"]
                ),
                "usage_ledger_id": _uuid_text(updated["usage_ledger_id"]),
                "charged_input_tokens": int(updated["charged_input_tokens"]),
                "charged_output_tokens": int(updated["charged_output_tokens"]),
                "charged_cost_microunits": int(updated["charged_cost_microunits"]),
                "requested_by_principal_id": updated["requested_by_principal_id"],
                "created_at": _iso(updated["created_at"]),
                "settled_at": _iso(updated["settled_at"]),
            }
            validate_model_budget_reservation(reservation_record)
            result = {
                "reservation_id": receipt.reservation_id,
                "usage_ledger_id": usage_ledger_id,
                "provider_usage_receipt_id": receipt.receipt_id,
                "status": settlement_status,
                "reservation_version": 2,
                "charged_input_tokens": charged_input,
                "charged_output_tokens": charged_output,
                "charged_cost_microunits": charged_cost,
                "over_budget": over_budget,
            }
            return _M4Mutation(
                event_type=f"model_budget.reservation.{settlement_status}",
                aggregate_type="model_budget_reservation",
                aggregate_id=receipt.reservation_id,
                aggregate_version=2,
                result=result,
            )

        return self._run_m4_program_command(envelope, operation)

    def _run_m4_program_command(
        self,
        envelope: M4CommandEnvelope,
        operation: Callable[[M4StateTransaction, Mapping[str, Any]], _M4Mutation],
    ) -> CommandResult:
        request_hash = m4_request_hash(envelope)
        with self._store.transaction() as transaction:
            self._m2._assert_program_scope(envelope.program_id, envelope.trusted_principal)
            member = self._m2._require_member(
                transaction,
                envelope.program_id,
                envelope.trusted_principal,
            )
            if member["program_role"] != "service":
                raise BusinessRuleError(
                    ReasonCode.PRINCIPAL_NOT_ALLOWED,
                    "M4 internal commands require persistent Program role service",
                )
            program = transaction.lock_program(envelope.program_id)
            observed_state_version = int(program["state_version"])
            replay = transaction.reserve_command_receipt(
                command_id=envelope.command_id,
                program_id=envelope.program_id,
                idempotency_key=envelope.idempotency_key,
                command_type=envelope.command_type.value,
                request_hash=request_hash,
                principal_id=envelope.trusted_principal.principal_id,
                source_channel=envelope.source_channel.value,
                state_version_before=observed_state_version,
            )
            if replay is not None:
                return self._m2._receipt_result(replay)
            try:
                mutation = operation(transaction, program)
                event, outbox = self._event_pair(envelope, mutation)
                unchanged_state_version = transaction.commit_auxiliary_change(
                    program_id=envelope.program_id,
                    command_id=envelope.command_id,
                    observed_state_version=observed_state_version,
                    result=mutation.result,
                    event=event,
                    outbox_event=outbox,
                )
                return self._m2._committed(
                    envelope.command_id,
                    unchanged_state_version,
                    mutation.result,
                )
            except BudgetReservationNotFoundError as exc:
                return self._persist_m4_rejection(
                    transaction,
                    envelope,
                    observed_state_version,
                    ReasonCode.NOT_FOUND,
                    M4ReasonCode.RESERVATION_NOT_FOUND,
                    str(exc),
                )
            except BudgetReservationStateError as exc:
                return self._persist_m4_rejection(
                    transaction,
                    envelope,
                    observed_state_version,
                    ReasonCode.STATE_TRANSITION_INVALID,
                    M4ReasonCode.RESERVATION_STATE_INVALID,
                    str(exc),
                )
            except BusinessRuleError as exc:
                details = dict(exc.details)
                m4_value = details.pop("m4_reason_code", None)
                return self._persist_m4_rejection(
                    transaction,
                    envelope,
                    observed_state_version,
                    exc.reason_code,
                    m4_value,
                    exc.message,
                    details=details,
                )

    def _persist_m4_rejection(
        self,
        transaction: M4StateTransaction,
        envelope: M4CommandEnvelope,
        observed_state_version: int,
        reason_code: ReasonCode,
        m4_reason_code: M4ReasonCode | str | None,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> CommandResult:
        m4_value = (
            m4_reason_code.value
            if isinstance(m4_reason_code, M4ReasonCode)
            else m4_reason_code
        )
        result = {
            "message": message,
            **({"m4_reason_code": m4_value} if m4_value else {}),
            **dict(details or {}),
        }
        transaction.reject_reserved_command(
            program_id=envelope.program_id,
            command_id=envelope.command_id,
            reason_code=reason_code.value,
            result=result,
            observed_state_version=observed_state_version,
        )
        return self._m2._rejected(
            envelope.command_id,
            reason_code,
            message,
            state_version=observed_state_version,
            details={key: value for key, value in result.items() if key != "message"},
        )

    @staticmethod
    def _event_pair(
        envelope: M4CommandEnvelope,
        mutation: _M4Mutation,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        event_id = str(uuid4())
        payload = dict(mutation.result)
        event = {
            "event_id": event_id,
            "event_type": mutation.event_type,
            "payload": payload,
            "traceparent": envelope.traceparent,
        }
        outbox = {
            "outbox_event_id": str(uuid4()),
            "domain_event_id": event_id,
            "aggregate_type": mutation.aggregate_type,
            "aggregate_id": mutation.aggregate_id,
            "aggregate_version": mutation.aggregate_version,
            "topic": mutation.event_type,
            "payload": payload,
        }
        return event, outbox


def _uuid_text(value: Any) -> str:
    return str(value if isinstance(value, UUID) else UUID(str(value)))


def _iso(value: Any) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _jsonable(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


__all__ = ("M4StateServiceFacade",)
