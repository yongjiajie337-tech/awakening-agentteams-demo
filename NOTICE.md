# NOTICE

Awakening AgentTeams Demo v1.0.2  
Copyright 2026 Awakening project contributors

This product is licensed under the Apache License, Version 2.0. See `LICENSE`.

## Project scope

This review package contains original Awakening orchestration, contracts, schemas, tests, example configuration, and sanitized evidence for a demonstration built on the AgentTeams design/runtime model.

The package does not redistribute Docker images, a Matrix homeserver, Element Web, Docker Desktop, Python, a model Provider service, or Provider credentials.

The AgentTeams/HiClaw upstream project is attributed as:

> Copyright 2026 HiClaw Contributors — Apache License 2.0.

Files under `infra/agentteams/m4/` contain interoperability configuration and integration wrappers for the pinned AgentTeams v1.1.2 runtime. Where these portions reflect or adapt upstream behavior, the upstream attribution and Apache-2.0 terms are retained; Awakening-specific orchestration, contracts, tests, examples, and evidence remain identified separately. No upstream container image is redistributed in this archive.

## Third-party software and services

The following components may be required by, referenced by, or used to reproduce the live reference environment. Their own licenses and terms remain authoritative.

| Component | Use | Distribution in this package | License/terms note |
|---|---|---:|---|
| AgentTeams (formerly associated with HiClaw naming) | Multi-agent runtime/design base | Configuration and modified integration wrappers; no container images | Upstream project is identified as Apache-2.0; retain its attribution for adapted portions |
| Python | Offline verification and host/runtime helpers | No interpreter redistributed | Python Software Foundation License |
| jsonschema | JSON Schema validation | Installed separately from lock file | MIT License |
| psycopg / psycopg-binary | PostgreSQL adapter used by runtime code | Installed separately from lock file | GNU LGPL v3 or later; consult package metadata for the exact selected release |
| PostgreSQL | State/runtime database in reference environment | Not redistributed | PostgreSQL License |
| Matrix / homeserver implementation | Agent message transport | Not redistributed | Depends on selected implementation; consult its distribution |
| Element Web | Human-visible Matrix client | Not redistributed | Consult the exact Element release and its notices |
| Docker Engine / Docker Desktop | Reference container runtime | Not redistributed | Separate product license and subscription terms may apply |
| Model Provider API | Worker model inference | Service only; no credentials redistributed | Provider terms, privacy rules and billing apply |

`requirements-demo.lock`, `infra/agentteams/m4/runtime-images.lock.json`, and the exact package/image metadata are the technical sources for version-specific review. This NOTICE is a concise disclosure, not a replacement for third-party license texts and not legal advice.

## Evidence and recordings

The sanitized evidence under `evidence/` is project-generated material. The separate screen recording is not included in this archive and may display Element/Matrix user interfaces governed by their respective terms.

## No endorsement

Third-party names are used only to identify interoperability and dependencies. No endorsement by the upstream projects or service providers is implied.
