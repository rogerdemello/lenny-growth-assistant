"""One adapter for every OpenAI-shaped endpoint.

Ollama, NVIDIA NIM, OpenAI, Groq, vLLM and Azure OpenAI all speak the same
`/chat/completions` and `/embeddings` wire format. Writing this once and
subclassing only for Azure's URL layout is why adding a provider is a config
change rather than a code change.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from app.core.errors import (
    MissingCredentialsError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.core.logging import get_logger
from app.providers.base import Delta, LLMProvider, Message, ProviderInfo

log = get_logger(__name__)


class OpenAICompatProvider(LLMProvider):
    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str = "",
        model: str = "",
        embed_model: str = "",
        timeout: float = 180.0,
        requires_key: bool = True,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.embed_model = embed_model or model
        self.timeout = timeout
        self.extra_headers = extra_headers or {}
        self.info = ProviderInfo(
            name=name,
            model=model,
            embed_model=self.embed_model,
            base_url=self.base_url,
            requires_key=requires_key,
            configured=bool(base_url) and (bool(api_key) or not requires_key),
        )

    # -- URL layout; Azure overrides these ---------------------------------

    def _chat_url(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _embed_url(self) -> str:
        return f"{self.base_url}/embeddings"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", **self.extra_headers}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _chat_body(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload["model"] = self.model
        return payload

    def _embed_body(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload["model"] = self.embed_model
        return payload

    def _assert_configured(self) -> None:
        if self.info.requires_key and not self.api_key:
            raise MissingCredentialsError(
                f"Provider '{self.info.name}' requires an API key but none is set.",
                hint=f"Set the API key for '{self.info.name}' in your .env — see .env.example.",
            )
        if not self.base_url:
            raise MissingCredentialsError(
                f"Provider '{self.info.name}' has no base URL configured.",
                hint="Set the corresponding *_BASE_URL or *_ENDPOINT variable in .env.",
            )

    # -- chat ---------------------------------------------------------------

    async def chat_stream(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> AsyncIterator[Delta]:
        self._assert_configured()

        body: dict[str, Any] = {
            "messages": [m.to_openai() for m in messages],
            "stream": True,
            "temperature": temperature,
        }
        if max_tokens:
            body["max_tokens"] = max_tokens
        if tools:
            body["tools"] = list(tools)
            body["tool_choice"] = "auto"
        body = self._chat_body(body)

        # Tool-call fragments arrive spread across deltas keyed by index; they
        # have to be stitched back together before the caller can use them.
        pending_tools: dict[int, dict[str, Any]] = {}

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream("POST", self._chat_url(), json=body, headers=self._headers()) as resp:
                    if resp.status_code >= 400:
                        detail = (await resp.aread()).decode("utf-8", "replace")[:500]
                        raise ProviderUnavailableError(
                            f"{self.info.name} returned HTTP {resp.status_code}: {detail}",
                            hint=_status_hint(resp.status_code, self.info.name),
                        )

                    async for raw in resp.aiter_lines():
                        if not raw or not raw.startswith("data:"):
                            continue
                        data = raw[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            event = json.loads(data)
                        except json.JSONDecodeError:
                            log.warning("provider.bad_sse_chunk", provider=self.info.name, chunk=data[:200])
                            continue

                        for delta in _parse_chunk(event, pending_tools):
                            yield delta

        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"{self.info.name} did not respond within {self.timeout:.0f}s."
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                f"Could not reach {self.info.name} at {self.base_url}: {exc}",
                hint=_connection_hint(self.info.name, self.base_url),
            ) from exc

        # Flush any tool calls that were still being assembled when the stream
        # ended without an explicit finish_reason carrying them.
        if pending_tools:
            yield Delta(tool_calls=[pending_tools[k] for k in sorted(pending_tools)], finish_reason="tool_calls")

    # -- embeddings ---------------------------------------------------------

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self._assert_configured()
        if not texts:
            return []

        body = self._embed_body({"input": list(texts)})
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(self._embed_url(), json=body, headers=self._headers())
                if resp.status_code >= 400:
                    raise ProviderUnavailableError(
                        f"{self.info.name} embeddings returned HTTP {resp.status_code}: {resp.text[:500]}",
                        hint=_embed_hint(self.info.name, self.embed_model),
                    )
                payload = resp.json()
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"{self.info.name} embeddings timed out.") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                f"Could not reach {self.info.name} embeddings: {exc}",
                hint=_connection_hint(self.info.name, self.base_url),
            ) from exc

        # The API guarantees nothing about ordering, but does return an index.
        items = sorted(payload.get("data", []), key=lambda d: d.get("index", 0))
        return [item["embedding"] for item in items]

    # -- health -------------------------------------------------------------

    async def ping(self) -> dict[str, Any]:
        if not self.info.configured:
            return {"ok": False, "reason": "not configured"}
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.base_url}/models", headers=self._headers())
            return {"ok": resp.status_code < 400, "status": resp.status_code}
        except Exception as exc:  # noqa: BLE001 — health must not raise
            return {"ok": False, "reason": str(exc)[:200]}


def _parse_chunk(event: dict[str, Any], pending_tools: dict[int, dict[str, Any]]) -> list[Delta]:
    """Translate one SSE chunk into zero or more Deltas."""
    out: list[Delta] = []
    choices = event.get("choices") or []
    if not choices:
        # Some providers send a usage-only trailing chunk.
        if usage := event.get("usage"):
            out.append(Delta(usage=usage))
        return out

    choice = choices[0]
    delta = choice.get("delta") or {}

    if text := delta.get("content"):
        out.append(Delta(text=text))

    for frag in delta.get("tool_calls") or []:
        idx = frag.get("index", 0)
        slot = pending_tools.setdefault(
            idx, {"id": frag.get("id", ""), "type": "function", "function": {"name": "", "arguments": ""}}
        )
        if frag.get("id"):
            slot["id"] = frag["id"]
        fn = frag.get("function") or {}
        if fn.get("name"):
            slot["function"]["name"] = fn["name"]
        if fn.get("arguments"):
            slot["function"]["arguments"] += fn["arguments"]

    if reason := choice.get("finish_reason"):
        assembled = [pending_tools[k] for k in sorted(pending_tools)]
        pending_tools.clear()
        out.append(Delta(tool_calls=assembled, finish_reason=reason, usage=event.get("usage") or {}))

    return out


def _status_hint(status: int, provider: str) -> str:
    if status in (401, 403):
        return f"Authentication failed for '{provider}'. Check the API key in .env."
    if status == 404:
        return f"Model or deployment not found on '{provider}'. Check the model/deployment name in .env."
    if status == 429:
        return f"'{provider}' is rate-limiting. Wait, or set LLM_FALLBACK_PROVIDER."
    return f"'{provider}' rejected the request."


def _connection_hint(provider: str, base_url: str) -> str:
    if provider == "ollama":
        return f"Ollama is not reachable at {base_url}. Start it with `ollama serve`, or set LLM_PROVIDER to a cloud provider."
    return f"Could not reach {provider} at {base_url}. Check network access and the endpoint URL."


def _embed_hint(provider: str, model: str) -> str:
    if provider == "ollama":
        return f"Ollama could not embed with '{model}'. Pull it first: `ollama pull {model}`."
    return f"The embedding model '{model}' is unavailable on {provider}."
