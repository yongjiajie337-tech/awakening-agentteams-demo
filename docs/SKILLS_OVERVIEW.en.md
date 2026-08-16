# Nine Skills: one-page judge overview

[中文导览](JUDGE_GUIDE.md) · [English guide](JUDGE_GUIDE.en.md) · [中文 Skill 总览](SKILLS_OVERVIEW.md) · [English Skills overview](SKILLS_OVERVIEW.en.md)

This page answers three questions in plain language:

1. What does each of the nine Skills do?
2. Which Skills were actually invoked in the competition Demo?
3. Where can a judge inspect the design, examples, Schemas, and real outputs?

> **Accurate scope: the package contains nine Skills, but not all nine made live model calls.**
>
> - `3 live`: Architect, Coach, and Reviewer each have one real Provider-call path;
> - `3 contract_only`: contracts, Schemas, and examples are defined, but these Skills were not activated in this Demo;
> - `3 deny_only`: safety boundaries that only permit fail-closed behavior in M4, not successful execution.

Although Reviewer is a live Worker, it only runs a no-tool `contract_smoke`. It demonstrates identity binding on the registered Reviewer live path and closure of a structured-output contract over a fixed closed synthetic package. It is **not a formal business review, fact verification, VerifiedClaim creation, or M5 acceptance**.

## What the three states mean

| State | Plain-language meaning |
|---|---|
| `live` | In the competition Demo, the path really went through Worker → Gateway → Provider and returned a structured result |
| `contract_only` | Design, input/output Schema, and examples are complete, but the Demo did not run this Skill |
| `deny_only` | M4 deliberately permits only a refusal, demonstrating that high-risk capabilities cannot bypass permission, module, or state boundaries |

## Overview of all nine Skills

| State | Skill / role | Plain-language purpose | Main input | Structured output | Accurate Demo boundary |
|---|---|---|---|---|---|
| **live** | [`analyze_role_gap`](../skills/awakening/analyze_role_gap/SKILL.md)<br/>Architect | Compares role requirements with confirmed user facts, identifying what is supported, still missing, or unable to determine | Role facts, confirmed user facts, state version, time constraints | Gaps, evidence references, and `unable_to_determine` | Real call; analysis only, with no plan activation or business-state write |
| **live** | [`coach_task_submission`](../skills/awakening/coach_task_submission/SKILL.md)<br/>Coach | Checks a task submission against acceptance criteria, identifies missing evidence, and proposes revisions | Task and version, acceptance criteria, registered evidence references | Per-criterion observations, missing evidence, revision suggestions | Real call; feedback is non-authoritative and `certifies_completion=false` |
| **live (restricted scope)** | [`review_evidence_against_rubric`](../skills/awakening/review_evidence_against_rubric/SKILL.md)<br/>Reviewer | Uses a fixed closed synthetic package to demonstrate registered-path identity binding and structured-contract closure | Package/context hashes, fixed rubric, synthetic criteria/facts, `tools_allowed=false` | Contract observations and limitations | Real no-tool call; `business_evaluation=false` and `verified_claim_created=false`; not a formal business review |
| **contract_only** | [`design_evidence_project`](../skills/awakening/design_evidence_project/SKILL.md)<br/>Architect | Designs exactly two candidate evidence projects from a gap and time constraints | Gap references, time constraints, allowed tools, rubric references | Two project candidates | Contract, Schema, and examples only; not activated at runtime |
| **contract_only** | [`build_versioned_plan`](../skills/awakening/build_versioned_plan/SKILL.md)<br/>Architect | Breaks a selected project into a versioned, ordered, dependency-aware task plan | Base state/plan version, selected project, duration, tasks | Versioned-plan proposal | Contract, Schema, and examples only; does not create or activate V2 |
| **contract_only** | [`propose_replan_under_constraints`](../skills/awakening/propose_replan_under_constraints/SKILL.md)<br/>Architect | Produces two or three adjustment candidates when constraints change, without exceeding frozen constraints | Current plan/version, synthetic trigger, immutable constraints | Two or three proposal-only candidates | Contract, Schema, and examples only; no plan activation or business-state write |
| **deny_only** | [`apply_authorized_change`](../skills/awakening/apply_authorized_change/SKILL.md)<br/>Manager | Reserves a narrow future interface for requesting an authorized change while proving that M4 cannot apply one successfully | Proposal ID, state version, idempotency key, optional HumanDecision ID | Machine-readable refusal; state remains unchanged | M4 Registry is fixed to `deny_only / M4_APPLY_DISABLED`; no successful write is demonstrated |
| **deny_only** | [`distill_experience_candidate`](../skills/awakening/distill_experience_candidate/SKILL.md)<br/>Architect | Defines how reusable experience candidates might later be distilled from sanitized traces | Run/trace references, outcome, error references | Fixed refusal; no creation or RAG publication | M6 staging and publication boundaries do not exist, so it always fails closed |
| **deny_only** | [`generate_evidence_bound_materials`](../skills/awakening/generate_evidence_bound_materials/SKILL.md)<br/>Coach | Defines how a future version could generate résumé, portfolio, or interview materials using only activated Claims | Material type, template reference, active-Claim references | Fixed refusal; no text or artifact is generated | M4 has no trusted active-Claim readback, so it always fails closed |

## Design files and examples for every Skill

`SKILL.md` explains the design. `manifest.json` provides machine-readable metadata. `minimal-input.json` and `minimal-output.json` are fixed synthetic format examples; they are **not run evidence**.

| Skill | Manifest | Input example | Output example | Input Schema | Output Schema |
|---|---|---|---|---|---|
| `analyze_role_gap` | [manifest](../skills/awakening/analyze_role_gap/manifest.json) | [input](../skills/awakening/analyze_role_gap/examples/minimal-input.json) | [output](../skills/awakening/analyze_role_gap/examples/minimal-output.json) | [schema](../schemas/m4/skills/analyze_role_gap.input.schema.json) | [schema](../schemas/m4/skills/analyze_role_gap.output.schema.json) |
| `coach_task_submission` | [manifest](../skills/awakening/coach_task_submission/manifest.json) | [input](../skills/awakening/coach_task_submission/examples/minimal-input.json) | [output](../skills/awakening/coach_task_submission/examples/minimal-output.json) | [schema](../schemas/m4/skills/coach_task_submission.input.schema.json) | [schema](../schemas/m4/skills/coach_task_submission.output.schema.json) |
| `review_evidence_against_rubric` | [manifest](../skills/awakening/review_evidence_against_rubric/manifest.json) | [input](../skills/awakening/review_evidence_against_rubric/examples/minimal-input.json) | [output](../skills/awakening/review_evidence_against_rubric/examples/minimal-output.json) | [schema](../schemas/m4/skills/review_evidence_against_rubric.input.schema.json) | [schema](../schemas/m4/skills/review_evidence_against_rubric.output.schema.json) |
| `design_evidence_project` | [manifest](../skills/awakening/design_evidence_project/manifest.json) | [input](../skills/awakening/design_evidence_project/examples/minimal-input.json) | [output](../skills/awakening/design_evidence_project/examples/minimal-output.json) | [schema](../schemas/m4/skills/design_evidence_project.input.schema.json) | [schema](../schemas/m4/skills/design_evidence_project.output.schema.json) |
| `build_versioned_plan` | [manifest](../skills/awakening/build_versioned_plan/manifest.json) | [input](../skills/awakening/build_versioned_plan/examples/minimal-input.json) | [output](../skills/awakening/build_versioned_plan/examples/minimal-output.json) | [schema](../schemas/m4/skills/build_versioned_plan.input.schema.json) | [schema](../schemas/m4/skills/build_versioned_plan.output.schema.json) |
| `propose_replan_under_constraints` | [manifest](../skills/awakening/propose_replan_under_constraints/manifest.json) | [input](../skills/awakening/propose_replan_under_constraints/examples/minimal-input.json) | [output](../skills/awakening/propose_replan_under_constraints/examples/minimal-output.json) | [schema](../schemas/m4/skills/propose_replan_under_constraints.input.schema.json) | [schema](../schemas/m4/skills/propose_replan_under_constraints.output.schema.json) |
| `apply_authorized_change` | [manifest](../skills/awakening/apply_authorized_change/manifest.json) | [input](../skills/awakening/apply_authorized_change/examples/minimal-input.json) | [output](../skills/awakening/apply_authorized_change/examples/minimal-output.json) | [schema](../schemas/m4/skills/apply_authorized_change.input.schema.json) | [schema](../schemas/m4/skills/apply_authorized_change.output.schema.json) |
| `distill_experience_candidate` | [manifest](../skills/awakening/distill_experience_candidate/manifest.json) | [input](../skills/awakening/distill_experience_candidate/examples/minimal-input.json) | [output](../skills/awakening/distill_experience_candidate/examples/minimal-output.json) | [schema](../schemas/m4/skills/distill_experience_candidate.input.schema.json) | [schema](../schemas/m4/skills/distill_experience_candidate.output.schema.json) |
| `generate_evidence_bound_materials` | [manifest](../skills/awakening/generate_evidence_bound_materials/manifest.json) | [input](../skills/awakening/generate_evidence_bound_materials/examples/minimal-input.json) | [output](../skills/awakening/generate_evidence_bound_materials/examples/minimal-output.json) | [schema](../schemas/m4/skills/generate_evidence_bound_materials.input.schema.json) | [schema](../schemas/m4/skills/generate_evidence_bound_materials.output.schema.json) |

The machine registration state is authoritative in the [Skill Registry](../contracts/m4/skill-registry.json).

## Real outputs from the three live Skills

These files are canonical Worker outputs extracted from and hash-bound to two successful runs. They are not the format examples under `examples/`. See [EVIDENCE.md](../EVIDENCE.md) for the verification method and its limits.

| Live Skill | Run A | Run B |
|---|---|---|
| `analyze_role_gap` / Architect | [canonical output](../evidence/run-a/outputs/role_project_architect.json) | [canonical output](../evidence/run-b/outputs/role_project_architect.json) |
| `coach_task_submission` / Coach | [canonical output](../evidence/run-a/outputs/execution_evidence_coach.json) | [canonical output](../evidence/run-b/outputs/execution_evidence_coach.json) |
| `review_evidence_against_rubric` / Reviewer contract smoke | [canonical output](../evidence/run-a/outputs/independent_quality_reviewer.json) | [canonical output](../evidence/run-b/outputs/independent_quality_reviewer.json) |

`contract_only` and `deny_only` have no live Worker outputs. That is part of the M4 activation boundary, not an omitted artifact.

The packaged hashes establish internal consistency between the published canonical outputs and sanitized projections. They do not independently prove raw Matrix events, raw Provider transport, or a remote Provider bill.

## Suggested reading order for judges

1. Read this page first to distinguish the three activation states across all nine Skills.
2. Open the three live `SKILL.md` files and their Run B canonical outputs.
3. Continue with the [three-minute multi-agent guide](JUDGE_GUIDE.en.md).
4. For technical detail, read [System architecture](ARCHITECTURE.md) and [Evidence boundaries](../EVIDENCE.md).

---

**中文摘要：** 本包包含 9 个 Skill 契约，但只有 3 个在比赛 Demo 中进行了 live 调用；另外 3 个只定义契约，3 个只用于证明安全拒绝。Reviewer live call 是 no-tool contract smoke，不是正式业务评审。
