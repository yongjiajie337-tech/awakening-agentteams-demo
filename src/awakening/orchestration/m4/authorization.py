"""Server-derived M4 runtime identity; request bodies cannot select a role."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
import hmac
from types import MappingProxyType

from awakening.model_gateway.m4.contracts import (
    GatewayReasonCode,
    ModelInvocation,
)
from awakening.model_gateway.runtime_input_policy import (
    FORBIDDEN_RUNTIME_BODY_FIELDS,
    find_forbidden_runtime_fields,
    requested_provider_tools,
)
from awakening.state.contracts import TrustedPrincipal


def _freeze_versions(value: Mapping[str, str]) -> Mapping[str, str]:
    return MappingProxyType({str(key): str(item) for key, item in value.items()})


@dataclass(frozen=True, slots=True)
class RuntimeBinding:
    credential_id: str
    agent_identity_id: str
    agent_identity_version: str
    trusted_principal: TrustedPrincipal
    program_id: str
    run_id: str
    runtime_config_snapshot_id: str
    public_model_alias: str
    allowed_skill_versions: Mapping[str, str]
    allowed_tools: tuple[str, ...]
    reviewer_mode: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "allowed_skill_versions",
            _freeze_versions(self.allowed_skill_versions),
        )
        object.__setattr__(self, "allowed_tools", tuple(self.allowed_tools))
        if self.agent_identity_id == "independent_quality_reviewer":
            if self.reviewer_mode != "contract_smoke" or self.allowed_tools:
                raise ValueError("M4 Reviewer must be contract_smoke with no tools")


@dataclass(frozen=True, slots=True)
class TrustedRuntimeSession:
    binding: RuntimeBinding
    credential_fingerprint: str


class RuntimeCredentialRegistry:
    """Authenticate opaque runtime tokens without storing or returning them."""

    def __init__(
        self,
        *,
        pepper: bytes,
        bindings_by_fingerprint: Mapping[str, RuntimeBinding],
    ) -> None:
        if len(pepper) < 32:
            raise ValueError("runtime credential pepper must contain at least 32 bytes")
        self._pepper = bytes(pepper)
        self._bindings = MappingProxyType(dict(bindings_by_fingerprint))

    def fingerprint(self, token: str) -> str:
        if len(token) < 32:
            raise ValueError("runtime credential is too short")
        return hmac.new(self._pepper, token.encode("utf-8"), sha256).hexdigest()

    def authenticate(self, token: str) -> TrustedRuntimeSession | None:
        fingerprint = self.fingerprint(token)
        matched: RuntimeBinding | None = None
        for candidate, binding in self._bindings.items():
            if hmac.compare_digest(candidate, fingerprint):
                matched = binding
        if matched is None:
            return None
        return TrustedRuntimeSession(matched, fingerprint)


class BoundRuntimeAuthorizer:
    """Authorize one server-authenticated session before any model transport."""

    def __init__(self, session: TrustedRuntimeSession) -> None:
        self._session = session

    def authorize_model_call(self, invocation: ModelInvocation) -> GatewayReasonCode:
        binding = self._session.binding
        if (
            invocation.program_id != binding.program_id
            or invocation.run_id != binding.run_id
            or invocation.runtime_config_snapshot_id
            != binding.runtime_config_snapshot_id
            or invocation.agent_identity_id != binding.agent_identity_id
            or invocation.agent_identity_version != binding.agent_identity_version
        ):
            return GatewayReasonCode.RUNTIME_PRINCIPAL_DENIED
        expected_version = binding.allowed_skill_versions.get(invocation.skill_name)
        if expected_version is None or expected_version != invocation.skill_version:
            return GatewayReasonCode.SKILL_NOT_ALLOWED
        if find_forbidden_runtime_fields(invocation.provider_input):
            return GatewayReasonCode.RUNTIME_BODY_FORBIDDEN
        try:
            requested_tools = requested_provider_tools(invocation.provider_input)
        except ValueError:
            return GatewayReasonCode.PROVIDER_INPUT_INVALID
        if not requested_tools.issubset(frozenset(binding.allowed_tools)):
            return GatewayReasonCode.TOOL_NOT_ALLOWED
        return GatewayReasonCode.OK


__all__ = (
    "BoundRuntimeAuthorizer",
    "FORBIDDEN_RUNTIME_BODY_FIELDS",
    "RuntimeBinding",
    "RuntimeCredentialRegistry",
    "TrustedRuntimeSession",
    "find_forbidden_runtime_fields",
    "requested_provider_tools",
)
