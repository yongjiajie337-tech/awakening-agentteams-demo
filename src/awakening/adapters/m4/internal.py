"""Internal-only adapter for M4 runtime configuration and model accounting."""

from __future__ import annotations

from awakening.state.contracts import (
    CommandResult,
    CommandStatus,
    SourceChannel,
    TrustedPrincipal,
)
from awakening.state.service import BusinessRuleError
from awakening.state.m4 import (
    M4CommandEnvelope,
    M4CommandType,
    M4StateServiceFacade,
    ModelBudgetRequest,
    RuntimeConfigSpec,
    TrustedProviderUsageReceipt,
    settlement_idempotency_key,
)


class M4InternalStateAdapter:
    """Build typed internal envelopes; it has no direct database access."""

    def __init__(self, state_service: M4StateServiceFacade) -> None:
        self._state_service = state_service

    def create_runtime_config_snapshot(
        self,
        *,
        program_id: str,
        idempotency_key: str,
        runtime_config: RuntimeConfigSpec,
        trusted_context: TrustedPrincipal,
        traceparent: str | None = None,
    ) -> CommandResult:
        return self._state_service.dispatch(
            M4CommandEnvelope.new(
                idempotency_key=idempotency_key,
                program_id=program_id,
                command_type=M4CommandType.RUNTIME_CONFIG_SNAPSHOT_CREATE,
                trusted_principal=trusted_context,
                source_channel=SourceChannel.INTERNAL,
                runtime_config=runtime_config,
                traceparent=traceparent,
            )
        )

    def get_runtime_config_snapshot(
        self,
        *,
        program_id: str,
        snapshot_id: str,
        trusted_context: TrustedPrincipal,
    ) -> dict[str, object]:
        try:
            return self._state_service.get_runtime_config_snapshot(
                program_id=program_id,
                snapshot_id=snapshot_id,
                trusted_principal=trusted_context,
            )
        except BusinessRuleError as exc:
            return self._query_rejection(exc)

    def reserve_model_budget(
        self,
        *,
        program_id: str,
        idempotency_key: str,
        budget_request: ModelBudgetRequest,
        trusted_context: TrustedPrincipal,
        traceparent: str | None = None,
    ) -> CommandResult:
        return self._state_service.dispatch(
            M4CommandEnvelope.new(
                idempotency_key=idempotency_key,
                program_id=program_id,
                command_type=M4CommandType.MODEL_BUDGET_RESERVE,
                trusted_principal=trusted_context,
                source_channel=SourceChannel.INTERNAL,
                budget_request=budget_request,
                traceparent=traceparent,
            )
        )

    def get_model_budget_reservation(
        self,
        *,
        program_id: str,
        reservation_id: str,
        trusted_context: TrustedPrincipal,
    ) -> dict[str, object]:
        try:
            return self._state_service.get_model_budget_reservation(
                program_id=program_id,
                reservation_id=reservation_id,
                trusted_principal=trusted_context,
            )
        except BusinessRuleError as exc:
            return self._query_rejection(exc)

    def settle_model_budget(
        self,
        *,
        program_id: str,
        usage_receipt: TrustedProviderUsageReceipt,
        trusted_context: TrustedPrincipal,
        idempotency_key: str | None = None,
        traceparent: str | None = None,
    ) -> CommandResult:
        command_key = idempotency_key or settlement_idempotency_key(
            usage_receipt.receipt_id
        )
        return self._state_service.dispatch(
            M4CommandEnvelope.new(
                idempotency_key=command_key,
                program_id=program_id,
                command_type=M4CommandType.MODEL_BUDGET_SETTLE,
                trusted_principal=trusted_context,
                source_channel=SourceChannel.INTERNAL,
                usage_receipt=usage_receipt,
                traceparent=traceparent,
            )
        )

    @staticmethod
    def _query_rejection(exc: BusinessRuleError) -> dict[str, object]:
        return {
            "status": CommandStatus.REJECTED.value,
            "reason_code": exc.reason_code.value,
            "result": {"message": exc.message, **exc.details},
        }


__all__ = ("M4InternalStateAdapter",)
