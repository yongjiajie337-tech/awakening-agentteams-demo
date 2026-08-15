"""Provider-free runtime listener used before the explicit M4 Provider gate.

The listener authenticates the four real AgentTeams gateway credentials and
then denies every call because the server owns no invocation plan.  It neither
loads nor constructs a Provider implementation.
"""

from __future__ import annotations

import argparse
import base64
from collections.abc import Mapping
from pathlib import Path
import secrets
from typing import NoReturn

from awakening.model_gateway.m4.http_adapter import (
    SingleUseRuntimeInvocationPlanRegistry,
    TrustedOpenAICompatibleHttpAdapter,
    build_http_server,
)
from awakening.orchestration.m4.authorization import (
    RuntimeBinding,
    RuntimeCredentialRegistry,
)
from awakening.state.contracts import PrincipalType, TrustedPrincipal

from .live_runtime import AUTHORIZED_MODEL_ID


_IDENTITIES: Mapping[str, tuple[str, tuple[str, ...], str | None]] = {
    "AWAKENING_PROGRAM_MANAGER_B64": (
        "awakening_program_manager",
        ("apply_authorized_change",),
        None,
    ),
    "ROLE_PROJECT_ARCHITECT_B64": (
        "role_project_architect",
        (
            "analyze_role_gap",
            "design_evidence_project",
            "build_versioned_plan",
            "propose_replan_under_constraints",
            "distill_experience_candidate",
        ),
        None,
    ),
    "EXECUTION_EVIDENCE_COACH_B64": (
        "execution_evidence_coach",
        ("coach_task_submission", "generate_evidence_bound_materials"),
        None,
    ),
    "INDEPENDENT_QUALITY_REVIEWER_B64": (
        "independent_quality_reviewer",
        ("review_evidence_against_rubric",),
        "contract_smoke",
    ),
}


def _read_tokens(path: Path) -> dict[str, str]:
    if not path.is_file() or path.is_symlink():
        raise ValueError("M4_GATEWAY_CREDENTIAL_FILE_INVALID")
    encoded: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator != "=" or key in encoded:
            raise ValueError("M4_GATEWAY_CREDENTIAL_FILE_INVALID")
        encoded[key] = value
    if set(encoded) != set(_IDENTITIES):
        raise ValueError("M4_GATEWAY_CREDENTIAL_IDENTITY_SET_MISMATCH")
    tokens: dict[str, str] = {}
    for key, value in encoded.items():
        try:
            token = base64.b64decode(value, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("M4_GATEWAY_CREDENTIAL_ENCODING_INVALID") from exc
        if len(token) < 32 or "\n" in token or "\r" in token:
            raise ValueError("M4_GATEWAY_CREDENTIAL_VALUE_INVALID")
        tokens[key] = token
    return tokens


def build_fail_closed_adapter(credential_path: Path) -> TrustedOpenAICompatibleHttpAdapter:
    tokens = _read_tokens(credential_path)
    pepper = secrets.token_bytes(32)
    empty = RuntimeCredentialRegistry(pepper=pepper, bindings_by_fingerprint={})
    bindings: dict[str, RuntimeBinding] = {}
    for key, token in tokens.items():
        identity, skills, reviewer_mode = _IDENTITIES[key]
        binding = RuntimeBinding(
            credential_id=f"m4-runtime-{identity}",
            agent_identity_id=identity,
            agent_identity_version="1.0.0",
            trusted_principal=TrustedPrincipal(
                principal_id=f"m4-runtime-principal-{identity}",
                principal_type=PrincipalType.AGENT,
                scopes=("model:invoke",),
                program_scope=("m4-runtime-unbound",),
                auth_context_id=f"m4-runtime-auth-{identity}",
            ),
            program_id="m4-runtime-unbound",
            run_id="m4-runtime-unbound",
            runtime_config_snapshot_id="m4-runtime-unbound",
            public_model_alias=AUTHORIZED_MODEL_ID,
            allowed_skill_versions={skill: "1.0.0" for skill in skills},
            allowed_tools=(),
            reviewer_mode=reviewer_mode,
        )
        bindings[empty.fingerprint(token)] = binding
    tokens.clear()
    credentials = RuntimeCredentialRegistry(
        pepper=pepper,
        bindings_by_fingerprint=bindings,
    )

    def provider_forbidden(_: object) -> NoReturn:
        raise RuntimeError("M4_FAIL_CLOSED_RUNTIME_HAS_NO_PROVIDER")

    return TrustedOpenAICompatibleHttpAdapter(
        credential_registry=credentials,
        invocation_plans=SingleUseRuntimeInvocationPlanRegistry({}),
        gateway_factory=provider_forbidden,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--credentials", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=18190, type=int)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("M4_FAIL_CLOSED_GATEWAY_LOOPBACK_REQUIRED")
    adapter = build_fail_closed_adapter(args.credentials.resolve())
    server = build_http_server(adapter, host=args.host, port=args.port)
    print("M4_FAIL_CLOSED_GATEWAY_READY=true", flush=True)
    print("M4_FAIL_CLOSED_GATEWAY_PROVIDER_CONFIGURED=false", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
