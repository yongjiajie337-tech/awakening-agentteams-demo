"""Windows-only synthetic contracts for the Provider Secret ACL gate.

The fixture uses a non-secret placeholder in a temporary directory.  It imports
the exact ACL functions from the packaged lifecycle script and never invokes
Preflight, Docker, Matrix, the network, or a Provider.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
LIFECYCLE = PACKAGE_ROOT / "scripts" / "demo" / "Invoke-AgentTeamsInPlaceDemo.ps1"
DRIVER = PACKAGE_ROOT / "scripts" / "demo" / "agentteams_in_place_demo.py"
FIXTURE = Path(__file__).with_name("Invoke-ProviderSecretAclFixtures.ps1")


def _windows_powershell() -> str | None:
    candidates = (
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe",
        Path(shutil.which("powershell.exe") or ""),
    )
    return next((str(path) for path in candidates if path.is_file()), None)


class ProviderSecretAclContractTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows ACL contract")
    def test_semantic_acl_gate_accepts_only_tight_or_restricted_reader(self) -> None:
        powershell = _windows_powershell()
        if powershell is None:
            self.skipTest("Windows PowerShell 5.1 is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            completed = subprocess.run(
                [
                    powershell,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(FIXTURE),
                    "-LifecycleScript",
                    str(LIFECYCLE),
                    "-FixtureRoot",
                    temporary,
                ],
                cwd=PACKAGE_ROOT,
                check=False,
                capture_output=True,
                text=False,
                timeout=30,
            )
        combined = completed.stdout + completed.stderr
        self.assertEqual(0, completed.returncode, combined.decode("utf-8", "replace"))
        expected = (
            b"ACL_FIXTURE_CASE=tight:PASS",
            b"ACL_FIXTURE_CASE=restricted-reader:PASS",
            b"ACL_FIXTURE_CASE=broad-principal:REJECTED",
            b"ACL_FIXTURE_CASE=writer:REJECTED",
            b"ACL_FIXTURE_CASE=two-readers:REJECTED",
            b"ACL_FIXTURE_CASE=missing-admin:REJECTED",
            b"ACL_FIXTURE_DIRECTORY_CASE=directory-tight:PASS",
            b"ACL_FIXTURE_DIRECTORY_CASE=directory-restricted-reader:PASS",
            b"ACL_FIXTURE_DIRECTORY_CASE=directory-broad-principal:REJECTED",
            b"ACL_FIXTURE_DIRECTORY_CASE=directory-writer:REJECTED",
            b"ACL_FIXTURE_SECRET_VALUE_READ=false",
            b"ACL_FIXTURE_PROVIDER_CALLED=false",
        )
        for marker in expected:
            with self.subTest(marker=marker):
                self.assertIn(marker, combined)

    @unittest.skipUnless(os.name == "nt", "Windows directory-junction contract")
    def test_parent_directory_junction_is_rejected_before_secret_access(self) -> None:
        powershell = _windows_powershell()
        if powershell is None:
            self.skipTest("Windows PowerShell 5.1 is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            completed = subprocess.run(
                [
                    powershell,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(FIXTURE),
                    "-LifecycleScript",
                    str(LIFECYCLE),
                    "-FixtureRoot",
                    temporary,
                ],
                cwd=PACKAGE_ROOT,
                check=False,
                capture_output=True,
                text=False,
                timeout=30,
            )
        combined = completed.stdout + completed.stderr
        self.assertEqual(0, completed.returncode, combined.decode("utf-8", "replace"))
        if b"ACL_FIXTURE_PARENT_JUNCTION=SKIP" in combined:
            self.skipTest("directory junction creation is unavailable")
        self.assertIn(b"ACL_FIXTURE_PARENT_JUNCTION=REJECTED", combined)

    def test_lifecycle_uses_semantic_acl_result_without_reading_secret(self) -> None:
        source = LIFECYCLE.read_text(encoding="utf-8")
        function = source.split("function Assert-DemoProviderSecretAcl", 1)[1].split(
            "function Assert-DemoHostRelayHelper", 1
        )[0]
        preflight = source.split("function Invoke-Preflight", 1)[1].split(
            "function Invoke-StartInfrastructure", 1
        )[0]
        secret_gate = preflight.split("$secretPath = Assert-RegularFile", 1)[1].split(
            "foreach ($port in $ports)", 1
        )[0]
        for sid in (
            "S-1-1-0",
            "S-1-5-7",
            "S-1-5-11",
            "S-1-5-32-545",
            "S-1-5-32-546",
        ):
            self.assertIn(sid, function)
        self.assertIn("FileSystemRights]::FullControl", function)
        self.assertIn("FileSystemRights]::Read", function)
        self.assertIn("FileSystemRights]::Synchronize", function)
        self.assertIn("$rules.Count -notin @(3, 4)", function)
        self.assertIn("$rule.IsInherited", function)
        self.assertIn("AccessControlType]::Allow", function)
        self.assertIn("Assert-DemoProviderSecretAcl", preflight)
        self.assertIn("Assert-DemoProviderSecretDirectory", preflight)
        self.assertLess(
            preflight.index("Assert-DemoProviderSecretDirectory"),
            preflight.index("$secretPath = Assert-RegularFile"),
        )
        self.assertNotIn("ReadAllBytes", secret_gate)
        self.assertNotIn("ReadAllText", secret_gate)
        self.assertNotIn("Get-Content", secret_gate)

        driver = DRIVER.read_text(encoding="utf-8")
        reader = driver.split("def _read_demo_provider_key", 1)[1].split(
            "def _build_live_gateway", 1
        )[0]
        self.assertIn("is_junction", reader)
        self.assertIn("DEMO_PROVIDER_SECRET_PARENT_DIRECTORY_INVALID", reader)
        self.assertLess(reader.index("parent.is_symlink()"), reader.index("_regular_file"))
        self.assertLess(reader.index("parent.resolve(strict=True)"), reader.index("read_text"))


if __name__ == "__main__":
    unittest.main()
