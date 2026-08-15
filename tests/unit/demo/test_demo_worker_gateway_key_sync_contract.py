"""Offline safety contract for the Demo-only Worker gateway-key helper.

The suite never executes a valid helper command, because a valid command reads
the protected in-container runtime file.  Dynamic checks stop at argument or
allowlist validation; all mutation and probe properties are source contracts.
"""

from __future__ import annotations

from collections import Counter
import os
from pathlib import Path
import re
import shutil
import subprocess
import unittest
from unittest.mock import patch


WORKSPACE = Path(__file__).resolve().parents[3]
HELPER = (
    WORKSPACE
    / "infra"
    / "agentteams"
    / "demo"
    / "runtime"
    / "demo-worker-gateway-key-sync.sh"
)
SOURCE = HELPER.read_text(encoding="utf-8")


def _is_git_for_windows_bash(candidate: str | None) -> bool:
    if not candidate:
        return False
    normalized = str(Path(candidate)).replace("/", "\\").casefold()
    return normalized.endswith("\\git\\bin\\bash.exe") or normalized.endswith(
        "\\git\\usr\\bin\\bash.exe"
    )


def _find_git_bash() -> str | None:
    candidates = [
        str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe"),
        str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "usr" / "bin" / "bash.exe"),
    ]
    local_app_data = os.environ.get("LocalAppData")
    if local_app_data:
        candidates.append(
            str(Path(local_app_data) / "Programs" / "Git" / "bin" / "bash.exe")
        )
    candidates.append(shutil.which("bash"))
    return next(
        (
            candidate
            for candidate in candidates
            if _is_git_for_windows_bash(candidate) and Path(candidate).is_file()
        ),
        None,
    )


class DemoWorkerGatewayKeySyncContractTests(unittest.TestCase):
    def test_git_bash_discovery_rejects_wsl_launcher(self) -> None:
        self.assertFalse(_is_git_for_windows_bash(r"C:\Windows\System32\bash.exe"))
        self.assertFalse(_is_git_for_windows_bash(r"C:\Windows\System32\wsl.exe"))
        self.assertTrue(_is_git_for_windows_bash(r"C:\Tools\Git\bin\bash.exe"))
        self.assertTrue(_is_git_for_windows_bash(r"C:\Tools\Git\usr\bin\bash.exe"))
        with patch.object(shutil, "which", return_value=r"C:\Windows\System32\bash.exe"):
            with patch.object(Path, "is_file", return_value=False):
                self.assertIsNone(_find_git_bash())

    def test_interface_and_paths_are_exactly_demo_scoped(self) -> None:
        self.assertTrue(SOURCE.startswith("#!/bin/bash\nset -euo pipefail\n"))
        self.assertIn('if [ "$#" -ne 2 ]; then', SOURCE)
        self.assertRegex(
            SOURCE,
            re.compile(
                r'case "\$\{command_name\}" in\s+'
                r'inspect\|apply\|probe\) ;;\s+'
                r'\*\) fail COMMAND_INVALID ;;\s+esac'
            ),
        )
        self.assertRegex(
            SOURCE,
            re.compile(
                r'case "\$\{role\}" in\s+'
                r'role_project_architect\|independent_quality_reviewer\) ;;\s+'
                r'\*\) fail ROLE_INVALID ;;\s+esac'
            ),
        )
        self.assertNotIn("execution_evidence_coach|", SOURCE)
        self.assertIn(
            'config="/root/hiclaw-fs/agents/${role}/openclaw.json"',
            SOURCE,
        )
        self.assertIn(
            'active="/root/hiclaw-fs/agents/${role}/.openclaw/openclaw.json"',
            SOURCE,
        )
        self.assertIn(
            "runtime_env=/run/secrets/awakening-m4/runtime.env",
            SOURCE,
        )
        self.assertNotRegex(
            SOURCE,
            re.compile(r'(?:CONFIG|RUNTIME|TARGET|ROOT)_OVERRIDE'),
        )

    def test_root_config_is_regular_and_active_config_is_its_symlink(self) -> None:
        self.assertIn(
            'test -f "${config}" && test ! -L "${config}" || fail CONFIG_INVALID',
            SOURCE,
        )
        self.assertIn('test -L "${active}" || fail ACTIVE_CONFIG_LINK_INVALID', SOURCE)
        self.assertIn(
            '[ "$(readlink -f -- "${active}" 2>/dev/null || true)" = "${config}" ]',
            SOURCE,
        )
        self.assertIn(
            'test -f "${runtime_env}" && test ! -L "${runtime_env}" || '
            "fail RUNTIME_ENV_INVALID",
            SOURCE,
        )
        self.assertRegex(
            SOURCE,
            re.compile(
                r'\[ "\$\(readlink -f -- "\$\{active\}" 2>/dev/null \|\| true\)" '
                r'= "\$\{config\}" \] \|\| \\\n\s+fail ACTIVE_CONFIG_POSTVERIFY_INVALID'
            ),
        )

    def test_only_gateway_api_key_is_read_and_secret_never_enters_argv_or_env(self) -> None:
        self.assertIn(
            "grep -c '^HICLAW_WORKER_GATEWAY_KEY=' \"${runtime_env}\"",
            SOURCE,
        )
        self.assertIn('[ "${key_count}" = 1 ] || fail RUNTIME_KEY_FIELD_INVALID', SOURCE)
        self.assertIn(
            "sed -n 's/^HICLAW_WORKER_GATEWAY_KEY=//p' \"${runtime_env}\"",
            SOURCE,
        )
        api_key_lines = [line for line in SOURCE.splitlines() if "apiKey" in line]
        self.assertGreaterEqual(len(api_key_lines), 6)
        self.assertTrue(
            all(
                '.models.providers["hiclaw-gateway"].apiKey' in line
                for line in api_key_lines
            )
        )

        self.assertIn(
            'jq --rawfile replacement <(printf \'%s\' "${runtime_key}")',
            SOURCE,
        )
        self.assertRegex(
            SOURCE,
            re.compile(
                r"curl -q --config <\(\s+"
                r"printf 'header = \"Authorization: Bearer %s\"\\n' "
                r'"\$\{config_key\}"\s+\)',
                re.MULTILINE,
            ),
        )
        self.assertNotRegex(SOURCE, re.compile(r'(?:^|\s)--arg(?:json)?(?:\s|=)'))
        self.assertNotRegex(
            SOURCE,
            re.compile(r'(?:^|\s)-H\s+[\'\"]Authorization:', re.MULTILINE),
        )
        self.assertNotRegex(
            SOURCE,
            re.compile(r'docker\s+exec[^\n]*\s-e(?:\s|=)'),
        )
        self.assertNotRegex(
            SOURCE,
            re.compile(r'export\s+(?:runtime_key|config_key)|env\s+[^\n]*(?:runtime_key|config_key)'),
        )
        self.assertNotIn("sha256sum", SOURCE)
        self.assertNotIn("shasum", SOURCE)
        self.assertNotIn("md5sum", SOURCE)
        self.assertNotIn("openssl dgst", SOURCE)
        self.assertEqual(3, SOURCE.count("DEMO_WORKER_GATEWAY_SYNC_SECRET_HASHED=false"))
        self.assertEqual(3, SOURCE.count("DEMO_WORKER_GATEWAY_SYNC_SECRET_ECHOED=false"))

    def test_apply_is_single_field_atomic_forward_only_and_metadata_preserving(self) -> None:
        self.assertIn('tmp="${config}.awakening-demo-gateway-key-sync.tmp"', SOURCE)
        self.assertIn('test ! -e "${tmp}" || fail TEMP_TARGET_EXISTS', SOURCE)
        self.assertIn("trap 'rm -f -- \"${tmp}\" 2>/dev/null || true' EXIT", SOURCE)
        self.assertIn(
            '.models.providers["hiclaw-gateway"].apiKey = $replacement',
            SOURCE,
        )
        self.assertEqual(2, SOURCE.count("__AWAKENING_DEMO_SENTINEL__"))
        self.assertRegex(
            SOURCE,
            re.compile(
                r'jq -e -s \'\s*'
                r'\(\(\.\[0\].*__AWAKENING_DEMO_SENTINEL__.*\)\s*'
                r'==\s*'
                r'\(\.\[1\].*__AWAKENING_DEMO_SENTINEL__.*\)\)\s*\'',
                re.DOTALL,
            ),
        )
        self.assertIn("fail CONFIG_NON_TARGET_DRIFT", SOURCE)
        self.assertIn("mode=\"$(stat -c '%a' -- \"${config}\"", SOURCE)
        self.assertIn("owner=\"$(stat -c '%u:%g' -- \"${config}\"", SOURCE)
        self.assertIn('chown "${owner}" "${tmp}"', SOURCE)
        self.assertIn('chmod "${mode}" "${tmp}"', SOURCE)
        self.assertIn('mv -f -- "${tmp}" "${config}"', SOURCE)
        self.assertIn("post_mode=\"$(stat -c '%a' -- \"${config}\"", SOURCE)
        self.assertIn("post_owner=\"$(stat -c '%u:%g' -- \"${config}\"", SOURCE)
        self.assertIn(
            '[ "${post_mode}" = "${mode}" ] && [ "${post_owner}" = "${owner}" ]',
            SOURCE,
        )
        self.assertNotRegex(SOURCE, re.compile(r'(?i)\b(?:backup|rollback)\b'))
        self.assertNotRegex(SOURCE, re.compile(r'(^|\s)cp\s', re.MULTILINE))
        self.assertNotIn('> "${config}"', SOURCE)

    def test_probe_is_fixed_fail_closed_authentication_without_provider_call(self) -> None:
        self.assertIn(
            '[ "${matches}" = true ] || fail CONFIG_RUNTIME_BINDING_INVALID',
            SOURCE,
        )
        self.assertIn(
            'probe_body=\'{"model":"qwen3.7-flash-2026-07-15",'
            '"messages":[{"role":"user","content":"demo fixed credential probe"}]}\'',
            SOURCE,
        )
        self.assertEqual(
            1,
            SOURCE.count(
                "http://host.docker.internal:18190/v1/chat/completions"
            ),
        )
        self.assertIn("--connect-timeout 3 --max-time 10", SOURCE)
        self.assertIn("status=\"${response##*|}\"", SOURCE)
        self.assertIn("reason=\"$(printf '%s' \"${body}\" | jq -er '.error.code'", SOURCE)
        self.assertIn(
            '[ "${status}" = 403 ] && [ "${reason}" = CALL_PLAN_UNAVAILABLE ]',
            SOURCE,
        )
        self.assertIn("DEMO_WORKER_GATEWAY_SYNC_GATEWAY_AUTH=PASS", SOURCE)
        self.assertIn("DEMO_WORKER_GATEWAY_SYNC_PROVIDER_CALL_COUNT=0", SOURCE)
        self.assertNotIn('printf \'%s\\n\' "${body}"', SOURCE)
        self.assertNotIn('printf \'%s\\n\' "${response}"', SOURCE)
        self.assertNotIn('printf \'%s\\n\' "${reason}"', SOURCE)
        self.assertNotRegex(
            SOURCE,
            re.compile(r'echo\s+[^\n]*"\$\{(?:body|response|reason)\}"'),
        )

    def test_stdout_markers_and_failure_channel_are_fixed(self) -> None:
        formats = [
            item
            for item in re.findall(r"printf '(DEMO_WORKER_GATEWAY_SYNC[^']*)'", SOURCE)
            if item != "DEMO_WORKER_GATEWAY_SYNC_%s\\n"
        ]
        expected = Counter(
            [
                "DEMO_WORKER_GATEWAY_SYNC=PASS\\n",
                "DEMO_WORKER_GATEWAY_SYNC_COMMAND=inspect\\n",
                "DEMO_WORKER_GATEWAY_SYNC_ROLE=%s\\n",
                "DEMO_WORKER_GATEWAY_SYNC_MATCH=%s\\n",
                "DEMO_WORKER_GATEWAY_SYNC_SECRET_READ=true\\n",
                "DEMO_WORKER_GATEWAY_SYNC_SECRET_HASHED=false\\n",
                "DEMO_WORKER_GATEWAY_SYNC_SECRET_ECHOED=false\\n",
                "DEMO_WORKER_GATEWAY_SYNC=PASS\\n",
                "DEMO_WORKER_GATEWAY_SYNC_COMMAND=apply\\n",
                "DEMO_WORKER_GATEWAY_SYNC_ROLE=%s\\n",
                "DEMO_WORKER_GATEWAY_SYNC_CHANGED=%s\\n",
                "DEMO_WORKER_GATEWAY_SYNC_SECRET_READ=true\\n",
                "DEMO_WORKER_GATEWAY_SYNC_SECRET_HASHED=false\\n",
                "DEMO_WORKER_GATEWAY_SYNC_SECRET_ECHOED=false\\n",
                "DEMO_WORKER_GATEWAY_SYNC=PASS\\n",
                "DEMO_WORKER_GATEWAY_SYNC_COMMAND=probe\\n",
                "DEMO_WORKER_GATEWAY_SYNC_ROLE=%s\\n",
                "DEMO_WORKER_GATEWAY_SYNC_GATEWAY_AUTH=PASS\\n",
                "DEMO_WORKER_GATEWAY_SYNC_PROVIDER_CALL_COUNT=0\\n",
                "DEMO_WORKER_GATEWAY_SYNC_SECRET_READ=true\\n",
                "DEMO_WORKER_GATEWAY_SYNC_SECRET_HASHED=false\\n",
                "DEMO_WORKER_GATEWAY_SYNC_SECRET_ECHOED=false\\n",
            ]
        )
        self.assertEqual(expected, Counter(formats))
        self.assertIn(
            "printf 'DEMO_WORKER_GATEWAY_SYNC_%s\\n' \"$1\" >&2",
            SOURCE,
        )
        self.assertIn("exit 78", SOURCE)
        for code in re.findall(r"\bfail ([A-Z][A-Z0-9_]+)", SOURCE):
            self.assertRegex(code, r"^[A-Z][A-Z0-9_]+$")

    def test_invalid_inputs_stop_before_files_network_or_secrets(self) -> None:
        bash = _find_git_bash()
        if bash is None:
            self.skipTest("bash is unavailable")
        cases = (
            ((), "ARGUMENTS_INVALID"),
            (("unknown", "role_project_architect"), "COMMAND_INVALID"),
            (("inspect", "execution_evidence_coach"), "ROLE_INVALID"),
        )
        for arguments, code in cases:
            with self.subTest(arguments=arguments):
                completed = subprocess.run(
                    [bash, str(HELPER), *arguments],
                    cwd=WORKSPACE,
                    check=False,
                    capture_output=True,
                    text=False,
                    timeout=10,
                )
                self.assertEqual(78, completed.returncode)
                self.assertEqual(b"", completed.stdout)
                self.assertEqual(
                    f"DEMO_WORKER_GATEWAY_SYNC_{code}\n".encode("ascii"),
                    completed.stderr,
                )


if __name__ == "__main__":
    unittest.main()
