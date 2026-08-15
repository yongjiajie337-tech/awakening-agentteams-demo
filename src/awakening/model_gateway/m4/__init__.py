"""M4 fail-closed model gateway."""

from .contracts import (
    GatewayReasonCode,
    GatewayResult,
    ModelInvocation,
    ProviderRequest,
    ProviderResponse,
)
from .gateway import M4ModelGateway
from .http_adapter import (
    HttpGatewayErrorCode,
    HttpGatewayResponse,
    RuntimeInvocationPlan,
    SingleUseRuntimeInvocationPlanRegistry,
    TrustedOpenAICompatibleHttpAdapter,
    build_http_server,
)
from .provider import OpenAICompatibleProvider, RecordingProvider

__all__ = (
    "GatewayReasonCode",
    "GatewayResult",
    "HttpGatewayErrorCode",
    "HttpGatewayResponse",
    "M4ModelGateway",
    "ModelInvocation",
    "OpenAICompatibleProvider",
    "ProviderRequest",
    "ProviderResponse",
    "RecordingProvider",
    "RuntimeInvocationPlan",
    "SingleUseRuntimeInvocationPlanRegistry",
    "TrustedOpenAICompatibleHttpAdapter",
    "build_http_server",
)
