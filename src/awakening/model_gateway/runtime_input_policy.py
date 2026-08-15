"""Pure validation for untrusted model-runtime provider input."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


FORBIDDEN_RUNTIME_BODY_FIELDS = frozenset(
    {
        "agent_id",
        "agent_identity_id",
        "agent_identity_version",
        "allowed_skills",
        "allowed_skill_versions",
        "auth_context_id",
        "principal",
        "principal_id",
        "principal_type",
        "program_scope",
        "runtime_config_snapshot_id",
        "tool_policy_version",
        "credential_fingerprint",
        "approved",
        "approval_token",
        "api_key",
        "token",
        "password",
        "private_key",
        "model",
        "max_tokens",
        "max_completion_tokens",
        "temperature",
        "seed",
        "enable_thinking",
        "response_format",
    }
)


def requested_provider_tools(value: Mapping[str, Any]) -> frozenset[str]:
    """Return exact provider function names or fail on an ambiguous tool shape."""

    raw_tools = value.get("tools")
    raw_choice = value.get("tool_choice")
    if raw_tools is None:
        if raw_choice not in (None, "none"):
            raise ValueError("tool_choice requires a declared tools array")
        return frozenset()
    if not isinstance(raw_tools, (list, tuple)):
        raise ValueError("tools must be an array")
    names: set[str] = set()
    for item in raw_tools:
        if not isinstance(item, Mapping) or item.get("type") != "function":
            raise ValueError("M4 supports only named provider function tools")
        function = item.get("function")
        if not isinstance(function, Mapping):
            raise ValueError("provider function tool is missing its definition")
        name = function.get("name")
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("provider function tool names must be unique strings")
        names.add(name)
    if isinstance(raw_choice, Mapping):
        function = raw_choice.get("function")
        chosen = function.get("name") if isinstance(function, Mapping) else None
        if chosen not in names:
            raise ValueError("tool_choice is not one of the declared functions")
    elif raw_choice not in (None, "auto", "none", "required"):
        raise ValueError("tool_choice is not supported")
    return frozenset(names)


def find_forbidden_runtime_fields(
    value: Any,
    *,
    path: tuple[str | int, ...] = (),
) -> tuple[tuple[str | int, ...], ...]:
    """Return paths for server-owned or secret-like fields at any depth."""

    matches: list[tuple[str | int, ...]] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            name = str(key).replace("-", "_").lower()
            child = (*path, str(key))
            if name in FORBIDDEN_RUNTIME_BODY_FIELDS or name in {"patch", "raw_patch"}:
                matches.append(child)
            matches.extend(find_forbidden_runtime_fields(item, path=child))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            matches.extend(find_forbidden_runtime_fields(item, path=(*path, index)))
    return tuple(matches)


__all__ = (
    "FORBIDDEN_RUNTIME_BODY_FIELDS",
    "find_forbidden_runtime_fields",
    "requested_provider_tools",
)
