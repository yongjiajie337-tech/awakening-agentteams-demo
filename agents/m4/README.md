# M4 Agent identities

This directory contains the four M4 AgentTeams identity contracts. Each
identity.json has exactly the eight competition fields:

1. name
2. role
3. capabilities
4. inputs
5. outputs
6. dependencies
7. decision_boundary
8. trace

Version, Skill allowlists, and M4 activation metadata live in
contracts/m4/identity-registry.json so that the eight-field identity contract
remains closed.

The Reviewer is limited to a fixed synthetic contract_smoke input and has no
tool credential. None of these identities is an authoritative business-state
writer.
