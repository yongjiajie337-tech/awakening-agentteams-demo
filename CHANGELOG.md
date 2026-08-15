# Changelog

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
