"""Toollery protocol-v2 bridge for OpenAI-compatible chat-completions APIs.

The Toollery command generator sends one normalized capability per request on
stdin. This bridge sends the paper prompt as the system message and the
capability specification as a JSON user message, then returns the validated
transport shape expected by ``CommandGenerator``.

Credentials are read from the environment. They are deliberately not accepted
in the JSON generator config, because that file is commonly checked into a
repository.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request


DEFAULT_API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_TIMEOUT = 120
DEFAULT_RETRIES = 2


class BridgeError(RuntimeError):
    """A deliberately safe, locally constructed diagnostic."""

    def __init__(self, message, code="configuration_error"):
        super().__init__(message)
        self.code = code


def _env_first(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return None


def _api_url(parameters: dict) -> str:
    value = parameters.get("api_url") or _env_first("TOOLLERY_API_URL", "DEEPSEEK_API_URL", "OPENAI_BASE_URL")
    url = value or DEFAULT_API_URL
    url = url.rstrip("/")
    # Permit either a complete endpoint or an OpenAI-compatible base URL.
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    parts = urlsplit(url)
    if (parts.scheme not in {"https", "http"} or not parts.hostname
            or parts.username or parts.password or parts.query or parts.fragment):
        raise BridgeError("API URL must be an HTTP(S) endpoint without credentials, query or fragment")
    if parts.scheme == "http" and parts.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise BridgeError("Use HTTPS for remote API endpoints")
    return url


def _api_key(parameters: dict) -> str:
    env_name = parameters.get("api_key_env")
    value = _env_first(env_name) if env_name else _env_first("TOOLLERY_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY")
    if not value:
        raise BridgeError(
            "Missing API key in the configured environment variable (default: TOOLLERY_API_KEY)."
        )
    return value


def _json_content(response: dict) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("API response has no choices")
    finish_reason = choices[0].get("finish_reason")
    if finish_reason not in {None, "stop"}:
        raise ValueError("API did not finish a normal text response")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ValueError("API response choice has no message")
    content = message.get("content")
    if isinstance(content, str):
        return content
    # A few compatible endpoints return structured content blocks.
    if isinstance(content, list):
        text = "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        )
        if text:
            return text
    raise ValueError("API response message content is not text")


def _strip_json_fence(content: str) -> str:
    text = content.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip()
    return text


def _request(payload: dict, timeout: float, retries: int, parameters: dict) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        _api_url(parameters),
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {_api_key(parameters)}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    # Never forward an Authorization header to a redirected endpoint.
    import urllib.request
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with opener.open(request, timeout=timeout) as response:
                raw = response.read()
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("API response must be a JSON object")
            return value
        except HTTPError as error:
            status = error.code
            error.close()
            if status not in {408, 429, 500, 502, 503, 504} or attempt == retries:
                raise BridgeError(f"API returned HTTP {status}", f"http_{status}") from None
            last_error = error
        except (URLError, TimeoutError, OSError) as error:
            last_error = error
        if attempt < retries:
            time.sleep(min(2 ** attempt, 4))
    # Never include response bodies or request headers here: providers may
    # echo sensitive request material and HTTP diagnostics can contain secrets.
    raise BridgeError(f"API transport failed after {retries + 1} attempts: {type(last_error).__name__}", "transport_error")


def main() -> int:
    # Windows redirected pipes otherwise inherit a locale encoding such as GBK.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict) or request.get("protocol_version") != 2:
            raise ValueError("Expected Toollery command bridge protocol_version 2")
        capabilities = request.get("capabilities")
        if not isinstance(capabilities, list) or len(capabilities) != 1:
            raise ValueError("This bridge expects exactly one capability per request")
        capability = capabilities[0]
        if not isinstance(capability, dict) or not isinstance(capability.get("name"), str):
            raise ValueError("Capability must contain a string name")
        name = capability["name"]
        count = request.get("proxy_query_count")
        if not isinstance(count, int) or count < 1:
            raise ValueError("proxy_query_count must be a positive integer")

        parameters = request.get("parameters")
        if not isinstance(parameters, dict):
            parameters = {}
        model = request.get("model")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("Toollery request is missing model")
        user_payload = {"capabilities": capabilities}
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": request["system_prompt"]},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            "temperature": parameters.get("temperature", 0),
            "response_format": {"type": "json_object"},
        }
        # Pass through a small allow-list of generation parameters. This keeps
        # provider-specific secrets and arbitrary request fields out of the bridge.
        for key in ("max_tokens", "top_p", "frequency_penalty", "presence_penalty", "seed"):
            if key in parameters:
                payload[key] = parameters[key]

        timeout = float(parameters.get("timeout_seconds", DEFAULT_TIMEOUT))
        retries = parameters.get("retries", DEFAULT_RETRIES)
        if not math.isfinite(timeout) or timeout <= 0 or type(retries) is not int or not 0 <= retries <= 5:
            raise BridgeError("timeout_seconds must be positive; retries must be an integer from 0 to 5")
        response = _request(
            payload,
            timeout=timeout,
            retries=retries,
            parameters=parameters,
        )
        content = _strip_json_fence(_json_content(response))
        generated = json.loads(content)
        if not isinstance(generated, dict) or set(generated) != {name}:
            raise ValueError("Model JSON must be an object keyed by the exact capability name")
        usage = response.get("usage", {})
        if not isinstance(usage, dict):
            usage = {}
        print(json.dumps({"manuals": generated, "usage": usage}, ensure_ascii=False))
        return 0
    except Exception as error:
        # Keep stdout machine-readable only on success; stderr contains no
        # request, response, URL or credential material.
        detail = str(error) if isinstance(error, BridgeError) else type(error).__name__
        print(f"provider_bridge_openai failed: {detail}", file=sys.stderr)
        code = error.code if isinstance(error, BridgeError) else "invalid_response"
        print(json.dumps({"error": {"code": code}}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
