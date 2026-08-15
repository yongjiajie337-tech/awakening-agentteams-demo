# Changelog

## 1.0.3 - Unreleased

- Added a Chinese-first GitHub landing page and a substantive English entry
  that state the real `1 Manager + 3 Worker` topology, deterministic Manager,
  Reviewer contract-smoke boundary, two sanitized live-run projections, and
  reference-environment reproduction limits without expanding the M5 claim.
- Added beginner-oriented contribution, community conduct, support, and
  citation documents with explicit Secret, privacy, evidence, and billing
  boundaries.
- Added a bilingual vulnerability-reporting policy and a detailed security
  model, separating responsible disclosure from operational Live, Secret, and
  recording rules.
- Added canonical Repository, Documentation, Issues, Changelog, and Homepage
  links to the Python project metadata.
- Added GitHub Issue/PR templates, a least-privilege Windows offline-verification
  workflow with commit-pinned actions, conservative Dependabot configuration,
  and exact-byte-safe Git/editor attributes.
- Replaced the public live path's M5-specific Provider Secret coupling with the
  neutral protected reference-workspace file `.secrets/demo-provider.env` and
  field `AWAKENING_DEMO_PROVIDER_API_KEY`; no credential value is distributed.
- Hardened Provider Secret admission so both the exact `.secrets` parent and
  Secret file must pass ordinary-path, reparse-point, owner, and least-privilege
  ACL checks before any value read, with ACL and junction regression tests.
- Made the package verifier usable from a normal Git checkout by excluding only
  root Git metadata while continuing to reject nested Git metadata and runtime
  residue such as `.secrets`, `.env`, `tmp`, and `__pycache__`.
- Added focused regression tests for the neutral Secret contract and Git
  checkout boundary; exact test counts remain enforced by the offline entrypoint.
- Kept the two packaged live-run evidence sets unchanged: v1.0.3 improves
  portability and open-source maintenance but does not claim a new live run.

## 1.0.2 - 2026-08-15

- Closed the packaged runtime dependency graph by including and pinning the two
  Python helpers that build trusted Worker inputs and validate/parse outputs.
- Added direct offline Matrix delegation behavior tests using a mocked
  subprocess boundary; no Docker, network, Provider, or Secret is required.
- Added Full, Stdlib, and PackageOnly verification modes with actionable,
  locale-safe dependency diagnostics before any package-level PASS marker.
- Promoted Stdlib as the no-third-party-Python fallback, added ASCII English
  dependency hints, and made Python bytecode residue fail with a specific
  re-extraction recovery marker instead of a generic directory error.
- Added the job-seeker scenario value, deterministic Manager boundary,
  Reviewer contract-smoke rationale, no-write M4 boundary, and honest
  observability/RAG limitations to the public review documentation.

## 1.0.1 - 2026-08-15

- Hardened the offline verifier for clean Windows environments, dependency
  diagnostics, proxy isolation, Git Bash discovery, locale-independent output,
  and stable reference-workspace errors.
- Corrected the public Manager and Reviewer descriptions: the Manager is a
  deterministic policy-and-contract control plane, and the Reviewer path is a
  no-tool contract smoke rather than a formal business evaluation.
- Added the six canonical Worker outputs from the two successful synthetic Demo
  runs, with role, Schema, canonical hash, and evidence bindings.
- Added selected offline M4 pure-logic tests and clarified the package's actual
  test-coverage boundary.
- Clarified Skill activation status, evidence provenance, runtime-image source
  pointers, and the distinct responsibilities of the three checksum manifests.

## 1.0.0 - 2026-08-15

- Initial dual-layer sanitized competition review package.
