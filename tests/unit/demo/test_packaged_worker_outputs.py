from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator, FormatChecker


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
RUN_SPECS = {
    "run-a": {
        "role_project_architect": (
            "role_project_architect.json",
            "schemas/m4/skills/analyze_role_gap.output.schema.json",
        ),
        "execution_evidence_coach": (
            "execution_evidence_coach.json",
            "schemas/m4/skills/coach_task_submission.output.schema.json",
        ),
        "independent_quality_reviewer": (
            "independent_quality_reviewer.json",
            "schemas/m4/skills/review_evidence_against_rubric.output.schema.json",
        ),
    },
    "run-b": {
        "role_project_architect": (
            "role_project_architect.json",
            "schemas/m4/skills/analyze_role_gap.output.schema.json",
        ),
        "execution_evidence_coach": (
            "execution_evidence_coach.json",
            "schemas/m4/skills/coach_task_submission.output.schema.json",
        ),
        "independent_quality_reviewer": (
            "independent_quality_reviewer.json",
            "schemas/m4/skills/review_evidence_against_rubric.output.schema.json",
        ),
    },
}


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class PackagedWorkerOutputTests(unittest.TestCase):
    maxDiff = None

    def _verify_fixture(self, run_name: str, role: str) -> None:
        filename, schema_relative = RUN_SPECS[run_name][role]
        run_root = PACKAGE_ROOT / "evidence" / run_name
        output_path = run_root / "outputs" / filename
        output_bytes = output_path.read_bytes()
        output = json.loads(output_bytes)
        canonical = _canonical_json_bytes(output)
        self.assertEqual(output_bytes, canonical + b"\n")

        provider_records = [
            json.loads(line)
            for line in (run_root / "provider-events.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        role_record = next(
            record
            for record in provider_records
            if record.get("record_type") == "worker-provider-outcome"
            and record.get("agent_identity_id") == role
        )
        self.assertEqual(sha256(canonical).hexdigest(), role_record["output_sha256"])

        schema = json.loads((PACKAGE_ROOT / schema_relative).read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        errors = sorted(validator.iter_errors(output), key=lambda error: list(error.path))
        self.assertEqual([], [error.message for error in errors])

    def test_run_a_role_project_architect(self) -> None:
        self._verify_fixture("run-a", "role_project_architect")

    def test_run_a_execution_evidence_coach(self) -> None:
        self._verify_fixture("run-a", "execution_evidence_coach")

    def test_run_a_independent_quality_reviewer(self) -> None:
        self._verify_fixture("run-a", "independent_quality_reviewer")

    def test_run_b_role_project_architect(self) -> None:
        self._verify_fixture("run-b", "role_project_architect")

    def test_run_b_execution_evidence_coach(self) -> None:
        self._verify_fixture("run-b", "execution_evidence_coach")

    def test_run_b_independent_quality_reviewer(self) -> None:
        self._verify_fixture("run-b", "independent_quality_reviewer")


if __name__ == "__main__":
    unittest.main()
