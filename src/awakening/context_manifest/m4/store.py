"""Append-only context and invocation receipt stores."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from typing import Any, Protocol
from uuid import UUID, uuid5

from psycopg import connect
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .builder import ContextManifest


_RECEIPT_NAMESPACE = UUID("e201ca39-dd8b-5e8e-a8fa-1728fb3af0e3")


class ContextManifestStore(Protocol):
    def append(self, manifest: ContextManifest) -> Mapping[str, Any]: ...

    def get(self, context_manifest_id: str) -> Mapping[str, Any] | None: ...


@dataclass(frozen=True, slots=True)
class SkillInvocationReceipt:
    skill_invocation_receipt_id: str
    program_id: str
    run_id: str
    model_call_id: str
    context_manifest_id: str
    reservation_id: str
    agent_identity_id: str
    agent_identity_version: str
    skill_name: str
    skill_version: str
    input_sha256: str
    output_sha256: str
    status: str

    @classmethod
    def create(cls, **values: str) -> "SkillInvocationReceipt":
        receipt_id = str(
            uuid5(
                _RECEIPT_NAMESPACE,
                ":".join(
                    (
                        values["program_id"],
                        values["run_id"],
                        values["model_call_id"],
                        values["skill_name"],
                        values["skill_version"],
                    )
                ),
            )
        )
        return cls(skill_invocation_receipt_id=receipt_id, **values)

    def to_dict(self) -> dict[str, str]:
        return {field: getattr(self, field) for field in self.__dataclass_fields__}


class InvocationReceiptStore(Protocol):
    def append(self, receipt: SkillInvocationReceipt) -> Mapping[str, Any]: ...


class InMemoryContextManifestStore:
    """A deterministic unit-test store; status may be forced to pending."""

    def __init__(self, *, committed: bool = True) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        self._committed = committed
        self.append_count = 0

    def append(self, manifest: ContextManifest) -> Mapping[str, Any]:
        self.append_count += 1
        record = manifest.to_dict()
        if not self._committed:
            record["status"] = "pending"
        existing = self._records.setdefault(manifest.context_manifest_id, record)
        if existing != record:
            raise ValueError("context manifest id reused with different content")
        return dict(existing)

    def get(self, context_manifest_id: str) -> Mapping[str, Any] | None:
        record = self._records.get(context_manifest_id)
        return dict(record) if record is not None else None


class InMemoryInvocationReceiptStore:
    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        self.append_count = 0

    def append(self, receipt: SkillInvocationReceipt) -> Mapping[str, Any]:
        self.append_count += 1
        record = receipt.to_dict()
        existing = self._records.setdefault(receipt.skill_invocation_receipt_id, record)
        if existing != record:
            raise ValueError("skill invocation receipt id reused with different content")
        return dict(existing)


class PostgresContextManifestStore:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def append(self, manifest: ContextManifest) -> Mapping[str, Any]:
        record = manifest.to_dict()
        with connect(self._dsn, row_factory=dict_row, connect_timeout=5) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO observability.context_manifests (
                        context_manifest_id, program_id, run_id, model_call_id,
                        runtime_config_snapshot_id, reservation_id,
                        agent_identity_id, agent_identity_version,
                        skill_name, skill_version, object_refs, exclusions,
                        input_sha256, status
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (context_manifest_id) DO NOTHING
                    """,
                    (
                        UUID(record["context_manifest_id"]),
                        UUID(record["program_id"]),
                        UUID(record["run_id"]),
                        UUID(record["model_call_id"]),
                        UUID(record["runtime_config_snapshot_id"]),
                        UUID(record["reservation_id"]),
                        record["agent_identity_id"],
                        record["agent_identity_version"],
                        record["skill_name"],
                        record["skill_version"],
                        Jsonb(record["object_refs"]),
                        list(record["exclusions"]),
                        record["input_sha256"],
                        record["status"],
                    ),
                )
                cursor.execute(
                    "SELECT * FROM observability.context_manifests WHERE context_manifest_id = %s",
                    (UUID(record["context_manifest_id"]),),
                )
                persisted = cursor.fetchone()
        if persisted is None:
            raise RuntimeError("context manifest append did not persist")
        normalized = _normal(persisted)
        for key, expected in record.items():
            if normalized.get(key) != expected:
                raise RuntimeError(f"context manifest conflict: {key}")
        return normalized

    def get(self, context_manifest_id: str) -> Mapping[str, Any] | None:
        with connect(self._dsn, row_factory=dict_row, connect_timeout=5) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM observability.context_manifests WHERE context_manifest_id = %s",
                    (UUID(context_manifest_id),),
                )
                row = cursor.fetchone()
        return _normal(row) if row is not None else None


class PostgresInvocationReceiptStore:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def append(self, receipt: SkillInvocationReceipt) -> Mapping[str, Any]:
        record = receipt.to_dict()
        columns = tuple(record)
        with connect(self._dsn, row_factory=dict_row, connect_timeout=5) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO observability.skill_invocation_receipts (
                        skill_invocation_receipt_id, program_id, run_id,
                        model_call_id, context_manifest_id, reservation_id,
                        agent_identity_id, agent_identity_version,
                        skill_name, skill_version, input_sha256, output_sha256,
                        status
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (skill_invocation_receipt_id) DO NOTHING
                    """,
                    tuple(
                        UUID(record[name])
                        if name.endswith("_id") and name not in {
                            "agent_identity_id"
                        }
                        else record[name]
                        for name in columns
                    ),
                )
                cursor.execute(
                    """
                    SELECT * FROM observability.skill_invocation_receipts
                    WHERE skill_invocation_receipt_id = %s
                    """,
                    (UUID(record["skill_invocation_receipt_id"]),),
                )
                persisted = cursor.fetchone()
        if persisted is None:
            raise RuntimeError("skill invocation receipt append did not persist")
        normalized = _normal(persisted)
        for key, expected in record.items():
            if normalized.get(key) != expected:
                raise RuntimeError(f"skill invocation receipt conflict: {key}")
        return normalized


def _normal(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _normal(item) for key, item in value.items()}
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, tuple):
        return [_normal(item) for item in value]
    if isinstance(value, list):
        return [_normal(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


__all__ = (
    "ContextManifestStore",
    "InMemoryContextManifestStore",
    "InMemoryInvocationReceiptStore",
    "InvocationReceiptStore",
    "PostgresContextManifestStore",
    "PostgresInvocationReceiptStore",
    "SkillInvocationReceipt",
)

