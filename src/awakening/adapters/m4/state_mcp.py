"""Restricted in-process M4 State MCP / Tool Invocation adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from awakening.state.contracts import (
    CommandResult,
    CommandStatus,
    CommandType,
    QueryType,
    ReasonCode,
    SourceChannel,
)
from awakening.state.m4 import (
    M4StateMcpMethod,
    M4StateServiceFacade,
    TrustedRuntimeContext,
    validate_state_mcp_call,
)
from awakening.state.service import BusinessRuleError
from awakening.state.validation import ContractValidationError, build_command_envelope


class M4StateMcpAdapter:
    """Expose only the four M4 methods with a server-issued Agent context."""

    def __init__(self, state_service: M4StateServiceFacade) -> None:
        self._state_service = state_service

    def get_snapshot(
        self,
        *,
        program_id: str,
        trusted_context: TrustedRuntimeContext,
    ) -> dict[str, Any]:
        method = M4StateMcpMethod.GET_SNAPSHOT
        try:
            validate_state_mcp_call(method, {"program_id": program_id})
            principal = self._state_service.authorize_runtime_method(
                method=method,
                program_id=program_id,
                trusted_context=trusted_context,
            )
            return self._state_service.query(
                query_type=QueryType.PROGRAM_SNAPSHOT_GET,
                program_id=program_id,
                payload={},
                trusted_principal=principal,
            )
        except (ContractValidationError, BusinessRuleError, TypeError, ValueError) as exc:
            return self._query_rejection(exc)

    def get_command_status(
        self,
        *,
        program_id: str,
        command_id: str,
        trusted_context: TrustedRuntimeContext,
    ) -> dict[str, Any]:
        method = M4StateMcpMethod.GET_COMMAND_STATUS
        try:
            validate_state_mcp_call(
                method,
                {"program_id": program_id, "command_id": command_id},
            )
            principal = self._state_service.authorize_runtime_method(
                method=method,
                program_id=program_id,
                trusted_context=trusted_context,
            )
            return self._state_service.query(
                query_type=QueryType.COMMAND_STATUS_GET,
                program_id=program_id,
                payload={"command_id": command_id},
                trusted_principal=principal,
            )
        except (ContractValidationError, BusinessRuleError, TypeError, ValueError) as exc:
            return self._query_rejection(exc)

    def submit_proposal(
        self,
        *,
        program_id: str,
        base_version: Mapping[str, Any],
        proposal_payload: Mapping[str, Any],
        idempotency_key: str,
        trusted_context: TrustedRuntimeContext,
        traceparent: str | None = None,
    ) -> CommandResult:
        method = M4StateMcpMethod.SUBMIT_PROPOSAL
        params: dict[str, Any] = {
            "program_id": program_id,
            "base_version": dict(base_version),
            "proposal_payload": dict(proposal_payload),
            "idempotency_key": idempotency_key,
        }
        if traceparent is not None:
            params["traceparent"] = traceparent
        try:
            validate_state_mcp_call(method, params)
            principal = self._state_service.authorize_runtime_method(
                method=method,
                program_id=program_id,
                trusted_context=trusted_context,
            )
            envelope = build_command_envelope(
                idempotency_key=idempotency_key,
                program_id=program_id,
                command_type=CommandType.STATE_PROPOSAL_SUBMIT,
                payload=proposal_payload,
                trusted_principal=principal,
                source_channel=SourceChannel.MCP,
                expected_state_version=int(base_version["state_version"]),
                base_plan_version_id=str(base_version["plan_version_id"]),
                traceparent=traceparent,
            )
            return self._state_service.dispatch(envelope)
        except (ContractValidationError, BusinessRuleError, TypeError, ValueError) as exc:
            return self._command_rejection(exc)

    def apply_authorized_change(
        self,
        *,
        program_id: str,
        proposal_id: str,
        expected_state_version: int,
        idempotency_key: str,
        trusted_context: TrustedRuntimeContext,
        human_decision_id: str | None = None,
    ) -> CommandResult:
        """Validate ID-only input and fail closed without calling M2 apply."""

        method = M4StateMcpMethod.APPLY_AUTHORIZED_CHANGE
        params: dict[str, Any] = {
            "program_id": program_id,
            "proposal_id": proposal_id,
            "expected_state_version": expected_state_version,
            "idempotency_key": idempotency_key,
        }
        if human_decision_id is not None:
            params["human_decision_id"] = human_decision_id
        try:
            validate_state_mcp_call(method, params)
            self._state_service.authorize_disabled_apply_context(
                program_id=program_id,
                trusted_context=trusted_context,
            )
            # The accepted M2 STATE_PROPOSAL_APPLY handler is intentionally not
            # called.  This is a no-write M4 module gate, including low-risk
            # proposals that M2 would otherwise be able to apply.
            return self._state_service.reject_m4_apply(
                program_id=program_id,
                proposal_id=proposal_id,
                expected_state_version=expected_state_version,
                idempotency_key=idempotency_key,
                human_decision_id=human_decision_id,
            )
        except (ContractValidationError, BusinessRuleError, TypeError, ValueError) as exc:
            return self._command_rejection(exc)

    @staticmethod
    def _command_rejection(exc: Exception) -> CommandResult:
        if isinstance(exc, ContractValidationError):
            reason = exc.reason_code
            details: dict[str, Any] = {}
        elif isinstance(exc, BusinessRuleError):
            reason = exc.reason_code
            details = exc.details
        else:
            reason = ReasonCode.INVALID_ENVELOPE
            details = {}
        return CommandResult(
            command_id=str(uuid4()),
            status=CommandStatus.REJECTED,
            reason_code=reason,
            result={"message": str(exc), **details},
        )

    @staticmethod
    def _query_rejection(exc: Exception) -> dict[str, Any]:
        result = M4StateMcpAdapter._command_rejection(exc).to_dict()
        result.pop("command_id", None)
        result.pop("replayed", None)
        return result


__all__ = ("M4StateMcpAdapter",)
