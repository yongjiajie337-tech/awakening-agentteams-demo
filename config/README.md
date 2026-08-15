# Configuration boundary

Files in this directory are human-readable review examples. They are not
silently loaded by `run_demo.ps1` and they contain no credential value.

- `reference-runtime.example.json` declares the supported live reference
  profile and its side effects. Its `worker_provider_calls_max: 3` is a hard
  per-run call limit; evidence `max_inflight` is an observed peak, not this
  configuration field.
- `demo-provider.env.example` declares only the neutral single-field format.
  It is not a credential file and is never loaded directly.  For a v1.0.3
  compatible live reference environment, the operator must create
  `.secrets/demo-provider.env` inside that separate reference workspace,
  replace the placeholder locally, and protect the file before preflight.
  This repository has no automatic Secret generator, migration, or copy tool;
  legacy `.env.m5.provider` is neither read nor migrated. Use the security
  workflow provided by the operator's organization or reference environment.
- `offline-verification.example.json` declares the offline verifier boundary.
- `reference-source-pins.json` binds the 180 public runtime source, contract,
  schema, Agent, and Skill files that a live reference workspace must match.
- Authoritative AgentTeams image pins are under
  `infra/agentteams/m4/runtime-images.lock.json`.
- Runtime resource examples are under `infra/agentteams/m4/resources/`.

The package does not contain, generate, or copy a Provider credential value.
A live reproduction uses only the operator-created protected credential in
the compatible reference workspace.  The value is read by the live Gateway
from that exact file at the final composition stage; it is never supplied in
an argument, process environment variable, configuration example, or log.

Preflight does not read the credential value. Before checking the file, it
requires the exact `<ReferenceWorkspace>/.secrets` parent to be an ordinary,
non-symlink, non-junction, non-reparse directory. Both that directory and the
credential file must have protected explicit ACLs, current-user ownership, and
exact `FullControl` entries for the current user, `SYSTEM`, and
`Administrators`. At most one additional explicit reader may have rights no
broader than `Read + Synchronize`; broad principals and any extra create,
write, delete, owner, or permission capability are rejected. Both a
three-entry tight ACL and a four-entry restricted-reader ACL are supported.
This is a fail-closed check of locally visible path and ACL metadata, not a
certification of the host, administrator, credential provenance, or cloud
account.

The `evidence` string inside `runtime-images.lock.json` points back to the
controlled M4 source-workspace artifact that originally recorded the image-pin
decision. That historical artifact is intentionally not distributed in this
sanitized package, so the pointer is not expected to resolve here. It is a
provenance hint, not a runtime dependency or a public URL; the image references
and digests in the lock file are the packaged admission data.

## Three integrity lists, three scopes

- Root `PACKAGE_MANIFEST.json` inventories every payload file except itself and
  `SHA256SUMS.txt`, binding path, byte size, and SHA-256.
- Root `SHA256SUMS.txt` binds every unpacked file except itself, including
  `PACKAGE_MANIFEST.json`.
- `reference-source-pins.json` binds only the 180 public source, contract,
  schema, Agent, and Skill files that must match an operator-supplied live
  reference workspace.

The first two verify the distributed archive; the third admits a compatible
live workspace. None of them contains or attests to a credential value.

The fixed JSON body in the frozen Worker Gateway credential-probe shell is a
live credential-preflight contract fixture; the public `-Mode Preflight` does
not execute it or read a Secret. The fixture must stop at
`403 / CALL_PLAN_UNAVAILABLE` with zero Provider calls; it is not a
project-supplied business prompt. The shell is kept unchanged so its
reference-source pin remains meaningful.
