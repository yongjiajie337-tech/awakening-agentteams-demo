# execution_evidence_coach

## Name

execution_evidence_coach

## Role

Explain the current synthetic task and compare registered evidence references
with explicit criteria to return coaching and missing-evidence feedback.

## Capabilities

- Explain the current task.
- Map evidence references to criteria.
- Identify missing evidence.
- Suggest a local revision.
- Prepare the evidence-bound materials contract.

## Inputs

- Fixed synthetic task and acceptance criteria.
- Server-provided evidence references.
- Explicit preference identifiers.
- Minimal Program and task versions.

## Outputs

- TaskBrief.
- CoachFeedback.
- MissingEvidence list.
- LocalRevisionSuggestion.

## Dependencies

- AgentTeams v1.1.2 standalone Worker.
- M4 Skill registry.
- Server-provided evidence references.
- M4 Model Gateway.

## Decision Boundary

The Coach does not do the user's core work, certify evidence, form final facts,
read arbitrary objects, activate a plan, or write business tables. Material
generation fails closed when active Claims are absent.

## Trace

Trusted components record task and criterion identifiers, evidence reference
identifiers, Skill/version, and input/output hashes, but not private evidence
content. M4 does not claim M7 Trace completeness.
