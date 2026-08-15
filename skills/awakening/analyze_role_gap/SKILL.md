---
name: analyze_role_gap
description: Compare source-referenced synthetic role requirements with confirmed user facts and explicit constraints. Use for the M4 Architect representative path to produce a proposal-only GapAssessment without RAG or business-state activation.
---

# analyze_role_gap

Version: 1.0.0

## Skill name

analyze_role_gap

## Type

analysis

## Scenario

Compare fixed synthetic role facts with confirmed synthetic user facts for the
M4 Architect representative call.

## Input params

Program ID, state version, source-referenced role facts, confirmed user facts,
and explicit time constraints. The exact contract is the registered input
Schema.

## Output

A GapAssessmentProposal containing evidence-bound gaps and explicit
unable_to_determine items. It is a proposal and never an activated fact.

## Invocation condition

Only role_project_architect may invoke it after the Manager has re-read the
authoritative snapshot and supplied fixed synthetic facts.

## Dependent tool/system

M4 Skill registry, M4 Model Gateway, and the Manager-provided Program snapshot.
M4 does not use RAG.

## Failure handling

Missing, unconfirmed, unsourced, or Schema-invalid facts fail closed. The Skill
returns no proposal on contract failure and never fills missing facts by
inference.

## Permission & safety

Read-only analysis. The Architect cannot activate a plan, write a business
table, change a rubric, or use request-body identity.

## Reuse value

The same source/fact/constraint contract can support role-gap analysis for
other authorized role packs without changing the business-write boundary.
