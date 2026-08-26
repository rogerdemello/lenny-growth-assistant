"""The tool registry — declared once, executed by both runtimes.

This module is the load-bearing piece of the agent architecture. The local
tool-calling loop and the Claude Agent SDK runtime do not each define their own
tools; they both consume this registry. `search_transcripts` behaves identically
whether a 3B model on the laptop or Claude called it, and adding a tool means
adding it here once.

Each tool declares its schema in OpenAI function-calling form. The Claude Agent
SDK runtime translates that into an in-process MCP server at startup; the local
runtime passes it through unchanged.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.rag.retrieval import search

log = get_logger(__name__)


@dataclass(slots=True)
class ToolContext:
    """Everything a tool needs that is not in its arguments.

    Passed by the runtime rather than reached for globally, so tools stay
    testable and cannot accidentally read another session's state.
    """

    session_id: UUID | None = None
    settings: Settings = field(default_factory=get_settings)
    # Tools append here so the runtime can attach citations to the reply and
    # persist them, without parsing them back out of the model's prose.
    collected_citations: list[dict[str, Any]] = field(default_factory=list)
    collected_artifacts: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any], ToolContext], Awaitable[dict[str, Any]]]
    # Read-only tools are safe to run in parallel and safe to auto-approve.
    read_only: bool = True

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


# ---------------------------------------------------------------------------
# search_transcripts
# ---------------------------------------------------------------------------


async def _search_transcripts(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        return {"error": "query is required", "results": []}

    top_k = int(args.get("top_k") or ctx.settings.retrieval_top_k)
    result = await search(query, top_k=top_k, settings=ctx.settings)

    for citation in result.citations:
        payload = citation.to_dict()
        if payload["chunk_id"] not in {c["chunk_id"] for c in ctx.collected_citations}:
            ctx.collected_citations.append(payload)

    if not result.grounded:
        return {
            "grounded": False,
            "reason": result.reason,
            "results": [],
            "guidance": (
                "The transcript corpus does not cover this. Tell the user plainly that the "
                "podcast archive does not address it, and do not answer from general knowledge."
            ),
        }

    return {
        "grounded": True,
        "strategy": result.strategy,
        "results": [
            {
                "label": f"S{i}",
                "guest": c.guest,
                "episode": c.episode_title,
                "timestamp": c.timestamp,
                "speaker": c.speaker,
                "text": c.text,
                "score": c.score,
            }
            for i, c in enumerate(result.citations, start=1)
        ],
    }


SEARCH_TRANSCRIPTS = Tool(
    name="search_transcripts",
    description=(
        "Search the Lenny's Podcast transcript archive for passages relevant to a product, "
        "growth, pricing, positioning or product-management question. Returns labelled "
        "passages with the guest, episode and timestamp. Always use this before answering a "
        "substantive question — never answer from prior knowledge."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "A standalone search query. Resolve pronouns and references to earlier "
                    "turns first — 'what about for PLG' should become 'product-led growth pricing'."
                ),
            },
            "top_k": {
                "type": "integer",
                "description": "How many passages to return. Defaults to the configured value.",
            },
        },
        "required": ["query"],
    },
    handler=_search_transcripts,
    read_only=True,
)


# ---------------------------------------------------------------------------
# create_artifact
# ---------------------------------------------------------------------------


async def _create_artifact(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    from app.artifacts.sanitize import sanitize_artifact

    kind = str(args.get("kind") or "markdown").lower()
    if kind not in {"markdown", "html"}:
        return {"error": f"kind must be 'markdown' or 'html', got {kind!r}"}

    content = str(args.get("content") or "")
    if not content.strip():
        return {"error": "content is required"}

    title = str(args.get("title") or "Untitled artifact").strip()[:200]
    sanitized, report = sanitize_artifact(kind, content)

    ctx.collected_artifacts.append(
        {
            "kind": kind,
            "title": title,
            "raw_content": content,
            "sanitized_content": sanitized,
            "sanitizer_report": report,
        }
    )
    log.info("tool.artifact_created", kind=kind, title=title, removed=report.get("removed", []))

    return {
        "created": True,
        "kind": kind,
        "title": title,
        "sanitizer": report,
        # The model does not need the content echoed back — it wrote it. Saying
        # so keeps a slow local model from re-emitting the whole document.
        "note": "The artifact is now displayed to the user in the viewer. Do not repeat its contents in your reply; briefly say what you made and what it contains.",
    }


CREATE_ARTIFACT = Tool(
    name="create_artifact",
    description=(
        "Render a document beside the chat in the artifact viewer. Use for anything the user "
        "will want to read, keep, or export: a written document, a summary, a one-pager, a "
        "checklist, a styled HTML page. Prefer 'markdown' unless the user explicitly wants "
        "styling or a web page, in which case use 'html' with inline CSS in a <style> block. "
        "Scripts are stripped from HTML artifacts and will not run."
    ),
    parameters={
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["markdown", "html"]},
            "title": {"type": "string", "description": "Short, descriptive title."},
            "content": {
                "type": "string",
                "description": "The complete document. For html, a full standalone snippet with a <style> block.",
            },
        },
        "required": ["kind", "title", "content"],
    },
    handler=_create_artifact,
    read_only=False,
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# `write_ship30_essay` is registered by app.skills.ship30 to avoid a circular
# import — the skill needs the provider layer, which needs config, which the
# tools module is imported by.
REGISTRY: dict[str, Tool] = {
    SEARCH_TRANSCRIPTS.name: SEARCH_TRANSCRIPTS,
    CREATE_ARTIFACT.name: CREATE_ARTIFACT,
}


def register(tool: Tool) -> Tool:
    REGISTRY[tool.name] = tool
    return tool


def get_tool(name: str) -> Tool | None:
    return REGISTRY.get(name)


def openai_schemas(names: list[str] | None = None) -> list[dict[str, Any]]:
    tools = REGISTRY.values() if names is None else [REGISTRY[n] for n in names if n in REGISTRY]
    return [t.to_openai_schema() for t in tools]


async def execute(name: str, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Run a tool by name, converting failures into results the model can read.

    A tool that raises would abort the turn. A tool that returns
    `{"error": ...}` lets the model recover — which matters most for small
    local models, whose arguments are frequently malformed.
    """
    tool = REGISTRY.get(name)
    if tool is None:
        log.warning("tool.unknown", name=name)
        return {"error": f"No tool named {name!r}. Available: {sorted(REGISTRY)}"}

    try:
        return await tool.handler(args, ctx)
    except Exception as exc:  # noqa: BLE001 — surface to the model, not the user
        log.error("tool.failed", name=name, error=str(exc))
        return {"error": f"{name} failed: {exc}"}
