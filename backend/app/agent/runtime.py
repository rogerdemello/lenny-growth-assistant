"""The agent runtime contract.

Two implementations satisfy it — `LocalToolLoopRuntime` and
`ClaudeAgentSDKRuntime` — and both consume the same tool registry
(`app.agent.tools`) and the same on-disk skills (`.claude/skills/`). That
sharing is what lets the model swap without the product changing.

Everything a runtime produces flows out as `AgentEvent`s, which map one-to-one
onto SSE frames. The API layer does not know which runtime produced them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID


@dataclass(slots=True)
class AgentEvent:
    """One thing worth telling the client about."""

    type: str  # stage | token | tool_call | citations | artifact | validation | outline | done | error
    data: dict[str, Any] = field(default_factory=dict)

    def to_sse(self) -> dict[str, Any]:
        return {"type": self.type, **self.data}


@dataclass(slots=True)
class AgentRequest:
    session_id: UUID
    message: str
    history: list[dict[str, Any]] = field(default_factory=list)


class AgentRuntime(ABC):
    """What the API layer is allowed to assume about an agent."""

    name: str

    @abstractmethod
    def run(self, request: AgentRequest) -> AsyncIterator[AgentEvent]:
        """Stream the turn. Must terminate with a `done` or `error` event."""
        raise NotImplementedError

    @abstractmethod
    async def describe(self) -> dict[str, Any]:
        """What /api/config reports about this runtime."""
        raise NotImplementedError


def build_runtime(name: str | None = None):  # noqa: ANN201
    """Construct the configured runtime.

    Imports are local so that a missing optional dependency (the Claude Agent
    SDK) only breaks the runtime that needs it, not the whole app.
    """
    from app.core.config import get_settings

    settings = get_settings()
    resolved = name or settings.agent_runtime

    if resolved == "claude_sdk":
        from app.agent.claude_sdk import ClaudeAgentSDKRuntime

        return ClaudeAgentSDKRuntime()

    from app.agent.local_loop import LocalToolLoopRuntime

    return LocalToolLoopRuntime()
