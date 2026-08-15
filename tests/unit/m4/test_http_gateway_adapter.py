"""Focused tests for the trusted AgentTeams-to-M4 HTTP boundary."""

from __future__ import annotations

from base64 import b64encode
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from hashlib import sha256
from uuid import uuid4

from awakening.context_manifest.m4 import (
    InMemoryContextManifestStore,
    InMemoryInvocationReceiptStore,
)
from awakening.model_gateway.m4 import (
    M4ModelGateway,
    ProviderResponse,
    RuntimeInvocationPlan,
    SingleUseRuntimeInvocationPlanRegistry,
    TrustedOpenAICompatibleHttpAdapter,
)
from awakening.model_gateway.m4.contracts import ProviderRequest, thaw
from awakening.model_gateway.m4.fail_closed_runtime import build_fail_closed_adapter
from awakening.model_gateway.m4.live_runtime import AUTHORIZED_MODEL_ID
from awakening.orchestration.m4 import (
    BoundRuntimeAuthorizer,
    RuntimeBinding,
    RuntimeCredentialRegistry,
)
from awakening.state.contracts import PrincipalType, TrustedPrincipal
from awakening.state.validation import canonical_json_bytes


class _CommittedState:
    def __init__(self, ids: dict[str, str]) -> None:
        self.ids = ids
        self.settlement_count = 0

    def get_runtime_config_snapshot(
        self,
        *,
        snapshot_id: str,
        program_id: str,
        run_id: str,
    ) -> dict[str, object]:
        return {
            "snapshot_id": snapshot_id,
            "program_id": program_id,
            "run_id": run_id,
            "provider_alias": "synthetic-provider",
            "model_id": "server-owned-model",
            "parameters": {
                "temperature": 0.01,
                "seed": 0,
                "enable_thinking": False,
                "response_format": {"type": "json_object"},
            },
            "max_output_tokens_per_call": 64,
        }

    def get_model_budget_reservation(
        self,
        *,
        reservation_id: str,
        program_id: str,
        run_id: str,
        model_call_id: str,
    ) -> dict[str, object]:
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
        return {"status": "settled"}


class _CapturingProvider:
    provider_alias = "synthetic-provider"

    def __init__(self) -> None:
        self.call_count = 0
        self.last_request: ProviderRequest | None = None

    def invoke(self, request: ProviderRequest) -> ProviderResponse:
        self.call_count += 1
        self.last_request = request
        output = {
            "id": "chatcmpl-synthetic",
            "object": "chat.completion",
            "created": 1,
            "model": "server-owned-model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "{}"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        }
        return ProviderResponse(
            provider_request_id="provider-request-synthetic",
            output_document=output,
            skill_output_document={},
            input_tokens=2,
            output_tokens=1,
            cost_microunits=0,
            response_sha256=sha256(canonical_json_bytes(output)).hexdigest(),
        )


class TrustedHttpGatewayAdapterTests(unittest.TestCase):
    def _binding(
        self,
        ids: dict[str, str],
        *,
        identity: str,
        public_alias: str,
        allowed_skill: str,
        reviewer_mode: str | None = None,
    ) -> RuntimeBinding:
        return RuntimeBinding(
            credential_id=f"credential-{identity}",
            agent_identity_id=identity,
            agent_identity_version="1.0.0",
            trusted_principal=TrustedPrincipal(
                principal_id=f"principal-{identity}",
                principal_type=PrincipalType.AGENT,
                scopes=("model:invoke",),
                program_scope=(ids["program_id"],),
                auth_context_id=f"auth-{identity}",
            ),
            program_id=ids["program_id"],
            run_id=ids["run_id"],
            runtime_config_snapshot_id=ids["snapshot_id"],
            public_model_alias=public_alias,
            allowed_skill_versions={allowed_skill: "1.0.0"},
            allowed_tools=(),
            reviewer_mode=reviewer_mode,
        )

    def _adapter(
        self,
        *,
        ids: dict[str, str],
        tokens_and_bindings: tuple[tuple[str, RuntimeBinding], ...],
        plans_by_token: dict[str, RuntimeInvocationPlan],
        provider: _CapturingProvider,
    ) -> TrustedOpenAICompatibleHttpAdapter:
        pepper = b"m4-http-test-pepper-is-at-least-32-bytes"
        empty_registry = RuntimeCredentialRegistry(
            pepper=pepper,
            bindings_by_fingerprint={},
        )
        bindings = {
            empty_registry.fingerprint(token): binding
            for token, binding in tokens_and_bindings
        }
        credentials = RuntimeCredentialRegistry(
            pepper=pepper,
            bindings_by_fingerprint=bindings,
        )
        plans = {
            credentials.fingerprint(token): plan for token, plan in plans_by_token.items()
        }
        state = _CommittedState(ids)
        manifests = InMemoryContextManifestStore()
        receipts = InMemoryInvocationReceiptStore()

        def gateway_factory(session: object) -> M4ModelGateway:
            return M4ModelGateway(
                state_authority=state,
                manifest_store=manifests,
                invocation_receipt_store=receipts,
                provider=provider,
                runtime_authorizer=BoundRuntimeAuthorizer(session),  # type: ignore[arg-type]
            )

        return TrustedOpenAICompatibleHttpAdapter(
            credential_registry=credentials,
            invocation_plans=SingleUseRuntimeInvocationPlanRegistry(plans),
            gateway_factory=gateway_factory,
        )

    @staticmethod
    def _ids() -> dict[str, str]:
        return {
            "program_id": str(uuid4()),
            "run_id": str(uuid4()),
            "snapshot_id": str(uuid4()),
            "model_call_id": str(uuid4()),
            "reservation_id": str(uuid4()),
        }

    def test_wire_model_and_limits_are_not_provider_authority(self) -> None:
        ids = self._ids()
        token = "architect-runtime-token-00000000000000000001"
        binding = self._binding(
            ids,
            identity="role_project_architect",
            public_alias="m4-approved-alias",
            allowed_skill="analyze_role_gap",
        )
        plan = RuntimeInvocationPlan(
            model_call_id=ids["model_call_id"],
            reservation_id=ids["reservation_id"],
            skill_name="analyze_role_gap",
            skill_version="1.0.0",
        )
        provider = _CapturingProvider()
        adapter = self._adapter(
            ids=ids,
            tokens_and_bindings=((token, binding),),
            plans_by_token={token: plan},
            provider=provider,
        )

        response = adapter.handle_request(
            method="POST",
            path="/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            body=json.dumps(
                {
                    "model": "m4-approved-alias",
                    "messages": [{"role": "user", "content": "synthetic fixture"}],
                    "max_tokens": 999_999,
                    "temperature": 2.0,
                    "stream": False,
                }
            ).encode(),
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual(1, provider.call_count)
        self.assertIsNotNone(provider.last_request)
        outbound = thaw(provider.last_request.input_document)  # type: ignore[union-attr]
        self.assertEqual("server-owned-model", outbound["model"])
        self.assertEqual(64, outbound["max_tokens"])
        self.assertEqual(0.01, outbound["temperature"])
        self.assertEqual(0, outbound["seed"])
        self.assertIs(False, outbound["enable_thinking"])
        self.assertEqual({"type": "json_object"}, outbound["response_format"])
        self.assertNotIn("stream", outbound)

    def test_controller_welcome_probe_has_no_plan_and_never_reaches_provider(self) -> None:
        ids = self._ids()
        token = "manager-runtime-token-0000000000000000000001"
        binding = self._binding(
            ids,
            identity="awakening_program_manager",
            public_alias="m4-approved-alias",
            allowed_skill="apply_authorized_change",
        )
        provider = _CapturingProvider()
        adapter = self._adapter(
            ids=ids,
            tokens_and_bindings=((token, binding),),
            plans_by_token={},
            provider=provider,
        )

        response = adapter.handle_request(
            method="POST",
            path="/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            body=json.dumps(
                {
                    "model": "m4-approved-alias",
                    "messages": [
                        {"role": "user", "content": "Reply with only one word: ok"}
                    ],
                }
            ).encode(),
        )

        self.assertEqual(403, response.status_code)
        self.assertEqual("CALL_PLAN_UNAVAILABLE", json.loads(response.body)["error"]["code"])
        self.assertEqual(0, provider.call_count)

    def test_fail_closed_runtime_uses_the_fixed_qwen_public_alias(self) -> None:
        fields = (
            "AWAKENING_PROGRAM_MANAGER_B64",
            "ROLE_PROJECT_ARCHITECT_B64",
            "EXECUTION_EVIDENCE_COACH_B64",
            "INDEPENDENT_QUALITY_REVIEWER_B64",
        )
        tokens = {
            field: f"m4-fail-closed-{index}-credential-000000000000000000000000"
            for index, field in enumerate(fields, start=1)
        }
        with TemporaryDirectory() as directory:
            credential_path = Path(directory) / "gateway-credentials.env"
            credential_path.write_text(
                "\n".join(
                    f"{field}={b64encode(token.encode()).decode()}"
                    for field, token in tokens.items()
                )
                + "\n",
                encoding="utf-8",
            )
            adapter = build_fail_closed_adapter(credential_path)

        token = tokens["ROLE_PROJECT_ARCHITECT_B64"]
        approved_response = adapter.handle_request(
            method="POST",
            path="/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            body=json.dumps(
                {
                    "model": AUTHORIZED_MODEL_ID,
                    "messages": [{"role": "user", "content": "fixed probe"}],
                }
            ).encode(),
        )
        self.assertEqual(403, approved_response.status_code)
        self.assertEqual(
            "CALL_PLAN_UNAVAILABLE",
            json.loads(approved_response.body)["error"]["code"],
        )

        legacy_response = adapter.handle_request(
            method="POST",
            path="/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            body=json.dumps(
                {
                    "model": "gpt-5-mini",
                    "messages": [{"role": "user", "content": "fixed probe"}],
                }
            ).encode(),
        )
        self.assertEqual(400, legacy_response.status_code)
        self.assertEqual(
            "MODEL_ALIAS_MISMATCH",
            json.loads(legacy_response.body)["error"]["code"],
        )

    def test_reviewer_tool_declaration_is_rejected_without_consuming_live_plan(self) -> None:
        ids = self._ids()
        token = "reviewer-runtime-token-00000000000000000001"
        binding = self._binding(
            ids,
            identity="independent_quality_reviewer",
            public_alias="m4-approved-alias",
            allowed_skill="review_evidence_against_rubric",
            reviewer_mode="contract_smoke",
        )
        plan = RuntimeInvocationPlan(
            model_call_id=ids["model_call_id"],
            reservation_id=ids["reservation_id"],
            skill_name="review_evidence_against_rubric",
            skill_version="1.0.0",
        )
        provider = _CapturingProvider()
        adapter = self._adapter(
            ids=ids,
            tokens_and_bindings=((token, binding),),
            plans_by_token={token: plan},
            provider=provider,
        )
        base = {
            "model": "m4-approved-alias",
            "messages": [{"role": "user", "content": "closed contract-smoke fixture"}],
        }
        with_tools = {
            **base,
            "tools": [
                {
                    "type": "function",
                    "function": {"name": "shell", "parameters": {"type": "object"}},
                }
            ],
        }

        denied = adapter.handle_request(
            method="POST",
            path="/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            body=json.dumps(with_tools).encode(),
        )
        allowed = adapter.handle_request(
            method="POST",
            path="/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            body=json.dumps({**base, "tools": [], "tool_choice": "none"}).encode(),
        )

        self.assertEqual(403, denied.status_code)
        self.assertEqual("TOOL_NOT_ALLOWED", json.loads(denied.body)["error"]["code"])
        self.assertEqual(200, allowed.status_code)
        self.assertEqual(1, provider.call_count)
        outbound = thaw(provider.last_request.input_document)  # type: ignore[union-attr]
        self.assertNotIn("tools", outbound)
        self.assertNotIn("tool_choice", outbound)

    def test_marker_mismatch_and_control_field_do_not_consume_single_use_plan(self) -> None:
        ids = self._ids()
        marker = f"m4-call:{ids['model_call_id']}"
        token = "coach-runtime-token-00000000000000000000001"
        binding = self._binding(
            ids,
            identity="execution_evidence_coach",
            public_alias="m4-approved-alias",
            allowed_skill="coach_task_submission",
        )
        plan = RuntimeInvocationPlan(
            model_call_id=ids["model_call_id"],
            reservation_id=ids["reservation_id"],
            skill_name="coach_task_submission",
            skill_version="1.0.0",
            request_marker=marker,
        )
        provider = _CapturingProvider()
        adapter = self._adapter(
            ids=ids,
            tokens_and_bindings=((token, binding),),
            plans_by_token={token: plan},
            provider=provider,
        )

        def invoke(content: object, **extra: object) -> object:
            return adapter.handle_request(
                method="POST",
                path="/v1/chat/completions",
                headers={"Authorization": f"Bearer {token}"},
                body=json.dumps(
                    {
                        "model": "m4-approved-alias",
                        "messages": [{"role": "user", "content": content}],
                        **extra,
                    }
                ).encode(),
            )

        missing = invoke("idle OpenClaw request")
        fuzzy = invoke(f"synthetic {marker}-suffix")
        stale = adapter.handle_request(
            method="POST",
            path="/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            body=json.dumps(
                {
                    "model": "m4-approved-alias",
                    "messages": [
                        {"role": "user", "content": f"stale {marker}"},
                        {"role": "assistant", "content": "continue"},
                        {"role": "user", "content": "latest idle request"},
                    ],
                }
            ).encode(),
        )
        forged_control = invoke(f"synthetic {marker}", request_marker=marker)
        denied_tool = invoke(
            f"synthetic {marker}",
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "shell",
                        "parameters": {"type": "object"},
                    },
                }
            ],
        )
        forbidden_runtime = invoke(
            f"synthetic {marker}",
            response_format={"type": "json_object"},
        )
        allowed = invoke(
            [
                {"type": "text", "text": "synthetic fixture"},
                {"type": "text", "text": f"authorized [{marker}]"},
            ],
            tools=[],
            tool_choice="none",
        )
        replay = invoke(f"synthetic {marker}")

        self.assertEqual(403, missing.status_code)
        self.assertEqual(403, fuzzy.status_code)
        self.assertEqual(403, stale.status_code)
        self.assertEqual(400, forged_control.status_code)
        self.assertEqual(403, denied_tool.status_code)
        self.assertEqual(
            "TOOL_NOT_ALLOWED", json.loads(denied_tool.body)["error"]["code"]
        )
        self.assertEqual(400, forbidden_runtime.status_code)
        self.assertEqual(
            "RUNTIME_BODY_FORBIDDEN",
            json.loads(forbidden_runtime.body)["error"]["code"],
        )
        self.assertEqual(200, allowed.status_code)
        self.assertEqual(403, replay.status_code)
        self.assertEqual(1, provider.call_count)


if __name__ == "__main__":
    unittest.main()
