"""Fail-closed M4 model call ordering."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from awakening.context_manifest.m4.builder import ContextManifestBuilder
from awakening.context_manifest.m4.store import (
    ContextManifestStore,
    InvocationReceiptStore,
    SkillInvocationReceipt,
)
from awakening.state.validation import canonical_json_bytes
from awakening.state.m4.contracts import (
    ProviderCallStatus,
    ProviderUsageStatus,
    TrustedProviderUsageReceipt,
    derive_provider_usage_receipt_id,
)

from .contracts import (
    GatewayReasonCode,
    GatewayResult,
    ModelInvocation,
    ProviderPort,
    ProviderRequest,
    RuntimeAuthorizerPort,
    StateAuthorityPort,
    thaw,
)


_SERVER_OWNED_PROVIDER_FIELDS = frozenset(
    {
        "model",
        "max_tokens",
        "max_completion_tokens",
        "temperature",
        "seed",
        "enable_thinking",
        "response_format",
    }
)
_TRUSTED_RUNTIME_PARAMETER_FIELDS = frozenset(
    {"temperature", "seed", "enable_thinking", "response_format"}
)


def _trusted_runtime_parameters_match(value: object) -> bool:
    if not isinstance(value, Mapping) or set(value) != _TRUSTED_RUNTIME_PARAMETER_FIELDS:
        return False
    temperature = value["temperature"]
    seed = value["seed"]
    response_format = value["response_format"]
    return (
        not isinstance(temperature, bool)
        and isinstance(temperature, (int, float))
        and float(temperature) == 0.01
        and not isinstance(seed, bool)
        and isinstance(seed, int)
        and seed == 0
        and value["enable_thinking"] is False
        and isinstance(response_format, Mapping)
        and set(response_format) == {"type"}
        and response_format["type"] == "json_object"
    )


class M4ModelGateway:
    """The only component holding Provider transport authority in M4."""

    def __init__(
        self,
        *,
        state_authority: StateAuthorityPort,
        manifest_store: ContextManifestStore,
        invocation_receipt_store: InvocationReceiptStore,
        provider: ProviderPort,
        runtime_authorizer: RuntimeAuthorizerPort,
        manifest_builder: ContextManifestBuilder | None = None,
    ) -> None:
        self._state = state_authority
        self._manifests = manifest_store
        self._receipts = invocation_receipt_store
        self._provider = provider
        self._authorizer = runtime_authorizer
        self._builder = manifest_builder or ContextManifestBuilder()

    def invoke(self, invocation: ModelInvocation) -> GatewayResult:
        authorization = self._authorizer.authorize_model_call(invocation)
        if authorization is not GatewayReasonCode.OK:
            return self._reject(invocation, authorization)

        snapshot = self._state.get_runtime_config_snapshot(
            snapshot_id=invocation.runtime_config_snapshot_id,
            program_id=invocation.program_id,
            run_id=invocation.run_id,
        )
        if snapshot is None:
            return self._reject(invocation, GatewayReasonCode.SNAPSHOT_NOT_COMMITTED)
        try:
            snapshot_matches = self._snapshot_matches(invocation, snapshot)
        except (TypeError, ValueError):
            snapshot_matches = False
        if not snapshot_matches:
            return self._reject(invocation, GatewayReasonCode.PRECALL_BINDING_MISMATCH)

        reservation = self._state.get_model_budget_reservation(
            reservation_id=invocation.reservation_id,
            program_id=invocation.program_id,
            run_id=invocation.run_id,
            model_call_id=invocation.model_call_id,
        )
        if reservation is None or reservation.get("status") != "reserved":
            return self._reject(invocation, GatewayReasonCode.RESERVATION_NOT_COMMITTED)
        try:
            reservation_matches = self._reservation_matches(invocation, reservation)
        except (TypeError, ValueError):
            reservation_matches = False
        if not reservation_matches:
            return self._reject(invocation, GatewayReasonCode.PRECALL_BINDING_MISMATCH)

        if str(snapshot.get("provider_alias")) != self._provider.provider_alias:
            return self._reject(invocation, GatewayReasonCode.PROVIDER_NOT_CONFIGURED)
        try:
            provider_document = self._build_provider_document(
                invocation=invocation,
                snapshot=snapshot,
                reservation=reservation,
            )
        except (KeyError, TypeError, ValueError):
            return self._reject(invocation, GatewayReasonCode.PROVIDER_INPUT_INVALID)
        provider_wire_bytes = canonical_json_bytes(provider_document)
        # A provider token must consume at least one encoded byte. Reserving one
        # input token for every final UTF-8 wire byte therefore over-reserves
        # without trusting or downloading a provider tokenizer.
        if len(provider_wire_bytes) > int(reservation["max_input_tokens"]):
            return self._reject(invocation, GatewayReasonCode.PROVIDER_INPUT_INVALID)
        request_sha256 = sha256(provider_wire_bytes).hexdigest()

        try:
            manifest = self._builder.build(
                program_id=invocation.program_id,
                run_id=invocation.run_id,
                model_call_id=invocation.model_call_id,
                runtime_config_snapshot_id=invocation.runtime_config_snapshot_id,
                reservation_id=invocation.reservation_id,
                agent_identity_id=invocation.agent_identity_id,
                agent_identity_version=invocation.agent_identity_version,
                skill_name=invocation.skill_name,
                skill_version=invocation.skill_version,
                provider_input=provider_document,
                object_refs=invocation.object_refs,
                exclusions=invocation.exclusions,
            )
            persisted_manifest = self._manifests.append(manifest)
        except Exception as exc:
            return self._reject(
                invocation,
                GatewayReasonCode.CONTEXT_MANIFEST_NOT_COMMITTED,
                details={"error_type": type(exc).__name__},
            )

        if not self._manifest_matches(manifest.to_dict(), persisted_manifest):
            return self._reject(
                invocation,
                GatewayReasonCode.CONTEXT_MANIFEST_NOT_COMMITTED,
                context_manifest_id=manifest.context_manifest_id,
            )

        request = ProviderRequest(
            provider_alias=str(snapshot["provider_alias"]),
            model_id=str(snapshot["model_id"]),
            model_call_id=invocation.model_call_id,
            request_sha256=request_sha256,
            input_document=provider_document,
        )
        try:
            response = self._provider.invoke(request)
        except Exception as exc:
            try:
                settled = self._state.settle_model_budget(
                    self._usage_receipt(
                        invocation=invocation,
                        snapshot=snapshot,
                        request_sha256=request_sha256,
                        provider_status=ProviderCallStatus.UNKNOWN,
                        usage_status=ProviderUsageStatus.UNKNOWN,
                    )
                )
            except Exception as settlement_exc:
                return self._reject(
                    invocation,
                    GatewayReasonCode.USAGE_SETTLEMENT_FAILED,
                    context_manifest_id=manifest.context_manifest_id,
                    details={
                        "provider_error_type": type(exc).__name__,
                        "settlement_error_type": type(settlement_exc).__name__,
                        "do_not_retry_provider": True,
                    },
                )
            if settled.get("status") != "conservative_settled":
                return self._reject(
                    invocation,
                    GatewayReasonCode.USAGE_SETTLEMENT_FAILED,
                    context_manifest_id=manifest.context_manifest_id,
                    details={
                        "provider_error_type": type(exc).__name__,
                        "do_not_retry_provider": True,
                    },
                )
            return self._reject(
                invocation,
                GatewayReasonCode.PROVIDER_TRANSPORT_FAILED,
                context_manifest_id=manifest.context_manifest_id,
                details={
                    "error_type": type(exc).__name__,
                    "budget_status": "conservative_settled",
                },
            )

        usage = self._usage_receipt(
            invocation=invocation,
            snapshot=snapshot,
            request_sha256=request_sha256,
            provider_status=ProviderCallStatus.SUCCEEDED,
            usage_status=ProviderUsageStatus.REPORTED,
            provider_request_id=response.provider_request_id,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost_microunits=response.cost_microunits,
            response_sha256=response.response_sha256,
        )
        try:
            settled = self._state.settle_model_budget(usage)
        except Exception as exc:
            return self._reject(
                invocation,
                GatewayReasonCode.USAGE_SETTLEMENT_FAILED,
                context_manifest_id=manifest.context_manifest_id,
                details={"error_type": type(exc).__name__},
            )
        if settled.get("status") not in {"settled", "conservative_settled"}:
            return self._reject(
                invocation,
                GatewayReasonCode.USAGE_SETTLEMENT_FAILED,
                context_manifest_id=manifest.context_manifest_id,
            )

        # The usage ledger separately binds the raw Provider response bytes.
        # A SkillInvocationReceipt must bind the exact structured assistant
        # output that AgentTeams relays to Matrix, not the outer Provider
        # envelope containing ids, usage and transport metadata.
        output_sha256 = sha256(
            canonical_json_bytes(thaw(response.skill_output_document))
        ).hexdigest()
        receipt = SkillInvocationReceipt.create(
            program_id=invocation.program_id,
            run_id=invocation.run_id,
            model_call_id=invocation.model_call_id,
            context_manifest_id=manifest.context_manifest_id,
            reservation_id=invocation.reservation_id,
            agent_identity_id=invocation.agent_identity_id,
            agent_identity_version=invocation.agent_identity_version,
            skill_name=invocation.skill_name,
            skill_version=invocation.skill_version,
            input_sha256=manifest.input_sha256,
            output_sha256=output_sha256,
            status="committed",
        )
        try:
            self._receipts.append(receipt)
        except Exception as exc:
            return self._reject(
                invocation,
                GatewayReasonCode.INVOCATION_RECEIPT_FAILED,
                context_manifest_id=manifest.context_manifest_id,
                details={"error_type": type(exc).__name__},
            )
        return GatewayResult(
            committed=True,
            reason_code=GatewayReasonCode.OK,
            model_call_id=invocation.model_call_id,
            context_manifest_id=manifest.context_manifest_id,
            provider_response=response,
        )

    @staticmethod
    def _snapshot_matches(
        invocation: ModelInvocation,
        snapshot: Mapping[str, Any],
    ) -> bool:
        return (
            str(snapshot.get("snapshot_id"))
            == invocation.runtime_config_snapshot_id
            and str(snapshot.get("program_id")) == invocation.program_id
            and str(snapshot.get("run_id")) == invocation.run_id
            and bool(snapshot.get("provider_alias"))
            and bool(snapshot.get("model_id"))
            and int(snapshot.get("max_output_tokens_per_call", 0)) > 0
            and _trusted_runtime_parameters_match(snapshot.get("parameters"))
        )

    @staticmethod
    def _reservation_matches(
        invocation: ModelInvocation,
        reservation: Mapping[str, Any],
    ) -> bool:
        return (
            str(reservation.get("reservation_id")) == invocation.reservation_id
            and str(reservation.get("snapshot_id"))
            == invocation.runtime_config_snapshot_id
            and str(reservation.get("program_id")) == invocation.program_id
            and str(reservation.get("run_id")) == invocation.run_id
            and str(reservation.get("model_call_id")) == invocation.model_call_id
            and int(reservation.get("max_input_tokens", 0)) > 0
            and int(reservation.get("max_output_tokens", 0)) > 0
        )

    @staticmethod
    def _build_provider_document(
        *,
        invocation: ModelInvocation,
        snapshot: Mapping[str, Any],
        reservation: Mapping[str, Any],
    ) -> dict[str, Any]:
        body = thaw(invocation.provider_input)
        parameters = thaw(snapshot["parameters"])
        if not isinstance(body, dict) or not isinstance(parameters, dict):
            raise ValueError("provider body and parameters must be objects")
        if _SERVER_OWNED_PROVIDER_FIELDS.intersection(body):
            raise ValueError("provider controls are server-owned")
        if not _trusted_runtime_parameters_match(parameters):
            raise ValueError("RuntimeConfig parameters are not the trusted fixed snapshot")
        max_output_tokens = int(reservation["max_output_tokens"])
        if max_output_tokens > int(snapshot["max_output_tokens_per_call"]):
            raise ValueError("reservation exceeds RuntimeConfig output cap")
        return {
            **body,
            "model": str(snapshot["model_id"]),
            "max_tokens": max_output_tokens,
            "temperature": 0.01,
            "seed": 0,
            "enable_thinking": False,
            "response_format": {"type": "json_object"},
        }

    @staticmethod
    def _usage_receipt(
        *,
        invocation: ModelInvocation,
        snapshot: Mapping[str, Any],
        request_sha256: str,
        provider_status: ProviderCallStatus,
        usage_status: ProviderUsageStatus,
        provider_request_id: str | None = None,
        response_sha256: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_microunits: int | None = None,
    ) -> TrustedProviderUsageReceipt:
        receipt_id = derive_provider_usage_receipt_id(
            program_id=invocation.program_id,
            run_id=invocation.run_id,
            model_call_id=invocation.model_call_id,
            reservation_id=invocation.reservation_id,
            request_sha256=request_sha256,
        )
        return TrustedProviderUsageReceipt(
            receipt_id=receipt_id,
            program_id=invocation.program_id,
            run_id=invocation.run_id,
            model_call_id=invocation.model_call_id,
            snapshot_id=invocation.runtime_config_snapshot_id,
            reservation_id=invocation.reservation_id,
            provider_alias=str(snapshot["provider_alias"]),
            provider_request_id=provider_request_id,
            request_sha256=request_sha256,
            response_sha256=response_sha256,
            usage_status=usage_status,
            provider_status=provider_status,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_microunits=cost_microunits,
            issued_by_principal_id="awakening-m4-model-gateway",
            issued_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _manifest_matches(
        expected: Mapping[str, Any],
        actual: Mapping[str, Any],
    ) -> bool:
        binding_fields = (
            "context_manifest_id",
            "program_id",
            "run_id",
            "model_call_id",
            "runtime_config_snapshot_id",
            "reservation_id",
            "agent_identity_id",
            "agent_identity_version",
            "skill_name",
            "skill_version",
            "input_sha256",
        )
        return actual.get("status") == "committed" and all(
            str(actual.get(field)) == str(expected.get(field))
            for field in binding_fields
        )

    @staticmethod
    def _reject(
        invocation: ModelInvocation,
        reason: GatewayReasonCode,
        *,
        context_manifest_id: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> GatewayResult:
        return GatewayResult(
            committed=False,
            reason_code=reason,
            model_call_id=invocation.model_call_id,
            context_manifest_id=context_manifest_id,
            details=details or {},
        )


__all__ = ("M4ModelGateway",)
