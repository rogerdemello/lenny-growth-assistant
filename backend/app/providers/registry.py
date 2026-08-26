"""Provider construction and the fallback chain.

This module is the only place that reads provider configuration, and the only
place that decides *which* model answers. Two consequences worth stating:

  * Adding a provider means adding a branch here and some variables to
    .env.example. Nothing in the API, agent, or retrieval layers changes.
  * When the primary provider fails, `chat_stream_with_fallback` retries on the
    fallback and tells the caller which one actually answered — so the UI can
    show it and the logs can record it, rather than silently degrading.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from app.core.config import ProviderName, Settings, get_settings
from app.core.errors import (
    MissingCredentialsError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.core.logging import get_logger
from app.providers.azure_openai import AzureOpenAIProvider
from app.providers.base import Delta, LLMProvider, Message
from app.providers.openai_compat import OpenAICompatProvider

log = get_logger(__name__)

_cache: dict[str, LLMProvider] = {}

# Errors worth failing over for. A validation error or a bad prompt will fail
# identically on the fallback, so retrying those just doubles the latency.
RETRYABLE = (ProviderUnavailableError, ProviderTimeoutError, MissingCredentialsError)


def build_provider(name: ProviderName, settings: Settings | None = None) -> LLMProvider:
    settings = settings or get_settings()

    if name == "ollama":
        return OpenAICompatProvider(
            name="ollama",
            base_url=settings.ollama_base_url,
            api_key="ollama",  # Ollama ignores it, but some clients require non-empty.
            model=settings.ollama_chat_model,
            embed_model=settings.ollama_embed_model,
            timeout=settings.llm_timeout_seconds,
            requires_key=False,
        )

    if name == "azure":
        return AzureOpenAIProvider(
            endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            chat_deployment=settings.azure_openai_chat_deployment,
            embed_deployment=settings.azure_openai_embed_deployment,
            api_version=settings.azure_openai_api_version,
            timeout=settings.llm_timeout_seconds,
        )

    if name == "openai_compat":
        return OpenAICompatProvider(
            name="openai_compat",
            base_url=settings.openai_compat_base_url,
            api_key=settings.openai_compat_api_key,
            model=settings.openai_compat_model,
            timeout=settings.llm_timeout_seconds,
            requires_key=True,
        )

    if name == "anthropic":
        # Anthropic's native Messages API is not OpenAI-shaped. Rather than
        # half-implement it, this provider is reached through the Claude Agent
        # SDK runtime, which speaks it properly.
        raise MissingCredentialsError(
            "The 'anthropic' provider is served by the Claude Agent SDK runtime.",
            hint="Set AGENT_RUNTIME=claude_sdk and ANTHROPIC_API_KEY instead of LLM_PROVIDER=anthropic.",
        )

    raise MissingCredentialsError(f"Unknown provider '{name}'.")


def get_provider(name: ProviderName | None = None, settings: Settings | None = None) -> LLMProvider:
    settings = settings or get_settings()
    resolved: ProviderName = name or settings.llm_provider
    if resolved not in _cache:
        _cache[resolved] = build_provider(resolved, settings)
    return _cache[resolved]


def get_embedding_provider(settings: Settings | None = None) -> LLMProvider:
    settings = settings or get_settings()
    provider = get_provider(settings.embed_provider, settings)
    return provider


def reset_cache() -> None:
    """Used by tests, and by config reloads."""
    _cache.clear()


async def chat_stream_with_fallback(
    messages: Sequence[Message],
    *,
    provider_name: ProviderName | None = None,
    tools: Sequence[dict[str, Any]] | None = None,
    temperature: float = 0.2,
    max_tokens: int | None = None,
    settings: Settings | None = None,
) -> AsyncIterator[tuple[Delta, str]]:
    """Stream from the primary provider, failing over once if it is unavailable.

    Yields `(delta, provider_name)` so callers always know who answered.

    The failover only applies before the first token. Once output has started
    we cannot restart cleanly without either duplicating text or discarding
    what the user already saw, so a mid-stream failure is surfaced as an error.
    """
    settings = settings or get_settings()
    primary: ProviderName = provider_name or settings.llm_provider
    chain: list[ProviderName] = [primary]
    if settings.llm_fallback_provider and settings.llm_fallback_provider != primary:
        chain.append(settings.llm_fallback_provider)

    last_error: Exception | None = None

    for attempt, name in enumerate(chain):
        emitted = False
        try:
            provider = get_provider(name, settings)
            async for delta in provider.chat_stream(
                messages, tools=tools, temperature=temperature, max_tokens=max_tokens
            ):
                emitted = True
                yield delta, name
            if attempt > 0:
                log.info("provider.fallback_succeeded", primary=primary, used=name)
            return
        except RETRYABLE as exc:
            last_error = exc
            if emitted:
                log.error("provider.failed_mid_stream", provider=name, error=str(exc))
                raise
            log.warning(
                "provider.failed",
                provider=name,
                error=str(exc),
                will_retry=attempt + 1 < len(chain),
            )
            continue

    assert last_error is not None
    raise last_error


def describe_configuration(settings: Settings | None = None) -> dict[str, Any]:
    """What /api/config reports, and what the UI badge renders.

    Never includes secrets — only whether each provider is configured.
    """
    settings = settings or get_settings()

    def _describe(name: ProviderName) -> dict[str, Any]:
        try:
            info = build_provider(name, settings).info
            return {
                "name": info.name,
                "model": info.model,
                "embed_model": info.embed_model,
                "base_url": info.base_url,
                "configured": info.configured,
            }
        except MissingCredentialsError:
            return {"name": name, "configured": False}

    return {
        "active_provider": settings.llm_provider,
        "active_model": _describe(settings.llm_provider).get("model"),
        "fallback_provider": settings.llm_fallback_provider,
        "essay_provider": settings.effective_essay_provider,
        "agent_runtime": settings.agent_runtime,
        "embed_provider": settings.embed_provider,
        "embed_model": settings.embed_model,
        "embed_dim": settings.embed_dim,
        "available": [_describe(n) for n in ("ollama", "azure", "openai_compat")],
    }
