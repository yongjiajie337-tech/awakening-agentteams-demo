"""M4 unit/contract item 3: server identity gates model and tool access."""

from __future__ import annotations

import unittest
from uuid import uuid4

from awakening.model_gateway.m4 import GatewayReasonCode, ModelInvocation
from awakening.orchestration.m4 import (
    BoundRuntimeAuthorizer,
    RuntimeBinding,
    TrustedRuntimeSession,
)
from awakening.state.contracts import PrincipalType, TrustedPrincipal
from awakening.state.m4 import M4StateMcpMethod, TrustedRuntimeContext


class RuntimePrincipalAllowlistTests(unittest.TestCase):
    def test_wrong_role_forged_identity_and_unlisted_tool_fail_before_execution(self) -> None:
        program_id = str(uuid4())
        run_id = str(uuid4())
        binding = RuntimeBinding(
            credential_id="runtime-architect-1",
            agent_identity_id="role_project_architect",
            agent_identity_version="1.0.0",
            trusted_principal=TrustedPrincipal(
                principal_id="m4-agent-architect",
                principal_type=PrincipalType.AGENT,
                scopes=("state:mcp",),
                program_scope=(program_id,),
                auth_context_id="m4-runtime-architect-context",
            ),
            program_id=program_id,
            run_id=run_id,
            runtime_config_snapshot_id=str(uuid4()),
            public_model_alias="m4-approved-model",
            allowed_skill_versions={"analyze_role_gap": "1.0.0"},
            allowed_tools=("submit_proposal",),
        )
        authorizer = BoundRuntimeAuthorizer(
            TrustedRuntimeSession(binding=binding, credential_fingerprint="f" * 64)
        )

        def invocation(**changes: object) -> ModelInvocation:
            values: dict[str, object] = {
                "program_id": program_id,
                "run_id": run_id,
                "model_call_id": str(uuid4()),
                "agent_identity_id": "role_project_architect",
                "agent_identity_version": "1.0.0",
                "skill_name": "analyze_role_gap",
                "skill_version": "1.0.0",
                "runtime_config_snapshot_id": binding.runtime_config_snapshot_id,
                "reservation_id": str(uuid4()),
                "provider_input": {"messages": []},
            }
            values.update(changes)
            return ModelInvocation(**values)  # type: ignore[arg-type]

        self.assertIs(
            GatewayReasonCode.RUNTIME_PRINCIPAL_DENIED,
            authorizer.authorize_model_call(
                invocation(agent_identity_id="execution_evidence_coach")
            ),
        )
        self.assertIs(
            GatewayReasonCode.RUNTIME_BODY_FORBIDDEN,
            authorizer.authorize_model_call(
                invocation(provider_input={"messages": [], "agent_id": "forged"})
            ),
        )
        for field, value in {
            "response_format": {"type": "text"},
            "enable_thinking": True,
            "seed": 99,
        }.items():
            with self.subTest(server_owned_field=field):
                self.assertIs(
                    GatewayReasonCode.RUNTIME_BODY_FORBIDDEN,
                    authorizer.authorize_model_call(
                        invocation(provider_input={"messages": [], field: value})
                    ),
                )
        self.assertIs(
            GatewayReasonCode.TOOL_NOT_ALLOWED,
            authorizer.authorize_model_call(
                invocation(
                    provider_input={
                        "messages": [],
                        "tools": [
                            {
                                "type": "function",
                                "function": {"name": "direct_business_write"},
                            }
                        ],
                    }
                )
            ),
        )

        coach_context = TrustedRuntimeContext(
            principal_id="m4-agent-coach",
            agent_identity="execution_evidence_coach",
            program_role="coach",
            program_scope=(program_id,),
            run_id=run_id,
            auth_context_id="m4-runtime-coach-context",
        )
        self.assertFalse(coach_context.allows(M4StateMcpMethod.SUBMIT_PROPOSAL))


if __name__ == "__main__":
    unittest.main()
