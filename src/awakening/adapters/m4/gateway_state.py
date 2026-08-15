"""Bind the Model Gateway to State Service without granting database access."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from awakening.state.contracts import PrincipalType, TrustedPrincipal
from awakening.state.m4 import TrustedProviderUsageReceipt, settlement_idempotency_key

from .internal import M4InternalStateAdapter


GATEWAY_PRINCIPAL_ID = "awakening-m4-model-gateway"


class GatewayStateAuthorityAdapter:
    """A fixed service-principal port used only by the M4 Model Gateway."""

    def __init__(
        self,
        *,
        internal_adapter: M4InternalStateAdapter,
        trusted_gateway_principal: TrustedPrincipal,
    ) -> None:
        if (
            trusted_gateway_principal.principal_type is not PrincipalType.SERVICE
            or trusted_gateway_principal.principal_id != GATEWAY_PRINCIPAL_ID
        ):
            raise ValueError("Gateway State authority requires the fixed service principal")
        self._internal = internal_adapter
        self._principal = trusted_gateway_principal

    def get_runtime_config_snapshot(
        self,
        *,
        snapshot_id: str,
        program_id: str,
        run_id: str,
    ) -> Mapping[str, Any] | None:
        record = self._internal.get_runtime_config_snapshot(
            program_id=program_id,
            snapshot_id=snapshot_id,
            trusted_context=self._principal,
        )
        if record.get("status") == "REJECTED":
            return None
        if str(record.get("run_id")) != run_id:
            return None
        return record

    def get_model_budget_reservation(
        self,
        *,
        reservation_id: str,
        program_id: str,
        run_id: str,
        model_call_id: str,
    ) -> Mapping[str, Any] | None:
        record = self._internal.get_model_budget_reservation(
            program_id=program_id,
            reservation_id=reservation_id,
            trusted_context=self._principal,
        )
        if record.get("status") == "REJECTED":
            return None
        if (
            str(record.get("run_id")) != run_id
            or str(record.get("model_call_id")) != model_call_id
        ):
            return None
        return record

    def settle_model_budget(
        self,
        receipt: TrustedProviderUsageReceipt,
    ) -> Mapping[str, Any]:
        if receipt.issued_by_principal_id != GATEWAY_PRINCIPAL_ID:
            raise ValueError("usage receipt issuer is not the fixed Gateway principal")
        result = self._internal.settle_model_budget(
            program_id=receipt.program_id,
            idempotency_key=settlement_idempotency_key(receipt.receipt_id),
            usage_receipt=receipt,
            trusted_context=self._principal,
        )
        if not result.committed:
            raise RuntimeError(
                f"State Service rejected model settlement: {result.reason_code.value}"
            )
        return dict(result.result)


__all__ = ("GATEWAY_PRINCIPAL_ID", "GatewayStateAuthorityAdapter")
