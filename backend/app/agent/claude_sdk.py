"""The Claude Agent SDK runtime.

The brief names the Anthropic Claude Agent SDK as the agent layer, so this is a
real implementation of it, not a stub: the same three tools from
`app.agent.tools` are exposed to the SDK as an in-process MCP server, the same
`.claude/skills/ship30/SKILL.md` is loaded through the SDK's own skill
mechanism, and sessions resume by id so conversation context carries across
turns.

**What we could not run, and why.** This runtime authenticates with
`ANTHROPIC_API_KEY`; the development machine had Azure OpenAI credentials and
no Anthropic key. Two consequences, stated plainly:

  * This code path is exercised by tests against a mocked transport, not
    against the live Anthropic API.
  * The recorded demo runs on `LocalToolLoopRuntime`, which is also what the
    mandatory local-Ollama requirement needs — the SDK's bundled agent binary
    sends a system prompt on the order of 10-15k tokens, which a 3B model on
    CPU cannot absorb in a usable amount of time (prefill alone measured at
    ~11s per 1k tokens on this hardware).

To run it: `pip install -e ".[agent-sdk]"`, set `ANTHROPIC_API_KEY`, and set
`AGENT_RUNTIME=claude_sdk`. It also works against any gateway that speaks the
Anthropic Messages format — set `ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN`.
The gateway must stream SSE, and model ids must contain "claude" or "anthropic"
to survive the SDK's model-discovery filter. See docs/architecture.md.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from app.agent.runtime import AgentEvent, AgentRequest, AgentRuntime
from app.agent.tools import REGISTRY, ToolContext, execute
from app.core.config import get_settings
from app.core.errors import MissingCredentialsError
from app.core.logging import get_logger
from app.skills.loader import get_skill

log = get_logger(__name__)

MCP_SERVER_NAME = "lenny"


def _sdk_available() -> bool:
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError:
        return False
    return True


class ClaudeAgentSDKRuntime(AgentRuntime):
    name = "claude_sdk"

    def __init__(self) -> None:
        self.settings = get_settings()
        # Maps our session id to the SDK's, so `resume=` continues the right
        # conversation rather than starting a fresh one each turn.
        self._sdk_sessions: dict[str, str] = {}

    # -- wiring ------------------------------------------------------------

    def _build_mcp_server(self, ctx: ToolContext):  # noqa: ANN201
        """Expose the shared tool registry as an in-process MCP server.

        The handlers here delegate to `app.agent.tools.execute`, which is the
        same function the local runtime calls. Neither runtime owns a private
        copy of a tool.
        """
        from claude_agent_sdk import create_sdk_mcp_server, tool

        sdk_tools = []
        for registered in REGISTRY.values():

            def _make(name: str):  # noqa: ANN202
                async def _handler(args: dict[str, Any]) -> dict[str, Any]:
                    result = await execute(name, args, ctx)
                    return {
                        "content": [{"type": "text", "text": json.dumps(result)}],
                        "is_error": bool(result.get("error")),
                    }

                return _handler

            sdk_tools.append(
                tool(registered.name, registered.description, registered.parameters)(_make(registered.name))
            )

        return create_sdk_mcp_server(name=MCP_SERVER_NAME, version="1.0.0", tools=sdk_tools)

    def _build_options(self, ctx: ToolContext, session_id: UUID):  # noqa: ANN201
        from claude_agent_sdk import ClaudeAgentOptions

        from app.agent import prompts

        qualified = [f"mcp__{MCP_SERVER_NAME}__{name}" for name in REGISTRY]

        kwargs: dict[str, Any] = {
            "system_prompt": prompts.ASSISTANT_SYSTEM,
            "mcp_servers": {MCP_SERVER_NAME: self._build_mcp_server(ctx)},
            "allowed_tools": [*qualified, "Skill"],
            # Skills are read from disk, from the same directory the local
            # runtime reads. `setting_sources` is what makes the SDK look there.
            "setting_sources": ["project"],
            "skills": "all",
            "cwd": str(self.settings.skills_dir.parent.parent),
            "permission_mode": "acceptEdits",
            "max_turns": 6,
        }
        if self.settings.anthropic_model:
            kwargs["model"] = self.settings.anthropic_model

        resumed = self._sdk_sessions.get(str(session_id))
        if resumed:
            kwargs["resume"] = resumed

        return ClaudeAgentOptions(**kwargs)

    # -- runtime contract --------------------------------------------------

    async def describe(self) -> dict[str, Any]:
        skill = get_skill("ship30")
        return {
            "runtime": self.name,
            "provider": "anthropic",
            "model": self.settings.anthropic_model,
            "sdk_installed": _sdk_available(),
            "credentials_present": bool(self.settings.anthropic_api_key or self.settings.anthropic_base_url),
            "tools": [f"mcp__{MCP_SERVER_NAME}__{n}" for n in sorted(REGISTRY)],
            "skills": [skill.name] if skill else [],
            "strategy": "model-directed tool use via in-process MCP server",
        }

    async def run(self, request: AgentRequest) -> AsyncIterator[AgentEvent]:
        if not _sdk_available():
            yield AgentEvent(
                "error",
                {
                    "code": "sdk_not_installed",
                    "message": "AGENT_RUNTIME=claude_sdk but claude-agent-sdk is not installed.",
                    "hint": 'Install it with: uv pip install -e ".[agent-sdk]" — or set AGENT_RUNTIME=local.',
                },
            )
            return

        if not (self.settings.anthropic_api_key or self.settings.anthropic_base_url):
            yield AgentEvent(
                "error",
                {
                    "code": "missing_credentials",
                    "message": "The Claude Agent SDK runtime needs ANTHROPIC_API_KEY or ANTHROPIC_BASE_URL.",
                    "hint": "Set ANTHROPIC_API_KEY in .env, or set AGENT_RUNTIME=local to run on Ollama.",
                },
            )
            return

        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeSDKClient,
            ResultMessage,
            TextBlock,
            ToolUseBlock,
        )

        ctx = ToolContext(session_id=request.session_id, settings=self.settings)

        try:
            yield AgentEvent("stage", {"stage": "generating", "detail": "Claude Agent SDK"})

            async with ClaudeSDKClient(options=self._build_options(ctx, request.session_id)) as client:
                await client.query(request.message)

                async for message in client.receive_response():
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock) and block.text:
                                yield AgentEvent("token", {"text": block.text})
                            elif isinstance(block, ToolUseBlock):
                                yield AgentEvent(
                                    "tool_call",
                                    {"name": block.name, "arguments": block.input},
                                )

                    elif isinstance(message, ResultMessage):
                        if sid := getattr(message, "session_id", None):
                            self._sdk_sessions[str(request.session_id)] = sid

            if ctx.collected_citations:
                yield AgentEvent("citations", {"citations": ctx.collected_citations, "final": True})
            for artifact in ctx.collected_artifacts:
                yield AgentEvent("artifact", artifact)

            yield AgentEvent(
                "done",
                {
                    "grounded": bool(ctx.collected_citations),
                    "citations": ctx.collected_citations,
                    "provider": "anthropic",
                    "runtime": self.name,
                },
            )

        except MissingCredentialsError as exc:
            yield AgentEvent("error", {"code": exc.code, "message": exc.message, "hint": exc.hint})
        except Exception as exc:  # noqa: BLE001
            log.exception("claude_sdk.failed")
            yield AgentEvent(
                "error",
                {
                    "code": "agent_sdk_error",
                    "message": str(exc),
                    "hint": "Check ANTHROPIC_API_KEY, or set AGENT_RUNTIME=local to fall back to the local runtime.",
                },
            )
