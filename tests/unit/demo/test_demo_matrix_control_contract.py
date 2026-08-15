"""Offline source contract for the Demo-only Matrix control helper."""

from __future__ import annotations

from pathlib import Path
import re
import unittest


WORKSPACE = Path(__file__).resolve().parents[3]
HELPER = (
    WORKSPACE
    / "infra"
    / "agentteams"
    / "demo"
    / "runtime"
    / "demo-matrix-control.sh"
)


class DemoMatrixControlContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = HELPER.read_text(encoding="utf-8")

    def section(self, start: str, end: str) -> str:
        start_index = self.source.index(start)
        return self.source[start_index : self.source.index(end, start_index)]

    def test_only_four_commands_have_exact_argument_counts(self) -> None:
        main = self.section('case "${1:-}" in', '        *) fail "DEMO_MATRIX_COMMAND_DENIED"')
        commands = re.findall(r"^        ([a-z][a-z-]+)\)$", main, re.MULTILINE)
        self.assertEqual(
            ["discover", "baseline", "await-human-request", "publish-event"],
            commands,
        )
        for command, count in (
            ("discover", 3),
            ("baseline", 4),
            ("await-human-request", 8),
            ("publish-event", 11),
        ):
            self.assertRegex(
                main,
                rf"{re.escape(command)}\)\n\s+\[\[ \$# -eq {count} \]\]",
            )

    def test_human_request_body_and_event_binding_are_exact(self) -> None:
        expected = (
            'Awakening AgentTeams Demo | demo_request_id=${demo_request_id} | '
            'demo_run_id=${demo_run_id} | fixed synthetic job package | '
            'Manager coordinates Architect, Coach, Reviewer.'
        )
        self.assertEqual(1, self.source.count(expected))
        capture = self.section("await_human_request()", "validate_publish_binding()")
        for binding in (
            '.sender == $human',
            '.content.msgtype == "m.text"',
            '.content.body == $body',
            '((.content."m.mentions"? // {}) == {})',
            "candidate_count <= 1",
            "verify_human_event",
        ):
            self.assertIn(binding, capture)
        readback = self.section("verify_human_event()", "discover_control_room()")
        self.assertIn(".event_id == $event_id", readback)
        self.assertIn(".sender == $human", readback)
        self.assertIn(".content.body == $body", readback)

    def test_publish_phases_and_targets_are_closed(self) -> None:
        binding = self.section("validate_publish_binding()", "build_publish_body()")
        branches = re.findall(r"^        ([a-z][a-z|-]+)\)$", binding, re.MULTILINE)
        self.assertEqual(
            [
                "request-accepted",
                "worker-dispatched|worker-completed",
                "summary-completed|summary-failed",
                "runtime-stopping",
            ],
            branches,
        )
        for target in (
            "role_project_architect",
            "execution_evidence_coach",
            "independent_quality_reviewer",
        ):
            self.assertIn(target, binding)
        self.assertIn('target}" == "all"', binding)
        self.assertIn('target}" == "manager"', binding)

    def test_publish_is_a_fixed_reply_with_server_readback(self) -> None:
        publish = self.section("publish_event()", "main()")
        self.assertIn('msgtype:"m.notice"', publish)
        self.assertIn('"m.relates_to":{"m.in_reply_to":{event_id:$parent}}', publish)
        self.assertIn("/send/m.room.message/", publish)
        self.assertIn("matrix_get", publish)
        self.assertIn(".sender == $manager", publish)
        self.assertIn(".content.body == $body", publish)
        self.assertIn('.content."m.relates_to" ==', publish)
        self.assertNotIn('"m.mentions"', publish)

    def test_manager_token_stays_in_container_temp_header(self) -> None:
        auth = self.section("prepare_auth_header()", "validate_room_id()")
        self.assertIn('Authorization: Bearer %s\\n', auth)
        self.assertIn('> "${AUTH_HEADER_FILE}"', auth)
        self.assertIn('chmod 600 "${AUTH_HEADER_FILE}"', auth)
        self.assertIn('token=""', auth)
        self.assertIn("unset token", auth)
        self.assertEqual(1, self.source.count("Authorization: Bearer"))
        self.assertNotIn("--arg token", self.source)
        self.assertNotIn("token_sha256", self.source)
        self.assertNotIn("access_token_sha256", self.source)
        self.assertNotRegex(self.source, r"(?m)^\s*(?:echo|Write-Output).*(?:token|TOKEN)")

    def test_sensitive_text_gate_covers_human_and_publish_bodies(self) -> None:
        gate = self.section("reject_sensitive_text()", "matrix_get()")
        for denied in (
            "access[_ -]?token",
            "api[_ -]?key",
            "password",
            "secret",
            "authorization",
            "bearer",
            "PRIVATE KEY",
            "HICLAW_",
            "WORKER_",
            '"channels"',
            '"plugins"',
        ):
            self.assertIn(denied, gate)
        self.assertGreaterEqual(self.source.count("reject_sensitive_text"), 4)
        self.assertIn("DEMO_MATRIX_SENSITIVE_TEXT_DENIED", gate)

    def test_helper_has_no_m4_or_m5_write_target(self) -> None:
        self.assertIn(
            'readonly STATE_DIR="/run/awakening-demo/matrix-control-v1"',
            self.source,
        )
        for forbidden in (
            "/run/awakening-m4",
            "/run/awakening-m5",
            "tmp/m4",
            "tmp/m5",
            "artifacts/m4",
            "artifacts/m5",
            # Legacy internal Secret path remains forbidden in Matrix control.
            ".env.m5.provider",
            "PROGRESS.md",
            "BLOCKED.md",
            "DECISIONS.md",
            "docker ",
        ):
            self.assertNotIn(forbidden, self.source)
        self.assertNotRegex(self.source, r"(?m)^\s*(?:cp|mv|sed|tee)\b")


if __name__ == "__main__":
    unittest.main()
