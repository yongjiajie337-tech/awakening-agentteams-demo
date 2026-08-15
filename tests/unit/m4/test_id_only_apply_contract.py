"""M4 unit/contract item 2: apply stays ID-only and deny-only."""

from __future__ import annotations

import unittest
from uuid import uuid4

from awakening.state.m4 import (
    M4StateMcpMethod,
    load_m4_schema,
    validate_state_mcp_call,
)
from awakening.state.validation import ContractValidationError


class IdOnlyApplyContractTests(unittest.TestCase):
    def test_only_identifiers_are_accepted_and_authority_assertions_are_rejected(self) -> None:
        params = {
            "program_id": str(uuid4()),
            "proposal_id": str(uuid4()),
            "expected_state_version": 1,
            "idempotency_key": "m4-apply-deny-only-1",
            "human_decision_id": str(uuid4()),
        }
        validate_state_mcp_call(M4StateMcpMethod.APPLY_AUTHORIZED_CHANGE, params)

        schema = load_m4_schema(
            "state-mcp/apply-authorized-change.params.schema.json"
        )
        self.assertEqual(
            {
                "program_id",
                "proposal_id",
                "expected_state_version",
                "idempotency_key",
                "human_decision_id",
            },
            set(schema["properties"]),
        )
        for forbidden in (
            {"patch": {"status": "active"}},
            {"approved": True},
            {"agent_id": "forged-agent"},
            {"principal": {"principal_id": "forged"}},
            {"program_scope": [params["program_id"]]},
            {"approval_token": "raw-secret-token"},
        ):
            with self.subTest(forbidden=next(iter(forbidden))):
                with self.assertRaises(ContractValidationError):
                    validate_state_mcp_call(
                        M4StateMcpMethod.APPLY_AUTHORIZED_CHANGE,
                        {**params, **forbidden},
                    )


if __name__ == "__main__":
    unittest.main()
