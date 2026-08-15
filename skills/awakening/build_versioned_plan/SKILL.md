---
name: build_versioned_plan
description: Build a version-bound ordered-task PlanVersionProposal from an authorized synthetic project candidate. Use only for the M4 Architect contract path; never activate V2, approve a proposal, or bypass State Service.
---

# build_versioned_plan

Version: 1.0.0

## Skill name

build_versioned_plan

## Type

planning

## Scenario

Create a version-bound PlanVersionProposal from one selected synthetic project
and explicit ordered tasks.

## Input params

Program ID, base state and plan identifiers, selected project, ordered task
definitions, and the fixed duration.

## Output

A PlanVersionProposal with the same base identifiers, ordered tasks, and
activation_allowed set to false.

## Invocation condition

Only role_project_architect may invoke it after a project candidate has been
selected through an authorized flow. M4 registers the contract without
activating a plan.

## Dependent tool/system

M4 Skill registry and M4 State MCP proposal boundary.

## Failure handling

Duplicate task keys or order values, missing dependencies, stale bases, or
Schema-invalid content fail closed and produce no proposal.

## Permission & safety

The Skill outputs a proposal only. It cannot write a PlanVersion, activate V2,
approve a proposal, or bypass State Service.

## Reuse value

The version-bound ordered-task contract can be reused for other long-running
authorized plans.
