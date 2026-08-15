# Sanitized run evidence

This directory contains safe, human-auditable projections from two completed
AgentTeams demo runs. Both runs used the same topology: one deterministic
policy/contract Manager coordinated three Workers (`role_project_architect`,
`execution_evidence_coach`, and `independent_quality_reviewer`). The Manager did
not call a model or perform model-autonomous routing. Each run completed all
three Worker calls and the eight-event Matrix flow, then restored the reference
environment.

| Evidence set | Outer run | Core run | Request | Result |
| --- | --- | --- | --- | --- |
| `run-a` | `eccfaadd-dd07-42aa-ae41-68ea7c97d65b` | `55dfc571-4cae-4483-b98e-8570bf5f9760` | `9d9ea216-66e2-405c-a63f-64cf0570bb3a` | 3/3 Workers completed |
| `run-b` | `3f481126-507b-41b9-8be6-01f4a1b17f2a` | `2bccbb6c-7bce-4211-b1a3-2b1e105de0fb` | `135540e8-ddd7-465a-a402-58f88139ee25` | 3/3 Workers completed |

Each run contains:

- `summary.json`: required success, concurrency, usage, cost, Matrix, and
  restore assertions;
- `provider-events.jsonl`: one sanitized outcome per Worker plus an aggregate;
- `outputs/`: the three real, canonical structured Worker outputs extracted
  from the frozen run result;
- `matrix-flow.jsonl`: the eight safe flow records without room IDs, event IDs,
  message bodies, or user content;
- `lifecycle-flow.jsonl`: selected safe lifecycle milestones without PIDs,
  ports, URLs, absolute paths, or secret metadata;
- `artifact-hashes.json`: integrity anchors for the frozen local source files
  and the generated projections.

Projection hashes, including the raw file bytes of all three `outputs/*.json`
files, are recomputed by the offline verifier because those files are included.
Source-artifact hashes are historical anchors only: the excluded frozen source
files can only be compared in a controlled reference-environment review.

Across `run-a` and `run-b`, the package therefore contains six canonical real
Worker outputs. For each output, the verifier parses the JSON and serializes it
again with UTF-8, sorted keys, compact separators, `ensure_ascii=false`, and
`allow_nan=false`. SHA-256 is computed over that canonical JSON value without
the display file's final LF, then compared exactly with the same role's
`output_sha256` in `provider-events.jsonl`. The parsed value is also checked
against the mapped Skill output schema (Draft 2020-12 with UUID format checks).
This lets a reviewer recompute the structured-result binding without the
excluded Provider transport envelope.

The Reviewer output is intentionally different in scope from a formal review:
it is a no-tool `contract_smoke_live_call`, and its canonical result says
`business_evaluation=false` and `verified_claim_created=false`. Architect and
Coach are the two `representative_live_call` results.

The trusted Reviewer fixture contains one criterion about an asserted expected
result and one synthetic evidence fact about a passing assertion. Both packaged
runs returned a non-empty observation that cites that fact for that criterion.
The complete-object template fixes keys, hashes, and safe false/limitation
fields and prevents `[]`, `null`, wrappers, or Markdown; it is not a fallback
result. The variable observation remains contract-smoke reasoning, not a formal
business-quality decision.

The per-run implementation caps both total Worker Provider calls and in-flight
calls at three. Evidence `max_inflight: 3` is the observed peak for that run,
not a declaration of the configured limit. It comes from the live process's
locked in-flight counter. The sanitized projection omits per-call start/end
timestamps, so it binds the recorded counter value but does not permit an
independent timing replay that recomputes the peak.

Both live runs also require `business_state_changed=false`.
`apply_authorized_change` is deny-only in M4 (`M4_APPLY_DISABLED`), which proves
a fail-closed write boundary but not an approved V2/V3 business-state loop.

The package includes the six canonical contract outputs but intentionally
excludes raw Provider response envelopes, prompts, message bodies, PIDs,
absolute machine paths, full runtime configuration, baseline snapshots,
credentials, and secret files. The cost fields are local gateway calculations
from recorded token usage; they are not an independently verified
remote-provider invoice. The screen recording is retained separately and is not
embedded in this code archive.

Three root/config integrity lists have separate scopes:

- `PACKAGE_MANIFEST.json` inventories payload files except itself and
  `SHA256SUMS.txt`, including byte sizes and hashes;
- `SHA256SUMS.txt` hashes every unpacked file except itself, including the
  package manifest;
- `config/reference-source-pins.json` binds only the 180 public files admitted
  into an operator-supplied live reference workspace.

The first two verify the distributed package; the third verifies live-source
compatibility. Per-run `artifact-hashes.json` adds evidence-local projection
hashes and non-recomputable historical source anchors; it does not replace any
of those three lists.
