"""M4 Provider usage truth and conservative input-reservation gates."""

from __future__ import annotations

import json
import unittest
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from awakening.context_manifest.m4 import (
    InMemoryContextManifestStore,
    InMemoryInvocationReceiptStore,
)
from awakening.model_gateway.m4 import (
    GatewayReasonCode,
    M4ModelGateway,
    ModelInvocation,
    OpenAICompatibleProvider,
    ProviderRequest,
    RecordingProvider,
)
from awakening.model_gateway.m4.contracts import thaw
from awakening.state.validation import canonical_json_bytes


class _HTTPResponse:
    def __init__(self, document: object) -> None:
        self._raw = json.dumps(document, separators=(",", ":")).encode("utf-8")
        self.headers = {"x-request-id": "provider-request-1"}

    def __enter__(self) -> "_HTTPResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._raw


class _StateAuthority:
    def __init__(self, ids: dict[str, str], max_input_tokens: int) -> None:
        self._ids = ids
        self._max_input_tokens = max_input_tokens
        self.settlement_count = 0

    def get_runtime_config_snapshot(self, **_: object) -> dict[str, object]:
        return {
            "snapshot_id": self._ids["snapshot_id"],
            "program_id": self._ids["program_id"],
            "run_id": self._ids["run_id"],
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

    def get_model_budget_reservation(self, **_: object) -> dict[str, object]:
        return {
            "reservation_id": self._ids["reservation_id"],
            "program_id": self._ids["program_id"],
            "run_id": self._ids["run_id"],
            "model_call_id": self._ids["model_call_id"],
            "snapshot_id": self._ids["snapshot_id"],
            "max_input_tokens": self._max_input_tokens,
            "max_output_tokens": 64,
            "status": "reserved",
        }

    def settle_model_budget(self, _: object) -> dict[str, str]:
        self.settlement_count += 1
        return {"status": "settled"}


class _AllowRuntime:
    def authorize_model_call(self, _: ModelInvocation) -> GatewayReasonCode:
        return GatewayReasonCode.OK


class ProviderUsageAndInputReservationTests(unittest.TestCase):
    def test_provider_separates_wire_envelope_from_structured_skill_output(self) -> None:
        request_document = {"model": "approved-model", "messages": []}
        request = ProviderRequest(
            provider_alias="approved-provider",
            model_id="approved-model",
            model_call_id=str(uuid4()),
            request_sha256=sha256(canonical_json_bytes(request_document)).hexdigest(),
            input_document=request_document,
        )
        skill_output = {
            "schema_version": "m4.test.v1",
            "observations": [],
        }
        response_document = {
            "id": "structured-completion",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(skill_output, separators=(",", ":")),
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3},
        }
        provider = OpenAICompatibleProvider(
            provider_alias="approved-provider",
            endpoint="https://provider.example/v1",
            api_key="not-a-real-secret",
            allowed_hostname="provider.example",
            input_microunits_per_million=1,
            output_microunits_per_million=1,
        )
        opener = SimpleNamespace(
            open=lambda *_args, **_kwargs: _HTTPResponse(response_document)
        )
        with patch(
            "awakening.model_gateway.m4.provider.build_opener",
            return_value=opener,
        ):
            response = provider.invoke(request)

        self.assertEqual(response_document, thaw(response.output_document))
        self.assertEqual(skill_output, thaw(response.skill_output_document))
        self.assertNotEqual(
            sha256(canonical_json_bytes(response_document)).hexdigest(),
            sha256(canonical_json_bytes(skill_output)).hexdigest(),
        )

    def test_provider_rejects_missing_or_illegal_reported_usage(self) -> None:
        document = {"model": "approved-model", "messages": []}
        request = ProviderRequest(
            provider_alias="approved-provider",
            model_id="approved-model",
            model_call_id=str(uuid4()),
            request_sha256=sha256(canonical_json_bytes(document)).hexdigest(),
            input_document=document,
        )
        invalid_responses = (
            {"id": "missing-usage"},
            {"id": "missing-completion", "usage": {"prompt_tokens": 1}},
            {
                "id": "negative-prompt",
                "usage": {"prompt_tokens": -1, "completion_tokens": 1},
            },
            {
                "id": "string-completion",
                "usage": {"prompt_tokens": 1, "completion_tokens": "1"},
            },
        )
        for response_document in invalid_responses:
            with self.subTest(response=response_document["id"]):
                provider = OpenAICompatibleProvider(
                    provider_alias="approved-provider",
                    endpoint="https://provider.example/v1",
                    api_key="not-a-real-secret",
                    allowed_hostname="provider.example",
                    input_microunits_per_million=1,
                    output_microunits_per_million=1,
                )
                opener = SimpleNamespace(
                    open=lambda *_args, **_kwargs: _HTTPResponse(response_document)
                )
                with patch(
                    "awakening.model_gateway.m4.provider.build_opener",
                    return_value=opener,
                ):
                    with self.assertRaises(ValueError):
                        provider.invoke(request)

    def test_final_wire_bytes_must_fit_input_reservation_and_keep_exact_hash(self) -> None:
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
            provider_input={"messages": [{"role": "user", "content": "fixture"}]},
        )
        expected_wire = {
            "messages": [{"role": "user", "content": "fixture"}],
            "model": "synthetic-model",
            "max_tokens": 64,
            "temperature": 0.01,
            "seed": 0,
            "enable_thinking": False,
            "response_format": {"type": "json_object"},
        }
        wire = canonical_json_bytes(expected_wire)

        blocked_provider = RecordingProvider()
        blocked_manifests = InMemoryContextManifestStore()
        blocked = M4ModelGateway(
            state_authority=_StateAuthority(ids, len(wire) - 1),
            manifest_store=blocked_manifests,
            invocation_receipt_store=InMemoryInvocationReceiptStore(),
            provider=blocked_provider,
            runtime_authorizer=_AllowRuntime(),
        ).invoke(invocation)
        self.assertFalse(blocked.committed)
        self.assertIs(GatewayReasonCode.PROVIDER_INPUT_INVALID, blocked.reason_code)
        self.assertEqual(0, blocked_provider.call_count)
        self.assertEqual(0, blocked_manifests.append_count)

        allowed_provider = RecordingProvider()
        allowed_manifests = InMemoryContextManifestStore()
        allowed = M4ModelGateway(
            state_authority=_StateAuthority(ids, len(wire)),
            manifest_store=allowed_manifests,
            invocation_receipt_store=InMemoryInvocationReceiptStore(),
            provider=allowed_provider,
            runtime_authorizer=_AllowRuntime(),
        ).invoke(invocation)
        self.assertTrue(allowed.committed)
        self.assertEqual(1, allowed_provider.call_count)
        persisted = allowed_manifests.get(str(allowed.context_manifest_id))
        self.assertEqual(sha256(wire).hexdigest(), persisted["input_sha256"])

    def test_worker_cannot_override_fixed_qwen_provider_controls(self) -> None:
        ids = {
            "program_id": str(uuid4()),
            "run_id": str(uuid4()),
            "model_call_id": str(uuid4()),
            "snapshot_id": str(uuid4()),
            "reservation_id": str(uuid4()),
        }
        forbidden_controls = {
            "response_format": {"type": "text"},
            "enable_thinking": True,
            "seed": 99,
        }
        for field, value in forbidden_controls.items():
            with self.subTest(field=field):
                provider = RecordingProvider()
                manifests = InMemoryContextManifestStore()
                result = M4ModelGateway(
                    state_authority=_StateAuthority(ids, 4096),
                    manifest_store=manifests,
                    invocation_receipt_store=InMemoryInvocationReceiptStore(),
                    provider=provider,
                    runtime_authorizer=_AllowRuntime(),
                ).invoke(
                    ModelInvocation(
                        program_id=ids["program_id"],
                        run_id=ids["run_id"],
                        model_call_id=ids["model_call_id"],
                        agent_identity_id="role_project_architect",
                        agent_identity_version="1.0.0",
                        skill_name="analyze_role_gap",
                        skill_version="1.0.0",
                        runtime_config_snapshot_id=ids["snapshot_id"],
                        reservation_id=ids["reservation_id"],
                        provider_input={"messages": [], field: value},
                    )
                )
                self.assertFalse(result.committed)
                self.assertIs(
                    GatewayReasonCode.PROVIDER_INPUT_INVALID,
                    result.reason_code,
                )
                self.assertEqual(0, provider.call_count)
                self.assertEqual(0, manifests.append_count)


if __name__ == "__main__":
    unittest.main()
