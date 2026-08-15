"""State-owned M4 provider and inference parameter contract."""

from __future__ import annotations

import copy
import unittest

from awakening.state.m4 import RuntimeConfigSpec
from awakening.state.m4.validation import validate_runtime_config_snapshot
from awakening.state.validation import ContractValidationError


AUTHORIZED_PARAMETERS = {
    "temperature": 0.01,
    "seed": 0,
    "enable_thinking": False,
    "response_format": {"type": "json_object"},
}


def _spec(**overrides):
    values = {
        "run_id": "20000000-0000-4000-8000-000000000001",
        "provider_alias": "aliyun-model-studio-official",
        "model_id": "qwen3.7-flash-2026-07-15",
        "parameters": copy.deepcopy(AUTHORIZED_PARAMETERS),
        "max_calls": 3,
        "max_input_tokens_per_call": 64_000,
        "max_output_tokens_per_call": 1_000,
        "max_cost_microunits_per_call": 30_000,
        "max_total_input_tokens": 192_000,
        "max_total_output_tokens": 3_000,
        "max_total_cost_microunits": 100_000,
    }
    values.update(overrides)
    return RuntimeConfigSpec(**values)


class RuntimeConfigTrustedParameterTests(unittest.TestCase):
    def test_exact_authorized_snapshot_contract_is_accepted(self) -> None:
        spec = _spec()
        self.assertEqual(AUTHORIZED_PARAMETERS, spec.to_dict()["parameters"])
        validate_runtime_config_snapshot(
            {
                "snapshot_id": "30000000-0000-4000-8000-000000000001",
                "program_id": "10000000-0000-4000-8000-000000000001",
                **spec.to_dict(),
                "config_sha256": "a" * 64,
                "created_by_principal_id": "awakening-m4-model-gateway",
                "created_at": "2026-08-03T00:00:00+00:00",
            }
        )

    def test_provider_model_or_parameter_drift_is_rejected(self) -> None:
        invalid_overrides = [
            {"provider_alias": "other-provider"},
            {"model_id": "other-model"},
        ]
        for field, value in (
            ("temperature", 0),
            ("seed", 1),
            ("enable_thinking", True),
            ("response_format", {"type": "text"}),
        ):
            parameters = copy.deepcopy(AUTHORIZED_PARAMETERS)
            parameters[field] = value
            invalid_overrides.append({"parameters": parameters})
        parameters = copy.deepcopy(AUTHORIZED_PARAMETERS)
        parameters["worker_override"] = True
        invalid_overrides.append({"parameters": parameters})

        for overrides in invalid_overrides:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValueError):
                    _spec(**overrides)

        valid = _spec().to_dict()
        snapshot = {
            "snapshot_id": "30000000-0000-4000-8000-000000000001",
            "program_id": "10000000-0000-4000-8000-000000000001",
            **valid,
            "config_sha256": "a" * 64,
            "created_by_principal_id": "awakening-m4-model-gateway",
            "created_at": "2026-08-03T00:00:00+00:00",
        }
        snapshot["parameters"] = {**AUTHORIZED_PARAMETERS, "worker_override": True}
        with self.assertRaises(ContractValidationError):
            validate_runtime_config_snapshot(snapshot)


if __name__ == "__main__":
    unittest.main()
