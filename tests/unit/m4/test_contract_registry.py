"""M4 unit/contract item 1: four identities and nine Skills."""

from __future__ import annotations

import unittest

from awakening.orchestration.m4 import load_and_validate_m4_registry


class ContractRegistryTests(unittest.TestCase):
    def test_four_identities_and_nine_skills_are_closed_and_symmetric(self) -> None:
        registry = load_and_validate_m4_registry()

        self.assertEqual(4, len(registry.identity_versions))
        self.assertEqual(9, len(registry.skill_versions))
        self.assertEqual(
            ("review_evidence_against_rubric",),
            registry.identity_skills["independent_quality_reviewer"],
        )
        self.assertEqual(
            (),
            registry.identity_state_methods["independent_quality_reviewer"],
        )
        self.assertEqual(
            "deny_only",
            registry.skill_activation["apply_authorized_change"],
        )
        registry.assert_skill_allowed(
            agent_identity_id="role_project_architect",
            agent_identity_version="1.0.0",
            skill_name="analyze_role_gap",
            skill_version="1.0.0",
        )


if __name__ == "__main__":
    unittest.main()
