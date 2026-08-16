"""Standard-library contracts for the public offline verification entrypoint."""

from __future__ import annotations

import importlib.util
from importlib.metadata import distributions
import os
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


def _load_package_verifier():
    module_name = "awakening_test_package_verifier"
    spec = importlib.util.spec_from_file_location(module_name, PACKAGE_VERIFIER)
    if spec is None or spec.loader is None:
        raise RuntimeError("PACKAGE_VERIFIER_TEST_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


VERIFIER = _load_package_verifier()


def _create_required_structure(root: Path) -> None:
    for relative in VERIFIER.REQUIRED_PATHS:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture\n")


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
    def test_open_source_release_files_are_required_and_text_scanned(self) -> None:
        expected_required = {
            ".editorconfig",
            ".gitattributes",
            ".github/workflows/offline-verify.yml",
            "README.en.md",
            "CONTRIBUTING.md",
            "CODE_OF_CONDUCT.md",
            "SUPPORT.md",
            "SECURITY.md",
            "docs/JUDGE_GUIDE.md",
            "docs/JUDGE_GUIDE.en.md",
            "docs/SKILLS_OVERVIEW.md",
            "docs/SKILLS_OVERVIEW.en.md",
            "docs/SECURITY_MODEL.md",
            "CITATION.cff",
            "config/demo-provider.env.example",
            "scripts/package/seal_package.py",
        }
        self.assertTrue(expected_required.issubset(set(VERIFIER.REQUIRED_PATHS)))
        self.assertIn(".cff", VERIFIER.TEXT_BOUNDARY_SUFFIXES)
        self.assertEqual(
            {".editorconfig", ".gitattributes", "LICENSE"},
            VERIFIER.TEXT_BOUNDARY_FILENAMES,
        )

    def test_root_git_metadata_is_pruned_from_checkout_payload(self) -> None:
        for metadata_name in (".git", ".GIT"):
            for metadata_kind in ("directory", "worktree-file"):
                with self.subTest(
                    metadata_name=metadata_name,
                    metadata_kind=metadata_kind,
                ):
                    with tempfile.TemporaryDirectory() as temporary:
                        root = Path(temporary)
                        _create_required_structure(root)
                        if metadata_kind == "directory":
                            git_config = root / metadata_name / "config"
                            git_config.parent.mkdir()
                            git_config.write_bytes(b"not-package-payload\n")
                        else:
                            (root / metadata_name).write_bytes(
                                b"gitdir: ../private-worktree-metadata\n"
                            )

                        VERIFIER._verify_structure(root)
                        payload = VERIFIER._all_payload_files(root)
                        self.assertFalse(
                            any(
                                path.casefold() == ".git"
                                or path.casefold().startswith(".git/")
                                for path in payload
                            )
                        )

    def test_nested_git_and_existing_transient_guards_remain_closed(self) -> None:
        cases = (
            ("payload/.git", "FORBIDDEN_DIRECTORY:payload/.git", True),
            ("payload/.GIT", "FORBIDDEN_DIRECTORY:payload/.GIT", True),
            (
                "payload-worktree/.git",
                "FORBIDDEN_DIRECTORY:payload-worktree/.git",
                False,
            ),
            (
                "payload-worktree/.GIT",
                "FORBIDDEN_DIRECTORY:payload-worktree/.GIT",
                False,
            ),
            (
                "payload/.secrets",
                "FORBIDDEN_DIRECTORY:payload/.secrets",
                True,
            ),
            (
                "payload/.SECRETS",
                "FORBIDDEN_DIRECTORY:payload/.SECRETS",
                True,
            ),
            (
                "payload/__pycache__",
                "PACKAGE_TRANSIENT_RESIDUE_FOUND="
                "type=python-bytecode;path=payload/__pycache__",
                True,
            ),
            (
                "payload/__PYCACHE__",
                "PACKAGE_TRANSIENT_RESIDUE_FOUND="
                "type=python-bytecode;path=payload/__PYCACHE__",
                True,
            ),
            ("payload/tmp", "FORBIDDEN_DIRECTORY:payload/tmp", True),
            ("payload/TMP", "FORBIDDEN_DIRECTORY:payload/TMP", True),
            ("payload/.env", "FORBIDDEN_ENV_FILE:payload/.env", False),
        )
        for relative, expected, is_directory in cases:
            with self.subTest(relative=relative):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    _create_required_structure(root)
                    target = root / relative
                    if is_directory:
                        target.mkdir(parents=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(b"forbidden-fixture\n")
                    with self.assertRaisesRegex(
                        VERIFIER.VerificationError,
                        "^" + re.escape(expected) + "$",
                    ):
                        VERIFIER._verify_structure(root)

    def test_payload_scan_statically_rejects_reparse_and_non_regular_paths(self) -> None:
        source = PACKAGE_VERIFIER.read_text(encoding="utf-8")
        for expected in (
            ".lstat()",
            "FILE_ATTRIBUTE_REPARSE_POINT",
            "stat.S_ISREG",
            "stat.S_ISDIR",
            "followlinks=False",
            "args.package_root.absolute()",
        ):
            self.assertIn(expected, source)
        self.assertNotIn("verify(args.package_root.resolve())", source)

    def test_file_symlink_and_directory_reparse_are_rejected_before_read(self) -> None:
        exercised = 0
        for reparse_kind in ("file-symlink", "directory-junction"):
            with self.subTest(reparse_kind=reparse_kind):
                with tempfile.TemporaryDirectory() as temporary:
                    base = Path(temporary)
                    root = base / "package"
                    root.mkdir()
                    _create_required_structure(root)
                    payload = root / "payload"
                    payload.mkdir()
                    if reparse_kind == "file-symlink":
                        outside = base / "outside-file.txt"
                        outside.write_bytes(b"outside-sensitive-marker\n")
                        link = payload / "file-link.txt"
                        try:
                            os.symlink(outside, link)
                        except OSError:
                            continue
                    else:
                        outside = base / "outside-directory"
                        outside.mkdir()
                        (outside / "marker.txt").write_bytes(
                            b"outside-sensitive-marker\n"
                        )
                        link = payload / "directory-link"
                        if os.name == "nt":
                            command = Path(
                                os.environ.get("SystemRoot", r"C:\Windows")
                            ) / "System32" / "cmd.exe"
                            completed = subprocess.run(
                                [
                                    str(command),
                                    "/d",
                                    "/c",
                                    "mklink",
                                    "/J",
                                    str(link),
                                    str(outside),
                                ],
                                check=False,
                                capture_output=True,
                                text=False,
                                timeout=15,
                            )
                            if completed.returncode != 0:
                                continue
                        else:
                            try:
                                os.symlink(outside, link, target_is_directory=True)
                            except OSError:
                                continue
                    exercised += 1
                    with self.assertRaisesRegex(
                        VERIFIER.VerificationError,
                        "^PACKAGE_NON_REGULAR_PATH:payload/",
                    ) as raised:
                        VERIFIER._verify_structure(root)
                    self.assertNotIn("outside-sensitive-marker", str(raised.exception))
        if exercised == 0:
            self.skipTest("file symlink and directory reparse creation unavailable")

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
