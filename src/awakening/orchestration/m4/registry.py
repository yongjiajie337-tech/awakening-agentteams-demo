"""Load and fail-close the frozen M4 identity and Skill contracts."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker


PROJECT_ROOT = Path(__file__).resolve().parents[4]
IDENTITY_REGISTRY_PATH = PROJECT_ROOT / "contracts" / "m4" / "identity-registry.json"
SKILL_REGISTRY_PATH = PROJECT_ROOT / "contracts" / "m4" / "skill-registry.json"

IDENTITY_FIELDS = frozenset(
    {
        "name",
        "role",
        "capabilities",
        "inputs",
        "outputs",
        "dependencies",
        "decision_boundary",
        "trace",
    }
)
SKILL_FIELDS = frozenset(
    {
        "skill_name",
        "type",
        "scenario",
        "input_params",
        "output",
        "invocation_condition",
        "dependent_tool_system",
        "failure_handling",
        "permission_safety",
        "reuse_value",
    }
)
M4_ID_ONLY_APPLY_FIELDS = frozenset(
    {
        "proposal_id",
        "expected_state_version",
        "idempotency_key",
        "human_decision_id",
    }
)
_SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


class RegistryContractError(ValueError):
    """Raised when a repository-owned M4 contract is not internally coherent."""


@dataclass(frozen=True, slots=True)
class M4ContractRegistry:
    identity_versions: Mapping[str, str]
    identity_skills: Mapping[str, tuple[str, ...]]
    identity_state_methods: Mapping[str, tuple[str, ...]]
    skill_versions: Mapping[str, str]
    skill_agents: Mapping[str, tuple[str, ...]]
    skill_activation: Mapping[str, str]

    def __post_init__(self) -> None:
        for field_name in (
            "identity_versions",
            "identity_skills",
            "identity_state_methods",
            "skill_versions",
            "skill_agents",
            "skill_activation",
        ):
            value = getattr(self, field_name)
            object.__setattr__(self, field_name, MappingProxyType(dict(value)))

    def assert_skill_allowed(
        self,
        *,
        agent_identity_id: str,
        agent_identity_version: str,
        skill_name: str,
        skill_version: str,
    ) -> None:
        if self.identity_versions.get(agent_identity_id) != agent_identity_version:
            raise RegistryContractError("agent identity or version is not registered")
        if self.skill_versions.get(skill_name) != skill_version:
            raise RegistryContractError("Skill or version is not registered")
        if skill_name not in self.identity_skills.get(agent_identity_id, ()):
            raise RegistryContractError("Skill is not allowed for the agent identity")
        if agent_identity_id not in self.skill_agents.get(skill_name, ()):
            raise RegistryContractError("Skill registry does not allow the agent identity")


def _read_json(path: Path) -> Mapping[str, Any]:
    resolved = path.resolve()
    if PROJECT_ROOT.resolve() not in resolved.parents:
        raise RegistryContractError(f"contract path escapes repository root: {path}")
    with resolved.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, Mapping):
        raise RegistryContractError(f"contract must be a JSON object: {path}")
    return value


def _repository_path(relative_path: str, *, expected_root: Path) -> Path:
    candidate = (PROJECT_ROOT / relative_path).resolve()
    root = expected_root.resolve()
    if root != candidate and root not in candidate.parents:
        raise RegistryContractError(f"contract path escapes its M4 root: {relative_path}")
    if not candidate.is_file():
        raise RegistryContractError(f"contract file is missing: {relative_path}")
    return candidate


def _require_semver(value: Any, *, label: str) -> str:
    version = str(value)
    if _SEMVER.fullmatch(version) is None:
        raise RegistryContractError(f"{label} must use stable semantic versioning")
    return version


def _assert_closed_object_schemas(value: Any, *, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, Mapping):
        if value.get("type") == "object" and value.get("additionalProperties") is not False:
            location = ".".join(path) or "$"
            raise RegistryContractError(
                f"object Schema must set additionalProperties=false at {location}"
            )
        for key, item in value.items():
            _assert_closed_object_schemas(item, path=(*path, str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_closed_object_schemas(item, path=(*path, str(index)))


def _validate_example(*, schema_path: Path, example_path: Path) -> None:
    schema = _read_json(schema_path)
    Draft202012Validator.check_schema(schema)
    _assert_closed_object_schemas(schema)
    example = _read_json(example_path)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(example),
        key=lambda error: (
            tuple(str(item) for item in error.absolute_path),
            error.message,
        ),
    )
    if errors:
        first = errors[0]
        location = ".".join(str(item) for item in first.absolute_path) or "$"
        raise RegistryContractError(
            f"example {example_path.relative_to(PROJECT_ROOT)} fails at {location}: "
            f"{first.message}"
        )


def load_and_validate_m4_registry() -> M4ContractRegistry:
    """Validate all 4 identities and 9 Skills, then return runtime allowlists."""

    identity_registry = _read_json(IDENTITY_REGISTRY_PATH)
    skill_registry = _read_json(SKILL_REGISTRY_PATH)
    identities = identity_registry.get("identities")
    skills = skill_registry.get("skills")
    if not isinstance(identities, list) or len(identities) != 4:
        raise RegistryContractError("M4 identity registry must contain exactly four identities")
    if not isinstance(skills, list) or len(skills) != 9:
        raise RegistryContractError("M4 Skill registry must contain exactly nine Skills")
    if identity_registry.get("identity_count") != 4:
        raise RegistryContractError("identity_count must equal four")
    if skill_registry.get("skill_count") != 9:
        raise RegistryContractError("skill_count must equal nine")
    if frozenset(identity_registry.get("official_fields", ())) != IDENTITY_FIELDS:
        raise RegistryContractError("identity registry official field set is not the M4 set")
    if frozenset(skill_registry.get("official_fields", ())) != SKILL_FIELDS:
        raise RegistryContractError("Skill registry official field set is not the M4 set")

    identity_versions: dict[str, str] = {}
    identity_skills: dict[str, tuple[str, ...]] = {}
    identity_state_methods: dict[str, tuple[str, ...]] = {}
    for entry in identities:
        if not isinstance(entry, Mapping):
            raise RegistryContractError("identity registry entries must be objects")
        name = str(entry.get("name", ""))
        if not name or name in identity_versions:
            raise RegistryContractError("identity names must be present and unique")
        definition_path = _repository_path(
            str(entry.get("definition", "")),
            expected_root=PROJECT_ROOT / "agents" / "m4",
        )
        definition = _read_json(definition_path)
        if frozenset(definition) != IDENTITY_FIELDS:
            raise RegistryContractError(f"identity {name} must contain exactly eight fields")
        if definition.get("name") != name:
            raise RegistryContractError(f"identity definition name mismatch for {name}")
        identity_versions[name] = _require_semver(
            entry.get("version"), label=f"identity {name}"
        )
        identity_skills[name] = tuple(str(item) for item in entry.get("allowed_skills", ()))
        identity_state_methods[name] = tuple(
            str(item) for item in entry.get("state_mcp_methods", ())
        )

    skill_versions: dict[str, str] = {}
    skill_agents: dict[str, tuple[str, ...]] = {}
    skill_activation: dict[str, str] = {}
    for entry in skills:
        if not isinstance(entry, Mapping):
            raise RegistryContractError("Skill registry entries must be objects")
        name = str(entry.get("skill_name", ""))
        if not name or name in skill_versions:
            raise RegistryContractError("Skill names must be present and unique")
        manifest_path = _repository_path(
            str(entry.get("manifest", "")),
            expected_root=PROJECT_ROOT / "skills" / "awakening" / name,
        )
        manifest = _read_json(manifest_path)
        if frozenset(manifest) != SKILL_FIELDS:
            raise RegistryContractError(f"Skill {name} must contain exactly ten fields")
        if manifest.get("skill_name") != name:
            raise RegistryContractError(f"Skill manifest name mismatch for {name}")
        version = _require_semver(entry.get("version"), label=f"Skill {name}")
        input_schema = _repository_path(
            str(entry.get("input_schema", "")),
            expected_root=PROJECT_ROOT / "schemas" / "m4" / "skills",
        )
        output_schema = _repository_path(
            str(entry.get("output_schema", "")),
            expected_root=PROJECT_ROOT / "schemas" / "m4" / "skills",
        )
        if manifest.get("input_params", {}).get("schema") != entry.get("input_schema"):
            raise RegistryContractError(f"Skill {name} input Schema reference mismatch")
        if manifest.get("output", {}).get("schema") != entry.get("output_schema"):
            raise RegistryContractError(f"Skill {name} output Schema reference mismatch")
        examples_root = PROJECT_ROOT / "skills" / "awakening" / name / "examples"
        _validate_example(
            schema_path=input_schema,
            example_path=_repository_path(
                str((examples_root / "minimal-input.json").relative_to(PROJECT_ROOT)),
                expected_root=examples_root,
            ),
        )
        _validate_example(
            schema_path=output_schema,
            example_path=_repository_path(
                str((examples_root / "minimal-output.json").relative_to(PROJECT_ROOT)),
                expected_root=examples_root,
            ),
        )
        skill_versions[name] = version
        skill_agents[name] = tuple(str(item) for item in entry.get("allowed_agents", ()))
        skill_activation[name] = str(entry.get("m4_activation", ""))

    for identity, allowed_skills in identity_skills.items():
        for skill in allowed_skills:
            if skill not in skill_versions or identity not in skill_agents.get(skill, ()):
                raise RegistryContractError(
                    f"identity/Skill allowlist is asymmetric for {identity}/{skill}"
                )
    for skill, allowed_agents in skill_agents.items():
        if not allowed_agents:
            raise RegistryContractError(f"Skill {skill} must have at least one allowed agent")
        for identity in allowed_agents:
            if identity not in identity_versions or skill not in identity_skills.get(identity, ()):
                raise RegistryContractError(
                    f"Skill/identity allowlist is asymmetric for {skill}/{identity}"
                )

    reviewer = "independent_quality_reviewer"
    if identity_state_methods.get(reviewer) or skill_activation.get(
        "review_evidence_against_rubric"
    ) != "contract_smoke_live_call":
        raise RegistryContractError("M4 Reviewer must be no-tool contract_smoke only")
    apply_entry = next(item for item in skills if item["skill_name"] == "apply_authorized_change")
    apply_schema = _read_json(
        _repository_path(
            str(apply_entry["input_schema"]),
            expected_root=PROJECT_ROOT / "schemas" / "m4" / "skills",
        )
    )
    if frozenset(apply_schema.get("properties", {})) != M4_ID_ONLY_APPLY_FIELDS:
        raise RegistryContractError("apply_authorized_change is not strictly ID-only")
    if skill_activation.get("apply_authorized_change") != "deny_only":
        raise RegistryContractError("M4 apply_authorized_change must remain deny-only")
    for staged_skill in (
        "distill_experience_candidate",
        "generate_evidence_bound_materials",
    ):
        if skill_activation.get(staged_skill) != "deny_only":
            raise RegistryContractError(f"{staged_skill} must fail closed in M4")

    return M4ContractRegistry(
        identity_versions=identity_versions,
        identity_skills=identity_skills,
        identity_state_methods=identity_state_methods,
        skill_versions=skill_versions,
        skill_agents=skill_agents,
        skill_activation=skill_activation,
    )


__all__ = (
    "IDENTITY_FIELDS",
    "M4ContractRegistry",
    "M4_ID_ONLY_APPLY_FIELDS",
    "RegistryContractError",
    "SKILL_FIELDS",
    "load_and_validate_m4_registry",
)
