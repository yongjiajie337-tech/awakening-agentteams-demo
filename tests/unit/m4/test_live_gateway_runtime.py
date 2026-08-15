"""Offline composition checks for the live M4 Gateway scaffold."""

from __future__ import annotations

import base64
import json
from pathlib import Path
import tempfile
import unittest

from awakening.model_gateway.m4.live_runtime import (
    LiveRuntimeConfigurationError,
    _build_runtime_security,
    load_live_runtime_config,
)


class LiveGatewayRuntimeTests(unittest.TestCase):
    def test_key_free_config_exact_bindings_and_three_worker_leases(self) -> None:
        identities = {
            "awakening_program_manager": "AWAKENING_PROGRAM_MANAGER_B64",
            "role_project_architect": "ROLE_PROJECT_ARCHITECT_B64",
            "execution_evidence_coach": "EXECUTION_EVIDENCE_COACH_B64",
            "independent_quality_reviewer": "INDEPENDENT_QUALITY_REVIEWER_B64",
        }
        representative_skills = {
            "role_project_architect": "analyze_role_gap",
            "execution_evidence_coach": "coach_task_submission",
            "independent_quality_reviewer": "review_evidence_against_rubric",
        }
        uuids = iter(
            (
                "00000000-0000-4000-8000-000000000001",
                "00000000-0000-4000-8000-000000000002",
                "00000000-0000-4000-8000-000000000003",
                "00000000-0000-4000-8000-000000000004",
                "00000000-0000-4000-8000-000000000005",
                "00000000-0000-4000-8000-000000000006",
                "00000000-0000-4000-8000-000000000007",
                "00000000-0000-4000-8000-000000000008",
                "00000000-0000-4000-8000-000000000009",
            )
        )
        program_id, run_id, snapshot_id = next(uuids), next(uuids), next(uuids)
        plans = {}
        for position, (identity, skill) in enumerate(representative_skills.items()):
            model_call_id = next(uuids)
            plans[identity] = {
                "model_call_id": model_call_id,
                "reservation_id": next(uuids),
                "skill_name": skill,
                "skill_version": "1.0.0",
                "request_marker": f"m4-call:{model_call_id}",
                "object_refs": [
                    {
                        "object_type": "synthetic_fixture",
                        "object_id": f"m4-package-{position}",
                        "object_version": "1",
                        "content_sha256": str(position + 1) * 64,
                    }
                ],
                "exclusions": ["private_raw_content", "unverified_claims"],
            }
        document = {
            "authorization_id": "AUTH-M4-001",
            "schema_version": 1,
            "provider": {
                "provider_alias": "aliyun-model-studio-official",
                "endpoint": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "allowed_hostname": "dashscope.aliyuncs.com",
                "model_id": "qwen3.7-flash-2026-07-15",
                "public_model_alias": "qwen3.7-flash-2026-07-15",
                "input_microunits_per_million": 150_000,
                "output_microunits_per_million": 600_000,
                "timeout_seconds": 60,
            },
            "parameters": {
                "temperature": 0.01,
                "seed": 0,
                "enable_thinking": False,
                "response_format": {"type": "json_object"},
            },
            "caps": {
                "max_calls": 3,
                "max_input_tokens_per_call": 64_000,
                "max_output_tokens_per_call": 1_000,
                "max_cost_microunits_per_call": 30_000,
                "max_total_input_tokens": 192_000,
                "max_total_output_tokens": 3_000,
                "max_total_cost_microunits": 100_000,
            },
            "state_binding": {
                "program_id": program_id,
                "run_id": run_id,
                "runtime_config_snapshot_id": snapshot_id,
            },
            "plans": plans,
        }
        tokens = {
            identity: f"m4-{position}-" + str(position) * 40
            for position, identity in enumerate(identities, start=1)
        }

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = root / "live-config.json"
            credentials_path = root / "gateway-credentials.env"
            config_path.write_text(json.dumps(document), encoding="utf-8")
            credentials_path.write_text(
                "\n".join(
                    f"{field}={base64.b64encode(tokens[identity].encode()).decode()}"
                    for identity, field in identities.items()
                )
                + "\n",
                encoding="utf-8",
            )

            config = load_live_runtime_config(config_path)
            self.assertEqual(
                {
                    "temperature": 0.01,
                    "seed": 0,
                    "enable_thinking": False,
                    "response_format": {"type": "json_object"},
                },
                config.parameters.to_dict(),
            )
            credentials, leases = _build_runtime_security(config, credentials_path)

            manager = credentials.authenticate(tokens["awakening_program_manager"])
            self.assertIsNotNone(manager)
            self.assertIsNone(leases.claim(manager))  # type: ignore[arg-type]
            for identity, expected_skill in representative_skills.items():
                session = credentials.authenticate(tokens[identity])
                self.assertIsNotNone(session)
                marker = plans[identity]["request_marker"]
                self.assertIsNone(
                    leases.claim(
                        session,  # type: ignore[arg-type]
                        messages=[{"role": "user", "content": f"idle {marker}x"}],
                    )
                )
                lease = leases.claim(
                    session,  # type: ignore[arg-type]
                    messages=[{"role": "user", "content": f"authorized [{marker}]"}],
                )
                self.assertIsNotNone(lease)
                self.assertEqual(expected_skill, lease.skill_name)  # type: ignore[union-attr]
                self.assertIsNone(leases.claim(session))  # type: ignore[arg-type]

            invalid_documents = []
            for field, value in (
                ("provider_alias", "other-provider"),
                ("endpoint", "https://dashscope.aliyuncs.com/compatible-mode/v2"),
                ("timeout_seconds", 59),
            ):
                candidate = json.loads(json.dumps(document))
                candidate["provider"][field] = value
                invalid_documents.append(candidate)
            model_candidate = json.loads(json.dumps(document))
            model_candidate["provider"]["model_id"] = "other-model"
            model_candidate["provider"]["public_model_alias"] = "other-model"
            invalid_documents.append(model_candidate)
            for candidate in invalid_documents:
                config_path.write_text(json.dumps(candidate), encoding="utf-8")
                with self.assertRaisesRegex(
                    LiveRuntimeConfigurationError,
                    "M4_LIVE_AUTHORIZED_PROVIDER_BINDING_INVALID",
                ):
                    load_live_runtime_config(config_path)

            for field, value, reason in (
                ("temperature", 0, "M4_LIVE_TEMPERATURE_INVALID"),
                ("seed", 1, "M4_LIVE_SEED_INVALID"),
                ("enable_thinking", True, "M4_LIVE_ENABLE_THINKING_INVALID"),
                (
                    "response_format",
                    {"type": "text"},
                    "M4_LIVE_RESPONSE_FORMAT_INVALID",
                ),
            ):
                candidate = json.loads(json.dumps(document))
                candidate["parameters"][field] = value
                config_path.write_text(json.dumps(candidate), encoding="utf-8")
                with self.assertRaisesRegex(LiveRuntimeConfigurationError, reason):
                    load_live_runtime_config(config_path)


if __name__ == "__main__":
    unittest.main()
