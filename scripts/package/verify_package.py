"""Offline verifier for the sanitized Awakening AgentTeams review package.

This verifier uses only the Python standard library.  It never starts Docker,
opens a network connection, reads an environment file, or invokes a model.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys


PACKAGE_NAME = "awakening-agentteams-demo"
PACKAGE_VERSION = "1.0.4"
OFFLINE_UNIT_TEST_COUNT = 97
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
REQUIRED_PATHS = (
    ".editorconfig",
    ".gitattributes",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/config.yml",
    ".github/ISSUE_TEMPLATE/feature_request.yml",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/dependabot.yml",
    ".github/workflows/offline-verify.yml",
    "README.md",
    "README.en.md",
    "QUICKSTART_WINDOWS.md",
    "EVIDENCE.md",
    "SECURITY.md",
    "SECURITY_AND_SECRETS.md",
    "docs/JUDGE_GUIDE.md",
    "docs/JUDGE_GUIDE.en.md",
    "docs/SKILLS_OVERVIEW.md",
    "docs/SKILLS_OVERVIEW.en.md",
    "docs/SECURITY_MODEL.md",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "SUPPORT.md",
    "CITATION.cff",
    "LICENSE",
    "NOTICE.md",
    "CHANGELOG.md",
    "VERSION",
    "run_demo.ps1",
    "verify_offline.ps1",
    "requirements-demo.lock",
    "pyproject.toml",
    "scripts/package/seal_package.py",
    "config/demo-provider.env.example",
    "config/reference-source-pins.json",
    "PACKAGE_MANIFEST.json",
    "SHA256SUMS.txt",
    "scripts/demo/agentteams_in_place_demo.py",
    "scripts/demo/Invoke-AgentTeamsInPlaceDemo.ps1",
    "scripts/m4/provision-provider-state.py",
    "scripts/m4/run-real-chain.py",
    "scripts/package/verify_package.py",
    "examples/input/demo-request.json",
    "examples/output/demo-result.json",
    "evidence/run-a/summary.json",
    "evidence/run-a/outputs/role_project_architect.json",
    "evidence/run-a/outputs/execution_evidence_coach.json",
    "evidence/run-a/outputs/independent_quality_reviewer.json",
    "evidence/run-b/summary.json",
    "evidence/run-b/outputs/role_project_architect.json",
    "evidence/run-b/outputs/execution_evidence_coach.json",
    "evidence/run-b/outputs/independent_quality_reviewer.json",
)
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
    # Legacy internal runtime filename: forbidden payload residue, not a Demo input.
    ".env.m5.provider",
    "controller.env",
    "gateway.pid",
    "state-http.pid",
}
FORBIDDEN_SUFFIXES = (".pem", ".p12", ".pfx", ".key", ".pid")
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
WORKER_OUTPUT_SPECS = {
    "role_project_architect": {
        "skill_name": "analyze_role_gap",
        "file": "role_project_architect.json",
        "schema": "schemas/m4/skills/analyze_role_gap.output.schema.json",
    },
    "execution_evidence_coach": {
        "skill_name": "coach_task_submission",
        "file": "execution_evidence_coach.json",
        "schema": "schemas/m4/skills/coach_task_submission.output.schema.json",
    },
    "independent_quality_reviewer": {
        "skill_name": "review_evidence_against_rubric",
        "file": "independent_quality_reviewer.json",
        "schema": "schemas/m4/skills/review_evidence_against_rubric.output.schema.json",
    },
}
WORKER_OUTPUT_FORBIDDEN_KEYS = {
    "absolute_path",
    "api_key",
    "authorization",
    "environment",
    "event_id",
    "headers",
    "matrix_body",
    "message",
    "messages",
    "password",
    "path",
    "pid",
    "process_id",
    "prompt",
    "prompts",
    "raw_response",
    "room_id",
    "secret",
    "token",
}
WORKER_OUTPUT_FORBIDDEN_VALUE_PATTERNS = (
    re.compile(r"(?i)(?:[A-Z]:\\|/(?:home|users|var|tmp)/)"),
    re.compile(r"(?i)(?:https?|matrix)://"),
    re.compile(r"![A-Za-z0-9_-]{5,}:[A-Za-z0-9._:-]+"),
    re.compile(r"\$[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?i)(?:api[_ -]?key|access[_ -]?token|secret|password)\s*[:=]\s*\S+"),
)
SUPPORTED_OUTPUT_SCHEMA_KEYWORDS = {
    "$schema",
    "$id",
    "title",
    "type",
    "additionalProperties",
    "required",
    "properties",
    "const",
    "enum",
    "minimum",
    "minItems",
    "maxItems",
    "uniqueItems",
    "items",
    "minLength",
    "maxLength",
    "pattern",
    "format",
    "allOf",
    "contains",
}


class VerificationError(RuntimeError):
    """A stable, user-facing package verification failure."""


def _digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f"JSON_INVALID:{path.as_posix()}") from exc


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise VerificationError(f"JSONL_READ_INVALID:{path.as_posix()}") from exc
    if not lines:
        raise VerificationError(f"JSONL_EMPTY:{path.as_posix()}")
    records: list[dict[str, object]] = []
    for index, line in enumerate(lines, start=1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise VerificationError(f"JSONL_INVALID:{path.as_posix()}:{index}") from exc
        if not isinstance(record, dict):
            raise VerificationError(f"JSONL_RECORD_INVALID:{path.as_posix()}:{index}")
        records.append(record)
    return records


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise VerificationError("WORKER_OUTPUT_CANONICAL_JSON_INVALID") from exc


def _schema_type_matches(value: object, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected, False)


def _json_values_equal(left: object, right: object) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    return left == right


def _schema_matches(value: object, schema: dict[str, object], path: str) -> bool:
    try:
        _validate_output_schema(value, schema, path)
    except VerificationError:
        return False
    return True


def _validate_output_schema(
    value: object,
    schema: dict[str, object],
    path: str = "$",
) -> None:
    """Validate the complete keyword subset used by the three bundled schemas.

    This intentionally stays in the standard library so ``-SkipUnitTests``
    still verifies the canonical payloads offline.  The default unit-test path
    independently repeats validation with the locked ``jsonschema`` package.
    """

    unsupported = set(schema) - SUPPORTED_OUTPUT_SCHEMA_KEYWORDS
    if unsupported:
        raise VerificationError(
            f"WORKER_OUTPUT_SCHEMA_KEYWORD_UNSUPPORTED:{path}:{','.join(sorted(unsupported))}"
        )

    expected_type = schema.get("type")
    if expected_type is not None:
        if not isinstance(expected_type, str) or not _schema_type_matches(value, expected_type):
            raise VerificationError(f"WORKER_OUTPUT_SCHEMA_TYPE_INVALID:{path}")

    if "const" in schema and not _json_values_equal(value, schema["const"]):
        raise VerificationError(f"WORKER_OUTPUT_SCHEMA_CONST_INVALID:{path}")
    choices = schema.get("enum")
    if choices is not None:
        if not isinstance(choices, list) or not any(
            _json_values_equal(value, choice) for choice in choices
        ):
            raise VerificationError(f"WORKER_OUTPUT_SCHEMA_ENUM_INVALID:{path}")

    for subschema in schema.get("allOf", []):
        if not isinstance(subschema, dict):
            raise VerificationError(f"WORKER_OUTPUT_SCHEMA_ALLOF_INVALID:{path}")
        _validate_output_schema(value, subschema, path)

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if not isinstance(properties, dict) or not isinstance(required, list):
            raise VerificationError(f"WORKER_OUTPUT_SCHEMA_OBJECT_INVALID:{path}")
        missing = [name for name in required if name not in value]
        if missing:
            raise VerificationError(
                f"WORKER_OUTPUT_SCHEMA_REQUIRED_INVALID:{path}:{','.join(missing)}"
            )
        if schema.get("additionalProperties") is False:
            extra = set(value) - set(properties)
            if extra:
                raise VerificationError(
                    f"WORKER_OUTPUT_SCHEMA_ADDITIONAL_PROPERTY:{path}:{','.join(sorted(extra))}"
                )
        for name, child in value.items():
            child_schema = properties.get(name)
            if child_schema is not None:
                if not isinstance(child_schema, dict):
                    raise VerificationError(f"WORKER_OUTPUT_SCHEMA_PROPERTY_INVALID:{path}.{name}")
                _validate_output_schema(child, child_schema, f"{path}.{name}")

    if isinstance(value, list):
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if isinstance(minimum, int) and len(value) < minimum:
            raise VerificationError(f"WORKER_OUTPUT_SCHEMA_MIN_ITEMS_INVALID:{path}")
        if isinstance(maximum, int) and len(value) > maximum:
            raise VerificationError(f"WORKER_OUTPUT_SCHEMA_MAX_ITEMS_INVALID:{path}")
        if schema.get("uniqueItems") is True:
            canonical_items = [_canonical_json_bytes(item) for item in value]
            if len(set(canonical_items)) != len(canonical_items):
                raise VerificationError(f"WORKER_OUTPUT_SCHEMA_UNIQUE_ITEMS_INVALID:{path}")
        item_schema = schema.get("items")
        if item_schema is not None:
            if not isinstance(item_schema, dict):
                raise VerificationError(f"WORKER_OUTPUT_SCHEMA_ITEMS_INVALID:{path}")
            for index, child in enumerate(value):
                _validate_output_schema(child, item_schema, f"{path}[{index}]")
        contains = schema.get("contains")
        if contains is not None:
            if not isinstance(contains, dict) or not any(
                _schema_matches(child, contains, f"{path}[{index}]")
                for index, child in enumerate(value)
            ):
                raise VerificationError(f"WORKER_OUTPUT_SCHEMA_CONTAINS_INVALID:{path}")

    if isinstance(value, str):
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        if isinstance(minimum, int) and len(value) < minimum:
            raise VerificationError(f"WORKER_OUTPUT_SCHEMA_MIN_LENGTH_INVALID:{path}")
        if isinstance(maximum, int) and len(value) > maximum:
            raise VerificationError(f"WORKER_OUTPUT_SCHEMA_MAX_LENGTH_INVALID:{path}")
        pattern = schema.get("pattern")
        if pattern is not None:
            if not isinstance(pattern, str) or re.search(pattern, value) is None:
                raise VerificationError(f"WORKER_OUTPUT_SCHEMA_PATTERN_INVALID:{path}")
        value_format = schema.get("format")
        if value_format is not None:
            if value_format != "uuid" or UUID_RE.fullmatch(value) is None:
                raise VerificationError(f"WORKER_OUTPUT_SCHEMA_FORMAT_INVALID:{path}")

    minimum_number = schema.get("minimum")
    if minimum_number is not None and isinstance(value, (int, float)):
        if isinstance(value, bool) or value < minimum_number:
            raise VerificationError(f"WORKER_OUTPUT_SCHEMA_MINIMUM_INVALID:{path}")


def _verify_worker_output_sensitivity(value: object, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in WORKER_OUTPUT_FORBIDDEN_KEYS:
                raise VerificationError(f"WORKER_OUTPUT_FORBIDDEN_KEY:{path}.{key}")
            _verify_worker_output_sensitivity(child, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _verify_worker_output_sensitivity(child, f"{path}[{index}]")
        return
    if not isinstance(value, str):
        return
    for fragment in FORBIDDEN_TEXT_FRAGMENTS:
        if fragment in value:
            raise VerificationError(f"WORKER_OUTPUT_HOST_PATH_FOUND:{path}")
    for pattern in (
        SENSITIVE_VALUE_PATTERNS
        + WORKER_OUTPUT_FORBIDDEN_VALUE_PATTERNS
        + HOST_PATH_PATTERNS
    ):
        if pattern.search(value):
            raise VerificationError(f"WORKER_OUTPUT_SENSITIVE_VALUE_FOUND:{path}")


def _safe_relative_path(text: object) -> str:
    if not isinstance(text, str) or not text:
        raise VerificationError("MANIFEST_PATH_INVALID")
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or "\\" in text:
        raise VerificationError(f"MANIFEST_PATH_UNSAFE:{text}")
    return path.as_posix()


def _is_reparse_point(metadata: os.stat_result) -> bool:
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _lstat_payload(path: Path, relative: str) -> os.stat_result:
    try:
        return path.lstat()
    except OSError as exc:
        raise VerificationError(f"PACKAGE_TREE_READ_FAILED:{relative}") from exc


def _verify_regular_payload_path(path: Path, relative: str) -> None:
    metadata = _lstat_payload(path, relative)
    if _is_reparse_point(metadata) or path.is_symlink():
        raise VerificationError(f"PACKAGE_NON_REGULAR_PATH:{relative}")
    if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)):
        raise VerificationError(f"PACKAGE_NON_REGULAR_PATH:{relative}")


def _iter_payload_paths(root: Path) -> Iterator[Path]:
    """Yield checkout payload paths without entering root Git metadata.

    A normal clone owns a top-level ``.git`` directory (or worktree metadata
    file), neither of which is part of the distributed payload.  Only that
    exact root entry is pruned.  A nested ``.git`` remains visible so the
    structure verifier can reject it as payload.
    """

    def fail_closed(error: OSError) -> None:
        raise VerificationError("PACKAGE_TREE_READ_FAILED") from error

    for current_text, directory_names, file_names in os.walk(
        root,
        topdown=True,
        onerror=fail_closed,
        followlinks=False,
    ):
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
            relative = path.relative_to(root).as_posix()
            _verify_regular_payload_path(path, relative)
            yield path
        for name in file_names:
            path = current / name
            relative = path.relative_to(root).as_posix()
            _verify_regular_payload_path(path, relative)
            yield path


def _all_payload_files(root: Path) -> list[str]:
    excluded = {"PACKAGE_MANIFEST.json", "SHA256SUMS.txt"}
    return sorted(
        path.relative_to(root).as_posix()
        for path in _iter_payload_paths(root)
        if path.is_file() and path.relative_to(root).as_posix() not in excluded
    )


def _verify_structure(root: Path) -> None:
    missing = [path for path in REQUIRED_PATHS if not (root / path).is_file()]
    if missing:
        raise VerificationError("REQUIRED_PATH_MISSING:" + ",".join(missing))

    for path in _iter_payload_paths(root):
        relative = path.relative_to(root)
        if path.is_dir() and path.name.casefold() == "__pycache__":
            raise VerificationError(
                "PACKAGE_TRANSIENT_RESIDUE_FOUND="
                f"type=python-bytecode;path={relative.as_posix()}"
            )
        if path.is_file():
            parent_parts = tuple(part.casefold() for part in relative.parts[:-1])
            if "__pycache__" in parent_parts:
                cache_index = parent_parts.index("__pycache__")
                residue_directory = PurePosixPath(
                    *relative.parts[: cache_index + 1]
                ).as_posix()
                raise VerificationError(
                    "PACKAGE_TRANSIENT_RESIDUE_FOUND="
                    f"type=python-bytecode;path={residue_directory}"
                )
            if path.suffix.casefold() in {".pyc", ".pyo"}:
                residue_directory = relative.parent.as_posix()
                raise VerificationError(
                    "PACKAGE_TRANSIENT_RESIDUE_FOUND="
                    f"type=python-bytecode;path={residue_directory}"
                )
        directory_parts = (
            relative.parts
            if path.is_dir() or path.name.casefold() == ".git"
            else relative.parts[:-1]
        )
        if any(
            part.casefold() in FORBIDDEN_DIRECTORY_NAMES
            for part in directory_parts
        ):
            raise VerificationError(f"FORBIDDEN_DIRECTORY:{relative.as_posix()}")
        if not path.is_file():
            continue
        name = path.name.lower()
        if name.startswith(".env") and name != ".env.example":
            raise VerificationError(f"FORBIDDEN_ENV_FILE:{relative.as_posix()}")
        if name in FORBIDDEN_EXACT_FILENAMES:
            raise VerificationError(f"FORBIDDEN_FILE:{relative.as_posix()}")
        if name.endswith(FORBIDDEN_SUFFIXES):
            raise VerificationError(f"FORBIDDEN_FILE_SUFFIX:{relative.as_posix()}")


def _verify_manifest(root: Path) -> None:
    document = _load_json(root / "PACKAGE_MANIFEST.json")
    if not isinstance(document, dict):
        raise VerificationError("MANIFEST_ROOT_INVALID")
    if set(document) != {
        "schema_version",
        "package_name",
        "package_version",
        "profile",
        "agentteams_reference_version",
        "offline_unit_test_count",
        "evidence_run_count",
        "payload_file_count",
        "files",
    }:
        raise VerificationError("MANIFEST_FIELDS_INVALID")
    if document.get("schema_version") != 1:
        raise VerificationError("MANIFEST_SCHEMA_INVALID")
    if document.get("package_name") != PACKAGE_NAME:
        raise VerificationError("MANIFEST_PACKAGE_NAME_INVALID")
    if document.get("package_version") != PACKAGE_VERSION:
        raise VerificationError("MANIFEST_PACKAGE_VERSION_INVALID")
    if document.get("profile") != "dual-layer-sanitized-review":
        raise VerificationError("MANIFEST_PROFILE_INVALID")
    if document.get("agentteams_reference_version") != "v1.1.2":
        raise VerificationError("MANIFEST_AGENTTEAMS_VERSION_INVALID")
    if document.get("offline_unit_test_count") != OFFLINE_UNIT_TEST_COUNT:
        raise VerificationError("MANIFEST_TEST_COUNT_INVALID")
    if document.get("evidence_run_count") != 2:
        raise VerificationError("MANIFEST_EVIDENCE_RUN_COUNT_INVALID")
    entries = document.get("files")
    if not isinstance(entries, list):
        raise VerificationError("MANIFEST_FILES_INVALID")
    if document.get("payload_file_count") != len(entries):
        raise VerificationError("MANIFEST_PAYLOAD_COUNT_INVALID")

    expected_paths = _all_payload_files(root)
    seen: dict[str, tuple[int, str]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "path",
            "size_bytes",
            "sha256",
        }:
            raise VerificationError("MANIFEST_FILE_ENTRY_INVALID")
        relative = _safe_relative_path(entry["path"])
        size = entry["size_bytes"]
        digest = entry["sha256"]
        if relative in seen or not isinstance(size, int) or size < 0:
            raise VerificationError(f"MANIFEST_FILE_METADATA_INVALID:{relative}")
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise VerificationError(f"MANIFEST_FILE_HASH_INVALID:{relative}")
        seen[relative] = (size, digest)
    if sorted(seen) != expected_paths:
        raise VerificationError("MANIFEST_FILE_SET_MISMATCH")
    for relative, (size, digest) in seen.items():
        path = root / relative
        if path.stat().st_size != size or _digest(path) != digest:
            raise VerificationError(f"MANIFEST_FILE_CONTENT_MISMATCH:{relative}")


def _verify_sha256sums(root: Path) -> None:
    sums_path = root / "SHA256SUMS.txt"
    lines = sums_path.read_text(encoding="utf-8").splitlines()
    seen: dict[str, str] = {}
    for line in lines:
        if not line or "  " not in line:
            raise VerificationError("SHA256SUMS_LINE_INVALID")
        digest, relative_text = line.split("  ", 1)
        relative = _safe_relative_path(relative_text)
        if not SHA256_RE.fullmatch(digest) or relative in seen:
            raise VerificationError(f"SHA256SUMS_ENTRY_INVALID:{relative}")
        seen[relative] = digest
    expected = sorted(
        path.relative_to(root).as_posix()
        for path in _iter_payload_paths(root)
        if path.is_file() and path.name != "SHA256SUMS.txt"
    )
    if sorted(seen) != expected:
        raise VerificationError("SHA256SUMS_FILE_SET_MISMATCH")
    for relative, digest in seen.items():
        if _digest(root / relative) != digest:
            raise VerificationError(f"SHA256SUMS_CONTENT_MISMATCH:{relative}")


def _verify_reference_source_pins(root: Path) -> None:
    document = _load_json(root / "config/reference-source-pins.json")
    if not isinstance(document, dict):
        raise VerificationError("REFERENCE_SOURCE_PINS_ROOT_INVALID")
    if (
        document.get("schema_version")
        != "awakening.agentteams.demo.reference-source-pins.v1"
        or document.get("agentteams_version") != "v1.1.2"
        or document.get("hash_algorithm") != "sha256"
    ):
        raise VerificationError("REFERENCE_SOURCE_PINS_SCHEMA_INVALID")
    entries = document.get("files")
    if not isinstance(entries, list) or document.get("file_count") != len(entries):
        raise VerificationError("REFERENCE_SOURCE_PINS_COUNT_INVALID")
    if len(entries) != 180:
        raise VerificationError("REFERENCE_SOURCE_PINS_RELEASE_COUNT_INVALID")
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256"}:
            raise VerificationError("REFERENCE_SOURCE_PIN_ENTRY_INVALID")
        relative = _safe_relative_path(entry["path"])
        digest = entry["sha256"]
        if relative in seen or not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise VerificationError(f"REFERENCE_SOURCE_PIN_METADATA_INVALID:{relative}")
        seen.add(relative)
        path = root / relative
        if not path.is_file() or _digest(path) != digest:
            raise VerificationError(f"REFERENCE_SOURCE_PIN_CONTENT_MISMATCH:{relative}")
    fixed_demo = {
        "scripts/demo/agentteams_in_place_demo.py",
        "scripts/demo/Invoke-AgentTeamsInPlaceDemo.ps1",
        "scripts/demo/Start-AgentTeamsDemoHostRelay.ps1",
    }
    expected = {
        path.relative_to(root).as_posix()
        for path in _iter_payload_paths(root)
        if path.is_file()
        and (
            path.relative_to(root).as_posix() in fixed_demo
            or path.relative_to(root).as_posix().startswith("scripts/m4/")
            or path.relative_to(root).as_posix().startswith("infra/agentteams/demo/runtime/")
            or path.relative_to(root).as_posix().startswith("infra/agentteams/m4/")
            or path.relative_to(root).as_posix().startswith("src/")
            or path.relative_to(root).as_posix().startswith("schemas/")
            or path.relative_to(root).as_posix().startswith("contracts/")
            or path.relative_to(root).as_posix().startswith("agents/")
            or path.relative_to(root).as_posix().startswith("skills/")
        )
        and path.relative_to(root).as_posix()
        != "infra/agentteams/m4/controller.env.example"
    }
    if seen != expected:
        raise VerificationError("REFERENCE_SOURCE_PINS_FILE_SET_MISMATCH")
    required_demo_helpers = {
        "scripts/m4/provision-provider-state.py",
        "scripts/m4/run-real-chain.py",
    }
    if not required_demo_helpers.issubset(seen):
        raise VerificationError("REFERENCE_SOURCE_PINS_DEMO_HELPER_MISSING")
    if "infra/agentteams/m4/controller.env.example" in seen:
        raise VerificationError("REFERENCE_SOURCE_PINS_PUBLIC_EXAMPLE_INCLUDED")


def _verify_public_entrypoint_contract(root: Path) -> None:
    source = (root / "run_demo.ps1").read_text(encoding="utf-8")
    required = (
        '[ValidateSet("PrintRunbook", "Preflight", "Live")]',
        '"StartInfrastructure"',
        '"AwaitHumanRequest"',
        '"StartLiveGateway"',
        '"RunChain"',
        '"StopRestore"',
        "$referencePinsRelative = \"config\\reference-source-pins.json\"",
        "IUnderstandThisUsesDockerAndNetwork",
        "IUnderstandThisChangesReferenceState",
        "IUnderstandThisMayReadProtectedSecret",
        "IUnderstandThisMayCallProvider",
        "<YOUR_EXISTING_HUMAN_MATRIX_USER_ID>",
        '"-ControlPeerUserId", "none"',
        "PACKAGE_ROOT_IS_NOT_A_LIVE_REFERENCE_WORKSPACE",
    )
    if any(fragment not in source for fragment in required):
        raise VerificationError("PUBLIC_ENTRYPOINT_REQUIRED_CONTRACT_MISSING")
    forbidden = (
        "ResumeAdmissionCheck",
        "ResumeInfrastructure",
        "ResumeAttempt",
        "@demo_operator:",
    )
    if any(fragment in source for fragment in forbidden):
        raise VerificationError("PUBLIC_ENTRYPOINT_UNSAFE_SURFACE_PRESENT")


def _verify_summary(path: Path) -> dict[str, object]:
    document = _load_json(path)
    if not isinstance(document, dict):
        raise VerificationError(f"EVIDENCE_SUMMARY_ROOT_INVALID:{path.name}")
    exact = {
        "schema_version": 1,
        "evidence_profile": "sanitized-success-summary",
        "status": "completed",
        "workers_expected": 3,
        "workers_completed": 3,
        "worker_failures": 0,
        "provider_begin": 3,
        "provider_succeeded": 3,
        "provider_failed": 0,
        "provider_end": 3,
        "manager_provider_calls": 0,
        "provider_retries": 0,
        "hidden_retries": 0,
        "max_inflight": 3,
        "matrix_event_count": 8,
        "restore_completed": True,
        "restored_container_count": 8,
        "listener_count_after_restore": 0,
    }
    for field, value in exact.items():
        if document.get(field) != value:
            raise VerificationError(f"EVIDENCE_SUMMARY_FIELD_INVALID:{path.name}:{field}")
    for field in ("outer_run_id", "core_run_id", "demo_request_id"):
        value = document.get(field)
        if not isinstance(value, str) or not UUID_RE.fullmatch(value):
            raise VerificationError(f"EVIDENCE_SUMMARY_UUID_INVALID:{path.name}:{field}")
    for field in ("input_tokens", "output_tokens", "total_tokens", "cost_microcny"):
        value = document.get(field)
        if not isinstance(value, int) or value < 0:
            raise VerificationError(f"EVIDENCE_SUMMARY_NUMBER_INVALID:{path.name}:{field}")
    if document["input_tokens"] + document["output_tokens"] != document["total_tokens"]:
        raise VerificationError(f"EVIDENCE_SUMMARY_TOKEN_TOTAL_INVALID:{path.name}")
    if document.get("cost_cny") != f"{document['cost_microcny'] / 1_000_000:.6f}":
        raise VerificationError(f"EVIDENCE_SUMMARY_COST_INVALID:{path.name}")
    sources = document.get("source_artifact_sha256")
    if not isinstance(sources, dict) or set(sources) != {
        "result",
        "matrix_events",
        "lifecycle",
        "gateway_stdout",
        "run_chain_stdout",
        "live_gateway_config",
        "package_role_project_architect",
        "package_execution_evidence_coach",
        "package_independent_quality_reviewer",
    }:
        raise VerificationError(f"EVIDENCE_SUMMARY_SOURCE_HASHES_INVALID:{path.name}")
    if any(not isinstance(value, str) or not SHA256_RE.fullmatch(value) for value in sources.values()):
        raise VerificationError(f"EVIDENCE_SUMMARY_SOURCE_HASH_INVALID:{path.name}")
    return document


def _verify_worker_outputs(
    root: Path,
    run_root: Path,
    run_name: str,
    worker_records: list[dict[str, object]],
) -> None:
    outputs_root = run_root / "outputs"
    expected_files = {str(spec["file"]) for spec in WORKER_OUTPUT_SPECS.values()}
    actual_files = {
        path.name
        for path in outputs_root.iterdir()
        if path.is_file()
    } if outputs_root.is_dir() else set()
    if actual_files != expected_files or any(path.is_dir() for path in outputs_root.glob("*")):
        raise VerificationError(f"WORKER_OUTPUT_FILE_SET_INVALID:{run_name}")

    records_by_role = {
        str(record.get("agent_identity_id")): record for record in worker_records
    }
    if set(records_by_role) != set(WORKER_OUTPUT_SPECS):
        raise VerificationError(f"WORKER_OUTPUT_ROLE_SET_INVALID:{run_name}")

    for role, spec in WORKER_OUTPUT_SPECS.items():
        record = records_by_role[role]
        if record.get("skill_name") != spec["skill_name"]:
            raise VerificationError(f"WORKER_OUTPUT_SKILL_BINDING_INVALID:{run_name}:{role}")
        expected_digest = record.get("output_sha256")
        if not isinstance(expected_digest, str) or SHA256_RE.fullmatch(expected_digest) is None:
            raise VerificationError(f"WORKER_OUTPUT_HASH_METADATA_INVALID:{run_name}:{role}")

        output_path = outputs_root / str(spec["file"])
        output = _load_json(output_path)
        if not isinstance(output, dict):
            raise VerificationError(f"WORKER_OUTPUT_ROOT_INVALID:{run_name}:{role}")
        canonical = _canonical_json_bytes(output)
        try:
            file_bytes = output_path.read_bytes()
        except OSError as exc:
            raise VerificationError(f"WORKER_OUTPUT_READ_INVALID:{run_name}:{role}") from exc
        if file_bytes != canonical + b"\n":
            raise VerificationError(f"WORKER_OUTPUT_FILE_NOT_CANONICAL:{run_name}:{role}")
        if sha256(canonical).hexdigest() != expected_digest:
            raise VerificationError(f"WORKER_OUTPUT_HASH_BINDING_INVALID:{run_name}:{role}")

        schema = _load_json(root / str(spec["schema"]))
        if not isinstance(schema, dict):
            raise VerificationError(f"WORKER_OUTPUT_SCHEMA_ROOT_INVALID:{run_name}:{role}")
        _validate_output_schema(output, schema)
        _verify_worker_output_sensitivity(output)


def _verify_evidence_run(root: Path, run_name: str) -> None:
    run_root = root / "evidence" / run_name
    summary = _verify_summary(run_root / "summary.json")
    provider = _load_jsonl(run_root / "provider-events.jsonl")
    matrix = _load_jsonl(run_root / "matrix-flow.jsonl")
    lifecycle = _load_jsonl(run_root / "lifecycle-flow.jsonl")
    hashes = _load_json(run_root / "artifact-hashes.json")

    roles = {
        "role_project_architect",
        "execution_evidence_coach",
        "independent_quality_reviewer",
    }
    if len(provider) != 4:
        raise VerificationError(f"EVIDENCE_PROVIDER_RECORD_COUNT_INVALID:{run_name}")
    worker_records = provider[:-1]
    aggregate = provider[-1]
    if {record.get("agent_identity_id") for record in worker_records} != roles:
        raise VerificationError(f"EVIDENCE_PROVIDER_ROLE_SET_INVALID:{run_name}")
    for record in worker_records:
        if (
            record.get("record_type") != "worker-provider-outcome"
            or record.get("status") != "succeeded"
            or record.get("begin_observed") is not True
            or record.get("end_observed") is not True
            or record.get("retries") != 0
        ):
            raise VerificationError(f"EVIDENCE_PROVIDER_WORKER_INVALID:{run_name}")
    _verify_worker_outputs(root, run_root, run_name, worker_records)
    expected_aggregate = {
        "record_type": "provider-aggregate",
        "status": "completed",
        "worker_calls_planned": 3,
        "begin_count": 3,
        "succeeded_count": 3,
        "failed_count": 0,
        "end_count": 3,
        "manager_call_count": 0,
        "retry_count": 0,
        "max_inflight": 3,
        "input_tokens": summary["input_tokens"],
        "output_tokens": summary["output_tokens"],
        "total_tokens": summary["total_tokens"],
        "cost_microcny": summary["cost_microcny"],
    }
    for field, value in expected_aggregate.items():
        if aggregate.get(field) != value:
            raise VerificationError(
                f"EVIDENCE_PROVIDER_AGGREGATE_INVALID:{run_name}:{field}"
            )
    for field in ("input_tokens", "output_tokens", "total_tokens", "cost_microcny"):
        if sum(int(record[field]) for record in worker_records) != int(summary[field]):
            raise VerificationError(f"EVIDENCE_PROVIDER_SUM_INVALID:{run_name}:{field}")

    if len(matrix) != 8:
        raise VerificationError(f"EVIDENCE_MATRIX_RECORD_COUNT_INVALID:{run_name}")
    phase_counts: dict[str, int] = {}
    for record in matrix:
        for field in ("outer_run_id", "core_run_id", "demo_request_id"):
            if record.get(field) != summary[field]:
                raise VerificationError(f"EVIDENCE_MATRIX_BINDING_INVALID:{run_name}:{field}")
        phase = record.get("phase")
        if not isinstance(phase, str):
            raise VerificationError(f"EVIDENCE_MATRIX_PHASE_INVALID:{run_name}")
        phase_counts[phase] = phase_counts.get(phase, 0) + 1
    if phase_counts != {
        "request-accepted": 1,
        "worker-dispatched": 3,
        "worker-completed": 3,
        "summary-completed": 1,
    }:
        raise VerificationError(f"EVIDENCE_MATRIX_PHASE_COUNTS_INVALID:{run_name}")
    if {
        record.get("target")
        for record in matrix
        if record.get("phase") == "worker-dispatched"
    } != roles:
        raise VerificationError(f"EVIDENCE_MATRIX_DISPATCH_ROLES_INVALID:{run_name}")
    if {
        record.get("target")
        for record in matrix
        if record.get("phase") == "worker-completed"
    } != roles:
        raise VerificationError(f"EVIDENCE_MATRIX_COMPLETED_ROLES_INVALID:{run_name}")
    provider_output_by_role = {
        str(record["agent_identity_id"]): record.get("output_sha256")
        for record in worker_records
    }
    matrix_output_by_role = {
        str(record["target"]): record.get("evidence_sha256")
        for record in matrix
        if record.get("phase") == "worker-completed"
    }
    if matrix_output_by_role != provider_output_by_role:
        raise VerificationError(f"EVIDENCE_MATRIX_OUTPUT_HASH_BINDING_INVALID:{run_name}")

    restore_records = [
        record
        for record in lifecycle
        if record.get("kind") == "restore" and record.get("status") == "completed"
    ]
    if len(restore_records) != 1:
        raise VerificationError(f"EVIDENCE_RESTORE_RECORD_INVALID:{run_name}")
    restore = restore_records[0]
    if restore.get("listener_count") != 0 or restore.get("exact_container_count") != 8:
        raise VerificationError(f"EVIDENCE_RESTORE_BOUNDARY_INVALID:{run_name}")
    run_chain = [
        record
        for record in lifecycle
        if record.get("kind") == "run-chain" and record.get("status") == "completed"
    ]
    if len(run_chain) != 1 or run_chain[0].get("exit_code") != 0:
        raise VerificationError(f"EVIDENCE_RUN_CHAIN_RECORD_INVALID:{run_name}")

    if not isinstance(hashes, dict) or hashes.get("schema_version") != 1:
        raise VerificationError(f"EVIDENCE_ARTIFACT_HASH_ROOT_INVALID:{run_name}")
    if hashes.get("outer_run_id") != summary["outer_run_id"]:
        raise VerificationError(f"EVIDENCE_ARTIFACT_RUN_BINDING_INVALID:{run_name}")
    source_artifacts = hashes.get("source_artifacts")
    projection_artifacts = hashes.get("projection_artifacts")
    if not isinstance(source_artifacts, dict) or len(source_artifacts) != 10:
        raise VerificationError(f"EVIDENCE_SOURCE_ARTIFACT_SET_INVALID:{run_name}")
    if any(not isinstance(value, str) or not SHA256_RE.fullmatch(value) for value in source_artifacts.values()):
        raise VerificationError(f"EVIDENCE_SOURCE_ARTIFACT_HASH_INVALID:{run_name}")
    if set(source_artifacts) != {
        "result.json",
        "matrix-events.jsonl",
        "lifecycle.jsonl",
        "demo-live-gateway.stdout.log",
        "demo-live-gateway.stderr.log",
        "run-chain.stdout.log",
        "live-gateway-config.json",
        "packages/role_project_architect.json",
        "packages/execution_evidence_coach.json",
        "packages/independent_quality_reviewer.json",
    }:
        raise VerificationError(f"EVIDENCE_SOURCE_ARTIFACT_KEYS_INVALID:{run_name}")
    source_map = {
        "result": "result.json",
        "matrix_events": "matrix-events.jsonl",
        "lifecycle": "lifecycle.jsonl",
        "gateway_stdout": "demo-live-gateway.stdout.log",
        "run_chain_stdout": "run-chain.stdout.log",
        "live_gateway_config": "live-gateway-config.json",
        "package_role_project_architect": "packages/role_project_architect.json",
        "package_execution_evidence_coach": "packages/execution_evidence_coach.json",
        "package_independent_quality_reviewer": "packages/independent_quality_reviewer.json",
    }
    summary_sources = summary["source_artifact_sha256"]
    if any(summary_sources[key] != source_artifacts[value] for key, value in source_map.items()):
        raise VerificationError(f"EVIDENCE_SOURCE_ARTIFACT_BINDING_INVALID:{run_name}")
    expected_projection_paths = {
        "summary.json",
        "provider-events.jsonl",
        "matrix-flow.jsonl",
        "lifecycle-flow.jsonl",
        "outputs/role_project_architect.json",
        "outputs/execution_evidence_coach.json",
        "outputs/independent_quality_reviewer.json",
    }
    if not isinstance(projection_artifacts, dict) or set(projection_artifacts) != expected_projection_paths:
        raise VerificationError(f"EVIDENCE_PROJECTION_ARTIFACT_SET_INVALID:{run_name}")
    for relative, digest in projection_artifacts.items():
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise VerificationError(f"EVIDENCE_PROJECTION_HASH_INVALID:{run_name}:{relative}")
        if _digest(run_root / relative) != digest:
            raise VerificationError(f"EVIDENCE_PROJECTION_CONTENT_MISMATCH:{run_name}:{relative}")


def _verify_evidence(root: Path) -> None:
    _verify_evidence_run(root, "run-a")
    _verify_evidence_run(root, "run-b")
    for relative in (
        "examples/input/demo-request.json",
        "examples/output/demo-result.json",
    ):
        if not isinstance(_load_json(root / relative), dict):
            raise VerificationError(f"EXAMPLE_ROOT_INVALID:{relative}")


def _verify_text_boundary(root: Path) -> None:
    for path in _iter_payload_paths(root):
        if not path.is_file() or (
            path.suffix.lower() not in TEXT_BOUNDARY_SUFFIXES
            and path.name not in TEXT_BOUNDARY_FILENAMES
        ):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeError as exc:
            raise VerificationError(f"TEXT_NOT_UTF8:{path.relative_to(root).as_posix()}") from exc
        for fragment in FORBIDDEN_TEXT_FRAGMENTS:
            if fragment in text:
                raise VerificationError(
                    f"HOST_PATH_OR_SECRET_MOUNT_LEAK:{path.relative_to(root).as_posix()}"
                )
        for pattern in SENSITIVE_VALUE_PATTERNS + HOST_PATH_PATTERNS:
            if pattern.search(text):
                raise VerificationError(
                    f"SENSITIVE_VALUE_PATTERN_FOUND:{path.relative_to(root).as_posix()}"
                )


def verify(root: Path) -> None:
    try:
        root_metadata = root.lstat()
    except OSError as exc:
        raise VerificationError("PACKAGE_ROOT_INVALID") from exc
    if (
        _is_reparse_point(root_metadata)
        or root.is_symlink()
        or not stat.S_ISDIR(root_metadata.st_mode)
    ):
        raise VerificationError("PACKAGE_ROOT_INVALID")
    _verify_structure(root)
    _verify_manifest(root)
    _verify_sha256sums(root)
    _verify_reference_source_pins(root)
    _verify_public_entrypoint_contract(root)
    _verify_evidence(root)
    _verify_text_boundary(root)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the sanitized review package offline.")
    parser.add_argument(
        "--package-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    args = parser.parse_args()
    try:
        verify(args.package_root.absolute())
    except VerificationError as exc:
        print(f"PACKAGE_PAYLOAD_VERIFY=FAIL:{exc}", file=sys.stderr)
        if str(exc).startswith("PACKAGE_TRANSIENT_RESIDUE_FOUND="):
            print(
                "PACKAGE_TRANSIENT_RESIDUE_RECOVERY=REEXTRACT_ORIGINAL_ZIP",
                file=sys.stderr,
            )
        return 1
    print("PACKAGE_PAYLOAD_VERIFY=PASS")
    print(f"PACKAGE_NAME={PACKAGE_NAME}")
    print(f"PACKAGE_VERSION={PACKAGE_VERSION}")
    print("NETWORK_USED=false")
    print("DOCKER_USED=false")
    print("PROVIDER_CALLED=false")
    print("SECRET_VALUE_READ=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
