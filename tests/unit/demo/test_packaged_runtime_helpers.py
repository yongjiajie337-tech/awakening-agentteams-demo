"""Offline closure checks for the two M4 helpers loaded by the Demo driver.

Importing and exercising these helpers must remain local-only: the tests build
synthetic inputs, validate packaged schemas, and parse frozen Worker outputs.
They do not call Docker, Matrix, Postgres, the Provider, or any Secret reader.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys
import unittest


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
HELPERS = {
    "scripts/m4/provision-provider-state.py": (
        "6dec03fc329491773a922775a122f19cc721f3735fa9c9f930c33eb4a382efc7"
    ),
    "scripts/m4/run-real-chain.py": (
        "dd279da8c517c0105926ed74b846a9c006151146dd1bbb86cf8ac5f19ba3045b"
    ),
}
PROGRAM_ID = "42fec130-9e27-4a01-9e0b-ac7d6b9b5403"
RUN_ID = "55dfc571-4cae-4483-b98e-8570bf5f9760"
SNAPSHOT_ID = "00000000-0000-4000-8000-000000000001"
STATE_VERSION = 2


def _load_module(name: str, relative: str):
    path = PACKAGE_ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"PACKAGED_HELPER_IMPORT_SPEC_INVALID:{relative}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


PROVISION = _load_module(
    "awakening_test_packaged_provision_provider_state",
    "scripts/m4/provision-provider-state.py",
)
REAL_CHAIN = _load_module(
    "awakening_test_packaged_run_real_chain",
    "scripts/m4/run-real-chain.py",
)
DRIVER = _load_module(
    "awakening_test_packaged_agentteams_in_place_demo",
    "scripts/demo/agentteams_in_place_demo.py",
)


class PackagedRuntimeHelperTests(unittest.TestCase):
    maxDiff = None

    def test_helpers_are_exactly_manifested_and_reference_pinned(self) -> None:
        manifest = json.loads(
            (PACKAGE_ROOT / "PACKAGE_MANIFEST.json").read_text(encoding="utf-8")
        )
        manifest_by_path = {entry["path"]: entry for entry in manifest["files"]}
        reference = json.loads(
            (PACKAGE_ROOT / "config" / "reference-source-pins.json").read_text(
                encoding="utf-8"
            )
        )
        reference_by_path = {entry["path"]: entry for entry in reference["files"]}

        self.assertEqual(180, reference["file_count"])
        for relative, expected_digest in HELPERS.items():
            with self.subTest(relative=relative):
                path = PACKAGE_ROOT / relative
                raw = path.read_bytes()
                self.assertEqual(expected_digest, sha256(raw).hexdigest())
                self.assertIn(relative, reference_by_path)
                self.assertEqual(expected_digest, reference_by_path[relative]["sha256"])
                self.assertIn(relative, manifest_by_path)
                self.assertEqual(expected_digest, manifest_by_path[relative]["sha256"])
                self.assertEqual(len(raw), manifest_by_path[relative]["size_bytes"])

        self.assertTrue(callable(DRIVER._provision_helpers()._skill_input))
        self.assertTrue(callable(DRIVER._real_chain_helpers()._validate_schema))
        self.assertTrue(callable(DRIVER._real_chain_helpers()._parse_worker_output))

    def test_skill_input_builds_all_three_worker_contracts(self) -> None:
        cases = {
            "role_project_architect": "analyze_role_gap",
            "execution_evidence_coach": "coach_task_submission",
            "independent_quality_reviewer": "review_evidence_against_rubric",
        }
        for identity, skill_name in cases.items():
            with self.subTest(identity=identity):
                document = PROVISION._skill_input(
                    identity=identity,
                    program_id=PROGRAM_ID,
                    run_id=RUN_ID,
                    state_version=STATE_VERSION,
                    snapshot_id=SNAPSHOT_ID,
                )
                self.assertIsInstance(document, dict)
                REAL_CHAIN._validate_schema(skill_name, "input", document)

    def test_validate_schema_accepts_all_six_canonical_worker_outputs(self) -> None:
        skills = {
            "role_project_architect": "analyze_role_gap",
            "execution_evidence_coach": "coach_task_submission",
            "independent_quality_reviewer": "review_evidence_against_rubric",
        }
        for run_name in ("run-a", "run-b"):
            for identity, skill_name in skills.items():
                with self.subTest(run_name=run_name, identity=identity):
                    output = json.loads(
                        (
                            PACKAGE_ROOT
                            / "evidence"
                            / run_name
                            / "outputs"
                            / f"{identity}.json"
                        ).read_text(encoding="utf-8")
                    )
                    REAL_CHAIN._validate_schema(skill_name, "output", output)

        with self.assertRaisesRegex(
            ValueError,
            "^M4_REAL_CHAIN_OUTPUT_SCHEMA_INVALID:analyze_role_gap$",
        ):
            REAL_CHAIN._validate_schema("analyze_role_gap", "output", {})

    def test_parse_worker_output_accepts_bound_output_and_rejects_forgery(self) -> None:
        skill_input = PROVISION._skill_input(
            identity="role_project_architect",
            program_id=PROGRAM_ID,
            run_id=RUN_ID,
            state_version=STATE_VERSION,
            snapshot_id=SNAPSHOT_ID,
        )
        output = json.loads(
            (
                PACKAGE_ROOT
                / "evidence"
                / "run-a"
                / "outputs"
                / "role_project_architect.json"
            ).read_text(encoding="utf-8")
        )
        parsed = REAL_CHAIN._parse_worker_output(
            identity="role_project_architect",
            skill_name="analyze_role_gap",
            text=json.dumps(output, ensure_ascii=False, separators=(",", ":")),
            skill_input=skill_input,
        )
        self.assertEqual(output, parsed)

        forged = deepcopy(output)
        forged["gaps"][0]["current_evidence_fact_ids"] = ["forged-fact-id"]
        with self.assertRaisesRegex(
            ValueError,
            "^M4_REAL_CHAIN_ARCHITECT_OUTPUT_REFERENCE_INVALID$",
        ):
            REAL_CHAIN._parse_worker_output(
                identity="role_project_architect",
                skill_name="analyze_role_gap",
                text=json.dumps(forged, ensure_ascii=False, separators=(",", ":")),
                skill_input=skill_input,
            )


if __name__ == "__main__":
    unittest.main()
