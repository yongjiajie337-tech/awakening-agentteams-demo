---
name: distill_experience_candidate
description: Define how an Architect would distill deidentified trace references, outcomes, and errors into an ExperienceCandidateProposal. In M4 the missing M6 staging and review boundary requires an immediate fail-closed refusal.
---

# Distill Experience Candidate

Contract version: `1.0.0`

## Official 10-field contract

| Field | Contract |
|---|---|
| `skill_name` | `distill_experience_candidate` |
| `type` | `learning` |
| `scenario` | Distill bounded, deidentified run evidence into a reusable experience candidate only after the M6 staging and review boundary exists. |
| `input_params` | Accept the bounded references and summaries defined by `schemas/m4/skills/distill_experience_candidate.input.schema.json`. |
| `output` | In M4 return only the fixed refusal defined by the output schema; create, stage, publish, and index nothing. |
| `invocation_condition` | Architect-only after a run ends and only when trusted M6 ExperienceCandidate staging is available. That prerequisite is absent in M4. |
| `dependent_tool_system` | Full operation depends on trusted Trace inputs, State MCP staging, human review, and isolated RAG publication. Those M6 dependencies are unavailable in M4. |
| `failure_handling` | Refuse before model or tool invocation when staging, trace provenance, deidentification, evidence, or review prerequisites are missing. Do not create a partial candidate. |
| `permission_safety` | Only `role_project_architect` may invoke. Never copy secrets or direct identity, write to RAG, publish experience, or treat a request-body deidentification claim as trusted. |
| `reuse_value` | After M6 authorization, reuse candidate staging for controlled lessons-learned research without contaminating active knowledge. |

## M4 fail-closed procedure

1. Validate the request shape without treating its contents as trusted provenance.
2. Require the server-derived principal to be `role_project_architect`.
3. Check the module capability gate before reading trace content or invoking a model or tool.
4. Because M6 ExperienceCandidate staging and its human-review boundary do not exist in M4, return `M6_EXPERIENCE_STAGING_UNAVAILABLE`.
5. Set `candidate_created=false`, `staged=false`, `published_to_rag=false`, and `source_data_retained=false`.
6. Do not generate candidate text, write business state, create an observability record that contains source content, or call RAG.

Use `examples/minimal-input.json` and `examples/minimal-output.json` to demonstrate the required refusal.
