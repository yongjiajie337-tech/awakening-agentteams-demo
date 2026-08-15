"""Build a content-free manifest from the exact final provider input."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType
from typing import Any
from uuid import UUID, uuid5

from awakening.state.validation import canonical_json_bytes


_MANIFEST_NAMESPACE = UUID("c6275d33-6086-5cb3-9679-09e4a8c0e5c5")
_ALLOWED_OBJECT_REF_FIELDS = frozenset(
    {"object_type", "object_id", "object_version", "content_sha256"}
)


def _strict_object_ref(value: Mapping[str, Any]) -> Mapping[str, str]:
    if set(value) != _ALLOWED_OBJECT_REF_FIELDS:
        raise ValueError("context object ref fields are not exact")
    result = {key: str(value[key]) for key in sorted(_ALLOWED_OBJECT_REF_FIELDS)}
    if not all(result.values()):
        raise ValueError("context object ref values cannot be empty")
    if len(result["content_sha256"]) != 64:
        raise ValueError("context object ref hash must be sha256")
    return MappingProxyType(result)


@dataclass(frozen=True, slots=True)
class ContextManifest:
    context_manifest_id: str
    program_id: str
    run_id: str
    model_call_id: str
    runtime_config_snapshot_id: str
    reservation_id: str
    agent_identity_id: str
    agent_identity_version: str
    skill_name: str
    skill_version: str
    object_refs: tuple[Mapping[str, str], ...]
    exclusions: tuple[str, ...]
    input_sha256: str
    status: str = "committed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_manifest_id": self.context_manifest_id,
            "program_id": self.program_id,
            "run_id": self.run_id,
            "model_call_id": self.model_call_id,
            "runtime_config_snapshot_id": self.runtime_config_snapshot_id,
            "reservation_id": self.reservation_id,
            "agent_identity_id": self.agent_identity_id,
            "agent_identity_version": self.agent_identity_version,
            "skill_name": self.skill_name,
            "skill_version": self.skill_version,
            "object_refs": [dict(item) for item in self.object_refs],
            "exclusions": list(self.exclusions),
            "input_sha256": self.input_sha256,
            "status": self.status,
        }


class ContextManifestBuilder:
    """Hashes provider input but never retains its messages or raw content."""

    def build(
        self,
        *,
        program_id: str,
        run_id: str,
        model_call_id: str,
        runtime_config_snapshot_id: str,
        reservation_id: str,
        agent_identity_id: str,
        agent_identity_version: str,
        skill_name: str,
        skill_version: str,
        provider_input: Mapping[str, Any],
        object_refs: tuple[Mapping[str, Any], ...],
        exclusions: tuple[str, ...],
    ) -> ContextManifest:
        input_sha256 = sha256(canonical_json_bytes(provider_input)).hexdigest()
        manifest_id = str(
            uuid5(
                _MANIFEST_NAMESPACE,
                ":".join((program_id, run_id, model_call_id, input_sha256)),
            )
        )
        strict_refs = tuple(_strict_object_ref(item) for item in object_refs)
        clean_exclusions = tuple(str(value) for value in exclusions)
        if any(not value for value in clean_exclusions):
            raise ValueError("context exclusions cannot be empty")
        return ContextManifest(
            context_manifest_id=manifest_id,
            program_id=program_id,
            run_id=run_id,
            model_call_id=model_call_id,
            runtime_config_snapshot_id=runtime_config_snapshot_id,
            reservation_id=reservation_id,
            agent_identity_id=agent_identity_id,
            agent_identity_version=agent_identity_version,
            skill_name=skill_name,
            skill_version=skill_version,
            object_refs=strict_refs,
            exclusions=clean_exclusions,
            input_sha256=input_sha256,
        )


__all__ = ("ContextManifest", "ContextManifestBuilder")

