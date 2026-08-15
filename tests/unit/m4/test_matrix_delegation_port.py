"""Offline behavior tests for the fixed M4 Matrix delegation port.

The subprocess boundary is mocked.  These tests must not start Docker, reach
Matrix, use the network, or read any runtime credential.
"""

from __future__ import annotations

import json
import subprocess
import unittest
from unittest.mock import patch
from uuid import uuid4

from awakening.orchestration.m4.matrix_delegation import (
    M4_DOCKER_EXECUTABLE,
    M4_MANAGER_CONTAINER,
    M4_MATRIX_HELPER,
    M4_MATRIX_INTERPRETER,
    MatrixDelegationError,
    MatrixManagerDelegationPort,
)


class MatrixDelegationPortTests(unittest.TestCase):
    def setUp(self) -> None:
        self.program_id = str(uuid4())
        self.run_id = str(uuid4())
        self.markers = {
            "role_project_architect": f"m4-call:{uuid4()}",
            "execution_evidence_coach": f"m4-call:{uuid4()}",
            "independent_quality_reviewer": f"m4-call:{uuid4()}",
        }
        self.packages = {
            "role_project_architect": {
                "program_id": self.program_id,
                "state_version": 2,
                "role_facts": [
                    {
                        "source_ref": "synthetic-role-source:jd-001",
                        "requirement": "Build one reproducible workflow test.",
                    }
                ],
                "user_facts": [
                    {
                        "fact_id": "fact-001",
                        "statement": "The synthetic candidate wrote one unit test.",
                        "confirmed": True,
                    }
                ],
                "constraints": {"duration_weeks": 4, "weekly_hours": 5},
            },
            "execution_evidence_coach": {
                "program_id": self.program_id,
                "state_version": 2,
                "task": {
                    "task_id": str(uuid4()),
                    "task_version": 1,
                    "title": "Create one deterministic workflow test",
                },
                "criteria": [
                    {
                        "criterion_id": "criterion-001",
                        "statement": "The result is asserted.",
                    }
                ],
                "evidence_refs": [
                    {
                        "evidence_item_id": str(uuid4()),
                        "object_ref_sha256": "a" * 64,
                    }
                ],
            },
            "independent_quality_reviewer": {
                "reviewer_mode": "contract_smoke",
                "package_kind": "fixed_synthetic_closed_package",
                "package_id": str(uuid4()),
                "package_sha256": "b" * 64,
                "context_sha256": "c" * 64,
                "rubric_version": "synthetic-rubric-v1",
                "criteria": [
                    {
                        "criterion_id": "criterion-001",
                        "statement": "The fixture contains an asserted result.",
                    }
                ],
                "evidence_facts": [
                    {
                        "evidence_fact_id": "evidence-fact-001",
                        "statement": "The synthetic result records one assertion.",
                    }
                ],
                "tools_allowed": False,
            },
        }
        self.port = MatrixManagerDelegationPort(
            request_markers=self.markers,
            trusted_worker_packages=self.packages,
        )

    @patch("awakening.orchestration.m4.matrix_delegation.subprocess.run")
    def test_dispatch_uses_fixed_shell_free_helper_argv(self, run: object) -> None:
        run.return_value = subprocess.CompletedProcess(  # type: ignore[attr-defined]
            args=(), returncode=0, stdout="$delivery:matrix-m4.local:8080\n", stderr=""
        )
        delivery_id = self.port.dispatch(
            target_identity="role_project_architect",
            payload={
                "program_id": self.program_id,
                "run_id": self.run_id,
                "state_version": 2,
                "skill_name": "analyze_role_gap",
                "skill_version": "1.0.0",
            },
        )
        self.assertEqual("$delivery:matrix-m4.local:8080", delivery_id)
        arguments = run.call_args.args[0]  # type: ignore[attr-defined]
        self.assertEqual(
            (
                M4_DOCKER_EXECUTABLE,
                "exec",
                M4_MANAGER_CONTAINER,
                M4_MATRIX_INTERPRETER,
                "--",
                M4_MATRIX_HELPER,
                "dispatch",
                "role_project_architect",
            ),
            arguments[:8],
        )
        self.assertEqual(self.markers["role_project_architect"], arguments[8])
        self.assertEqual(
            {"program_id", "run_id", "state_version", "skill_name", "skill_version"},
            set(json.loads(arguments[9])),
        )
        self.assertEqual(
            self.packages["role_project_architect"], json.loads(arguments[10])
        )
        self.assertFalse(run.call_args.kwargs["shell"])  # type: ignore[attr-defined]

    @patch("awakening.orchestration.m4.matrix_delegation.subprocess.run")
    def test_explicit_docker_path_is_one_argument(self, run: object) -> None:
        run.return_value = subprocess.CompletedProcess(  # type: ignore[attr-defined]
            args=(), returncode=0, stdout="$delivery:matrix-m4.local:8080\n", stderr=""
        )
        port = MatrixManagerDelegationPort(
            request_markers=self.markers,
            trusted_worker_packages=self.packages,
            docker_executable=r"C:\Program Files\Docker\docker.exe",
        )
        port.dispatch(
            target_identity="role_project_architect",
            payload={
                "program_id": self.program_id,
                "run_id": self.run_id,
                "state_version": 2,
                "skill_name": "analyze_role_gap",
                "skill_version": "1.0.0",
            },
        )
        arguments = run.call_args.args[0]  # type: ignore[attr-defined]
        self.assertEqual(r"C:\Program Files\Docker\docker.exe", arguments[0])
        self.assertFalse(run.call_args.kwargs["shell"])  # type: ignore[attr-defined]

    @patch("awakening.orchestration.m4.matrix_delegation.subprocess.run")
    def test_forged_target_and_skill_fail_before_helper(self, run: object) -> None:
        with self.assertRaises(MatrixDelegationError):
            self.port.dispatch(
                target_identity="independent_quality_reviewer",
                payload={
                    "program_id": self.program_id,
                    "run_id": self.run_id,
                    "state_version": 2,
                    "skill_name": "review_evidence_against_rubric",
                    "skill_version": "1.0.0",
                },
            )
        with self.assertRaises(MatrixDelegationError):
            self.port.dispatch(
                target_identity="role_project_architect",
                payload={
                    "program_id": self.program_id,
                    "run_id": self.run_id,
                    "state_version": 2,
                    "skill_name": "coach_task_submission",
                    "skill_version": "1.0.0",
                },
            )
        run.assert_not_called()  # type: ignore[attr-defined]

    @patch("awakening.orchestration.m4.matrix_delegation.subprocess.run")
    def test_reviewer_uses_separate_id_only_command(self, run: object) -> None:
        run.return_value = subprocess.CompletedProcess(  # type: ignore[attr-defined]
            args=(), returncode=0, stdout="$review:matrix-m4.local:8080\n", stderr=""
        )
        self.port.dispatch_reviewer_contract_smoke(
            context={
                "program_id": self.program_id,
                "run_id": self.run_id,
                "state_version": 2,
            }
        )
        arguments = run.call_args.args[0]  # type: ignore[attr-defined]
        self.assertEqual("dispatch-reviewer-contract-smoke", arguments[6])
        self.assertEqual(self.markers["independent_quality_reviewer"], arguments[7])
        self.assertEqual(
            {"program_id", "run_id", "state_version"}, set(json.loads(arguments[8]))
        )
        self.assertEqual(
            self.packages["independent_quality_reviewer"], json.loads(arguments[9])
        )

    @patch("awakening.orchestration.m4.matrix_delegation.subprocess.run")
    def test_trusted_packages_are_detached_and_not_overridable(self, run: object) -> None:
        with self.assertRaises(ValueError):
            MatrixManagerDelegationPort(
                request_markers=self.markers,
                trusted_worker_packages={
                    key: value
                    for key, value in self.packages.items()
                    if key != "independent_quality_reviewer"
                },
            )

        self.packages["role_project_architect"]["role_facts"].append(  # type: ignore[union-attr]
            {"source_ref": "forged", "requirement": "Caller mutation"}
        )
        run.return_value = subprocess.CompletedProcess(  # type: ignore[attr-defined]
            args=(), returncode=0, stdout="$delivery:matrix-m4.local:8080\n", stderr=""
        )
        self.port.dispatch(
            target_identity="role_project_architect",
            payload={
                "program_id": self.program_id,
                "run_id": self.run_id,
                "state_version": 2,
                "skill_name": "analyze_role_gap",
                "skill_version": "1.0.0",
            },
        )
        arguments = run.call_args.args[0]  # type: ignore[attr-defined]
        frozen_package = json.loads(arguments[10])
        self.assertEqual(1, len(frozen_package["role_facts"]))

        with self.assertRaises(MatrixDelegationError):
            self.port.dispatch(
                target_identity="role_project_architect",
                payload={
                    "program_id": self.program_id,
                    "run_id": self.run_id,
                    "state_version": 2,
                    "skill_name": "analyze_role_gap",
                    "skill_version": "1.0.0",
                    "trusted_package": {"forged": True},
                },
            )

    @patch("awakening.orchestration.m4.matrix_delegation.subprocess.run")
    def test_secret_marker_in_worker_response_fails_closed(self, run: object) -> None:
        run.return_value = subprocess.CompletedProcess(  # type: ignore[attr-defined]
            args=(),
            returncode=0,
            stdout=json.dumps(
                {
                    "target_identity": "execution_evidence_coach",
                    "delivery_id": "$delivery:matrix-m4.local:8080",
                    "response_event_id": "$response:matrix-m4.local:8080",
                    "text": "Authorization: Bearer hidden-secret-value",
                }
            ),
            stderr="",
        )
        with self.assertRaises(MatrixDelegationError):
            self.port.await_response(
                target_identity="execution_evidence_coach",
                delivery_id="$delivery:matrix-m4.local:8080",
            )

    @patch("awakening.orchestration.m4.matrix_delegation.subprocess.run")
    def test_exact_allowlisted_helper_error_is_reportable(self, run: object) -> None:
        for line_ending in ("", "\n", "\r\n"):
            with self.subTest(line_ending=repr(line_ending)):
                run.return_value = subprocess.CompletedProcess(  # type: ignore[attr-defined]
                    args=(),
                    returncode=78,
                    stdout="",
                    stderr="M4_MATRIX_WORKER_RESPONSE_TIMEOUT" + line_ending,
                )
                with self.assertRaises(MatrixDelegationError) as caught:
                    self.port.await_response(
                        target_identity="role_project_architect",
                        delivery_id="$delivery:matrix-m4.local:8080",
                        timeout_seconds=1,
                    )
                self.assertEqual(
                    "M4_MATRIX_WORKER_RESPONSE_TIMEOUT",
                    caught.exception.safe_helper_code,
                )

    @patch("awakening.orchestration.m4.matrix_delegation.subprocess.run")
    def test_worker_wait_allows_240_seconds_and_denies_241(self, run: object) -> None:
        run.return_value = subprocess.CompletedProcess(  # type: ignore[attr-defined]
            args=(),
            returncode=0,
            stdout=json.dumps(
                {
                    "target_identity": "role_project_architect",
                    "delivery_id": "$delivery:matrix-m4.local:8080",
                    "response_event_id": "$response:matrix-m4.local:8080",
                    "text": "{}",
                }
            ),
            stderr="",
        )
        response = self.port.await_response(
            target_identity="role_project_architect",
            delivery_id="$delivery:matrix-m4.local:8080",
            timeout_seconds=240,
        )
        self.assertEqual("{}", response.text)
        self.assertEqual(255, run.call_args.kwargs["timeout"])  # type: ignore[attr-defined]

        run.reset_mock()  # type: ignore[attr-defined]
        with self.assertRaises(MatrixDelegationError):
            self.port.await_response(
                target_identity="role_project_architect",
                delivery_id="$delivery:matrix-m4.local:8080",
                timeout_seconds=241,
            )
        run.assert_not_called()  # type: ignore[attr-defined]

    @patch("awakening.orchestration.m4.matrix_delegation.subprocess.run")
    def test_untrusted_helper_stderr_remains_opaque(self, run: object) -> None:
        denied = (
            "M4_MATRIX_WORKER_RESPONSE_TIMEOUT ",
            "M4_MATRIX_WORKER_RESPONSE_TIMEOUT\nAuthorization: Bearer hidden-secret",
            "M4_MATRIX_FUTURE_CODE\n",
        )
        for stderr in denied:
            with self.subTest(stderr=stderr.splitlines()[0]):
                run.return_value = subprocess.CompletedProcess(  # type: ignore[attr-defined]
                    args=(), returncode=78, stdout="", stderr=stderr
                )
                with self.assertRaises(MatrixDelegationError) as caught:
                    self.port.await_response(
                        target_identity="role_project_architect",
                        delivery_id="$delivery:matrix-m4.local:8080",
                        timeout_seconds=1,
                    )
                self.assertEqual("matrix_helper_failed", str(caught.exception))
                self.assertIsNone(caught.exception.safe_helper_code)
                self.assertNotIn("hidden-secret", repr(caught.exception))

        run.return_value = subprocess.CompletedProcess(  # type: ignore[attr-defined]
            args=(),
            returncode=125,
            stdout="",
            stderr="M4_MATRIX_WORKER_RESPONSE_TIMEOUT\n",
        )
        with self.assertRaises(MatrixDelegationError) as caught:
            self.port.await_response(
                target_identity="role_project_architect",
                delivery_id="$delivery:matrix-m4.local:8080",
                timeout_seconds=1,
            )
        self.assertIsNone(caught.exception.safe_helper_code)


if __name__ == "__main__":
    unittest.main()
