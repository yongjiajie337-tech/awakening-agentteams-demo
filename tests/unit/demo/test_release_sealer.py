"""Standard-library tests for the deterministic package release sealer."""

from __future__ import annotations

from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
SEALER = PACKAGE_ROOT / "scripts" / "package" / "seal_package.py"
PACKAGE_VERIFIER = PACKAGE_ROOT / "scripts" / "package" / "verify_package.py"
SOURCE_PINS = PACKAGE_ROOT / "config" / "reference-source-pins.json"
GENERATED = (
    "config/reference-source-pins.json",
    "PACKAGE_MANIFEST.json",
    "SHA256SUMS.txt",
)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("RELEASE_SEAL_TEST_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SEALER_MODULE = _load_module("awakening_test_release_sealer", SEALER)
VERIFIER_MODULE = _load_module("awakening_test_release_verifier", PACKAGE_VERIFIER)


def _write(root: Path, relative: str, value: bytes) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(value)


def _seed_fixture(
    root: Path,
    *,
    git_marker: bytes = b"ignored-git-metadata\n",
    git_name: str = ".git",
) -> None:
    source_document = json.loads(SOURCE_PINS.read_text(encoding="utf-8"))
    source_paths = [entry["path"] for entry in source_document["files"]]
    if len(source_paths) != 180:
        raise AssertionError("release fixture requires the frozen 180-file pin scope")
    for relative in source_paths:
        _write(root, relative, f"fixture:{relative}\n".encode("utf-8"))

    _write(root, "VERSION", b"9.8.7\n")
    _write(root, "verify_offline.ps1", b"$expectedFullUnitTestCount = 321\n")
    _write(
        root,
        "scripts/package/verify_package.py",
        (
            'PACKAGE_NAME = "awakening-agentteams-demo"\n'
            'PACKAGE_VERSION = "9.8.7"\n'
            "OFFLINE_UNIT_TEST_COUNT = 321\n"
        ).encode("utf-8"),
    )
    _write(
        root,
        "pyproject.toml",
        b'[project]\nname = "awakening-agentteams-demo"\nversion = "9.8.7"\n',
    )
    _write(root, "README.md", b"fixture package\n")
    (root / "config").mkdir(exist_ok=True)
    _write(root, f"{git_name}/config", git_marker)


def _run(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(SEALER),
            "--package-root",
            str(root),
            *arguments,
        ],
        cwd=PACKAGE_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )


def _generated_bytes(root: Path) -> dict[str, bytes]:
    return {relative: (root / relative).read_bytes() for relative in GENERATED}


def _generated_metadata(root: Path) -> dict[str, tuple[int, int]]:
    return {
        relative: (
            (root / relative).stat().st_size,
            (root / relative).stat().st_mtime_ns,
        )
        for relative in GENERATED
    }


class ReleaseSealerTests(unittest.TestCase):
    def test_write_check_and_generated_file_scopes_are_exact(self) -> None:
        self.assertEqual(
            tuple(pattern.pattern for pattern in VERIFIER_MODULE.SENSITIVE_VALUE_PATTERNS),
            tuple(pattern.pattern for pattern in SEALER_MODULE.SENSITIVE_VALUE_PATTERNS),
        )
        self.assertEqual(
            tuple(pattern.pattern for pattern in VERIFIER_MODULE.HOST_PATH_PATTERNS),
            tuple(pattern.pattern for pattern in SEALER_MODULE.HOST_PATH_PATTERNS),
        )
        self.assertEqual(
            VERIFIER_MODULE.TEXT_BOUNDARY_SUFFIXES,
            SEALER_MODULE.TEXT_BOUNDARY_SUFFIXES,
        )
        self.assertEqual(
            VERIFIER_MODULE.TEXT_BOUNDARY_FILENAMES,
            SEALER_MODULE.TEXT_BOUNDARY_FILENAMES,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "package"
            root.mkdir()
            _seed_fixture(root)

            written = _run(root, "--write")
            self.assertEqual(0, written.returncode, written.stdout + written.stderr)
            self.assertIn("PACKAGE_RELEASE_SEAL_MODE=write", written.stdout)
            self.assertIn("PACKAGE_RELEASE_SEAL_UPDATED=3", written.stdout)

            generated = _generated_bytes(root)
            for value in generated.values():
                self.assertTrue(value.endswith(b"\n"))
                self.assertNotIn(b"\r\n", value)
                value.decode("utf-8")

            manifest = json.loads(generated["PACKAGE_MANIFEST.json"])
            manifest_paths = {entry["path"] for entry in manifest["files"]}
            self.assertNotIn("PACKAGE_MANIFEST.json", manifest_paths)
            self.assertNotIn("SHA256SUMS.txt", manifest_paths)
            self.assertIn("config/reference-source-pins.json", manifest_paths)
            self.assertEqual(len(manifest_paths), manifest["payload_file_count"])

            sums = {}
            for line in generated["SHA256SUMS.txt"].decode("utf-8").splitlines():
                digest, relative = line.split("  ", 1)
                sums[relative] = digest
            self.assertIn("PACKAGE_MANIFEST.json", sums)
            self.assertNotIn("SHA256SUMS.txt", sums)
            for relative, digest in sums.items():
                self.assertEqual(sha256((root / relative).read_bytes()).hexdigest(), digest)

            before_bytes = _generated_bytes(root)
            before_metadata = _generated_metadata(root)
            checked = _run(root)
            self.assertEqual(0, checked.returncode, checked.stdout + checked.stderr)
            self.assertIn("PACKAGE_RELEASE_SEAL_MODE=check", checked.stdout)
            self.assertIn("PACKAGE_RELEASE_SEAL_UPDATED=0", checked.stdout)
            self.assertEqual(before_bytes, _generated_bytes(root))
            self.assertEqual(before_metadata, _generated_metadata(root))

            idempotent_write = _run(root, "--write")
            self.assertEqual(
                0,
                idempotent_write.returncode,
                idempotent_write.stdout + idempotent_write.stderr,
            )
            self.assertIn("PACKAGE_RELEASE_SEAL_UPDATED=0", idempotent_write.stdout)
            self.assertEqual(before_bytes, _generated_bytes(root))
            self.assertEqual(before_metadata, _generated_metadata(root))
            self.assertEqual([], list(root.rglob(".seal-package-*.tmp")))

    def test_outputs_are_deterministic_across_independent_fixtures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            roots = (base / "one", base / "two")
            _seed_fixture(roots[0], git_marker=b"first-private-git-state\n")
            _seed_fixture(
                roots[1],
                git_marker=b"different-private-git-state\n",
                git_name=".GIT",
            )
            for root in roots:
                completed = _run(root, "--write")
                self.assertEqual(
                    0,
                    completed.returncode,
                    completed.stdout + completed.stderr,
                )
            self.assertEqual(_generated_bytes(roots[0]), _generated_bytes(roots[1]))

    def test_stale_check_is_read_only_and_does_not_echo_changed_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "package"
            root.mkdir()
            _seed_fixture(root)
            self.assertEqual(0, _run(root, "--write").returncode)
            before_bytes = _generated_bytes(root)
            before_metadata = _generated_metadata(root)
            sensitive_marker = "DO_NOT_ECHO_THIS_CHANGED_CONTENT"
            (root / "README.md").write_text(sensitive_marker + "\n", encoding="utf-8")

            checked = _run(root)
            combined = checked.stdout + checked.stderr
            self.assertEqual(1, checked.returncode, combined)
            self.assertIn("PACKAGE_RELEASE_SEAL=FAIL:STALE", combined)
            self.assertIn("PACKAGE_RELEASE_SEAL_STALE=PACKAGE_MANIFEST.json", combined)
            self.assertIn("PACKAGE_RELEASE_SEAL_STALE=SHA256SUMS.txt", combined)
            self.assertNotIn(sensitive_marker, combined)
            self.assertNotIn(str(root), combined)
            self.assertEqual(before_bytes, _generated_bytes(root))
            self.assertEqual(before_metadata, _generated_metadata(root))
            self.assertEqual([], list(root.rglob(".seal-package-*.tmp")))

    def test_nested_metadata_secret_and_transient_residue_fail_closed(self) -> None:
        cases = (
            ("payload/.git/config", b"nested-private-git-content\n"),
            ("payload/.GIT/config", b"nested-uppercase-git-content\n"),
            ("payload/.secrets/provider.env", b"secret-fixture-value\n"),
            ("payload/.SECRETS/provider.env", b"uppercase-secret-fixture\n"),
            ("payload/__pycache__/probe.pyc", b"bytecode-fixture\n"),
            ("payload/TMP/probe.txt", b"uppercase-tmp-fixture\n"),
            ("payload/.env", b"environment-fixture\n"),
            (".seal-package-probe.1.deadbeef.tmp", b"stale-stage-fixture\n"),
            ("payload/api-key-note.md", b"sk-" + (b"A" * 24) + b"\n"),
            ("payload/auth-note.txt", b"Bearer " + (b"z" * 24) + b"\n"),
            (
                "payload/private-key-note.md",
                b"-----BEGIN " + b"PRIVATE KEY-----\n",
            ),
        )
        for relative, content in cases:
            with self.subTest(relative=relative):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary) / "package"
                    root.mkdir()
                    _seed_fixture(root)
                    self.assertEqual(0, _run(root, "--write").returncode)
                    before = _generated_bytes(root)
                    _write(root, relative, content)

                    checked = _run(root)
                    combined = checked.stdout + checked.stderr
                    self.assertEqual(1, checked.returncode, combined)
                    self.assertIn("PACKAGE_RELEASE_SEAL=FAIL:", combined)
                    self.assertNotIn(content.decode("utf-8").strip(), combined)
                    self.assertNotIn(str(root), combined)
                    self.assertNotIn("Traceback", combined)
                    self.assertEqual(before, _generated_bytes(root))


if __name__ == "__main__":
    unittest.main()
