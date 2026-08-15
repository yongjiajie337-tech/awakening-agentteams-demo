"""M4 runtime identity and contract authorization."""

from .authorization import (
    BoundRuntimeAuthorizer,
    RuntimeBinding,
    RuntimeCredentialRegistry,
    TrustedRuntimeSession,
)
from .registry import (
    M4ContractRegistry,
    RegistryContractError,
    load_and_validate_m4_registry,
)
from .manager_route import (
    MANAGER_ROUTE_OPERATION,
    MANAGER_ROUTE_OPERATION_VERSION,
    MANAGER_ROUTE_PUBLIC_SKILL,
    ManagerRouteOperation,
    ManagerRouteReasonCode,
    ManagerRouteRequest,
    ManagerRouteResult,
)

__all__ = (
    "BoundRuntimeAuthorizer",
    "MANAGER_ROUTE_OPERATION",
    "MANAGER_ROUTE_OPERATION_VERSION",
    "MANAGER_ROUTE_PUBLIC_SKILL",
    "M4ContractRegistry",
    "ManagerRouteOperation",
    "ManagerRouteReasonCode",
    "ManagerRouteRequest",
    "ManagerRouteResult",
    "RegistryContractError",
    "RuntimeBinding",
    "RuntimeCredentialRegistry",
    "TrustedRuntimeSession",
    "load_and_validate_m4_registry",
)
