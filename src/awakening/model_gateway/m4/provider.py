"""Provider ports. Only the gateway process may construct a real provider."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
import ssl
from typing import Any
from urllib.parse import urlparse
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener
from uuid import uuid4

from awakening.state.validation import canonical_json_bytes

from .contracts import ProviderRequest, ProviderResponse, thaw


class RecordingProvider:
    """A call-counting unit-test port; never acceptable as live M4 evidence."""

    def __init__(
        self,
        response: ProviderResponse | None = None,
        *,
        provider_alias: str = "synthetic-provider",
    ) -> None:
        self._call_count = 0
        self._response = response
        self._provider_alias = provider_alias

    @property
    def provider_alias(self) -> str:
        return self._provider_alias

    @property
    def call_count(self) -> int:
        return self._call_count

    def invoke(self, request: ProviderRequest) -> ProviderResponse:
        if sha256(canonical_json_bytes(thaw(request.input_document))).hexdigest() != (
            request.request_sha256
        ):
            raise ValueError("provider request hash does not match exact wire input")
        self._call_count += 1
        if self._response is not None:
            return self._response
        output = {
            "recording_provider": True,
            "model_call_id": request.model_call_id,
        }
        return ProviderResponse(
            provider_request_id=f"recording-{uuid4()}",
            output_document=output,
            skill_output_document=output,
            input_tokens=1,
            output_tokens=1,
            cost_microunits=0,
            response_sha256=sha256(canonical_json_bytes(output)).hexdigest(),
        )


class OpenAICompatibleProvider:
    """Exact-host OpenAI-compatible transport with a non-exported API key."""

    def __init__(
        self,
        *,
        provider_alias: str,
        endpoint: str,
        api_key: str,
        allowed_hostname: str,
        input_microunits_per_million: int,
        output_microunits_per_million: int,
        timeout_seconds: float = 60.0,
    ) -> None:
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" or parsed.hostname != allowed_hostname:
            raise ValueError("provider endpoint must be the approved exact HTTPS hostname")
        if parsed.port not in (None, 443):
            raise ValueError("provider endpoint must use the approved default HTTPS port")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("provider endpoint contains forbidden URL components")
        if not provider_alias:
            raise ValueError("provider alias is required")
        if not api_key:
            raise ValueError("provider API key is required")
        if input_microunits_per_million < 0 or output_microunits_per_million < 0:
            raise ValueError("provider prices cannot be negative")
        self._endpoint = endpoint.rstrip("/")
        self._provider_alias = provider_alias
        self._api_key = api_key
        self._allowed_hostname = allowed_hostname
        self._input_rate = input_microunits_per_million
        self._output_rate = output_microunits_per_million
        self._timeout = timeout_seconds
        self._call_count = 0

    @property
    def provider_alias(self) -> str:
        return self._provider_alias

    @property
    def call_count(self) -> int:
        return self._call_count

    def invoke(self, request: ProviderRequest) -> ProviderResponse:
        document = thaw(request.input_document)
        if not isinstance(document, dict):
            raise ValueError("provider input must be an object")
        body = canonical_json_bytes(document)
        if sha256(body).hexdigest() != request.request_sha256:
            raise ValueError("provider request hash does not match exact wire input")
        target = f"{self._endpoint}/chat/completions"
        if urlparse(target).hostname != self._allowed_hostname:
            raise ValueError("provider target hostname changed")
        http_request = Request(
            target,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-Awakening-Model-Call-Id": request.model_call_id,
            },
        )
        self._call_count += 1
        opener = build_opener(
            HTTPSHandler(context=ssl.create_default_context()),
            _RejectRedirects(),
        )
        with opener.open(http_request, timeout=self._timeout) as response:
            raw = response.read()
            response_id = response.headers.get("x-request-id", "")
        parsed = json.loads(raw.decode("utf-8"))
        if not isinstance(parsed, Mapping):
            raise ValueError("provider response must be an object")
        usage = parsed.get("usage")
        if not isinstance(usage, Mapping):
            raise ValueError("provider response must report usage")
        input_tokens = _reported_token_count(usage, "prompt_tokens")
        output_tokens = _reported_token_count(usage, "completion_tokens")
        skill_output_document = _extract_skill_output_document(parsed)
        cost = (
            input_tokens * self._input_rate
            + output_tokens * self._output_rate
            + 999_999
        ) // 1_000_000
        return ProviderResponse(
            provider_request_id=response_id or str(parsed.get("id", "")) or f"provider-{uuid4()}",
            output_document=parsed,
            skill_output_document=skill_output_document,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_microunits=cost,
            response_sha256=sha256(raw).hexdigest(),
        )


class _RejectRedirects(HTTPRedirectHandler):
    """Never forward the Gateway Authorization header across a redirect."""

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        raise HTTPError(req.full_url, code, "provider redirects are forbidden", headers, fp)


def _reported_token_count(usage: Mapping[str, Any], field: str) -> int:
    value = usage.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"provider usage field {field} must be a non-negative integer")
    return value


def _extract_skill_output_document(response: Mapping[str, Any]) -> Any:
    """Return the exact JSON value carried by the single assistant message."""

    choices = response.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise ValueError("provider response must contain exactly one choice")
    choice = choices[0]
    if not isinstance(choice, Mapping):
        raise ValueError("provider response choice must be an object")
    message = choice.get("message")
    if not isinstance(message, Mapping):
        raise ValueError("provider response choice must contain a message")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("provider response message content must be non-empty JSON text")
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("provider response message content must be valid JSON") from exc


__all__ = ("OpenAICompatibleProvider", "RecordingProvider")
