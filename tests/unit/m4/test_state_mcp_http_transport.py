"""Focused unit coverage for the real M4 State MCP HTTP boundary."""

from __future__ import annotations

import json
import unittest
from typing import Any
from uuid import uuid4

from awakening.adapters.m4 import (
    M4BearerPrincipalRegistry,
    M4StateMcpHttpTransport,
)
from awakening.state.contracts import CommandResult, CommandStatus, ReasonCode
from awakening.state.m4 import TrustedRuntimeContext


class _CapturingStateMcp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def get_snapshot(
        self,
        *,
        program_id: str,
        trusted_context: TrustedRuntimeContext,
    ) -> dict[str, Any]:
        self.calls.append(("get_snapshot", trusted_context.agent_identity.value))
        return {"program": {"program_id": program_id, "state_version": 0}}

    def get_command_status(self, **values: Any) -> dict[str, Any]:
        raise AssertionError(f"unexpected get_command_status call: {values!r}")

    def submit_proposal(
        self,
        *,
        program_id: str,
        base_version: dict[str, Any],
        proposal_payload: dict[str, Any],
        idempotency_key: str,
        trusted_context: TrustedRuntimeContext,
        traceparent: str | None = None,
    ) -> CommandResult:
        self.calls.append(("submit_proposal", trusted_context.agent_identity.value))
        return CommandResult(
            command_id=str(uuid4()),
            status=CommandStatus.COMMITTED,
            reason_code=ReasonCode.OK,
            state_version=int(base_version["state_version"]),
            result={
                "program_id": program_id,
                "proposal_id": str(uuid4()),
                "idempotency_key": idempotency_key,
                "payload": proposal_payload,
                "traceparent": traceparent,
            },
        )

    def apply_authorized_change(self, **values: Any) -> CommandResult:
        raise AssertionError(f"unexpected apply_authorized_change call: {values!r}")


class StateMcpHttpTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.program_id = str(uuid4())
        self.run_id = str(uuid4())
        self.tokens = {
            "awakening_program_manager": "m" * 32,
            "role_project_architect": "a" * 32,
            "execution_evidence_coach": "c" * 32,
            "independent_quality_reviewer": "r" * 32,
        }
        roles = {
            "awakening_program_manager": "manager",
            "role_project_architect": "architect",
            "execution_evidence_coach": "coach",
            "independent_quality_reviewer": None,
        }
        contexts = {
            identity: TrustedRuntimeContext(
                principal_id=f"m4-http-{identity}",
                agent_identity=identity,
                program_role=role,
                program_scope=(self.program_id,),
                run_id=self.run_id,
                auth_context_id=f"m4-http-context-{identity}",
            )
            for identity, role in roles.items()
        }
        self.adapter = _CapturingStateMcp()
        registry = M4BearerPrincipalRegistry(
            {self.tokens[identity]: context for identity, context in contexts.items()}
        )
        self.transport = M4StateMcpHttpTransport(
            bearer_principals=registry,
            state_mcp=self.adapter,  # type: ignore[arg-type]
        )

    def _rpc(
        self,
        *,
        identity: str,
        method: str,
        params: dict[str, Any],
        request_id: int,
    ) -> tuple[int, dict[str, Any]]:
        response = self.transport.handle_request(
            method="POST",
            path="/mcp",
            headers={
                "Authorization": f"Bearer {self.tokens[identity]}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            body=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params,
                }
            ).encode("utf-8"),
        )
        return response.status_code, json.loads(response.body)

    def test_manager_and_architect_handshake_list_and_call_allowed_tools(self) -> None:
        status, initialized = self._rpc(
            identity="awakening_program_manager",
            method="initialize",
            params={
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "mcporter", "version": "unit"},
            },
            request_id=1,
        )
        self.assertEqual(200, status)
        self.assertEqual("awakening-m4-state", initialized["result"]["serverInfo"]["name"])

        _, manager_list = self._rpc(
            identity="awakening_program_manager",
            method="tools/list",
            params={},
            request_id=2,
        )
        self.assertEqual(
            {"get_snapshot", "get_command_status", "apply_authorized_change"},
            {tool["name"] for tool in manager_list["result"]["tools"]},
        )
        _, manager_call = self._rpc(
            identity="awakening_program_manager",
            method="tools/call",
            params={
                "name": "get_snapshot",
                "arguments": {"program_id": self.program_id},
            },
            request_id=3,
        )
        self.assertFalse(manager_call["result"]["isError"])

        _, architect_list = self._rpc(
            identity="role_project_architect",
            method="tools/list",
            params={},
            request_id=4,
        )
        self.assertEqual(
            {"submit_proposal", "get_command_status"},
            {tool["name"] for tool in architect_list["result"]["tools"]},
        )
        _, architect_call = self._rpc(
            identity="role_project_architect",
            method="tools/call",
            params={
                "name": "submit_proposal",
                "arguments": {
                    "program_id": self.program_id,
                    "base_version": {
                        "state_version": 0,
                        "plan_version_id": str(uuid4()),
                    },
                    "proposal_payload": {"target_role": "backend_engineer"},
                    "idempotency_key": "m4-http-positive-1",
                },
            },
            request_id=5,
        )
        self.assertFalse(architect_call["result"]["isError"])
        self.assertEqual(
            [
                ("get_snapshot", "awakening_program_manager"),
                ("submit_proposal", "role_project_architect"),
            ],
            self.adapter.calls,
        )

    def test_coach_reviewer_and_body_assertions_are_rejected_before_adapter(self) -> None:
        for request_id, (identity, tool) in enumerate(
            (
                ("execution_evidence_coach", "submit_proposal"),
                ("independent_quality_reviewer", "get_snapshot"),
            ),
            start=10,
        ):
            with self.subTest(identity=identity):
                _, listed = self._rpc(
                    identity=identity,
                    method="tools/list",
                    params={},
                    request_id=request_id,
                )
                self.assertEqual([], listed["result"]["tools"])
                arguments: dict[str, Any] = {"program_id": self.program_id}
                if tool == "submit_proposal":
                    arguments.update(
                        {
                            "base_version": {
                                "state_version": 0,
                                "plan_version_id": str(uuid4()),
                            },
                            "proposal_payload": {},
                            "idempotency_key": "m4-http-denied",
                        }
                    )
                _, denied = self._rpc(
                    identity=identity,
                    method="tools/call",
                    params={"name": tool, "arguments": arguments},
                    request_id=request_id + 20,
                )
                self.assertTrue(denied["result"]["isError"])
                self.assertEqual(
                    "M4_METHOD_NOT_ALLOWED",
                    denied["result"]["structuredContent"]["reason_code"],
                )

        forbidden_facts = {
            "agentId": "forged",
            "programScope": [self.program_id],
            "objectUri": "file:///untrusted",
            "contentHash": "0" * 64,
            "receiptId": str(uuid4()),
        }
        for request_id, (field, value) in enumerate(forbidden_facts.items(), start=50):
            with self.subTest(forbidden_field=field):
                _, body_denied = self._rpc(
                    identity="role_project_architect",
                    method="tools/call",
                    params={
                        "name": "submit_proposal",
                        "arguments": {
                            "program_id": self.program_id,
                            "base_version": {
                                "state_version": 0,
                                "plan_version_id": str(uuid4()),
                            },
                            "proposal_payload": {field: value},
                            "idempotency_key": f"m4-http-body-denied-{request_id}",
                        },
                    },
                    request_id=request_id,
                )
                self.assertTrue(body_denied["result"]["isError"])
                self.assertEqual(
                    "IDENTITY_FIELD_FORBIDDEN",
                    body_denied["result"]["structuredContent"]["reason_code"],
                )
        self.assertEqual([], self.adapter.calls)


if __name__ == "__main__":
    unittest.main()
