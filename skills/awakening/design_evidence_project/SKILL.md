---
name: design_evidence_project
description: Turn a schema-valid evidence-bound gap assessment into exactly two feasible project candidates. Use only for the M4 Architect contract path; never activate a project, invent resources, or use RAG.
---

# design_evidence_project

Version: 1.0.0

## Skill name

design_evidence_project

## Type

planning

## Scenario

Turn an evidence-bound synthetic gap assessment into exactly two feasible
project candidates.

## Input params

Program and state identifiers, gap references, time constraints, allowed tools,
and a frozen rubric reference.

## Output

Exactly two ProjectCandidate proposals with deliverables, evidence targets,
effort, risks, and no activation.

## Invocation condition

Only role_project_architect may invoke it after a Schema-valid gap assessment.
M4 registers the full contract but does not make this a representative live
call.

## Dependent tool/system

M4 Skill registry and fixed synthetic inputs. RAG is unavailable in M4.

## Failure handling

If two feasible candidates cannot be supported by the supplied constraints,
the Skill fails closed with no candidate list and requests human direction.

## Permission & safety

Proposal-only. It cannot invent resources, perform user work, activate a
project, use RAG, or write business state.

## Reuse value

The two-candidate contract is reusable for constrained project selection in
training and authorized internal development scenarios.
