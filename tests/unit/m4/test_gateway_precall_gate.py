"""M4 unit/contract item 4: every pre-call fact binds before transport."""

from __future__ import annotations

import unittest
from hashlib import sha256
from uuid import uuid4

from awakening.context_manifest.m4 import (
    InMemoryContextManifestStore,
    InMemoryInvocationReceiptStore,
)
from awakening.model_gateway.m4 import (
    GatewayReasonCode,
    M4ModelGateway,
    ModelInvocation,
    RecordingProvider,
)
from awakening.state.validation import canonical_json_bytes


class _CommittedStateAuthority:
    def __init__(
        self,
        ids: dict[str, str],
        *,
        snapshot_available: bool = True,
        reservation_available: bool = True,
    ) -> None:
        self.ids = ids
        self.snapshot_available = snapshot_available
        self.reservation_available = reservation_available
        self.settlement_count = 0
        self.last_usage_receipt: object | None = None

    def get_runtime_config_snapshot(
        self,
        *,
        snapshot_id: str,
        program_id: str,
        run_id: str,
    ) -> dict[str, object] | None:
        if not self.snapshot_available:
            return None
        return {
            "snapshot_id": snapshot_id,
            "program_id": program_id,
            "run_id": run_id,
            "provider_alias": "synthetic-provider",
            "model_id": "synthetic-model",
            "parameters": {
                "temperature": 0.01,
                "seed": 0,
                "enable_thinking": False,
                "response_format": {"type": "json_object"},
            },
            "max_output_tokens_per_call": 64,
            "status": "committed",
        }

    def get_model_budget_reservation(
        self,
        *,
        reservation_id: str,
        program_id: str,
        run_id: str,
        model_call_id: str,
    ) -> dict[str, object] | None:
        if not self.reservation_available:
            return None
        return {
            "reservation_id": reservation_id,
            "program_id": program_id,
            "run_id": run_id,
            "model_call_id": model_call_id,
            "snapshot_id": self.ids["snapshot_id"],
            "max_input_tokens": 4096,
            "max_output_tokens": 64,
            "status": "reserved",
        }

    def settle_model_budget(self, receipt: object) -> dict[str, str]:
        self.settlement_count += 1
        self.last_usage_receipt = receipt
        return {"status": "settled"}


class _AllowExactRuntime:
    def authorize_model_call(self, invocation: ModelInvocation) -> GatewayReasonCode:
        return GatewayReasonCode.OK


class _TamperedManifestStore(InMemoryContextManifestStore):
    def append(self, manifest: object) -> dict[str, object]:
        record = dict(super().append(manifest))  # type: ignore[arg-type]
        record["input_sha256"] = "0" * 64
        return record


class GatewayPrecallGateTests(unittest.TestCase):
    def test_snapshot_reservation_manifest_and_exact_hash_gate_transport(self) -> None:
        ids = {
            "program_id": str(uuid4()),
            "run_id": str(uuid4()),
            "model_call_id": str(uuid4()),
            "snapshot_id": str(uuid4()),
            "reservation_id": str(uuid4()),
        }
        invocation = ModelInvocation(
            program_id=ids["program_id"],
            run_id=ids["run_id"],
            model_call_id=ids["model_call_id"],
            agent_identity_id="role_project_architect",
            agent_identity_version="1.0.0",
            skill_name="analyze_role_gap",
            skill_version="1.0.0",
            runtime_config_snapshot_id=ids["snapshot_id"],
            reservation_id=ids["reservation_id"],
            provider_input={
                "messages": [
                    {
                        "role": "user",
                        "content": "synthetic closed-package role gap fixture",
                    }
                ]
            },
            object_refs=(
                {
                    "object_type": "program_snapshot",
                    "object_id": ids["program_id"],
                    "object_version": "1",
                    "content_sha256": "a" * 64,
                },
            ),
            exclusions=("private_raw_content", "unverified_claims"),
        )

        blocked_cases = (
            (
                "snapshot_missing",
                _CommittedStateAuthority(ids, snapshot_available=False),
                InMemoryContextManifestStore(),
                GatewayReasonCode.SNAPSHOT_NOT_COMMITTED,
            ),
            (
                "reservation_missing",
                _CommittedStateAuthority(ids, reservation_available=False),
                InMemoryContextManifestStore(),
                GatewayReasonCode.RESERVATION_NOT_COMMITTED,
            ),
            (
                "manifest_pending",
                _CommittedStateAuthority(ids),
                InMemoryContextManifestStore(committed=False),
                GatewayReasonCode.CONTEXT_MANIFEST_NOT_COMMITTED,
            ),
            (
                "manifest_hash_mismatch",
                _CommittedStateAuthority(ids),
                _TamperedManifestStore(),
                GatewayReasonCode.CONTEXT_MANIFEST_NOT_COMMITTED,
            ),
        )
        for label, authority, manifest_store, reason in blocked_cases:
            with self.subTest(case=label):
                provider = RecordingProvider()
                invocation_receipts = InMemoryInvocationReceiptStore()
                gateway = M4ModelGateway(
                    state_authority=authority,
                    manifest_store=manifest_store,
                    invocation_receipt_store=invocation_receipts,
                    provider=provider,
                    runtime_authorizer=_AllowExactRuntime(),
                )
                result = gateway.invoke(invocation)
                self.assertEqual(0, provider.call_count)
                self.assertFalse(result.committed)
                self.assertIs(reason, result.reason_code)
                self.assertEqual(0, authority.settlement_count)
                self.assertEqual(0, invocation_receipts.append_count)

        authority = _CommittedStateAuthority(ids)
        provider = RecordingProvider()
        manifests = InMemoryContextManifestStore()
        invocation_receipts = InMemoryInvocationReceiptStore()
        result = M4ModelGateway(
            state_authority=authority,
            manifest_store=manifests,
            invocation_receipt_store=invocation_receipts,
            provider=provider,
            runtime_authorizer=_AllowExactRuntime(),
        ).invoke(invocation)

        self.assertTrue(result.committed)
        self.assertEqual(1, provider.call_count)
        self.assertEqual(1, authority.settlement_count)
        self.assertEqual(1, invocation_receipts.append_count)
        persisted = manifests.get(str(result.context_manifest_id))
        self.assertIsNotNone(persisted)
        expected_wire = {
            "messages": [
                {
                    "role": "user",
                    "content": "synthetic closed-package role gap fixture",
                }
            ],
            "model": "synthetic-model",
            "max_tokens": 64,
            "temperature": 0.01,
            "seed": 0,
            "enable_thinking": False,
            "response_format": {"type": "json_object"},
        }
        self.assertEqual(
            sha256(canonical_json_bytes(expected_wire)).hexdigest(),
            persisted["input_sha256"],  # type: ignore[index]
        )


if __name__ == "__main__":
    unittest.main()
