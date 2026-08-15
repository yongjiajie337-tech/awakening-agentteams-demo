"""Deterministic M2 State Service command handler.

All business writes enter through :class:`StateService`.  Adapters only build
validated envelopes and never receive database credentials.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from typing import Any
from uuid import UUID, uuid4

from psycopg import Error as PsycopgError, IntegrityError

from .contracts import (
    CommandEnvelope,
    CommandResult,
    CommandStatus,
    CommandType,
    PrincipalType,
    QueryType,
    ReasonCode,
    SourceChannel,
    TrustedPrincipal,
)
from .database import (
    CommandInProgressError,
    DatabaseUnavailableError,
    IdempotencyConflictError,
    PersistenceInvariantError,
    PostgresStateStore,
    ProgramNotFoundError,
    StateTransaction,
    StateVersionConflictError,
)
from .validation import (
    ContractValidationError,
    canonical_json_bytes,
    canonical_sha256,
    validate_command_envelope,
    validate_query_payload,
)


@dataclass(frozen=True, slots=True)
class BootstrapMember:
    """Server-owned Program membership added during local M2 bootstrap."""

    principal_id: str
    principal_type: PrincipalType
    program_role: str


class BusinessRuleError(RuntimeError):
    def __init__(
        self,
        reason_code: ReasonCode,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.reason_code = reason_code
        self.message = message
        self.details = dict(details or {})
        super().__init__(message)


class StateService:
    """Authoritative command registry and deterministic business state machine."""

    def __init__(
        self,
        store: PostgresStateStore,
        *,
        bootstrap_members: tuple[BootstrapMember, ...] = (),
        approval_ttl: timedelta = timedelta(minutes=30),
    ) -> None:
        if approval_ttl <= timedelta(0):
            raise ValueError("approval_ttl must be positive")
        self._store = store
        self._bootstrap_members = tuple(bootstrap_members)
        self._approval_ttl = approval_ttl
        self._handlers = {
            CommandType.PROGRAM_CREATE: self._create_program,
            CommandType.STATE_PROPOSAL_SUBMIT: self._submit_proposal,
            CommandType.HUMAN_DECISION_RECORD: self._record_human_decision,
            CommandType.STATE_PROPOSAL_APPLY: self._apply_proposal,
            CommandType.APPROVAL_EXPIRE: self._expire_approval,
        }

    @property
    def registered_commands(self) -> frozenset[str]:
        return frozenset(command.value for command in self._handlers)

    def dispatch(self, envelope: CommandEnvelope) -> CommandResult:
        """Validate and execute one command without any LLM decision path."""

        try:
            validate_command_envelope(envelope)
            self._assert_entrypoint(envelope)
            handler = self._handlers.get(envelope.command_type)
            if handler is None:
                raise BusinessRuleError(
                    ReasonCode.COMMAND_NOT_REGISTERED,
                    "command is not registered in M2",
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
        except StateVersionConflictError as exc:
            return self._rejected(
                envelope.command_id,
                ReasonCode.VERSION_CONFLICT,
                str(exc),
                details={"expected": exc.expected, "actual": exc.actual},
            )
        except ProgramNotFoundError as exc:
            return self._rejected(envelope.command_id, ReasonCode.NOT_FOUND, str(exc))
        except IdempotencyConflictError as exc:
            return self._rejected(
                envelope.command_id,
                ReasonCode.IDEMPOTENCY_KEY_REUSED,
                str(exc),
            )
        except CommandInProgressError as exc:
            return self._rejected(envelope.command_id, ReasonCode.CONFLICT, str(exc))
        except DatabaseUnavailableError as exc:
            return self._rejected(
                envelope.command_id,
                ReasonCode.DATABASE_UNAVAILABLE,
                str(exc),
            )
        except IntegrityError as exc:
            return self._rejected(
                envelope.command_id,
                ReasonCode.CONFLICT,
                "database constraint rejected the command",
                details={"sqlstate": exc.sqlstate},
            )
        except PersistenceInvariantError as exc:
            return self._rejected(
                envelope.command_id,
                ReasonCode.TRANSACTION_ABORTED,
                str(exc),
            )
        except PsycopgError:
            return self._rejected(
                envelope.command_id,
                ReasonCode.TRANSACTION_ABORTED,
                "PostgreSQL aborted the authoritative transaction",
            )

    def query(
        self,
        *,
        query_type: QueryType | str,
        program_id: str,
        payload: Mapping[str, Any],
        trusted_principal: TrustedPrincipal,
    ) -> dict[str, Any]:
        """Run a strict read through the same Program authorization boundary."""

        validate_query_payload(query_type, payload)
        query = QueryType(query_type)
        self._assert_program_scope(program_id, trusted_principal)
        with self._store.transaction() as transaction:
            transaction.get_snapshot(program_id)
            self._require_member(transaction, program_id, trusted_principal)
            if query is QueryType.PROGRAM_SNAPSHOT_GET:
                result = transaction.get_snapshot(program_id)
            elif query is QueryType.APPROVAL_GET:
                result = transaction.get_approval_request(
                    program_id,
                    payload["approval_request_id"],
                )
            elif query is QueryType.DECISION_GET:
                result = transaction.get_human_decision(
                    program_id,
                    payload["human_decision_id"],
                )
            else:
                result = transaction.get_command_receipt_by_id(
                    program_id,
                    payload["command_id"],
                )
            if result is None:
                raise BusinessRuleError(ReasonCode.NOT_FOUND, "object not found")
        return _jsonable(result)

    def _create_program(self, envelope: CommandEnvelope) -> CommandResult:
        # Program identifiers are server-routed scope, including on creation.
        # An empty scope is never a wildcard.
        self._assert_program_scope(
            envelope.program_id,
            envelope.trusted_principal,
        )
        payload = _plain_json(envelope.payload)
        plan = payload["plan"]
        plan_version_id = str(uuid4())
        request_hash = self._request_hash(envelope)
        event, outbox = self._event_pair(
            envelope,
            "program.created",
            {"plan_version_id": plan_version_id, "plan_version_no": 1},
        )

        with self._store.transaction() as transaction:
            replay = transaction.reserve_command_receipt(
                command_id=envelope.command_id,
                program_id=envelope.program_id,
                idempotency_key=envelope.idempotency_key,
                command_type=envelope.command_type.value,
                request_hash=request_hash,
                principal_id=envelope.trusted_principal.principal_id,
                source_channel=envelope.source_channel.value,
                state_version_before=None,
            )
            if replay is not None:
                return self._receipt_result(replay)

            transaction.create_program(
                {
                    "program_id": envelope.program_id,
                    "owner_principal_id": envelope.trusted_principal.principal_id,
                    "state_version": 0,
                    "auto_change_policy": payload["auto_change_policy"],
                }
            )
            transaction.add_program_member(
                {
                    "program_id": envelope.program_id,
                    "principal_id": envelope.trusted_principal.principal_id,
                    "principal_type": PrincipalType.USER.value,
                    "program_role": "owner",
                }
            )
            for member in self._bootstrap_members:
                if member.principal_id == envelope.trusted_principal.principal_id:
                    continue
                transaction.add_program_member(
                    {
                        "program_id": envelope.program_id,
                        "principal_id": member.principal_id,
                        "principal_type": member.principal_type.value,
                        "program_role": member.program_role,
                    }
                )

            transaction.insert_plan_version(
                {
                    "plan_version_id": plan_version_id,
                    "program_id": envelope.program_id,
                    "version_no": 1,
                    "change_risk": "bootstrap",
                    "content": plan,
                    "content_hash": canonical_sha256(plan),
                    "created_by_principal_id": envelope.trusted_principal.principal_id,
                }
            )
            transaction.insert_tasks(
                self._task_records(envelope.program_id, plan_version_id, plan)
            )
            result = {
                "program_id": envelope.program_id,
                "active_plan_version_id": plan_version_id,
                "plan_version_no": 1,
            }
            state_version = transaction.commit_state_change(
                program_id=envelope.program_id,
                command_id=envelope.command_id,
                expected_state_version=0,
                active_plan_version_id=plan_version_id,
                result=result,
                events=[event],
                outbox_events=[outbox],
            )
        return self._committed(envelope.command_id, state_version, result)

    def _submit_proposal(self, envelope: CommandEnvelope) -> CommandResult:
        def operation(
            transaction: StateTransaction,
            program: Mapping[str, Any],
        ) -> tuple[int, Mapping[str, Any]]:
            active_plan_id = _uuid_text(program["active_plan_version_id"])
            if active_plan_id != envelope.base_plan_version_id:
                raise BusinessRuleError(
                    ReasonCode.VERSION_CONFLICT,
                    "base plan is no longer active",
                )
            base = transaction.get_plan_version(envelope.program_id, active_plan_id)
            if base is None:
                raise BusinessRuleError(ReasonCode.NOT_FOUND, "active plan not found")

            payload = _plain_json(envelope.payload)
            change_type = payload["change_type"]
            change = payload["change"]
            proposed_plan = self._apply_change(
                transaction,
                envelope.program_id,
                base["plan"]["content"],
                change_type,
                change,
            )
            diff = {
                "change_type": change_type,
                "base_plan_version_id": active_plan_id,
                "before_hash": base["plan"]["content_hash"],
                "after_hash": canonical_sha256(proposed_plan),
            }
            diff_hash = canonical_sha256(diff)
            is_low = (
                change_type == "reorder_tasks"
                and bool(program["auto_change_policy"].get("allow_reorder_tasks"))
            )
            risk = "low" if is_low else "high"
            proposal_id = str(uuid4())
            proposal_status = "pending" if is_low else "approval_required"
            transaction.insert_proposal(
                {
                    "proposal_id": proposal_id,
                    "program_id": envelope.program_id,
                    "base_state_version": envelope.expected_state_version,
                    "base_plan_version_id": active_plan_id,
                    "change_type": change_type,
                    "risk_level": risk,
                    "status": proposal_status,
                    "change_payload": change,
                    "diff": diff,
                    "diff_hash": diff_hash,
                    "proposed_by_principal_id": envelope.trusted_principal.principal_id,
                }
            )

            approval_request_id: str | None = None
            if not is_low:
                approval_request_id = str(uuid4())
                database_now = transaction.execute_scalar("SELECT clock_timestamp()")
                transaction.insert_approval_request(
                    {
                        "approval_request_id": approval_request_id,
                        "program_id": envelope.program_id,
                        "proposal_id": proposal_id,
                        "base_state_version": envelope.expected_state_version,
                        "base_plan_version_id": active_plan_id,
                        "diff_hash": diff_hash,
                        "status": "pending",
                        "expires_at": database_now + self._approval_ttl,
                    }
                )

            result = {
                "proposal_id": proposal_id,
                "risk_level": risk,
                "status": proposal_status,
                "diff_hash": diff_hash,
                "approval_request_id": approval_request_id,
            }
            event, outbox = self._event_pair(
                envelope,
                "state.proposal.submitted",
                result,
            )
            version = transaction.commit_state_change(
                program_id=envelope.program_id,
                command_id=envelope.command_id,
                expected_state_version=envelope.expected_state_version,
                result=result,
                events=[event],
                outbox_events=[outbox],
            )
            return version, result

        return self._run_existing_program_command(envelope, operation)

    def _record_human_decision(self, envelope: CommandEnvelope) -> CommandResult:
        def operation(
            transaction: StateTransaction,
            program: Mapping[str, Any],
        ) -> tuple[int, Mapping[str, Any]]:
            payload = _plain_json(envelope.payload)
            approval = transaction.get_approval_request(
                envelope.program_id,
                payload["approval_request_id"],
                for_update=True,
            )
            if approval is None:
                raise BusinessRuleError(ReasonCode.NOT_FOUND, "approval request not found")
            if approval["status"] != "pending":
                raise BusinessRuleError(
                    ReasonCode.STATE_TRANSITION_INVALID,
                    "approval request is not pending",
                )
            database_now = transaction.execute_scalar("SELECT clock_timestamp()")
            if database_now >= approval["expires_at"]:
                raise BusinessRuleError(
                    ReasonCode.APPROVAL_EXPIRED,
                    "approval request has expired",
                )
            proposal = transaction.get_proposal(
                envelope.program_id,
                approval["proposal_id"],
                for_update=True,
            )
            if proposal is None or proposal["status"] != "approval_required":
                raise BusinessRuleError(
                    ReasonCode.STATE_TRANSITION_INVALID,
                    "proposal cannot receive a decision",
                )

            decision_id = str(uuid4())
            decision = payload["decision"]
            transaction.insert_human_decision(
                {
                    "human_decision_id": decision_id,
                    "approval_request_id": approval["approval_request_id"],
                    "program_id": envelope.program_id,
                    "proposal_id": approval["proposal_id"],
                    "base_state_version": approval["base_state_version"],
                    "base_plan_version_id": approval["base_plan_version_id"],
                    "diff_hash": approval["diff_hash"],
                    "decision": decision,
                    "decided_by_principal_id": envelope.trusted_principal.principal_id,
                    "reason": payload.get("comment"),
                    "decided_at": database_now,
                    "expires_at": approval["expires_at"],
                }
            )
            approval_status = "approved" if decision == "approve" else "denied"
            proposal_status = "approved" if decision == "approve" else "denied"
            transaction.update_approval_status(
                envelope.program_id,
                approval["approval_request_id"],
                expected_status="pending",
                new_status=approval_status,
            )
            transaction.update_proposal_status(
                envelope.program_id,
                approval["proposal_id"],
                expected_status="approval_required",
                new_status=proposal_status,
            )
            result = {
                "human_decision_id": decision_id,
                "proposal_id": _uuid_text(approval["proposal_id"]),
                "decision": decision,
                "status": approval_status,
            }
            event, outbox = self._event_pair(
                envelope,
                "human.decision.recorded",
                result,
            )
            version = transaction.commit_state_change(
                program_id=envelope.program_id,
                command_id=envelope.command_id,
                expected_state_version=envelope.expected_state_version,
                result=result,
                events=[event],
                outbox_events=[outbox],
            )
            return version, result

        return self._run_existing_program_command(envelope, operation)

    def _apply_proposal(self, envelope: CommandEnvelope) -> CommandResult:
        def operation(
            transaction: StateTransaction,
            program: Mapping[str, Any],
        ) -> tuple[int, Mapping[str, Any]]:
            payload = _plain_json(envelope.payload)
            proposal = transaction.get_proposal(
                envelope.program_id,
                payload["proposal_id"],
                for_update=True,
            )
            if proposal is None:
                raise BusinessRuleError(ReasonCode.NOT_FOUND, "proposal not found")
            if proposal["status"] == "applied":
                raise BusinessRuleError(
                    ReasonCode.DECISION_REPLAYED,
                    "proposal has already been applied",
                )
            if _uuid_text(proposal["base_plan_version_id"]) != envelope.base_plan_version_id:
                raise BusinessRuleError(ReasonCode.DECISION_INVALID, "proposal base mismatch")
            if _uuid_text(program["active_plan_version_id"]) != envelope.base_plan_version_id:
                raise BusinessRuleError(ReasonCode.VERSION_CONFLICT, "active plan changed")

            base = transaction.get_plan_version(
                envelope.program_id,
                proposal["base_plan_version_id"],
            )
            if base is None:
                raise BusinessRuleError(ReasonCode.NOT_FOUND, "base plan not found")
            change = proposal["change_payload"]
            proposed_plan = self._apply_change(
                transaction,
                envelope.program_id,
                base["plan"]["content"],
                proposal["change_type"],
                change,
            )
            expected_diff = {
                "change_type": proposal["change_type"],
                "base_plan_version_id": _uuid_text(proposal["base_plan_version_id"]),
                "before_hash": base["plan"]["content_hash"],
                "after_hash": canonical_sha256(proposed_plan),
            }
            if canonical_sha256(expected_diff) != proposal["diff_hash"]:
                raise BusinessRuleError(ReasonCode.DECISION_INVALID, "proposal diff mismatch")

            decision_id: str | None = None
            approval_id: str | None = None
            if proposal["risk_level"] == "high":
                decision_id = payload.get("human_decision_id")
                if not decision_id:
                    raise BusinessRuleError(
                        ReasonCode.APPROVAL_REQUIRED,
                        "high-risk proposal requires HumanDecision ID",
                    )
                decision = transaction.get_human_decision(
                    envelope.program_id,
                    decision_id,
                    for_update=False,
                )
                if decision is None:
                    raise BusinessRuleError(ReasonCode.DECISION_INVALID, "decision not found")
                if (
                    decision["decision"] != "approve"
                    or _uuid_text(decision["proposal_id"]) != _uuid_text(proposal["proposal_id"])
                    or _uuid_text(decision["base_plan_version_id"])
                    != _uuid_text(proposal["base_plan_version_id"])
                    or decision["diff_hash"] != proposal["diff_hash"]
                ):
                    raise BusinessRuleError(
                        ReasonCode.DECISION_INVALID,
                        "HumanDecision is not bound to this proposal",
                    )
                database_now = transaction.execute_scalar("SELECT clock_timestamp()")
                if database_now >= decision["expires_at"]:
                    raise BusinessRuleError(ReasonCode.APPROVAL_EXPIRED, "decision expired")
                approval_id = _uuid_text(decision["approval_request_id"])
                approval = transaction.get_approval_request(
                    envelope.program_id,
                    approval_id,
                    for_update=True,
                )
                if approval is None or approval["status"] != "approved":
                    raise BusinessRuleError(ReasonCode.DECISION_INVALID, "approval is not valid")
                expected_proposal_status = "approved"
            else:
                if payload.get("human_decision_id") is not None:
                    raise BusinessRuleError(
                        ReasonCode.DECISION_INVALID,
                        "low-risk proposal must not carry a HumanDecision",
                    )
                if not bool(program["auto_change_policy"].get("allow_reorder_tasks")):
                    raise BusinessRuleError(ReasonCode.APPROVAL_REQUIRED, "low-risk policy revoked")
                expected_proposal_status = "pending"

            new_plan_id = str(uuid4())
            version_no = int(base["plan"]["version_no"]) + 1
            is_restore = proposal["change_type"] == "restore_plan"
            target_id = (
                _uuid_text(change["target_plan_version_id"])
                if is_restore
                else None
            )
            transaction.insert_plan_version(
                {
                    "plan_version_id": new_plan_id,
                    "program_id": envelope.program_id,
                    "version_no": version_no,
                    "previous_plan_version_id": proposal["base_plan_version_id"],
                    "base_state_version": proposal["base_state_version"],
                    "base_plan_version_id": proposal["base_plan_version_id"],
                    "proposal_id": proposal["proposal_id"],
                    "human_decision_id": decision_id,
                    "change_risk": proposal["risk_level"],
                    "content": proposed_plan,
                    "content_hash": canonical_sha256(proposed_plan),
                    "diff_hash": proposal["diff_hash"],
                    "rolled_back_from": proposal["base_plan_version_id"] if is_restore else None,
                    "restored_from": target_id,
                    "created_by_principal_id": envelope.trusted_principal.principal_id,
                }
            )
            transaction.insert_tasks(
                self._task_records(envelope.program_id, new_plan_id, proposed_plan)
            )
            transaction.update_proposal_status(
                envelope.program_id,
                proposal["proposal_id"],
                expected_status=expected_proposal_status,
                new_status="applied",
            )
            if approval_id is not None:
                transaction.update_approval_status(
                    envelope.program_id,
                    approval_id,
                    expected_status="approved",
                    new_status="consumed",
                )
            result = {
                "proposal_id": _uuid_text(proposal["proposal_id"]),
                "plan_version_id": new_plan_id,
                "plan_version_no": version_no,
                "risk_level": proposal["risk_level"],
                "restored_from": target_id,
            }
            event_type = "plan.version.compensated" if is_restore else "plan.version.created"
            event, outbox = self._event_pair(envelope, event_type, result)
            version = transaction.commit_state_change(
                program_id=envelope.program_id,
                command_id=envelope.command_id,
                expected_state_version=envelope.expected_state_version,
                active_plan_version_id=new_plan_id,
                result=result,
                events=[event],
                outbox_events=[outbox],
            )
            return version, result

        return self._run_existing_program_command(envelope, operation)

    def _expire_approval(self, envelope: CommandEnvelope) -> CommandResult:
        def operation(
            transaction: StateTransaction,
            program: Mapping[str, Any],
        ) -> tuple[int, Mapping[str, Any]]:
            payload = _plain_json(envelope.payload)
            approval = transaction.get_approval_request(
                envelope.program_id,
                payload["approval_request_id"],
                for_update=True,
            )
            if approval is None:
                raise BusinessRuleError(ReasonCode.NOT_FOUND, "approval request not found")
            if approval["status"] != "pending":
                raise BusinessRuleError(
                    ReasonCode.STATE_TRANSITION_INVALID,
                    "approval request is not pending",
                )
            database_now = transaction.execute_scalar("SELECT clock_timestamp()")
            if database_now < approval["expires_at"]:
                raise BusinessRuleError(
                    ReasonCode.STATE_TRANSITION_INVALID,
                    "approval request has not expired",
                )
            transaction.update_approval_status(
                envelope.program_id,
                approval["approval_request_id"],
                expected_status="pending",
                new_status="expired",
            )
            transaction.update_proposal_status(
                envelope.program_id,
                approval["proposal_id"],
                expected_status="approval_required",
                new_status="expired",
            )
            result = {
                "approval_request_id": _uuid_text(approval["approval_request_id"]),
                "proposal_id": _uuid_text(approval["proposal_id"]),
                "status": "expired",
            }
            event, outbox = self._event_pair(envelope, "approval.expired", result)
            version = transaction.commit_state_change(
                program_id=envelope.program_id,
                command_id=envelope.command_id,
                expected_state_version=envelope.expected_state_version,
                result=result,
                events=[event],
                outbox_events=[outbox],
            )
            return version, result

        return self._run_existing_program_command(envelope, operation)

    def _run_existing_program_command(
        self,
        envelope: CommandEnvelope,
        operation: Any,
    ) -> CommandResult:
        request_hash = self._request_hash(envelope)
        with self._store.transaction() as transaction:
            # Authorization must precede receipt reservation.  Otherwise an
            # unauthorized caller can occupy a legitimate idempotency key.
            self._assert_program_scope(
                envelope.program_id,
                envelope.trusted_principal,
            )
            member = self._require_member(
                transaction,
                envelope.program_id,
                envelope.trusted_principal,
            )
            self._assert_command_role(envelope, member)
            replay = transaction.reserve_command_receipt(
                command_id=envelope.command_id,
                program_id=envelope.program_id,
                idempotency_key=envelope.idempotency_key,
                command_type=envelope.command_type.value,
                request_hash=request_hash,
                principal_id=envelope.trusted_principal.principal_id,
                source_channel=envelope.source_channel.value,
                state_version_before=envelope.expected_state_version,
            )
            if replay is not None:
                return self._receipt_result(replay)
            try:
                program = transaction.lock_program(
                    envelope.program_id,
                    envelope.expected_state_version,
                )
                state_version, result = operation(transaction, program)
                return self._committed(envelope.command_id, state_version, result)
            except StateVersionConflictError as exc:
                result = {"message": str(exc), "expected": exc.expected, "actual": exc.actual}
                transaction.reject_reserved_command(
                    program_id=envelope.program_id,
                    command_id=envelope.command_id,
                    reason_code=ReasonCode.VERSION_CONFLICT.value,
                    result=result,
                    observed_state_version=exc.actual,
                )
                return self._rejected(
                    envelope.command_id,
                    ReasonCode.VERSION_CONFLICT,
                    str(exc),
                    state_version=exc.actual,
                    details={"expected": exc.expected, "actual": exc.actual},
                )
            except BusinessRuleError as exc:
                transaction.reject_reserved_command(
                    program_id=envelope.program_id,
                    command_id=envelope.command_id,
                    reason_code=exc.reason_code.value,
                    result={"message": exc.message, **exc.details},
                    observed_state_version=int(program["state_version"]),
                )
                return self._rejected(
                    envelope.command_id,
                    exc.reason_code,
                    exc.message,
                    state_version=int(program["state_version"]),
                    details=exc.details,
                )

    def _assert_entrypoint(self, envelope: CommandEnvelope) -> None:
        principal_type = envelope.trusted_principal.principal_type
        channel = envelope.source_channel
        if envelope.command_type is CommandType.PROGRAM_CREATE:
            allowed = channel is SourceChannel.WEB and principal_type is PrincipalType.USER
        elif envelope.command_type is CommandType.HUMAN_DECISION_RECORD:
            allowed = channel is SourceChannel.WEB and principal_type is PrincipalType.USER
        elif envelope.command_type is CommandType.APPROVAL_EXPIRE:
            allowed = (
                channel is SourceChannel.INTERNAL
                and principal_type in {PrincipalType.SERVICE, PrincipalType.SYSTEM}
            )
        else:
            allowed = (
                (channel is SourceChannel.WEB and principal_type is PrincipalType.USER)
                or (channel is SourceChannel.MCP and principal_type is PrincipalType.AGENT)
                or (channel is SourceChannel.INTERNAL and principal_type is PrincipalType.SERVICE)
            )
        if not allowed:
            raise BusinessRuleError(
                ReasonCode.PRINCIPAL_NOT_ALLOWED,
                "principal type is not allowed on this trusted adapter channel",
            )

    @staticmethod
    def _assert_program_scope(program_id: str, principal: TrustedPrincipal) -> None:
        if program_id not in principal.program_scope:
            raise BusinessRuleError(
                ReasonCode.PROGRAM_SCOPE_DENIED,
                "trusted principal is outside the Program scope",
            )

    @staticmethod
    def _assert_command_role(
        envelope: CommandEnvelope,
        member: Mapping[str, Any],
    ) -> None:
        if (
            envelope.command_type is CommandType.HUMAN_DECISION_RECORD
            and member["program_role"] != "owner"
        ):
            raise BusinessRuleError(
                ReasonCode.PRINCIPAL_NOT_ALLOWED,
                "only the Program owner may record a HumanDecision in M2",
            )

    @staticmethod
    def _require_member(
        transaction: StateTransaction,
        program_id: str,
        principal: TrustedPrincipal,
    ) -> Mapping[str, Any]:
        member = transaction.get_program_member(program_id, principal.principal_id)
        if member is None:
            raise BusinessRuleError(
                ReasonCode.PROGRAM_SCOPE_DENIED,
                "principal is not a Program member",
            )
        if member["principal_type"] != principal.principal_type.value:
            raise BusinessRuleError(
                ReasonCode.PRINCIPAL_NOT_ALLOWED,
                "principal type does not match persistent Program membership",
            )
        return member

    def _apply_change(
        self,
        transaction: StateTransaction,
        program_id: str,
        base_content: Mapping[str, Any],
        change_type: str,
        change: Mapping[str, Any],
    ) -> dict[str, Any]:
        result = _plain_json(base_content)
        if change_type == "reorder_tasks":
            requested = list(change["task_order"])
            tasks = list(result["tasks"])
            by_key = {task["task_key"]: task for task in tasks}
            if set(requested) != set(by_key) or len(requested) != len(tasks):
                raise BusinessRuleError(
                    ReasonCode.STATE_TRANSITION_INVALID,
                    "reorder_tasks must contain every current task exactly once",
                )
            result["tasks"] = [
                {**by_key[key], "order": position}
                for position, key in enumerate(requested)
            ]
        elif change_type == "change_target_role":
            result["target_role"] = change["target_role"]
        elif change_type == "replace_core_project":
            result["core_project"] = change["core_project"]
        elif change_type == "change_duration":
            result["duration_weeks"] = change["duration_weeks"]
        elif change_type == "change_rubric":
            result["rubric"] = change["rubric"]
        elif change_type == "restore_plan":
            restored = transaction.get_plan_version(
                program_id,
                change["target_plan_version_id"],
            )
            if restored is None:
                raise BusinessRuleError(ReasonCode.NOT_FOUND, "restore target not found")
            result = _plain_json(restored["plan"]["content"])
        else:
            raise BusinessRuleError(
                ReasonCode.COMMAND_NOT_REGISTERED,
                "change type is not registered in M2",
            )
        return result

    @staticmethod
    def _task_records(
        program_id: str,
        plan_version_id: str,
        plan: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        return [
            {
                "task_id": str(uuid4()),
                "program_id": program_id,
                "plan_version_id": plan_version_id,
                "task_key": task["task_key"],
                "position": task["order"],
                "title": task["title"],
                "task_payload": {},
            }
            for task in plan["tasks"]
        ]

    @staticmethod
    def _event_pair(
        envelope: CommandEnvelope,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        event_id = str(uuid4())
        event = {
            "event_id": event_id,
            "event_type": event_type,
            "payload": _plain_json(payload),
            "traceparent": envelope.traceparent,
        }
        outbox = {
            "outbox_event_id": str(uuid4()),
            "domain_event_id": event_id,
            "aggregate_type": "program",
            "aggregate_id": envelope.program_id,
            "topic": event_type,
            "payload": _plain_json(payload),
        }
        return event, outbox

    @staticmethod
    def _request_hash(envelope: CommandEnvelope) -> str:
        return canonical_sha256(
            {
                "program_id": envelope.program_id,
                "command_type": envelope.command_type.value,
                "source_channel": envelope.source_channel.value,
                "trusted_principal": envelope.trusted_principal.to_dict(),
                "expected_state_version": envelope.expected_state_version,
                "base_plan_version_id": envelope.base_plan_version_id,
                "payload": _plain_json(envelope.payload),
            }
        )

    @staticmethod
    def _receipt_result(receipt: Mapping[str, Any]) -> CommandResult:
        status = (
            CommandStatus.COMMITTED
            if receipt["status"] == "committed"
            else CommandStatus.REJECTED
        )
        reason = ReasonCode(receipt["reason_code"] or ReasonCode.OK.value)
        return CommandResult(
            command_id=_uuid_text(receipt["command_id"]),
            status=status,
            reason_code=reason,
            state_version=receipt["state_version_after"],
            result=_jsonable(receipt["result"] or {}),
            replayed=True,
        )

    @staticmethod
    def _committed(
        command_id: str,
        state_version: int,
        result: Mapping[str, Any],
    ) -> CommandResult:
        return CommandResult(
            command_id=command_id,
            status=CommandStatus.COMMITTED,
            reason_code=ReasonCode.OK,
            state_version=state_version,
            result=_jsonable(result),
        )

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
        text = value.astimezone(timezone.utc).isoformat()
        return text.replace("+00:00", "Z")
    return value


__all__ = (
    "BootstrapMember",
    "BusinessRuleError",
    "StateService",
)
