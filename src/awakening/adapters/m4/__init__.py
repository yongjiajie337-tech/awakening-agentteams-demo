"""M4 restricted State MCP and internal State Service adapters."""

from .gateway_state import GATEWAY_PRINCIPAL_ID, GatewayStateAuthorityAdapter
from .http_mcp import (
    M4BearerPrincipalRegistry,
    M4McpHttpErrorCode,
    M4McpHttpResponse,
    M4StateMcpHttpTransport,
    M4_STATE_MCP_HTTP_PATH,
    M4_STATE_MCP_PROTOCOL_VERSION,
    build_m4_state_mcp_http_server,
    serve_m4_state_mcp_http,
)
from .internal import M4InternalStateAdapter
from .state_mcp import M4StateMcpAdapter

__all__ = (
    "GATEWAY_PRINCIPAL_ID",
    "GatewayStateAuthorityAdapter",
    "M4BearerPrincipalRegistry",
    "M4InternalStateAdapter",
    "M4McpHttpErrorCode",
    "M4McpHttpResponse",
    "M4StateMcpAdapter",
    "M4StateMcpHttpTransport",
    "M4_STATE_MCP_HTTP_PATH",
    "M4_STATE_MCP_PROTOCOL_VERSION",
    "build_m4_state_mcp_http_server",
    "serve_m4_state_mcp_http",
)
