"""Trusted OpenAI-compatible ingress for AgentTeams M4 model calls.

The HTTP body is an untrusted OpenAI wire envelope.  Runtime identity, Skill,
model controls, budget reservation and call identifiers are selected only from
server-owned bindings before :class:`M4ModelGateway` can reach a Provider.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
from threading import Lock
from types import MappingProxyType
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import UUID

from awakening.model_gateway.runtime_input_policy import (
    find_forbidden_runtime_fields,
    requested_provider_tools,
)

from .contracts import GatewayReasonCode, ModelInvocation, thaw
from .gateway import M4ModelGateway


OPENAI_CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
_SERVER_OWNED_OPENAI_FIELDS = frozenset(
    {
        "model",
        "max_tokens",
        "max_completion_tokens",
        "temperature",
        "n",
        "best_of",
        "stream",
        "stream_options",
    }
)
_SERVER_OWNED_INVOCATION_FIELDS = frozenset(
    {
        "agent_identity_id",
        "agent_identity_version",
        "exclusions",
        "model_call_id",
        "object_refs",
        "program_id",
        "request_marker",
        "reservation_id",
        "run_id",
        "runtime_config_snapshot_id",
        "skill_name",
        "skill_version",
    }
)
_REQUEST_MARKER = re.compile(
    r"m4-call:([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12})\Z",
    re.ASCII,
)
_REQUEST_MARKER_BOUNDARY_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_:-"
)


def validate_runtime_request_marker(value: Any) -> str:
    """Return one canonical v4 request marker or reject the binding."""

    if not isinstance(value, str):
        raise ValueError("request marker must be a canonical m4-call UUID")
    matched = _REQUEST_MARKER.fullmatch(value)
    if matched is None:
        raise ValueError("request marker must be a canonical m4-call UUID")
    parsed = UUID(matched.group(1))
    if parsed.version != 4 or f"m4-call:{parsed}" != value:
        raise ValueError("request marker must be a canonical m4-call UUID")
    return value


def _text_marker_counts(text: str, marker: str) -> tuple[int, int]:
    """Count marker prefixes and exact tokens without accepting fuzzy suffixes."""

    exact_occurrences = 0
    offset = 0
    while True:
        position = text.find(marker, offset)
        if position < 0:
            return text.count("m4-call:"), exact_occurrences
        end = position + len(marker)
        left_ok = (
            position == 0
            or text[position - 1] not in _REQUEST_MARKER_BOUNDARY_CHARACTERS
        )
        right_ok = end == len(text) or text[end] not in _REQUEST_MARKER_BOUNDARY_CHARACTERS
        if left_ok and right_ok:
            exact_occurrences += 1
        offset = end


def _last_user_content_contains_marker(messages: Any, marker: str) -> bool:
    if not isinstance(messages, (list, tuple)) or not messages:
        return False
    last_user_content: Any | None = None
    for message in reversed(messages):
        if isinstance(message, Mapping) and message.get("role") == "user":
            last_user_content = message.get("content")
            break
    if isinstance(last_user_content, str):
        prefix_count, exact_count = _text_marker_counts(last_user_content, marker)
        return prefix_count == 1 and exact_count == 1
    if not isinstance(last_user_content, (list, tuple)) or not last_user_content:
        return False
    prefix_count = 0
    exact_count = 0
    for part in last_user_content:
        if (
            not isinstance(part, Mapping)
            or set(part) != {"type", "text"}
            or part.get("type") != "text"
            or not isinstance(part.get("text"), str)
        ):
            return False
        part_prefixes, part_exact = _text_marker_counts(part["text"], marker)
        prefix_count += part_prefixes
        exact_count += part_exact
    return prefix_count == 1 and exact_count == 1


class HttpGatewayErrorCode(StrEnum):
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    PATH_NOT_FOUND = "PATH_NOT_FOUND"
    REQUEST_TOO_LARGE = "REQUEST_TOO_LARGE"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTH_INVALID = "AUTH_INVALID"
    REQUEST_INVALID = "REQUEST_INVALID"
    MODEL_ALIAS_MISMATCH = "MODEL_ALIAS_MISMATCH"
    CALL_PLAN_UNAVAILABLE = "CALL_PLAN_UNAVAILABLE"
    GATEWAY_UNAVAILABLE = "GATEWAY_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class RuntimeInvocationPlan:
    """One server-issued, single-use model invocation lease."""

    model_call_id: str
    reservation_id: str
    skill_name: str
    skill_version: str
    request_marker: str | None = None
    object_refs: tuple[Mapping[str, Any], ...] = ()
    exclusions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.request_marker is not None:
            object.__setattr__(
                self,
                "request_marker",
                validate_runtime_request_marker(self.request_marker),
            )
        object.__setattr__(
            self,
            "object_refs",
            tuple(MappingProxyType(dict(item)) for item in self.object_refs),
        )
        object.__setattr__(self, "exclusions", tuple(self.exclusions))


class TrustedRuntimeSessionPort(Protocol):
    credential_fingerprint: str
    binding: Any


class RuntimeCredentialRegistryPort(Protocol):
    def authenticate(self, token: str) -> TrustedRuntimeSessionPort | None: ...


class RuntimeInvocationPlanPort(Protocol):
    def claim(
        self,
        session: TrustedRuntimeSessionPort,
        *,
        messages: Any | None = None,
    ) -> RuntimeInvocationPlan | None: ...


class SingleUseRuntimeInvocationPlanRegistry:
    """Claim a pre-reserved call once, keyed only by a token fingerprint."""

    def __init__(
        self,
        plans_by_fingerprint: Mapping[str, RuntimeInvocationPlan],
    ) -> None:
        self._plans = MappingProxyType(dict(plans_by_fingerprint))
        self._claimed: set[str] = set()
        self._lock = Lock()

    def claim(
        self,
        session: TrustedRuntimeSessionPort,
        *,
        messages: Any | None = None,
    ) -> RuntimeInvocationPlan | None:
        fingerprint = session.credential_fingerprint
        with self._lock:
            if fingerprint in self._claimed:
                return None
            plan = self._plans.get(fingerprint)
            if plan is None:
                return None
            expected_version = session.binding.allowed_skill_versions.get(plan.skill_name)
            if expected_version != plan.skill_version:
                return None
            if plan.request_marker is not None and not _last_user_content_contains_marker(
                messages,
                plan.request_marker,
            ):
                return None
            self._claimed.add(fingerprint)
            return plan


@dataclass(frozen=True, slots=True)
class HttpGatewayResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))


class TrustedOpenAICompatibleHttpAdapter:
    """Authenticate AgentTeams and translate one chat-completions call."""

    max_body_bytes = 1_048_576

    def __init__(
        self,
        *,
        credential_registry: RuntimeCredentialRegistryPort,
        invocation_plans: RuntimeInvocationPlanPort,
        gateway_factory: Callable[[TrustedRuntimeSessionPort], M4ModelGateway],
    ) -> None:
        self._credentials = credential_registry
        self._plans = invocation_plans
        self._gateway_factory = gateway_factory

    def handle_request(
        self,
        *,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes,
    ) -> HttpGatewayResponse:
        if method.upper() != "POST":
            return self._error(405, HttpGatewayErrorCode.METHOD_NOT_ALLOWED)
        if urlsplit(path).path != OPENAI_CHAT_COMPLETIONS_PATH:
            return self._error(404, HttpGatewayErrorCode.PATH_NOT_FOUND)
        if len(body) > self.max_body_bytes:
            return self._error(413, HttpGatewayErrorCode.REQUEST_TOO_LARGE)

        token = self._bearer_token(headers)
        if token is None:
            return self._error(
                401,
                HttpGatewayErrorCode.AUTH_REQUIRED,
                extra_headers={"WWW-Authenticate": "Bearer"},
            )
        try:
            session = self._credentials.authenticate(token)
        except (TypeError, ValueError):
            session = None
        if session is None:
            return self._error(
                401,
                HttpGatewayErrorCode.AUTH_INVALID,
                extra_headers={"WWW-Authenticate": "Bearer"},
            )

        try:
            wire_document = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._error(400, HttpGatewayErrorCode.REQUEST_INVALID)
        if not isinstance(wire_document, dict):
            return self._error(400, HttpGatewayErrorCode.REQUEST_INVALID)
        if any(
            str(key).replace("-", "_").lower() in _SERVER_OWNED_INVOCATION_FIELDS
            for key in wire_document
        ):
            return self._error(400, HttpGatewayErrorCode.REQUEST_INVALID)
        messages = wire_document.get("messages")
        if not isinstance(messages, list) or not messages:
            return self._error(400, HttpGatewayErrorCode.REQUEST_INVALID)

        supplied_model = wire_document.get("model")
        if supplied_model is not None and (
            not isinstance(supplied_model, str)
            or supplied_model != session.binding.public_model_alias
        ):
            # Equality is only a configuration guard.  The actual Provider model
            # still comes from the committed RuntimeConfigSnapshot.
            return self._error(400, HttpGatewayErrorCode.MODEL_ALIAS_MISMATCH)

        stream_requested = wire_document.get("stream", False)
        if not isinstance(stream_requested, bool):
            return self._error(400, HttpGatewayErrorCode.REQUEST_INVALID)
        provider_input = {
            str(key): value
            for key, value in wire_document.items()
            if str(key) not in _SERVER_OWNED_OPENAI_FIELDS
        }

        try:
            requested_tools = requested_provider_tools(provider_input)
        except ValueError:
            return self._gateway_error(GatewayReasonCode.PROVIDER_INPUT_INVALID)
        allowed_tools = frozenset(session.binding.allowed_tools)
        if not requested_tools.issubset(allowed_tools):
            return self._gateway_error(GatewayReasonCode.TOOL_NOT_ALLOWED)
        if not allowed_tools and provider_input.get("tool_choice") not in (None, "none"):
            return self._gateway_error(GatewayReasonCode.TOOL_NOT_ALLOWED)
        if not requested_tools:
            # OpenClaw serializes a deny-all tool policy as ``tools: []``.
            # Remove only that empty transport declaration.  Non-empty or
            # malformed declarations were rejected above from the trusted
            # binding, before a single-use plan can be consumed.
            provider_input.pop("tools", None)
            provider_input.pop("tool_choice", None)
        if find_forbidden_runtime_fields(provider_input):
            return self._gateway_error(GatewayReasonCode.RUNTIME_BODY_FORBIDDEN)

        plan = self._plans.claim(session, messages=messages)
        if plan is None:
            # This is also the expected fail-closed result for the upstream
            # controller's unsolicited Manager welcome LLM probe.  No synthetic
            # 200 is emitted and no Provider object is constructed.
            return self._error(403, HttpGatewayErrorCode.CALL_PLAN_UNAVAILABLE)

        invocation = ModelInvocation(
            program_id=session.binding.program_id,
            run_id=session.binding.run_id,
            model_call_id=plan.model_call_id,
            agent_identity_id=session.binding.agent_identity_id,
            agent_identity_version=session.binding.agent_identity_version,
            skill_name=plan.skill_name,
            skill_version=plan.skill_version,
            runtime_config_snapshot_id=session.binding.runtime_config_snapshot_id,
            reservation_id=plan.reservation_id,
            provider_input=provider_input,
            object_refs=plan.object_refs,
            exclusions=plan.exclusions,
        )
        try:
            gateway = self._gateway_factory(session)
            result = gateway.invoke(invocation)
        except Exception:
            return self._error(500, HttpGatewayErrorCode.GATEWAY_UNAVAILABLE)
        if not result.committed or result.provider_response is None:
            return self._gateway_error(result.reason_code, model_call_id=plan.model_call_id)

        output = thaw(result.provider_response.output_document)
        if not isinstance(output, dict):
            return self._error(502, HttpGatewayErrorCode.GATEWAY_UNAVAILABLE)
        if stream_requested:
            return self._stream_response(
                output,
                model_call_id=plan.model_call_id,
                public_model_alias=session.binding.public_model_alias,
            )
        return self._json_response(
            200,
            output,
            extra_headers={"X-Awakening-Model-Call-Id": plan.model_call_id},
        )

    @staticmethod
    def _bearer_token(headers: Mapping[str, str]) -> str | None:
        value = next(
            (str(item) for key, item in headers.items() if str(key).lower() == "authorization"),
            "",
        )
        scheme, separator, token = value.partition(" ")
        if separator != " " or scheme.lower() != "bearer" or not token.strip():
            return None
        return token.strip()

    def _gateway_error(
        self,
        reason: GatewayReasonCode,
        *,
        model_call_id: str | None = None,
    ) -> HttpGatewayResponse:
        status = 403
        if reason in {
            GatewayReasonCode.PROVIDER_INPUT_INVALID,
            GatewayReasonCode.RUNTIME_BODY_FORBIDDEN,
        }:
            status = 400
        elif reason in {
            GatewayReasonCode.SNAPSHOT_NOT_COMMITTED,
            GatewayReasonCode.RESERVATION_NOT_COMMITTED,
            GatewayReasonCode.CONTEXT_MANIFEST_NOT_COMMITTED,
            GatewayReasonCode.PRECALL_BINDING_MISMATCH,
        }:
            status = 409
        elif reason is GatewayReasonCode.PROVIDER_TRANSPORT_FAILED:
            status = 502
        elif reason in {
            GatewayReasonCode.USAGE_SETTLEMENT_FAILED,
            GatewayReasonCode.INVOCATION_RECEIPT_FAILED,
        }:
            status = 500
        headers = (
            {"X-Awakening-Model-Call-Id": model_call_id}
            if model_call_id is not None
            else None
        )
        return self._error(status, reason.value, extra_headers=headers)

    @classmethod
    def _error(
        cls,
        status_code: int,
        code: str | StrEnum,
        *,
        extra_headers: Mapping[str, str] | None = None,
    ) -> HttpGatewayResponse:
        code_text = str(code.value if isinstance(code, StrEnum) else code)
        return cls._json_response(
            status_code,
            {
                "error": {
                    "message": code_text,
                    "type": "awakening_m4_gateway_error",
                    "code": code_text,
                }
            },
            extra_headers=extra_headers,
        )

    @staticmethod
    def _json_response(
        status_code: int,
        document: Mapping[str, Any],
        *,
        extra_headers: Mapping[str, str] | None = None,
    ) -> HttpGatewayResponse:
        encoded = json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        headers = {"Content-Type": "application/json; charset=utf-8"}
        headers.update(dict(extra_headers or {}))
        return HttpGatewayResponse(status_code, headers, encoded)

    @classmethod
    def _stream_response(
        cls,
        document: Mapping[str, Any],
        *,
        model_call_id: str,
        public_model_alias: str,
    ) -> HttpGatewayResponse:
        chunk = cls._completion_chunk(
            document,
            model_call_id=model_call_id,
            public_model_alias=public_model_alias,
        )
        payload = (
            "data: "
            + json.dumps(chunk, ensure_ascii=False, separators=(",", ":"))
            + "\n\ndata: [DONE]\n\n"
        ).encode("utf-8")
        return HttpGatewayResponse(
            200,
            {
                "Content-Type": "text/event-stream; charset=utf-8",
                "Cache-Control": "no-cache",
                "X-Awakening-Model-Call-Id": model_call_id,
            },
            payload,
        )

    @staticmethod
    def _completion_chunk(
        document: Mapping[str, Any],
        *,
        model_call_id: str,
        public_model_alias: str,
    ) -> dict[str, Any]:
        choices: list[dict[str, Any]] = []
        raw_choices = document.get("choices", [])
        if isinstance(raw_choices, list):
            for position, raw_choice in enumerate(raw_choices):
                if not isinstance(raw_choice, Mapping):
                    continue
                raw_message = raw_choice.get("message", {})
                message = raw_message if isinstance(raw_message, Mapping) else {}
                delta = {
                    key: message[key]
                    for key in ("role", "content", "tool_calls", "refusal")
                    if key in message
                }
                choices.append(
                    {
                        "index": int(raw_choice.get("index", position)),
                        "delta": delta,
                        "finish_reason": raw_choice.get("finish_reason"),
                    }
                )
        chunk: dict[str, Any] = {
            "id": str(document.get("id") or f"chatcmpl-{model_call_id}"),
            "object": "chat.completion.chunk",
            "created": int(document.get("created", 0)),
            "model": str(document.get("model") or public_model_alias),
            "choices": choices,
        }
        if isinstance(document.get("usage"), Mapping):
            chunk["usage"] = dict(document["usage"])
        return chunk


def build_http_server(
    adapter: TrustedOpenAICompatibleHttpAdapter,
    *,
    host: str,
    port: int,
) -> ThreadingHTTPServer:
    """Build, but do not start, the standard-library M4 HTTP server."""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "AwakeningM4Gateway/1.0"

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            raw_length = self.headers.get("Content-Length", "")
            try:
                content_length = int(raw_length)
            except ValueError:
                response = adapter._error(400, HttpGatewayErrorCode.REQUEST_INVALID)
                self._write(response)
                return
            if content_length < 0 or content_length > adapter.max_body_bytes:
                response = adapter._error(413, HttpGatewayErrorCode.REQUEST_TOO_LARGE)
                self._write(response)
                return
            body = self.rfile.read(content_length)
            response = adapter.handle_request(
                method="POST",
                path=self.path,
                headers={key: value for key, value in self.headers.items()},
                body=body,
            )
            self._write(response)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self._write(adapter._error(405, HttpGatewayErrorCode.METHOD_NOT_ALLOWED))

        def _write(self, response: HttpGatewayResponse) -> None:
            self.send_response(response.status_code)
            for key, value in response.headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(response.body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(response.body)

        def log_message(self, format: str, *args: object) -> None:
            # Never let the default HTTP logger accidentally gain access to a
            # future Authorization-bearing request representation.
            return

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


__all__ = (
    "HttpGatewayErrorCode",
    "HttpGatewayResponse",
    "OPENAI_CHAT_COMPLETIONS_PATH",
    "RuntimeInvocationPlan",
    "RuntimeInvocationPlanPort",
    "RuntimeCredentialRegistryPort",
    "SingleUseRuntimeInvocationPlanRegistry",
    "TrustedRuntimeSessionPort",
    "TrustedOpenAICompatibleHttpAdapter",
    "build_http_server",
    "validate_runtime_request_marker",
)
