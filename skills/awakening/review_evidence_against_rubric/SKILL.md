---
name: review_evidence_against_rubric
description: Run a no-tool M4 contract_smoke against a fixed synthetic closed package. Use only for the independent Reviewer representative path; never create a formal QualityReview, ReviewDecision, VerifiedClaim, or business state.
---

# review_evidence_against_rubric

Version: 1.0.0

## Skill name

review_evidence_against_rubric

## Type

evaluation

## Scenario

Run one M4 contract_smoke against a fixed synthetic closed package to prove the
Reviewer identity and structured output contract.

## Input params

An explicit contract_smoke marker, fixed package kind, package and context
hashes, frozen rubric version, synthetic criteria and synthetic evidence facts,
and tools_allowed fixed to false.

## Output

A ReviewerContractSmoke containing Schema observations and mandatory
non-business limitations. It is not QualityReview, ReviewDecision, or
VerifiedClaim.

## Invocation condition

Only independent_quality_reviewer in a fresh M4 session may invoke it. The
input must be a fixed synthetic closed package and no tool credential may be
present.

## Dependent tool/system

M4 Skill registry and M4 Model Gateway only. The Reviewer has no State MCP,
object-store, external-tool, or Matrix-history access.

## Failure handling

Any missing hash, non-contract-smoke mode, tool enablement, extra field, or
non-synthetic package fails closed before model invocation.

## Permission & safety

No tools, no free room history, no business write, no formal evaluation, and
no final fact. M5 Review Dispatch Gate remains not implemented.

## Reuse value

The closed no-tool evaluation shape can be reused as a contract fixture before
an authorized deterministic Review Dispatch Gate exists.
