"""Deterministically build or check the public package release seal.

The default mode is read-only. Pass ``--write`` explicitly to replace each
stale generated release-index file with a per-file atomic operation. The
implementation uses only the Python standard library and never starts Docker, opens a network
connection, reads a Provider Secret, or invokes a model.
"""

from __future__ import annotations

import argparse
import ast
from collections.abc import Mapping
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import sys
import tomllib


DEFAULT_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_NAME = "awakening-agentteams-demo"
PACKAGE_PROFILE = "dual-layer-sanitized-review"
AGENTTEAMS_REFERENCE_VERSION = "v1.1.2"
EVIDENCE_RUN_COUNT = 2
REFERENCE_PIN_COUNT = 180

REFERENCE_PINS_PATH = "config/reference-source-pins.json"
MANIFEST_PATH = "PACKAGE_MANIFEST.json"
SUMS_PATH = "SHA256SUMS.txt"
GENERATED_PATHS = (REFERENCE_PINS_PATH, MANIFEST_PATH, SUMS_PATH)

FIXED_DEMO_PIN_PATHS = {
    "scripts/demo/agentteams_in_place_demo.py",
    "scripts/demo/Invoke-AgentTeamsInPlaceDemo.ps1",
    "scripts/demo/Start-AgentTeamsDemoHostRelay.ps1",
}
PIN_PREFIXES = (
    "scripts/m4/",
    "infra/agentteams/demo/runtime/",
    "infra/agentteams/m4/",
    "src/",
    "schemas/",
    "contracts/",
    "agents/",
    "skills/",
)
PIN_EXCLUSIONS = {"infra/agentteams/m4/controller.env.example"}

FORBIDDEN_DIRECTORY_NAMES = {
    ".git",
    ".secrets",
    ".venv",
    "__pycache__",
    "tmp",
}
FORBIDDEN_EXACT_FILENAMES = {
    ".env",
    ".env.m2",
    ".env.m4",
    ".env.m5.provider",
    "controller.env",
    "gateway.pid",
    "state-http.pid",
}
FORBIDDEN_SUFFIXES = (".pem", ".p12", ".pfx", ".key", ".pid")
TEXT_BOUNDARY_SUFFIXES = {
    ".cff",
    ".json",
    ".jsonl",
    ".md",
    ".ps1",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
TEXT_BOUNDARY_FILENAMES = {".editorconfig", ".gitattributes", "LICENSE"}
FORBIDDEN_TEXT_FRAGMENTS: tuple[str, ...] = ()
HOST_PATH_PATTERNS = (
    re.compile(r"(?i)[A-Z]:\\Users\\[^\\\r\n\"']+\\"),
    re.compile(r"[A-Z]:\\[^\r\n\"'`]*[\u4e00-\u9fff][^\r\n\"'`]*"),
)
SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?<![A-Za-z0-9])(?:sk|ak)-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~-]{20,}"),
    re.compile(r"https?://[^\s/:@]+:[^\s/@]+@"),
    re.compile(r"(?i)postgres(?:ql)?://[^\s/:@]+:[^\s/@]+@"),
)
SAFE_DISPLAY_PATH_RE = re.compile(r"^[A-Za-z0-9._/ -]{1,240}$")
SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
SEAL_TEMP_PREFIX = ".seal-package-"


class SealError(RuntimeError):
    """A stable release-seal failure that never includes file contents."""

    def __init__(self, code: str, relative: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.relative = relative

    def __str__(self) -> str:
        if self.relative is None:
            return self.code
        return f"{self.code}:path={_display_path(self.relative)}"


def _display_path(relative: str) -> str:
    if SAFE_DISPLAY_PATH_RE.fullmatch(relative):
        return relative
    digest = sha256(relative.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"<redacted-{digest}>"


def _json_bytes(document: object) -> bytes:
    return (
        json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def _digest_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def _is_reparse_point(metadata: os.stat_result) -> bool:
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _safe_relative(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError as exc:
        raise SealError("PACKAGE_PATH_OUTSIDE_ROOT") from exc
    parsed = PurePosixPath(relative)
    if (
        not relative
        or parsed.is_absolute()
        or ".." in parsed.parts
        or "\\" in relative
        or any(ord(character) < 32 or ord(character) == 127 for character in relative)
    ):
        raise SealError("PACKAGE_PATH_UNSAFE")
    return parsed.as_posix()


def _lstat(path: Path, code: str, relative: str | None = None) -> os.stat_result:
    try:
        return path.lstat()
    except OSError as exc:
        raise SealError(code, relative) from exc


def _file_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_mode,
    )


def _read_stable_file(path: Path, relative: str) -> bytes:
    before = _lstat(path, "PACKAGE_FILE_READ_FAILED", relative)
    if _is_reparse_point(before) or not stat.S_ISREG(before.st_mode):
        raise SealError("PACKAGE_NON_REGULAR_FILE", relative)
    try:
        value = path.read_bytes()
    except OSError as exc:
        raise SealError("PACKAGE_FILE_READ_FAILED", relative) from exc
    after = _lstat(path, "PACKAGE_FILE_READ_FAILED", relative)
    if (
        _is_reparse_point(after)
        or not stat.S_ISREG(after.st_mode)
        or _file_identity(before) != _file_identity(after)
        or len(value) != after.st_size
    ):
        raise SealError("PACKAGE_FILE_CHANGED_DURING_READ", relative)
    return value


def _validate_directory(relative: str, metadata: os.stat_result) -> None:
    if _is_reparse_point(metadata) or not stat.S_ISDIR(metadata.st_mode):
        raise SealError("PACKAGE_NON_REGULAR_DIRECTORY", relative)
    name = PurePosixPath(relative).name.casefold()
    if name == "__pycache__":
        raise SealError("PACKAGE_TRANSIENT_RESIDUE", relative)
    if name in FORBIDDEN_DIRECTORY_NAMES:
        raise SealError("FORBIDDEN_DIRECTORY", relative)


def _validate_file(relative: str, metadata: os.stat_result) -> None:
    if _is_reparse_point(metadata) or not stat.S_ISREG(metadata.st_mode):
        raise SealError("PACKAGE_NON_REGULAR_FILE", relative)
    name = PurePosixPath(relative).name.casefold()
    if name.startswith(SEAL_TEMP_PREFIX) and name.endswith(".tmp"):
        raise SealError("PACKAGE_SEAL_TEMP_RESIDUE", relative)
    if "__pycache__" in (part.casefold() for part in PurePosixPath(relative).parts[:-1]):
        raise SealError("PACKAGE_TRANSIENT_RESIDUE", relative)
    if name.endswith((".pyc", ".pyo")):
        raise SealError("PACKAGE_TRANSIENT_RESIDUE", relative)
    if name == ".git" or ".git" in (
        part.casefold() for part in PurePosixPath(relative).parts[:-1]
    ):
        raise SealError("FORBIDDEN_DIRECTORY", relative)
    if name.startswith(".env") and name != ".env.example":
        raise SealError("FORBIDDEN_ENV_FILE", relative)
    if name in FORBIDDEN_EXACT_FILENAMES:
        raise SealError("FORBIDDEN_FILE", relative)
    if name.endswith(FORBIDDEN_SUFFIXES):
        raise SealError("FORBIDDEN_FILE_SUFFIX", relative)


def _scan_payload(root: Path) -> dict[str, bytes]:
    root_metadata = _lstat(root, "PACKAGE_ROOT_INVALID")
    if _is_reparse_point(root_metadata) or not stat.S_ISDIR(root_metadata.st_mode):
        raise SealError("PACKAGE_ROOT_INVALID")

    def fail_closed(_error: OSError) -> None:
        raise SealError("PACKAGE_TREE_READ_FAILED")

    result: dict[str, bytes] = {}
    try:
        iterator = os.walk(
            root,
            topdown=True,
            onerror=fail_closed,
            followlinks=False,
        )
        for current_text, directory_names, file_names in iterator:
            current = Path(current_text)
            if current == root:
                directory_names[:] = [
                    name for name in directory_names if name.casefold() != ".git"
                ]
                file_names[:] = [
                    name for name in file_names if name.casefold() != ".git"
                ]
            directory_names.sort(key=str.casefold)
            file_names.sort(key=str.casefold)

            for name in directory_names:
                path = current / name
                relative = _safe_relative(path, root)
                metadata = _lstat(path, "PACKAGE_TREE_READ_FAILED", relative)
                _validate_directory(relative, metadata)

            for name in file_names:
                path = current / name
                relative = _safe_relative(path, root)
                metadata = _lstat(path, "PACKAGE_TREE_READ_FAILED", relative)
                _validate_file(relative, metadata)
                if relative in result:
                    raise SealError("PACKAGE_PATH_DUPLICATE", relative)
                result[relative] = _read_stable_file(path, relative)
    except SealError:
        raise
    except OSError as exc:
        raise SealError("PACKAGE_TREE_READ_FAILED") from exc
    _verify_text_boundary(result)
    return result


def _verify_text_boundary(files: Mapping[str, bytes]) -> None:
    """Apply the package verifier's public text-boundary policy before sealing."""

    for relative, value in files.items():
        path = PurePosixPath(relative)
        if (
            path.suffix.casefold() not in TEXT_BOUNDARY_SUFFIXES
            and path.name not in TEXT_BOUNDARY_FILENAMES
        ):
            continue
        try:
            text = value.decode("utf-8")
        except UnicodeError as exc:
            raise SealError("TEXT_NOT_UTF8", relative) from exc
        for fragment in FORBIDDEN_TEXT_FRAGMENTS:
            if fragment in text:
                raise SealError("HOST_PATH_OR_SECRET_MOUNT_LEAK", relative)
        for pattern in SENSITIVE_VALUE_PATTERNS + HOST_PATH_PATTERNS:
            if pattern.search(text):
                raise SealError("SENSITIVE_VALUE_PATTERN_FOUND", relative)


def _decode_utf8(files: Mapping[str, bytes], relative: str) -> str:
    try:
        value = files[relative]
    except KeyError as exc:
        raise SealError("RELEASE_AUTHORITY_FILE_MISSING", relative) from exc
    try:
        return value.decode("utf-8")
    except UnicodeError as exc:
        raise SealError("RELEASE_AUTHORITY_FILE_NOT_UTF8", relative) from exc


def _read_version(files: Mapping[str, bytes]) -> str:
    text = _decode_utf8(files, "VERSION")
    lines = text.splitlines()
    if len(lines) != 1 or not SEMVER_RE.fullmatch(lines[0]):
        raise SealError("VERSION_FILE_INVALID", "VERSION")
    return lines[0]


def _read_full_test_count(files: Mapping[str, bytes]) -> int:
    text = _decode_utf8(files, "verify_offline.ps1")
    matches = re.findall(
        r"(?m)^\$expectedFullUnitTestCount\s*=\s*([0-9]+)\s*$",
        text,
    )
    if len(matches) != 1:
        raise SealError("FULL_TEST_COUNT_AUTHORITY_INVALID", "verify_offline.ps1")
    value = int(matches[0])
    if value <= 0:
        raise SealError("FULL_TEST_COUNT_AUTHORITY_INVALID", "verify_offline.ps1")
    return value


def _read_verifier_constants(files: Mapping[str, bytes]) -> dict[str, object]:
    relative = "scripts/package/verify_package.py"
    text = _decode_utf8(files, relative)
    try:
        tree = ast.parse(text, filename=relative)
    except SyntaxError as exc:
        raise SealError("PACKAGE_VERIFIER_SYNTAX_INVALID", relative) from exc
    wanted = {"PACKAGE_NAME", "PACKAGE_VERSION", "OFFLINE_UNIT_TEST_COUNT"}
    values: dict[str, object] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id not in wanted:
            continue
        if target.id in values:
            raise SealError("PACKAGE_VERIFIER_CONSTANT_DUPLICATE", relative)
        try:
            values[target.id] = ast.literal_eval(node.value)
        except (ValueError, TypeError) as exc:
            raise SealError("PACKAGE_VERIFIER_CONSTANT_INVALID", relative) from exc
    if set(values) != wanted:
        raise SealError("PACKAGE_VERIFIER_CONSTANT_MISSING", relative)
    return values


def _verify_authorities(files: Mapping[str, bytes]) -> tuple[str, int]:
    # VERSION is the release authority. pyproject.toml and the offline verifier
    # constants must agree with it. CITATION.cff and NOTICE.md may intentionally
    # describe the immutable v1.0.2 competition baseline, so they are not
    # treated as current-release version authorities here.
    version = _read_version(files)
    full_test_count = _read_full_test_count(files)
    verifier = _read_verifier_constants(files)
    if verifier["PACKAGE_NAME"] != PACKAGE_NAME:
        raise SealError(
            "PACKAGE_NAME_AUTHORITY_MISMATCH",
            "scripts/package/verify_package.py",
        )
    if verifier["PACKAGE_VERSION"] != version:
        raise SealError(
            "PACKAGE_VERSION_AUTHORITY_MISMATCH",
            "scripts/package/verify_package.py",
        )
    if verifier["OFFLINE_UNIT_TEST_COUNT"] != full_test_count:
        raise SealError(
            "FULL_TEST_COUNT_AUTHORITY_MISMATCH",
            "scripts/package/verify_package.py",
        )

    pyproject_text = _decode_utf8(files, "pyproject.toml")
    try:
        pyproject = tomllib.loads(pyproject_text)
        project = pyproject["project"]
        pyproject_version = project["version"]
    except (tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        raise SealError("PYPROJECT_VERSION_AUTHORITY_INVALID", "pyproject.toml") from exc
    if pyproject_version != version:
        raise SealError("PYPROJECT_VERSION_AUTHORITY_MISMATCH", "pyproject.toml")
    return version, full_test_count


def _is_reference_pin_path(relative: str) -> bool:
    if relative in PIN_EXCLUSIONS:
        return False
    return relative in FIXED_DEMO_PIN_PATHS or relative.startswith(PIN_PREFIXES)


def _build_documents(files: Mapping[str, bytes]) -> dict[str, bytes]:
    version, full_test_count = _verify_authorities(files)
    pin_paths = sorted(
        (relative for relative in files if _is_reference_pin_path(relative)),
        key=str.casefold,
    )
    if len(pin_paths) != REFERENCE_PIN_COUNT:
        raise SealError("REFERENCE_SOURCE_PIN_SCOPE_COUNT_INVALID")

    reference_pins = {
        "schema_version": "awakening.agentteams.demo.reference-source-pins.v1",
        "agentteams_version": AGENTTEAMS_REFERENCE_VERSION,
        "hash_algorithm": "sha256",
        "file_count": len(pin_paths),
        "excluded_public_examples": sorted(PIN_EXCLUSIONS, key=str.casefold),
        "files": [
            {"path": relative, "sha256": _digest_bytes(files[relative])}
            for relative in pin_paths
        ],
    }
    pins_bytes = _json_bytes(reference_pins)

    overlay = dict(files)
    overlay[REFERENCE_PINS_PATH] = pins_bytes
    manifest_paths = sorted(
        (
            relative
            for relative in overlay
            if relative not in {MANIFEST_PATH, SUMS_PATH}
        ),
        key=str.casefold,
    )
    manifest = {
        "schema_version": 1,
        "package_name": PACKAGE_NAME,
        "package_version": version,
        "profile": PACKAGE_PROFILE,
        "agentteams_reference_version": AGENTTEAMS_REFERENCE_VERSION,
        "offline_unit_test_count": full_test_count,
        "evidence_run_count": EVIDENCE_RUN_COUNT,
        "payload_file_count": len(manifest_paths),
        "files": [
            {
                "path": relative,
                "size_bytes": len(overlay[relative]),
                "sha256": _digest_bytes(overlay[relative]),
            }
            for relative in manifest_paths
        ],
    }
    manifest_bytes = _json_bytes(manifest)

    overlay[MANIFEST_PATH] = manifest_bytes
    sums_entries = [
        (_digest_bytes(value), relative)
        for relative, value in overlay.items()
        if relative != SUMS_PATH
    ]
    sums_entries.sort(key=lambda item: (item[1].casefold(), item[1]))
    sums_bytes = "".join(
        f"{digest}  {relative}\n" for digest, relative in sums_entries
    ).encode("utf-8")
    return {
        REFERENCE_PINS_PATH: pins_bytes,
        MANIFEST_PATH: manifest_bytes,
        SUMS_PATH: sums_bytes,
    }


def build_release_seal(root: Path) -> tuple[dict[str, bytes], dict[str, bytes]]:
    """Return current payload bytes and the deterministic generated bytes."""

    files = _scan_payload(root)
    return files, _build_documents(files)


def _atomic_write_documents(
    root: Path,
    documents: Mapping[str, bytes],
) -> None:
    staged: list[tuple[Path, Path]] = []
    try:
        for relative in GENERATED_PATHS:
            if relative not in documents:
                continue
            target = root / PurePosixPath(relative)
            parent_metadata = _lstat(
                target.parent,
                "PACKAGE_SEAL_PARENT_INVALID",
                PurePosixPath(relative).parent.as_posix(),
            )
            if _is_reparse_point(parent_metadata) or not stat.S_ISDIR(
                parent_metadata.st_mode
            ):
                raise SealError("PACKAGE_SEAL_PARENT_INVALID", relative)
            if target.exists() or target.is_symlink():
                target_metadata = _lstat(
                    target,
                    "PACKAGE_SEAL_TARGET_INVALID",
                    relative,
                )
                if _is_reparse_point(target_metadata) or not stat.S_ISREG(
                    target_metadata.st_mode
                ):
                    raise SealError("PACKAGE_SEAL_TARGET_INVALID", relative)
                target_mode = stat.S_IMODE(target_metadata.st_mode)
            else:
                target_mode = 0o644

            token = secrets.token_hex(8)
            temporary = target.parent / (
                f"{SEAL_TEMP_PREFIX}{target.name}.{os.getpid()}.{token}.tmp"
            )
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            flags |= getattr(os, "O_BINARY", 0)
            try:
                descriptor = os.open(temporary, flags, target_mode)
                staged.append((temporary, target))
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(documents[relative])
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError as exc:
                raise SealError("PACKAGE_SEAL_STAGE_WRITE_FAILED", relative) from exc

        for temporary, target in staged:
            relative = target.relative_to(root).as_posix()
            try:
                os.replace(temporary, target)
            except OSError as exc:
                raise SealError("PACKAGE_SEAL_ATOMIC_REPLACE_FAILED", relative) from exc
    finally:
        for temporary, _target in staged:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _stale_paths(
    files: Mapping[str, bytes],
    documents: Mapping[str, bytes],
) -> list[str]:
    return [
        relative
        for relative in GENERATED_PATHS
        if files.get(relative) != documents[relative]
    ]


def seal(root: Path, *, write: bool) -> tuple[str, list[str]]:
    files, documents = build_release_seal(root)
    stale = _stale_paths(files, documents)
    if not write:
        if stale:
            return "stale", stale
        return "current", []

    if stale:
        _atomic_write_documents(
            root,
            {relative: documents[relative] for relative in stale},
        )
    post_files, post_documents = build_release_seal(root)
    if post_documents != documents or _stale_paths(post_files, post_documents):
        raise SealError("PACKAGE_SEAL_POSTWRITE_VERIFY_FAILED")
    return "written", stale


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check or rebuild the deterministic public package release seal."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check",
        action="store_true",
        help="read-only check (default)",
    )
    mode.add_argument(
        "--write",
        action="store_true",
        help="replace each stale generated seal file with a per-file atomic operation",
    )
    parser.add_argument(
        "--package-root",
        type=Path,
        default=DEFAULT_PACKAGE_ROOT,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()

    try:
        supplied_root = args.package_root.absolute()
        supplied_metadata = _lstat(supplied_root, "PACKAGE_ROOT_INVALID")
        if _is_reparse_point(supplied_metadata) or not stat.S_ISDIR(
            supplied_metadata.st_mode
        ):
            raise SealError("PACKAGE_ROOT_INVALID")
        root = supplied_root.resolve(strict=True)
        result, paths = seal(root, write=args.write)
    except SealError as exc:
        print(f"PACKAGE_RELEASE_SEAL=FAIL:{exc}", file=sys.stderr)
        return 1
    except (OSError, RuntimeError, ValueError):
        print("PACKAGE_RELEASE_SEAL=FAIL:UNEXPECTED_INTERNAL_ERROR", file=sys.stderr)
        return 2

    if result == "stale":
        print("PACKAGE_RELEASE_SEAL=FAIL:STALE", file=sys.stderr)
        for relative in paths:
            print(
                f"PACKAGE_RELEASE_SEAL_STALE={_display_path(relative)}",
                file=sys.stderr,
            )
        return 1

    print("PACKAGE_RELEASE_SEAL=PASS")
    print(f"PACKAGE_RELEASE_SEAL_MODE={'write' if args.write else 'check'}")
    print(f"PACKAGE_RELEASE_SEAL_UPDATED={len(paths)}")
    for relative in paths:
        print(f"PACKAGE_RELEASE_SEAL_FILE={_display_path(relative)}")
    print("NETWORK_USED=false")
    print("DOCKER_USED=false")
    print("PROVIDER_CALLED=false")
    print("SECRET_VALUE_READ=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
