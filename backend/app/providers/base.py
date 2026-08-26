"""The provider contract.

Everything the application needs from a language model is expressed here.
Nothing above this layer knows whether it is talking to a 3B model on the
laptop or a frontier model in Azure — which is the entire point of the
requirement that the evaluator can swap models without touching code.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(slots=True)
class Message:
    role: str
    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None

    def to_openai(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            payload["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            payload["tool_call_id"] = self.tool_call_id
        if self.name:
            payload["name"] = self.name
        return payload


@dataclass(slots=True)
class Delta:
    """One streamed increment.

    `text` is the common case. `tool_calls` carries partial function-call
    fragments, which arrive split across deltas and are reassembled by the
    caller. `finish_reason` marks the final delta.
    """

    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ProviderInfo:
    name: str
    model: str
    embed_model: str | None = None
    base_url: str | None = None
    requires_key: bool = False
    configured: bool = True


@runtime_checkable
class LLMProvider(Protocol):
    info: ProviderInfo

    async def chat_stream(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> AsyncIterator[Delta]:
        """Stream a completion. Must yield at least one Delta."""
        ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one vector per input string, in order."""
        ...

    async def ping(self) -> dict[str, Any]:
        """Cheap reachability probe for /health. Must not raise."""
        ...
