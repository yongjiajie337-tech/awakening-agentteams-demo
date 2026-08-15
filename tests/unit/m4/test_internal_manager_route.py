"""The Manager route is internal, deterministic and State-bound."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any
from uuid import uuid4

from awakening.orchestration.m4 import (
    MANAGER_ROUTE_OPERATION,
    MANAGER_ROUTE_PUBLIC_SKILL,
    ManagerRouteOperation,
    ManagerRouteReasonCode,
    ManagerRouteRequest,
)
from awakening.state.m4 import TrustedRuntimeContext


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class _SnapshotReader:
    def __init__(self, program_id: str, state_version: int) -> None:
        self.program_id = program_id
        self.state_version = state_version
        self.read_count = 0

    def get_snapshot(self, *, program_id: str, trusted_context: object) -> dict[str, Any]:
        self.read_count += 1
        return {
            "program": {
                "program_id": self.program_id,
                "state_version": self.state_version,
            },
            "active_plan": None,
            "tasks": [],
        }


class _DelegationRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def dispatch(self, *, target_identity: str, payload: object) -> str:
        self.calls.append((target_identity, dict(payload)))  # type: ignore[arg-type]
        return f"matrix-event-{len(self.calls)}"


class InternalManagerRouteTests(unittest.TestCase):
    def test_internal_route_reads_state_and_dispatches_only_fixed_worker_paths(self) -> None:
        program_id = str(uuid4())
        run_id = str(uuid4())
        reader = _SnapshotReader(program_id, 7)
        delegation = _DelegationRecorder()
        operation = ManagerRouteOperation(
            state_reader=reader,
            delegation_port=delegation,
            trusted_manager_context=TrustedRuntimeContext(
                principal_id="m4-manager",
                agent_identity="awakening_program_manager",
                program_role="manager",
                program_scope=(program_id,),
                run_id=run_id,
                auth_context_id="m4-manager-route-context",
            ),
        )

        result = operation.execute(
            ManagerRouteRequest.from_mapping(
                {"program_id": program_id, "run_id": run_id, "state_version": 7}
            )
        )

        self.assertTrue(result.routed)
        self.assertIs(ManagerRouteReasonCode.OK, result.reason_code)
        self.assertEqual(1, reader.read_count)
        self.assertEqual(
            ["role_project_architect", "execution_evidence_coach"],
            [target for target, _ in delegation.calls],
        )
        self.assertEqual(
            ["analyze_role_gap", "coach_task_submission"],
            [payload["skill_name"] for _, payload in delegation.calls],
        )
        self.assertEqual(MANAGER_ROUTE_OPERATION, result.operation)
        self.assertFalse(MANAGER_ROUTE_PUBLIC_SKILL)

        skill_registry = json.loads(
            (PROJECT_ROOT / "contracts" / "m4" / "skill-registry.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(9, skill_registry["skill_count"])
        self.assertNotIn(
            MANAGER_ROUTE_OPERATION,
            {entry["skill_name"] for entry in skill_registry["skills"]},
        )

    def test_extra_input_or_stale_state_cannot_select_a_route(self) -> None:
        program_id = str(uuid4())
        run_id = str(uuid4())
        with self.assertRaises(ValueError):
            ManagerRouteRequest.from_mapping(
                {
                    "program_id": program_id,
                    "run_id": run_id,
                    "state_version": 2,
                    "worker": "forged-target",
                }
            )

        delegation = _DelegationRecorder()
        operation = ManagerRouteOperation(
            state_reader=_SnapshotReader(program_id, 3),
            delegation_port=delegation,
            trusted_manager_context=TrustedRuntimeContext(
                principal_id="m4-manager",
                agent_identity="awakening_program_manager",
                program_role="manager",
                program_scope=(program_id,),
                run_id=run_id,
                auth_context_id="m4-manager-route-context",
            ),
        )
        result = operation.execute(
            ManagerRouteRequest(program_id=program_id, run_id=run_id, state_version=2)
        )
        self.assertFalse(result.routed)
        self.assertIs(ManagerRouteReasonCode.SNAPSHOT_BINDING_MISMATCH, result.reason_code)
        self.assertEqual([], delegation.calls)


if __name__ == "__main__":
    unittest.main()
