"""Standard-library contracts for the public offline verification entrypoint."""

from __future__ import annotations

from importlib.metadata import distributions
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = PACKAGE_ROOT / "verify_offline.ps1"
PACKAGE_VERIFIER = PACKAGE_ROOT / "scripts" / "package" / "verify_package.py"


def _dependency_preflight_source() -> str:
    source = ENTRYPOINT.read_text(encoding="utf-8")
    match = re.search(
        r"\$dependencyPreflight = @'\r?\n(?P<body>.*?)\r?\n'@",
        source,
        re.DOTALL,
    )
    if match is None:
        raise AssertionError("dependency preflight here-string is missing")
    return match.group("body")


def _run_dependency_preflight(lock_text: str) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "requirements-demo.lock").write_text(lock_text, encoding="utf-8")
        return subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                _dependency_preflight_source(),
                str(root),
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )


class OfflineVerifyContractTests(unittest.TestCase):
    def test_modes_and_legacy_alias_are_explicit_and_closed(self) -> None:
        source = ENTRYPOINT.read_text(encoding="utf-8")
        self.assertIn('[ValidateSet("Full", "Stdlib", "PackageOnly")]', source)
        self.assertIn('$effectiveMode = "PackageOnly"', source)
        self.assertIn("OFFLINE_VERIFY_MODE_CONFLICT", source)
        self.assertIn(
            "OFFLINE_VERIFY_COMPATIBILITY_ALIAS=SkipUnitTests->PackageOnly",
            source,
        )
        for relative in (
            "tests/unit/demo/test_demo_matrix_control_contract.py",
            "tests/unit/demo/test_demo_worker_gateway_key_sync_contract.py",
            "tests/unit/demo/test_offline_verify_contract.py",
        ):
            self.assertIn(relative, source)

        dependency_gate = source.index(
            'Write-Output "OFFLINE_VERIFY_DEPENDENCY_PREFLIGHT=PASS"'
        )
        payload_verifier = source.index(
            '"-I", "-B", $verifier, "--package-root", $packageRoot'
        )
        total_pass = source.index('Write-Output "PACKAGE_OFFLINE_VERIFY=PASS"')
        self.assertLess(dependency_gate, payload_verifier)
        self.assertLess(payload_verifier, total_pass)

    def test_dependency_recovery_hints_have_ascii_fallbacks(self) -> None:
        source = ENTRYPOINT.read_text(encoding="utf-8")
        hints = re.findall(r'Write-Host "OFFLINE_VERIFY_HINT_EN=([^"\r\n]+)"', source)
        self.assertEqual(5, len(hints))
        self.assertTrue(all(hint.isascii() for hint in hints))
        combined = "\n".join(hints)
        for expected in (
            "requirements-demo.lock is invalid",
            "A locked dependency is missing",
            "A dependency version does not match the lock",
            "A locked dependency cannot be imported",
            "Dependency preflight failed unexpectedly",
        ):
            self.assertIn(expected, combined)

    def test_python_bytecode_residue_fails_without_deleting_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied_root = Path(temporary) / "package"
            shutil.copytree(
                PACKAGE_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
            )
            residue = (
                copied_root
                / "scripts"
                / "package"
                / "__pycache__"
                / "probe.cpython-312.pyc"
            )
            residue.parent.mkdir(parents=True, exist_ok=False)
            residue.write_bytes(b"not-executable-bytecode")
            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    str(copied_root / "scripts" / "package" / "verify_package.py"),
                    "--package-root",
                    str(copied_root),
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
            )
            combined = completed.stdout + completed.stderr
            self.assertEqual(1, completed.returncode)
            self.assertIn(
                "PACKAGE_TRANSIENT_RESIDUE_FOUND="
                "type=python-bytecode;path=scripts/package/__pycache__",
                combined,
            )
            self.assertIn(
                "PACKAGE_TRANSIENT_RESIDUE_RECOVERY=REEXTRACT_ORIGINAL_ZIP",
                combined,
            )
            self.assertTrue(residue.is_file())
            self.assertNotIn("PACKAGE_PAYLOAD_VERIFY=PASS", combined)

    def test_malformed_lock_reports_category_without_echoing_line(self) -> None:
        unsafe_line = "TOKEN_SUPER_SECRET==1.0==2.0"
        completed = _run_dependency_preflight(unsafe_line + "\n")
        combined = completed.stdout + completed.stderr
        self.assertEqual(78, completed.returncode)
        self.assertIn(
            "OFFLINE_VERIFY_DEPENDENCY_LOCK_INVALID="
            "line=1;category=exact-pin-separator-count",
            combined,
        )
        self.assertNotIn(unsafe_line, combined)
        self.assertNotIn("Traceback", combined)

    def test_missing_package_reports_name_expected_and_missing_actual(self) -> None:
        package = "awakening-review-package-never-installed"
        completed = _run_dependency_preflight(f"{package}==1.2.3\n")
        combined = completed.stdout + completed.stderr
        self.assertEqual(79, completed.returncode)
        self.assertIn(
            "OFFLINE_VERIFY_DEPENDENCY_MISSING="
            f"package={package};expected=1.2.3;actual=MISSING",
            combined,
        )
        self.assertNotIn("Traceback", combined)

    def test_version_mismatch_reports_expected_and_sanitized_actual(self) -> None:
        installed = sorted(
            (
                str(distribution.metadata.get("Name", "")),
                str(distribution.version),
            )
            for distribution in distributions()
            if re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._-]*",
                str(distribution.metadata.get("Name", "")),
            )
            and re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._+!-]*",
                str(distribution.version),
            )
        )
        if not installed:
            self.skipTest("no installed distribution is available for mismatch fixture")
        package, actual = installed[0]
        expected = "0.0.0" if actual != "0.0.0" else "0.0.1"
        completed = _run_dependency_preflight(f"{package}=={expected}\n")
        combined = completed.stdout + completed.stderr
        self.assertEqual(80, completed.returncode)
        self.assertIn(
            "OFFLINE_VERIFY_DEPENDENCY_VERSION_MISMATCH="
            f"package={package};expected={expected};actual={actual}",
            combined,
        )
        self.assertNotIn("Traceback", combined)


if __name__ == "__main__":
    unittest.main()
