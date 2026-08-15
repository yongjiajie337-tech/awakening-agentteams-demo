"""Fail-closed adapter for M4's local AgentTeams Matrix delegation.

This adapter deliberately exposes only the two routes frozen by
``ManagerRouteOperation``.  The Reviewer contract-smoke message has a separate
method so it cannot be selected through the Manager's route payload.  All
Matrix credentials and room resolution stay inside the Manager container's
helper; Python passes only ID-only context plus a constructor-frozen synthetic
package for the selected Worker.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import re
import subprocess
from typing import Any
from uuid import UUID


M4_DOCKER_EXECUTABLE = "docker"
M4_MANAGER_CONTAINER = "awakening-m4-manager"
M4_MATRIX_INTERPRETER = "/bin/bash"
M4_MATRIX_HELPER = "/root/manager-workspace/config/m4-matrix-dispatch.sh"
_ROUTE_FIELDS = frozenset(
    {"program_id", "run_id", "state_version", "skill_name", "skill_version"}
)
_ID_ONLY_FIELDS = frozenset({"program_id", "run_id", "state_version"})
_ROUTE_TARGETS = {
    "role_project_architect": ("analyze_role_gap", "1.0.0"),
    "execution_evidence_coach": ("coach_task_submission", "1.0.0"),
}
_REVIEWER_TARGET = "independent_quality_reviewer"
_ALL_TARGETS = frozenset((*_ROUTE_TARGETS, _REVIEWER_TARGET))
_PACKAGE_FIELDS = {
    "role_project_architect": frozenset(
        {"program_id", "state_version", "role_facts", "user_facts", "constraints"}
    ),
    "execution_evidence_coach": frozenset(
        {"program_id", "state_version", "task", "criteria", "evidence_refs"}
    ),
    "independent_quality_reviewer": frozenset(
        {
            "reviewer_mode",
            "package_kind",
            "package_id",
            "package_sha256",
            "context_sha256",
            "rubric_version",
            "criteria",
            "evidence_facts",
            "tools_allowed",
        }
    ),
}
_EVENT_ID = re.compile(r"^\$[A-Za-z0-9._~+:/=-]{1,255}$")
_REQUEST_MARKER = re.compile(
    r"^m4-call:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_SENSITIVE_TEXT = re.compile(
    r"(?:access[_ -]?token|api[_ -]?key|password|"
    r"authorization\s*:|bearer\s+[A-Za-z0-9._~-]{12,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"HICLAW_(?:LLM_API_KEY|MANAGER_PASSWORD|MANAGER_GATEWAY_KEY)|"
    r"WORKER_(?:PASSWORD|GATEWAY_KEY|MATRIX_TOKEN)|"
    r'\"channels\"\s*:|\"plugins\"\s*:)',
    re.IGNORECASE,
)
_SAFE_HELPER_ERROR_CODES = frozenset(
    {
        "M4_MATRIX_ARGUMENT_COUNT_INVALID",
        "M4_MATRIX_COMMAND_DENIED",
        "M4_MATRIX_CONFIG_MODE_INVALID",
        "M4_MATRIX_CONFIG_MODE_UNAVAILABLE",
        "M4_MATRIX_CONFIG_NOT_PRIVATE",
        "M4_MATRIX_CONTEXT_UUID_INVALID",
        "M4_MATRIX_DELIVERY_ALREADY_RECORDED",
        "M4_MATRIX_DELIVERY_BINDING_MISMATCH",
        "M4_MATRIX_DELIVERY_DIGEST_INVALID",
        "M4_MATRIX_DELIVERY_ID_INVALID",
        "M4_MATRIX_DELIVERY_RECORD_FAILED",
        "M4_MATRIX_DELIVERY_RECORD_INVALID",
        "M4_MATRIX_DELIVERY_RECORD_MISSING",
        "M4_MATRIX_DELIVERY_RECORD_NOT_PRIVATE",
        "M4_MATRIX_DELIVERY_TIMESTAMP_INVALID",
        "M4_MATRIX_HELPER_DEPENDENCY_MISSING",
        "M4_MATRIX_JOINED_MEMBERS_INVALID",
        "M4_MATRIX_LOCAL_API_FAILED",
        "M4_MATRIX_LOCAL_API_UNAVAILABLE",
        "M4_MATRIX_MESSAGE_BUILD_FAILED",
        "M4_MATRIX_PAYLOAD_TOO_LARGE",
        "M4_MATRIX_REQUEST_MARKER_HASH_INVALID",
        "M4_MATRIX_REQUEST_MARKER_INVALID",
        "M4_MATRIX_REVIEWER_CONTEXT_DENIED",
        "M4_MATRIX_ROOM_INVALID",
        "M4_MATRIX_ROOM_WORKER_SCOPE_INVALID",
        "M4_MATRIX_ROUTE_PAYLOAD_DENIED",
        "M4_MATRIX_ROUTE_TARGET_DENIED",
        "M4_MATRIX_RUNTIME_FILE_COUNT_INVALID",
        "M4_MATRIX_RUNTIME_FILE_INVALID",
        "M4_MATRIX_RUNTIME_FILE_NAME_INVALID",
        "M4_MATRIX_RUNTIME_FILE_OUTSIDE_WORKSPACE",
        "M4_MATRIX_STATE_DIR_INVALID",
        "M4_MATRIX_STATE_DIR_NOT_PRIVATE",
        "M4_MATRIX_SYNC_TOKEN_INVALID",
        "M4_MATRIX_TARGET_DENIED",
        "M4_MATRIX_TOKEN_FORMAT_INVALID",
        "M4_MATRIX_TOKEN_UNAVAILABLE",
        "M4_MATRIX_TRUSTED_PACKAGE_DENIED",
        "M4_MATRIX_TRUSTED_PACKAGE_SENSITIVE_CONTENT_BLOCKED",
        "M4_MATRIX_TRUSTED_PACKAGE_TOO_LARGE",
        "M4_MATRIX_USER_BINDING_MISMATCH",
        "M4_MATRIX_USER_INVALID",
        "M4_MATRIX_VISIBLE_MENTION_INVALID",
        "M4_MATRIX_WAIT_TIMEOUT_INVALID",
        "M4_MATRIX_WORK_DIR_INVALID",
        "M4_MATRIX_WORKER_MEMBERSHIP_INVALID",
        "M4_MATRIX_WORKER_RESPONSE_AMBIGUOUS",
        "M4_MATRIX_WORKER_RESPONSE_CONTRACT_PREFLIGHT_FAILED",
        "M4_MATRIX_WORKER_RESPONSE_INVALID",
        "M4_MATRIX_WORKER_RESPONSE_SENSITIVE_CONTENT_BLOCKED",
        "M4_MATRIX_WORKER_RESPONSE_TIMEOUT",
    }
)


def _safe_helper_error_code(*, returncode: int, stderr: str) -> str | None:
    """Return one exact helper-owned code without exposing arbitrary stderr."""

    if returncode != 78 or len(stderr.encode("utf-8")) > 128:
        return None
    if stderr.endswith("\r\n"):
        candidate = stderr[:-2]
    elif stderr.endswith("\n"):
        candidate = stderr[:-1]
    else:
        candidate = stderr
    if not candidate or "\r" in candidate or "\n" in candidate:
        return None
    if candidate not in _SAFE_HELPER_ERROR_CODES:
        return None
    return candidate


class MatrixDelegationError(RuntimeError):
    """A safe, non-secret-bearing Matrix delegation failure."""

    @property
    def safe_helper_code(self) -> str | None:
        """Expose only a helper code frozen in this module's explicit allowlist."""

        if (
            len(self.args) == 1
            and isinstance(self.args[0], str)
            and self.args[0] in _SAFE_HELPER_ERROR_CODES
        ):
            return self.args[0]
        return None


@dataclass(frozen=True, slots=True)
class MatrixWorkerResponse:
    target_identity: str
    delivery_id: str
    response_event_id: str
    text: str


def _validate_id_context(value: Mapping[str, Any]) -> dict[str, Any]:
    if frozenset(value) != _ID_ONLY_FIELDS:
        raise MatrixDelegationError("reviewer_context_not_id_only")
    try:
        program_id = str(UUID(str(value["program_id"])))
        run_id = str(UUID(str(value["run_id"])))
    except (TypeError, ValueError, AttributeError) as exc:
        raise MatrixDelegationError("context_uuid_invalid") from exc
    state_version = value["state_version"]
    if (
        isinstance(state_version, bool)
        or not isinstance(state_version, int)
        or state_version < 0
    ):
        raise MatrixDelegationError("context_state_version_invalid")
    return {
        "program_id": program_id,
        "run_id": run_id,
        "state_version": state_version,
    }


def _validate_event_id(value: Any) -> str:
    if not isinstance(value, str) or _EVENT_ID.fullmatch(value) is None:
        raise MatrixDelegationError("matrix_event_id_invalid")
    return value


def _validate_request_marker(value: Any) -> str:
    if not isinstance(value, str) or _REQUEST_MARKER.fullmatch(value) is None:
        raise MatrixDelegationError("request_marker_invalid")
    try:
        parsed = UUID(value.removeprefix("m4-call:"))
    except ValueError as exc:
        raise MatrixDelegationError("request_marker_invalid") from exc
    if parsed.version != 4 or f"m4-call:{parsed}" != value:
        raise MatrixDelegationError("request_marker_invalid")
    return value


def _freeze_trusted_package(*, target_identity: str, value: Any) -> str:
    """Return a detached canonical JSON copy of one trusted Worker package."""

    if not isinstance(value, Mapping):
        raise ValueError("trusted Worker package must be an object")
    expected_fields = _PACKAGE_FIELDS[target_identity]
    if frozenset(value) != expected_fields:
        raise ValueError("trusted Worker package fields do not match the target")
    try:
        raw = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        detached = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("trusted Worker package is not canonical JSON") from exc
    if not isinstance(detached, dict) or not 2 <= len(raw.encode("utf-8")) <= 16_384:
        raise ValueError("trusted Worker package size is invalid")
    if _SENSITIVE_TEXT.search(raw) is not None:
        raise ValueError("trusted Worker package contains denied sensitive material")

    if target_identity in _ROUTE_TARGETS:
        try:
            program_id = str(UUID(str(detached["program_id"])))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("trusted Worker package program_id is invalid") from exc
        state_version = detached["state_version"]
        if (
            isinstance(state_version, bool)
            or not isinstance(state_version, int)
            or state_version < 0
        ):
            raise ValueError("trusted Worker package state_version is invalid")
        detached["program_id"] = program_id
        raw = json.dumps(
            detached,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    else:
        if (
            detached["reviewer_mode"] != "contract_smoke"
            or detached["package_kind"] != "fixed_synthetic_closed_package"
            or detached["tools_allowed"] is not False
        ):
            raise ValueError("trusted Reviewer package is not the frozen no-tool fixture")
    return raw


class MatrixManagerDelegationPort:
    """Invoke the fixed in-container Matrix helper without a shell."""

    def __init__(
        self,
        *,
        request_markers: Mapping[str, str],
        trusted_worker_packages: Mapping[str, Mapping[str, Any]],
        docker_executable: str = M4_DOCKER_EXECUTABLE,
        helper_timeout_seconds: int = 20,
    ) -> None:
        if (
            isinstance(helper_timeout_seconds, bool)
            or not isinstance(helper_timeout_seconds, int)
            or not 1 <= helper_timeout_seconds <= 60
        ):
            raise ValueError("helper_timeout_seconds must be between 1 and 60")
        if frozenset(request_markers) != _ALL_TARGETS:
            raise ValueError("request_markers must bind exactly the three M4 Workers")
        if frozenset(trusted_worker_packages) != _ALL_TARGETS:
            raise ValueError(
                "trusted_worker_packages must bind exactly the three M4 Workers"
            )
        if (
            not isinstance(docker_executable, str)
            or not docker_executable.strip()
            or docker_executable != docker_executable.strip()
            or "\n" in docker_executable
            or "\r" in docker_executable
        ):
            raise ValueError("docker_executable must be one exact executable path or name")
        markers = {
            target: _validate_request_marker(request_markers[target])
            for target in sorted(_ALL_TARGETS)
        }
        if len(set(markers.values())) != len(_ALL_TARGETS):
            raise ValueError("request_markers must be unique per Worker")
        self._helper_timeout_seconds = helper_timeout_seconds
        self._docker_executable = docker_executable
        self._request_markers = markers
        self._trusted_worker_packages = {
            target: _freeze_trusted_package(
                target_identity=target,
                value=trusted_worker_packages[target],
            )
            for target in sorted(_ALL_TARGETS)
        }

    def request_marker_hash_for(self, *, target_identity: str) -> str:
        """Expose only a stable marker hash for trace correlation."""

        marker = self._request_markers.get(target_identity)
        if marker is None:
            raise MatrixDelegationError("request_marker_target_denied")
        return hashlib.sha256(marker.encode("ascii")).hexdigest()

    def _invoke(self, *arguments: str, timeout_seconds: int | None = None) -> str:
        try:
            completed = subprocess.run(
                (
                    self._docker_executable,
                    "exec",
                    M4_MANAGER_CONTAINER,
                    M4_MATRIX_INTERPRETER,
                    "--",
                    M4_MATRIX_HELPER,
                    *arguments,
                ),
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
                timeout=timeout_seconds or self._helper_timeout_seconds,
                shell=False,
                close_fds=True,
            )
        except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
            raise MatrixDelegationError("matrix_helper_unavailable") from exc
        # Never propagate arbitrary helper stderr: upstream tools can include
        # credential material in diagnostics.  Only one exact, helper-owned,
        # explicitly allowlisted exit-78 code may cross this boundary.
        if completed.returncode != 0:
            safe_code = _safe_helper_error_code(
                returncode=completed.returncode,
                stderr=completed.stderr,
            )
            if safe_code is not None:
                raise MatrixDelegationError(safe_code)
            raise MatrixDelegationError("matrix_helper_failed")
        stdout = completed.stdout
        if len(stdout.encode("utf-8")) > 12_288:
            raise MatrixDelegationError("matrix_helper_output_too_large")
        return stdout.strip()

    def dispatch(
        self,
        *,
        target_identity: str,
        payload: Mapping[str, Any],
    ) -> str:
        """Dispatch only a frozen Architect or Coach Manager route."""

        expected = _ROUTE_TARGETS.get(target_identity)
        if expected is None:
            raise MatrixDelegationError("manager_route_target_denied")
        if frozenset(payload) != _ROUTE_FIELDS:
            raise MatrixDelegationError("manager_route_payload_not_frozen")
        context = _validate_id_context(
            {
                "program_id": payload["program_id"],
                "run_id": payload["run_id"],
                "state_version": payload["state_version"],
            }
        )
        skill_name, skill_version = expected
        if (
            payload["skill_name"] != skill_name
            or payload["skill_version"] != skill_version
        ):
            raise MatrixDelegationError("manager_route_skill_binding_mismatch")
        frozen_payload = {
            **context,
            "skill_name": skill_name,
            "skill_version": skill_version,
        }
        raw = json.dumps(
            frozen_payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        trusted_package = self._trusted_worker_packages[target_identity]
        package_value = json.loads(trusted_package)
        if (
            package_value["program_id"] != context["program_id"]
            or package_value["state_version"] != context["state_version"]
        ):
            raise MatrixDelegationError("trusted_worker_package_context_mismatch")
        marker = self._request_markers[target_identity]
        return _validate_event_id(
            self._invoke("dispatch", target_identity, marker, raw, trusted_package)
        )

    def dispatch_reviewer_contract_smoke(
        self,
        *,
        context: Mapping[str, Any],
    ) -> str:
        """Dispatch the separate no-tool Reviewer contract-smoke request."""

        id_only = _validate_id_context(context)
        raw = json.dumps(
            id_only,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        marker = self._request_markers[_REVIEWER_TARGET]
        return _validate_event_id(
            self._invoke(
                "dispatch-reviewer-contract-smoke",
                marker,
                raw,
                self._trusted_worker_packages[_REVIEWER_TARGET],
            )
        )

    def await_response(
        self,
        *,
        target_identity: str,
        delivery_id: str,
        timeout_seconds: int = 60,
    ) -> MatrixWorkerResponse:
        """Poll the exact target after a previously recorded delivery event."""

        if target_identity not in _ALL_TARGETS:
            raise MatrixDelegationError("response_target_denied")
        event_id = _validate_event_id(delivery_id)
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int)
            or not 1 <= timeout_seconds <= 240
        ):
            raise MatrixDelegationError("response_timeout_invalid")
        raw = self._invoke(
            "await-response",
            target_identity,
            event_id,
            str(timeout_seconds),
            timeout_seconds=timeout_seconds + 15,
        )
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise MatrixDelegationError("worker_response_invalid") from exc
        if not isinstance(value, dict) or frozenset(value) != {
            "target_identity",
            "delivery_id",
            "response_event_id",
            "text",
        }:
            raise MatrixDelegationError("worker_response_invalid")
        response_event_id = _validate_event_id(value["response_event_id"])
        text = value["text"]
        if (
            value["target_identity"] != target_identity
            or value["delivery_id"] != event_id
            or not isinstance(text, str)
            or not text.strip()
            or len(text.encode("utf-8")) > 8_192
            or _SENSITIVE_TEXT.search(text) is not None
        ):
            raise MatrixDelegationError("worker_response_contract_denied")
        return MatrixWorkerResponse(
            target_identity=target_identity,
            delivery_id=event_id,
            response_event_id=response_event_id,
            text=text,
        )


__all__ = (
    "M4_DOCKER_EXECUTABLE",
    "M4_MANAGER_CONTAINER",
    "M4_MATRIX_INTERPRETER",
    "M4_MATRIX_HELPER",
    "MatrixDelegationError",
    "MatrixManagerDelegationPort",
    "MatrixWorkerResponse",
)
