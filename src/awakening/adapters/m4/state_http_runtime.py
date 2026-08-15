"""Runnable loopback-only M4 State MCP composition.

Four State-only tokens are loaded only long enough to build the fingerprint
registry.  Program scope and runtime identity come exclusively from the
server-owned fixture state and the closed M4 identity map.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Final
from uuid import UUID

from awakening.state.admin import build_runtime_dsn, load_m2_env
from awakening.state.m4 import (
    M4PostgresStateStore,
    M4StateServiceFacade,
    TrustedRuntimeContext,
)

from .http_mcp import (
    M4BearerPrincipalRegistry,
    M4StateMcpHttpTransport,
    serve_m4_state_mcp_http,
)
from .state_mcp import M4StateMcpAdapter


_STATE_TOKEN_IDENTITIES: Final = {
    "AWAKENING_M4_STATE_MANAGER_TOKEN": (
        "awakening_program_manager",
        "manager",
        "m4-runtime-principal-awakening_program_manager",
    ),
    "AWAKENING_M4_STATE_ARCHITECT_TOKEN": (
        "role_project_architect",
        "architect",
        "m4-runtime-principal-role_project_architect",
    ),
    "AWAKENING_M4_STATE_COACH_TOKEN": (
        "execution_evidence_coach",
        "coach",
        "m4-runtime-principal-execution_evidence_coach",
    ),
    "AWAKENING_M4_STATE_REVIEWER_TOKEN": (
        "independent_quality_reviewer",
        None,
        "m4-runtime-principal-independent_quality_reviewer",
    ),
}


def _uuid_text(value: Any, field: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"M4_STATE_HTTP_{field.upper()}_INVALID") from exc


def _read_fixture_state(path: Path) -> tuple[str, str]:
    if not path.is_file() or path.is_symlink():
        raise ValueError("M4_STATE_HTTP_FIXTURE_STATE_INVALID")
    document = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(document, dict)
        or document.get("schema_version") != 1
        or document.get("status") != "ready"
    ):
        raise ValueError("M4_STATE_HTTP_FIXTURE_NOT_READY")
    return (
        _uuid_text(document.get("program_id"), "program_id"),
        _uuid_text(document.get("run_id"), "run_id"),
    )


def _read_state_tokens(path: Path) -> dict[str, str]:
    if not path.is_file() or path.is_symlink():
        raise ValueError("M4_STATE_HTTP_CREDENTIAL_FILE_INVALID")
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator != "=" or key in values:
            raise ValueError("M4_STATE_HTTP_CREDENTIAL_FILE_INVALID")
        values[key] = value
    expected = {
        "AWAKENING_M4_OBSERVABILITY_DB_PASSWORD",
        *_STATE_TOKEN_IDENTITIES,
    }
    if set(values) != expected:
        raise ValueError("M4_STATE_HTTP_CREDENTIAL_IDENTITY_SET_MISMATCH")

    values.pop("AWAKENING_M4_OBSERVABILITY_DB_PASSWORD")
    for token in values.values():
        if len(token) < 32 or any(character.isspace() for character in token):
            raise ValueError("M4_STATE_HTTP_CREDENTIAL_VALUE_INVALID")
    return values


def build_state_http_transport(
    *,
    m2_env_path: Path,
    m4_env_path: Path,
    fixture_state_path: Path,
) -> M4StateMcpHttpTransport:
    program_id, run_id = _read_fixture_state(fixture_state_path)
    state = M4StateServiceFacade(
        M4PostgresStateStore(build_runtime_dsn(load_m2_env(m2_env_path)))
    )
    adapter = M4StateMcpAdapter(state)

    raw_tokens = _read_state_tokens(m4_env_path)
    bindings: dict[str, TrustedRuntimeContext] = {}
    for key, (identity, role, principal_id) in _STATE_TOKEN_IDENTITIES.items():
        token = raw_tokens.pop(key)
        bindings[token] = TrustedRuntimeContext(
            principal_id=principal_id,
            agent_identity=identity,
            program_role=role,
            program_scope=(program_id,),
            run_id=run_id,
            auth_context_id=f"m4-state-http-{identity}",
        )
    raw_tokens.clear()
    principals = M4BearerPrincipalRegistry(bindings)
    bindings.clear()
    return M4StateMcpHttpTransport(
        bearer_principals=principals,
        state_mcp=adapter,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--m2-env", required=True, type=Path)
    parser.add_argument("--m4-env", required=True, type=Path)
    parser.add_argument("--fixture-state", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=18191, type=int)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        raise ValueError("M4_STATE_HTTP_LOOPBACK_REQUIRED")

    transport = build_state_http_transport(
        m2_env_path=args.m2_env.resolve(),
        m4_env_path=args.m4_env.resolve(),
        fixture_state_path=args.fixture_state.resolve(),
    )
    print("M4_STATE_HTTP_READY=true", flush=True)
    print("M4_STATE_HTTP_LOOPBACK_ONLY=true", flush=True)
    print("M4_STATE_HTTP_IDENTITY_COUNT=4", flush=True)
    serve_m4_state_mcp_http(
        transport,
        host=args.host,
        port=args.port,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
