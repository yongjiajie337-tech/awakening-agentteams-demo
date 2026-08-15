---
name: generate_evidence_bound_materials
description: Define evidence-bound portfolio, resume, or interview material generation for the Coach. In M4, fail closed before generation because trusted active Claims are unavailable.
---

# Generate Evidence-Bound Materials

Contract version: `1.0.0`

## Official 10-field contract

| Field | Contract |
|---|---|
| `skill_name` | `generate_evidence_bound_materials` |
| `type` | `generation` |
| `scenario` | Generate portfolio, resume, or interview material whose every factual statement maps to a trusted active Claim. |
| `input_params` | Accept only a Program ID, material type, template reference, and active-Claim references defined by the strict input schema. |
| `output` | In M4 return only the fixed refusal in the output schema; produce no prose, artifact, or new fact. |
| `invocation_condition` | Coach-only after State Service confirms all referenced Claims are active and final. M4 lacks this trusted prerequisite and therefore always fails closed. |
| `dependent_tool_system` | Full operation depends on State MCP active-Claim readback and a bounded document generator. Neither may be called in M4 without trusted active Claims. |
| `failure_handling` | If any Claim is missing, inactive, unverified, or cannot be read authoritatively, block the whole generation request rather than omit the mapping or fill gaps. |
| `permission_safety` | Only `execution_evidence_coach` may invoke. Never add experience, numbers, identity, results, or causal claims; request-body Claim IDs do not prove active status. |
| `reuse_value` | Reuse the claim-to-sentence boundary for evidence-backed portfolios, resumes, interview stories, and other trustworthy outcome material. |

## M4 fail-closed procedure

1. Validate the request shape against `schemas/m4/skills/generate_evidence_bound_materials.input.schema.json`.
2. Require the server-derived principal to be `execution_evidence_coach`.
3. Treat all request-body Claim IDs as untrusted references, not proof of active status.
4. Check for authoritative active-Claims capability before any model, State MCP, template, or document-generator call.
5. Because that prerequisite is unavailable in M4, return `ACTIVE_CLAIMS_UNAVAILABLE_IN_M4`.
6. Set `material_generated=false`, `artifact_created=false`, `facts_added=false`, and `source_data_retained=false`.
7. Generate no draft text and do not silently drop unsupported facts.

Use `examples/minimal-input.json` and `examples/minimal-output.json` to demonstrate the required refusal.
