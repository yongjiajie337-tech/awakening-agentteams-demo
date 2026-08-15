from __future__ import annotations

import unittest

from awakening.state.m4.service import (
    _DEC_M4_006_PROGRAM_CAPS,
    _M4_MULTI_CALL_POLICY_ID,
    _program_provider_policy_violations,
)


class M4ProgramProviderBudgetPolicyTests(unittest.TestCase):
    def _violations(
        self,
        *,
        slots: int,
        input_tokens: int = 0,
        output_tokens: int = 0,
        requested_input_tokens: int = 0,
        requested_output_tokens: int = 0,
    ) -> tuple[str, ...]:
        return _program_provider_policy_violations(
            program_totals={
                "call_slot_count": slots,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_microunits": 0,
            },
            requested_input_tokens=requested_input_tokens,
            requested_output_tokens=requested_output_tokens,
        )

    def test_policy_is_finite_and_owned_by_dec_m4_006(self) -> None:
        self.assertEqual("DEC-M4-006", _M4_MULTI_CALL_POLICY_ID)
        self.assertEqual(
            {
                "reservation_slot_count": 300,
                "input_tokens": 50_000_000,
                "output_tokens": 12_500_000,
                "cost_microcny": 10_000_000,
            },
            dict(_DEC_M4_006_PROGRAM_CAPS),
        )

    def test_three_hundredth_slot_allowed_and_next_refused(self) -> None:
        self.assertNotIn("PROGRAM_TOTAL_CALL_CAP", self._violations(slots=299))
        self.assertIn("PROGRAM_TOTAL_CALL_CAP", self._violations(slots=300))

    def test_exact_input_only_rmb_limit_allowed_and_next_token_refused(self) -> None:
        exact = self._violations(
            slots=0,
            requested_input_tokens=50_000_000,
        )
        over = self._violations(
            slots=0,
            requested_input_tokens=50_000_001,
        )
        self.assertNotIn("PROGRAM_TOTAL_INPUT_CAP", exact)
        self.assertNotIn("PROGRAM_RMB_COST_CAP", exact)
        self.assertIn("PROGRAM_TOTAL_INPUT_CAP", over)
        self.assertIn("PROGRAM_RMB_COST_CAP", over)

    def test_exact_output_only_rmb_limit_allowed_and_next_token_refused(self) -> None:
        exact = self._violations(
            slots=0,
            requested_output_tokens=12_500_000,
        )
        over = self._violations(
            slots=0,
            requested_output_tokens=12_500_001,
        )
        self.assertNotIn("PROGRAM_TOTAL_OUTPUT_CAP", exact)
        self.assertNotIn("PROGRAM_RMB_COST_CAP", exact)
        self.assertIn("PROGRAM_TOTAL_OUTPUT_CAP", over)
        self.assertIn("PROGRAM_RMB_COST_CAP", over)

    def test_mixed_token_cost_remains_the_decisive_hard_gate(self) -> None:
        violations = self._violations(
            slots=0,
            requested_input_tokens=25_000_000,
            requested_output_tokens=6_250_001,
        )
        self.assertNotIn("PROGRAM_TOTAL_INPUT_CAP", violations)
        self.assertNotIn("PROGRAM_TOTAL_OUTPUT_CAP", violations)
        self.assertIn("PROGRAM_RMB_COST_CAP", violations)


if __name__ == "__main__":
    unittest.main()
