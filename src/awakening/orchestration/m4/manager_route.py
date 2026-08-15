"""Deterministic Manager routing as an internal M4 control-plane operation.

``manager_route`` is deliberately not a public business Skill and never calls a
model.  It reads authoritative State through the Manager's trusted MCP context,
then emits fixed, server-owned delegations to the two Worker paths that M4 asks
the Manager to route.  Reviewer contract-smoke orchestration remains separate.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol
from uuid import UUID

from awakening.state.m4 import TrustedRuntimeContext


MANAGER_ROUTE_OPERATION = "manager_route"
MANAGER_ROUTE_OPERATION_VERSION = "1.0.0"
MANAGER_ROUTE_PUBLIC_SKILL = False
_MANAGER_IDENTITY = "awakening_program_manager"
_ROUTE_TARGETS = (
    ("role_project_architect", "analyze_role_gap", "1.0.0"),
    ("execution_evidence_coach", "coach_task_submission", "1.0.0"),
)
_REQUEST_FIELDS = frozenset({"program_id", "run_id", "state_version"})


class ManagerRouteReasonCode(StrEnum):
    OK = "OK"
    INVALID_ID_ONLY_REQUEST = "INVALID_ID_ONLY_REQUEST"
    MANAGER_PRINCIPAL_DENIED = "MANAGER_PRINCIPAL_DENIED"
    SNAPSHOT_READ_FAILED = "SNAPSHOT_READ_FAILED"
    SNAPSHOT_BINDING_MISMATCH = "SNAPSHOT_BINDING_MISMATCH"
    ROUTE_DISPATCH_FAILED = "ROUTE_DISPATCH_FAILED"


@dataclass(frozen=True, slots=True)
class ManagerRouteRequest:
    program_id: str
    run_id: str
    state_version: int

    def __post_init__(self) -> None:
        UUID(self.program_id)
        UUID(self.run_id)
        if isinstance(self.state_version, bool) or not isinstance(self.state_version, int):
            raise ValueError("state_version must be an integer")
        if self.state_version < 0:
            raise ValueError("state_version cannot be negative")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ManagerRouteRequest":
        if frozenset(value) != _REQUEST_FIELDS:
            raise ValueError("manager_route accepts only program_id, run_id and state_version")
        program_id = str(value["program_id"])
        run_id = str(value["run_id"])
        state_version = value["state_version"]
        return cls(program_id=program_id, run_id=run_id, state_version=state_version)


@dataclass(frozen=True, slots=True)
class ManagerRouteDelivery:
    target_identity: str
    skill_name: str
    skill_version: str
    delivery_id: str


@dataclass(frozen=True, slots=True)
class ManagerRouteResult:
    routed: bool
    reason_code: ManagerRouteReasonCode
    operation: str = MANAGER_ROUTE_OPERATION
    operation_version: str = MANAGER_ROUTE_OPERATION_VERSION
    public_skill: bool = MANAGER_ROUTE_PUBLIC_SKILL
    deliveries: tuple[ManagerRouteDelivery, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "deliveries", tuple(self.deliveries))


class ManagerSnapshotReaderPort(Protocol):
    def get_snapshot(
        self,
        *,
        program_id: str,
        trusted_context: TrustedRuntimeContext,
    ) -> Mapping[str, Any]: ...


class ManagerDelegationPort(Protocol):
    def dispatch(
        self,
        *,
        target_identity: str,
        payload: Mapping[str, Any],
    ) -> str: ...


class ManagerRouteOperation:
    """Read State once and route fixed representative Worker operations."""

    def __init__(
        self,
        *,
        state_reader: ManagerSnapshotReaderPort,
        delegation_port: ManagerDelegationPort,
        trusted_manager_context: TrustedRuntimeContext,
    ) -> None:
        self._state_reader = state_reader
        self._delegation = delegation_port
        self._context = trusted_manager_context

    def execute(self, request: ManagerRouteRequest) -> ManagerRouteResult:
        if (
            self._context.agent_identity != _MANAGER_IDENTITY
            or self._context.run_id != request.run_id
            or request.program_id not in self._context.program_scope
        ):
            return ManagerRouteResult(
                routed=False,
                reason_code=ManagerRouteReasonCode.MANAGER_PRINCIPAL_DENIED,
            )
        try:
            snapshot = self._state_reader.get_snapshot(
                program_id=request.program_id,
                trusted_context=self._context,
            )
        except Exception:
            return ManagerRouteResult(
                routed=False,
                reason_code=ManagerRouteReasonCode.SNAPSHOT_READ_FAILED,
            )
        program = snapshot.get("program") if isinstance(snapshot, Mapping) else None
        if not isinstance(program, Mapping):
            return ManagerRouteResult(
                routed=False,
                reason_code=ManagerRouteReasonCode.SNAPSHOT_READ_FAILED,
            )
        try:
            snapshot_matches = (
                str(program.get("program_id")) == request.program_id
                and int(program.get("state_version", -1)) == request.state_version
            )
        except (TypeError, ValueError):
            snapshot_matches = False
        if not snapshot_matches:
            return ManagerRouteResult(
                routed=False,
                reason_code=ManagerRouteReasonCode.SNAPSHOT_BINDING_MISMATCH,
            )

        deliveries: list[ManagerRouteDelivery] = []
        for target_identity, skill_name, skill_version in _ROUTE_TARGETS:
            payload = MappingProxyType(
                {
                    "program_id": request.program_id,
                    "run_id": request.run_id,
                    "state_version": request.state_version,
                    "skill_name": skill_name,
                    "skill_version": skill_version,
                }
            )
            try:
                delivery_id = self._delegation.dispatch(
                    target_identity=target_identity,
                    payload=payload,
                )
            except Exception:
                return ManagerRouteResult(
                    routed=False,
                    reason_code=ManagerRouteReasonCode.ROUTE_DISPATCH_FAILED,
                    deliveries=tuple(deliveries),
                )
            if not isinstance(delivery_id, str) or not delivery_id:
                return ManagerRouteResult(
                    routed=False,
                    reason_code=ManagerRouteReasonCode.ROUTE_DISPATCH_FAILED,
                    deliveries=tuple(deliveries),
                )
            deliveries.append(
                ManagerRouteDelivery(
                    target_identity=target_identity,
                    skill_name=skill_name,
                    skill_version=skill_version,
                    delivery_id=delivery_id,
                )
            )
        return ManagerRouteResult(
            routed=True,
            reason_code=ManagerRouteReasonCode.OK,
            deliveries=tuple(deliveries),
        )


__all__ = (
    "MANAGER_ROUTE_OPERATION",
    "MANAGER_ROUTE_OPERATION_VERSION",
    "MANAGER_ROUTE_PUBLIC_SKILL",
    "ManagerDelegationPort",
    "ManagerRouteDelivery",
    "ManagerRouteOperation",
    "ManagerRouteReasonCode",
    "ManagerRouteRequest",
    "ManagerRouteResult",
    "ManagerSnapshotReaderPort",
)
