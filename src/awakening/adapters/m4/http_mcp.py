"""Minimal stateless HTTP MCP transport for the restricted M4 State adapter.

The wire document is untrusted.  A Bearer credential selects one of exactly
four server-owned :class:`TrustedRuntimeContext` objects; no identity, Program
scope, object URI, content hash or Receipt is accepted from ``tools/call``
arguments.  The transport has no persistence port and can reach business
state only through :class:`M4StateMcpAdapter`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

from awakening.state.contracts import CommandResult, ReasonCode
from awakening.state.m4 import (
    M4AgentIdentity,
    M4ReasonCode,
    M4StateMcpMethod,
    TrustedRuntimeContext,
    load_m4_schema,
    validate_state_mcp_call,
)
from awakening.state.m4.validation import STATE_MCP_SCHEMAS
from awakening.state.validation import ContractValidationError

from .state_mcp import M4StateMcpAdapter


M4_STATE_MCP_HTTP_PATH = "/mcp"
M4_STATE_MCP_PROTOCOL_VERSION = "2025-06-18"
_SUPPORTED_PROTOCOL_VERSIONS = frozenset(
    {"2025-03-26", M4_STATE_MCP_PROTOCOL_VERSION}
)
_ORDERED_METHODS = (
    M4StateMcpMethod.GET_SNAPSHOT,
    M4StateMcpMethod.SUBMIT_PROPOSAL,
    M4StateMcpMethod.GET_COMMAND_STATUS,
    M4StateMcpMethod.APPLY_AUTHORIZED_CHANGE,
)
_TOOL_DESCRIPTIONS: Mapping[M4StateMcpMethod, str] = MappingProxyType(
    {
        M4StateMcpMethod.GET_SNAPSHOT: "Read the authoritative Program snapshot.",
        M4StateMcpMethod.SUBMIT_PROPOSAL: (
            "Submit a version-bound, non-active State proposal."
        ),
        M4StateMcpMethod.GET_COMMAND_STATUS: (
            "Read the authoritative result of one State command."
        ),
        M4StateMcpMethod.APPLY_AUTHORIZED_CHANGE: (
            "Validate an ID-only apply request; M4 always rejects activation."
        ),
    }
)

# Exact names only.  Business fields such as ``target_role`` remain valid,
# while server-owned location/content/provenance facts fail closed at the
# transport boundary even when nested inside a Proposal payload.
_FORBIDDEN_CALL_FIELDS = frozenset(
    {
        "agent_id",
        "agent_identity",
        "agent_identity_id",
        "auth_context",
        "auth_context_id",
        "authenticated_principal",
        "authenticated_user",
        "authorization",
        "authorization_token",
        "bearer_token",
        "content_hash",
        "hash",
        "internal_identity",
        "jwt",
        "object_ref",
        "object_refs",
        "object_uri",
        "object_uris",
        "principal",
        "principal_id",
        "principal_type",
        "program_scope",
        "provider_usage_receipt",
        "provider_usage_receipt_id",
        "raw_sha256",
        "receipt",
        "receipt_id",
        "receipts",
        "request_sha256",
        "response_sha256",
        "role",
        "roles",
        "scope",
        "scopes",
        "session_id",
        "sha256",
        "token",
        "trusted_principal",
        "trusted_runtime_context",
        "uri",
        "uris",
        "user_id",
    }
)


class M4McpHttpErrorCode(StrEnum):
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    PATH_NOT_FOUND = "PATH_NOT_FOUND"
    REQUEST_TOO_LARGE = "REQUEST_TOO_LARGE"
    CONTENT_TYPE_INVALID = "CONTENT_TYPE_INVALID"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTH_INVALID = "AUTH_INVALID"
    REQUEST_INVALID = "REQUEST_INVALID"


@dataclass(frozen=True, slots=True)
class M4McpHttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))


class M4BearerPrincipalRegistry:
    """Map four opaque Bearer tokens to four server-issued runtime principals.

    Raw tokens are fingerprinted during construction and are never retained or
    exposed by the registry.
    """

    def __init__(self, bindings: Mapping[str, TrustedRuntimeContext]) -> None:
        if len(bindings) != len(M4AgentIdentity):
            raise ValueError("exactly four M4 Bearer principal bindings are required")

        by_fingerprint: dict[str, TrustedRuntimeContext] = {}
        identities: set[M4AgentIdentity] = set()
        for raw_token, context in bindings.items():
            if not isinstance(raw_token, str) or raw_token.strip() != raw_token:
                raise ValueError("Bearer tokens must be non-whitespace strings")
            if len(raw_token) < 32 or any(character.isspace() for character in raw_token):
                raise ValueError("Bearer tokens must contain at least 32 non-space characters")
            if not isinstance(context, TrustedRuntimeContext):
                raise TypeError("Bearer bindings require TrustedRuntimeContext values")
            identity = M4AgentIdentity(context.agent_identity)
            if identity in identities:
                raise ValueError(f"duplicate Bearer binding for {identity.value}")
            fingerprint = sha256(raw_token.encode("utf-8")).hexdigest()
            if fingerprint in by_fingerprint:
                raise ValueError("Bearer tokens must be unique")
            identities.add(identity)
            by_fingerprint[fingerprint] = context

        if identities != set(M4AgentIdentity):
            raise ValueError("Bearer bindings must cover the closed four-identity registry")
        self._by_fingerprint = MappingProxyType(by_fingerprint)

    def authenticate(self, raw_token: str) -> TrustedRuntimeContext | None:
        if not isinstance(raw_token, str) or not raw_token:
            return None
        fingerprint = sha256(raw_token.encode("utf-8")).hexdigest()
        return self._by_fingerprint.get(fingerprint)


class M4StateMcpHttpTransport:
    """Serve a small stateless subset of MCP over HTTP/JSON-RPC 2.0."""

    max_body_bytes = 262_144

    def __init__(
        self,
        *,
        bearer_principals: M4BearerPrincipalRegistry,
        state_mcp: M4StateMcpAdapter,
    ) -> None:
        self._principals = bearer_principals
        self._state_mcp = state_mcp

    def handle_request(
        self,
        *,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes,
    ) -> M4McpHttpResponse:
        if method.upper() != "POST":
            return self._http_error(
                405,
                M4McpHttpErrorCode.METHOD_NOT_ALLOWED,
                extra_headers={"Allow": "POST"},
            )
        if urlsplit(path).path != M4_STATE_MCP_HTTP_PATH:
            return self._http_error(404, M4McpHttpErrorCode.PATH_NOT_FOUND)
        if len(body) > self.max_body_bytes:
            return self._http_error(413, M4McpHttpErrorCode.REQUEST_TOO_LARGE)

        content_type = self._header(headers, "content-type")
        if content_type and content_type.split(";", 1)[0].strip().lower() != "application/json":
            return self._http_error(415, M4McpHttpErrorCode.CONTENT_TYPE_INVALID)

        raw_token = self._bearer_token(headers)
        if raw_token is None:
            return self._http_error(
                401,
                M4McpHttpErrorCode.AUTH_REQUIRED,
                extra_headers={"WWW-Authenticate": "Bearer"},
            )
        context = self._principals.authenticate(raw_token)
        if context is None:
            return self._http_error(
                401,
                M4McpHttpErrorCode.AUTH_INVALID,
                extra_headers={"WWW-Authenticate": "Bearer"},
            )

        try:
            request = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._rpc_error(400, None, -32700, "Parse error")
        if not isinstance(request, Mapping):
            return self._rpc_error(400, None, -32600, "Invalid Request")

        request_id = request.get("id")
        if request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
            return self._rpc_error(400, request_id, -32600, "Invalid Request")
        if isinstance(request_id, bool) or not isinstance(request_id, (str, int, type(None))):
            return self._rpc_error(400, None, -32600, "Invalid Request")

        rpc_method = str(request["method"])
        raw_params = request.get("params", {})
        if not isinstance(raw_params, Mapping):
            return self._rpc_error(200, request_id, -32602, "Invalid params")
        params = dict(raw_params)

        if self._first_forbidden_field(request) is not None:
            if rpc_method == "tools/call":
                return self._rpc_result(
                    request_id,
                    self._tool_error(
                        ReasonCode.IDENTITY_FIELD_FORBIDDEN,
                        (
                            "Caller body contains a server-owned identity, scope, URI, "
                            "hash or Receipt"
                        ),
                    ),
                )
            return self._rpc_error(200, request_id, -32602, "Invalid params")

        protocol_header = self._header(headers, "mcp-protocol-version")
        if protocol_header and protocol_header not in _SUPPORTED_PROTOCOL_VERSIONS:
            return self._rpc_error(400, request_id, -32600, "Unsupported protocol version")

        if rpc_method == "notifications/initialized":
            return self._empty_response(202)
        if rpc_method == "initialize":
            return self._rpc_result(request_id, self._initialize_result(params))
        if rpc_method == "ping":
            return self._rpc_result(request_id, {})
        if rpc_method == "tools/list":
            return self._rpc_result(
                request_id,
                {"tools": self._allowed_tools(context)},
            )
        if rpc_method == "tools/call":
            result = self._call_tool(params=params, context=context)
            return self._rpc_result(request_id, result)
        return self._rpc_error(200, request_id, -32601, "Method not found")

    def _call_tool(
        self,
        *,
        params: Mapping[str, Any],
        context: TrustedRuntimeContext,
    ) -> dict[str, Any]:
        if not set(params).issubset({"name", "arguments", "_meta"}):
            return self._tool_error(ReasonCode.SCHEMA_INVALID, "Invalid tools/call params")
        raw_name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(raw_name, str) or not isinstance(arguments, Mapping):
            return self._tool_error(ReasonCode.SCHEMA_INVALID, "Invalid tools/call params")
        try:
            mcp_method = M4StateMcpMethod(raw_name)
        except ValueError:
            return self._tool_error(
                ReasonCode.COMMAND_NOT_REGISTERED,
                "State MCP tool is not registered",
            )

        try:
            validate_state_mcp_call(mcp_method, arguments)
        except ContractValidationError as exc:
            return self._tool_error(exc.reason_code, str(exc))
        if not context.allows(mcp_method):
            return self._tool_error(
                M4ReasonCode.METHOD_NOT_ALLOWED,
                "Trusted M4 identity is not allowed to call this State MCP method",
            )

        values = dict(arguments)
        try:
            if mcp_method is M4StateMcpMethod.GET_SNAPSHOT:
                payload = self._state_mcp.get_snapshot(
                    program_id=values["program_id"],
                    trusted_context=context,
                )
            elif mcp_method is M4StateMcpMethod.GET_COMMAND_STATUS:
                payload = self._state_mcp.get_command_status(
                    program_id=values["program_id"],
                    command_id=values["command_id"],
                    trusted_context=context,
                )
            elif mcp_method is M4StateMcpMethod.SUBMIT_PROPOSAL:
                payload = self._state_mcp.submit_proposal(
                    program_id=values["program_id"],
                    base_version=values["base_version"],
                    proposal_payload=values["proposal_payload"],
                    idempotency_key=values["idempotency_key"],
                    trusted_context=context,
                    traceparent=values.get("traceparent"),
                )
            else:
                payload = self._state_mcp.apply_authorized_change(
                    program_id=values["program_id"],
                    proposal_id=values["proposal_id"],
                    expected_state_version=values["expected_state_version"],
                    idempotency_key=values["idempotency_key"],
                    trusted_context=context,
                    human_decision_id=values.get("human_decision_id"),
                )
        except Exception:
            return self._tool_error(
                ReasonCode.TRANSACTION_ABORTED,
                "State MCP adapter failed without exposing internal details",
            )

        document = payload.to_dict() if isinstance(payload, CommandResult) else dict(payload)
        is_error = document.get("status") == "REJECTED"
        return self._tool_result(document, is_error=is_error)

    @staticmethod
    def _initialize_result(params: Mapping[str, Any]) -> dict[str, Any]:
        requested = params.get("protocolVersion")
        protocol_version = (
            requested
            if isinstance(requested, str) and requested in _SUPPORTED_PROTOCOL_VERSIONS
            else M4_STATE_MCP_PROTOCOL_VERSION
        )
        return {
            "protocolVersion": protocol_version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "awakening-m4-state", "version": "1.0.0"},
        }

    @staticmethod
    def _allowed_tools(context: TrustedRuntimeContext) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        for method in _ORDERED_METHODS:
            if not context.allows(method):
                continue
            tools.append(
                {
                    "name": method.value,
                    "description": _TOOL_DESCRIPTIONS[method],
                    "inputSchema": _json_ready(
                        load_m4_schema(STATE_MCP_SCHEMAS[method.value])
                    ),
                }
            )
        return tools

    @classmethod
    def _tool_error(cls, reason_code: str | StrEnum, message: str) -> dict[str, Any]:
        code = reason_code.value if isinstance(reason_code, StrEnum) else str(reason_code)
        return cls._tool_result(
            {"status": "REJECTED", "reason_code": code, "message": message},
            is_error=True,
        )

    @staticmethod
    def _tool_result(document: Mapping[str, Any], *, is_error: bool) -> dict[str, Any]:
        structured = _json_ready(document)
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        structured,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                }
            ],
            "structuredContent": structured,
            "isError": is_error,
        }

    @staticmethod
    def _first_forbidden_field(value: Any) -> tuple[str | int, ...] | None:
        def walk(item: Any, path: tuple[str | int, ...]) -> tuple[str | int, ...] | None:
            if isinstance(item, Mapping):
                for raw_key, child in item.items():
                    key = str(raw_key)
                    child_path = (*path, key)
                    normalised = _normalise_field_name(key)
                    if normalised in _FORBIDDEN_CALL_FIELDS:
                        return child_path
                    match = walk(child, child_path)
                    if match is not None:
                        return match
            elif isinstance(item, Sequence) and not isinstance(
                item, (str, bytes, bytearray)
            ):
                for index, child in enumerate(item):
                    match = walk(child, (*path, index))
                    if match is not None:
                        return match
            return None

        return walk(value, ())

    @staticmethod
    def _header(headers: Mapping[str, str], name: str) -> str:
        return next(
            (str(value) for key, value in headers.items() if str(key).lower() == name),
            "",
        )

    @classmethod
    def _bearer_token(cls, headers: Mapping[str, str]) -> str | None:
        value = cls._header(headers, "authorization")
        scheme, separator, token = value.partition(" ")
        if (
            separator != " "
            or scheme.lower() != "bearer"
            or not token
            or token.strip() != token
            or any(character.isspace() for character in token)
        ):
            return None
        return token

    @classmethod
    def _http_error(
        cls,
        status_code: int,
        code: M4McpHttpErrorCode,
        *,
        extra_headers: Mapping[str, str] | None = None,
    ) -> M4McpHttpResponse:
        return cls._json_response(
            status_code,
            {
                "error": {
                    "code": code.value,
                    "message": code.value,
                    "type": "awakening_m4_mcp_http_error",
                }
            },
            extra_headers=extra_headers,
        )

    @classmethod
    def _rpc_result(cls, request_id: Any, result: Mapping[str, Any]) -> M4McpHttpResponse:
        return cls._json_response(
            200,
            {"jsonrpc": "2.0", "id": request_id, "result": result},
        )

    @classmethod
    def _rpc_error(
        cls,
        status_code: int,
        request_id: Any,
        code: int,
        message: str,
    ) -> M4McpHttpResponse:
        return cls._json_response(
            status_code,
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": code, "message": message},
            },
        )

    @staticmethod
    def _empty_response(status_code: int) -> M4McpHttpResponse:
        return M4McpHttpResponse(status_code, {}, b"")

    @staticmethod
    def _json_response(
        status_code: int,
        document: Mapping[str, Any],
        *,
        extra_headers: Mapping[str, str] | None = None,
    ) -> M4McpHttpResponse:
        body = json.dumps(
            _json_ready(document),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/json; charset=utf-8",
        }
        headers.update(dict(extra_headers or {}))
        return M4McpHttpResponse(status_code, headers, body)


def _json_ready(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_ready(item) for item in value]
    if isinstance(value, StrEnum):
        return value.value
    return value


def _normalise_field_name(value: str) -> str:
    text = value.strip().replace("-", "_")
    text = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    return text.lower()


def build_m4_state_mcp_http_server(
    transport: M4StateMcpHttpTransport,
    *,
    host: str,
    port: int,
    allow_non_loopback: bool = False,
) -> ThreadingHTTPServer:
    """Build, but do not start, the standard-library State MCP HTTP server.

    Clear-text Bearer transport is loopback-only by default.  A separately
    controlled container network can opt in explicitly at composition time.
    """

    if not allow_non_loopback and host.strip().lower() not in {"127.0.0.1", "localhost"}:
        raise ValueError(
            "non-loopback State MCP binding requires explicit allow_non_loopback=True"
        )

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "AwakeningM4StateMCP/1.0"

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            raw_length = self.headers.get("Content-Length", "")
            try:
                content_length = int(raw_length)
            except ValueError:
                response = transport._http_error(
                    400,
                    M4McpHttpErrorCode.REQUEST_INVALID,
                )
                self._write(response)
                return
            if content_length < 0 or content_length > transport.max_body_bytes:
                response = transport._http_error(
                    413,
                    M4McpHttpErrorCode.REQUEST_TOO_LARGE,
                )
                self._write(response)
                return
            body = self.rfile.read(content_length)
            response = transport.handle_request(
                method="POST",
                path=self.path,
                headers={key: value for key, value in self.headers.items()},
                body=body,
            )
            self._write(response)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self._write(
                transport._http_error(
                    405,
                    M4McpHttpErrorCode.METHOD_NOT_ALLOWED,
                    extra_headers={"Allow": "POST"},
                )
            )

        def _write(self, response: M4McpHttpResponse) -> None:
            self.send_response(response.status_code)
            for key, value in response.headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(response.body)))
            self.send_header("Connection", "close")
            self.end_headers()
            if response.body:
                self.wfile.write(response.body)

        def log_message(self, format: str, *args: object) -> None:
            # Never let the default HTTP logger evolve into a credential leak.
            return

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def serve_m4_state_mcp_http(
    transport: M4StateMcpHttpTransport,
    *,
    host: str,
    port: int,
    allow_non_loopback: bool = False,
) -> None:
    """Run the endpoint when an approved M4 composition explicitly invokes it."""

    server = build_m4_state_mcp_http_server(
        transport,
        host=host,
        port=port,
        allow_non_loopback=allow_non_loopback,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()


__all__ = (
    "M4BearerPrincipalRegistry",
    "M4McpHttpErrorCode",
    "M4McpHttpResponse",
    "M4StateMcpHttpTransport",
    "M4_STATE_MCP_HTTP_PATH",
    "M4_STATE_MCP_PROTOCOL_VERSION",
    "build_m4_state_mcp_http_server",
    "serve_m4_state_mcp_http",
)
