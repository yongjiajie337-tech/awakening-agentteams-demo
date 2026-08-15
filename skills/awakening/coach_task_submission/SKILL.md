---
name: coach_task_submission
description: Compare a fixed synthetic task submission's registered evidence references with explicit criteria. Use for the M4 Coach representative path to return non-authoritative feedback without certifying evidence or reading arbitrary objects.
---

# coach_task_submission

Version: 1.0.0

## Skill name

coach_task_submission

## Type

coaching

## Scenario

Compare a fixed synthetic task submission's registered evidence references with
explicit acceptance criteria and return coaching feedback.

## Input params

Program/task versions, the current task, criteria, and server-provided
EvidenceItem identifiers with object-reference hashes. Raw private content and
arbitrary URIs are not accepted.

## Output

CoachFeedback with criterion observations, missing evidence, and a local
revision suggestion. It never certifies completion.

## Invocation condition

Only execution_evidence_coach may invoke it for the M4 representative live call
after the Manager has supplied a version-bound synthetic task.

## Dependent tool/system

M4 Skill registry, M4 Model Gateway, and server-provided registered evidence
references. There is no arbitrary object-store read.

## Failure handling

Missing task versions, unknown evidence identifiers, absent criteria, or
Schema-invalid content fail closed as unverifiable.

## Permission & safety

The Coach cannot do the user's work, certify evidence, form final facts, read
quarantine content, activate a plan, or write business state.

## Reuse value

The criterion-to-evidence feedback contract can support other authorized
project and learning tasks.
