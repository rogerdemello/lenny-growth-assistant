"""The Ship 30 essay skill: outline, then section by section.

Why two stages instead of one long generation:

  * **Structure survives.** Asked for 1,250 words in one shot, a small model
    drifts — it forgets the section budget around word 600 and stops citing
    around word 900. Committing to an outline first turns one hard instruction
    into five easy ones.
  * **The user sees progress.** A ~1,250-word essay takes 3-5 minutes on a 3B
    model over CPU. Sections arriving one at a time is the difference between a
    progress bar and a hang.

The cost is more round trips. That would be fatal if each section re-sent every
previous one, because prefill is the expensive part on CPU — measured at ~11s
for a 1k-token prompt and ~22s for 2k. So each section call sends the outline
and the sources, **not** the accumulated draft. Sections stay coherent because
the outline tells each one what its neighbours cover, and the cost per section
stays flat instead of growing with the essay.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from app.agent.tools import Tool, ToolContext, register
from app.core.config import Settings
from app.core.logging import get_logger
from app.providers.base import Message
from app.providers.registry import chat_stream_with_fallback
from app.rag.retrieval import Citation, format_sources_block, search
from app.skills.loader import get_skill
from app.skills.ship30_validator import validate

log = get_logger(__name__)

OUTLINE_SYSTEM = """You are planning a Ship 30 for 30 style long-form essay of about 1,250 words.

Return ONLY a JSON object, no prose, no code fence:

{
  "title": "<headline: clear not clever, names the WHO and the WHAT, promises an outcome>",
  "hook": "<ONE sentence. Declarative, a question, a controversial opinion, a moment in time, a vulnerable statement, or a weird insight. No context-setting.>",
  "sections": [
    {"heading": "<descriptive H2>", "point": "<the single argument this section makes>", "sources": ["S1", "S3"]}
  ]
}

Rules:
- Exactly 5 sections.
- The final section must be the takeaway: one specific thing the reader can do tomorrow. Its heading must contain the word "takeaway" or "do".
- Every section must cite at least one source label that exists in the provided sources.
- Each section makes ONE point. If two points belong together, merge them."""

SECTION_SYSTEM = """You are writing ONE section of a Ship 30 for 30 style essay. Write only this section.

Formatting rules, which are not optional:
- Start with the H2 heading exactly as given, on its own line, prefixed with "## ".
- Open the section with a SINGLE-sentence paragraph. Close it with a SINGLE-sentence paragraph.
- Between them use 1/3/1 rhythm: alternate paragraph lengths. Never 2/2/2, never 5/5/5, never one sentence per paragraph throughout.
- No paragraph exceeds 5 sentences.
- If you list anything, make it a bulleted list.
- Bold at most one phrase — the one a skimmer must not miss.
- Second person. Active verbs. Concrete nouns.
- Never write: delve, tapestry, "in the ever-evolving", "it's worth noting", "game-changer".

Grounding rules:
- Every claim must come from the sources below. Cite inline with [S1], [S2] exactly as labelled.
- Name the person when it adds weight: "Brian Balfour argues that...".
- If the sources do not support a point, leave it out. Do not fill from general knowledge.
- Do not invent quotes.

Write ONLY this section. No preamble, no closing summary, no other headings."""


@dataclass(slots=True)
class EssaySection:
    heading: str
    point: str
    sources: list[str]


@dataclass(slots=True)
class EssayPlan:
    title: str
    hook: str
    sections: list[EssaySection]


def _extract_json(text: str) -> dict[str, Any] | None:
    """Small models wrap JSON in prose or fences; take the outermost object."""
    text = re.sub(r"```(?:json)?", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _fallback_plan(topic: str, citations: list[Citation]) -> EssayPlan:
    """Used when the model cannot produce usable JSON.

    A 3B model fails to emit valid JSON often enough that treating it as fatal
    would make the skill unreliable. A generic-but-valid skeleton still
    produces a grounded, correctly-structured essay.
    """
    labels = [f"S{i}" for i in range(1, min(len(citations), 4) + 1)] or ["S1"]
    log.warning("ship30.outline_fallback", topic=topic[:80])
    return EssayPlan(
        title=f"What Lenny's guests actually say about {topic}",
        hook=f"Most advice about {topic} is repeated far more often than it is tested.",
        sections=[
            EssaySection("Where the conventional answer breaks down", "The default approach fails and here is why", labels),
            EssaySection("What the operators actually did", "Concrete practice from the transcripts", labels),
            EssaySection("The mechanism underneath", "Why that practice works", labels),
            EssaySection("What this costs you", "The trade-off nobody mentions", labels),
            EssaySection("Your takeaway: what to do tomorrow", "One specific action", labels),
        ],
    )


async def _plan_outline(
    topic: str, sources_block: str, citations: list[Citation], settings: Settings, provider_name: str
) -> EssayPlan:
    buf = ""
    async for delta, _ in chat_stream_with_fallback(
        [
            Message(role="system", content=OUTLINE_SYSTEM),
            Message(
                role="user",
                content=f"Topic: {topic}\n\nAvailable sources:\n\n{sources_block}\n\nReturn the JSON outline.",
            ),
        ],
        provider_name=provider_name,
        temperature=0.4,
        max_tokens=900,
        settings=settings,
    ):
        buf += delta.text

    data = _extract_json(buf)
    if not data or not isinstance(data.get("sections"), list) or not data["sections"]:
        return _fallback_plan(topic, citations)

    sections = []
    for raw in data["sections"][:6]:
        if not isinstance(raw, dict) or not raw.get("heading"):
            continue
        srcs = raw.get("sources") or []
        sections.append(
            EssaySection(
                heading=str(raw["heading"]).strip().lstrip("#").strip(),
                point=str(raw.get("point") or "").strip(),
                sources=[str(s).strip() for s in srcs if str(s).strip()],
            )
        )

    if len(sections) < 3:
        return _fallback_plan(topic, citations)

    return EssayPlan(
        title=str(data.get("title") or topic).strip(),
        hook=str(data.get("hook") or "").strip(),
        sections=sections,
    )


def _sources_footer(citations: list[Citation]) -> str:
    lines = ["", "## Sources", ""]
    for i, c in enumerate(citations, start=1):
        link = f" — [{c.timestamp}]({c.youtube_url})" if c.youtube_url else f" — {c.timestamp}"
        lines.append(f"- **[S{i}]** {c.guest}, *{c.episode_title}*{link}")
    return "\n".join(lines)


async def generate_essay(
    topic: str,
    *,
    ctx: ToolContext,
    on_event: Any = None,
) -> dict[str, Any]:
    """Produce the essay. `on_event` receives progress dicts for SSE streaming."""
    settings = ctx.settings
    provider_name = settings.effective_essay_provider

    async def emit(event: dict[str, Any]) -> None:
        if on_event is not None:
            await on_event(event)

    await emit({"type": "stage", "stage": "searching", "detail": f"Searching transcripts for “{topic}”"})

    # Long-form needs more source material than a chat turn, and the essay
    # provider is often the cloud one where a bigger prompt is cheap.
    result = await search(topic, top_k=max(settings.retrieval_top_k, 10), settings=settings)
    if not result.grounded:
        return {
            "grounded": False,
            "reason": result.reason,
            "message": (
                f"I can't write this essay — the transcript archive doesn't cover “{topic}”. "
                "Ask about a topic in the ingested corpus (see INGESTED.md), or ingest more episodes."
            ),
        }

    citations = result.citations
    for c in citations:
        payload = c.to_dict()
        if payload["chunk_id"] not in {x["chunk_id"] for x in ctx.collected_citations}:
            ctx.collected_citations.append(payload)

    sources_block = format_sources_block(citations)
    skill = get_skill("ship30")
    skill_rules = skill.body if skill else ""

    await emit({"type": "stage", "stage": "outlining", "detail": "Planning the essay structure"})
    plan = await _plan_outline(topic, sources_block, citations, settings, provider_name)

    await emit(
        {
            "type": "outline",
            "title": plan.title,
            "hook": plan.hook,
            "sections": [s.heading for s in plan.sections],
        }
    )

    parts: list[str] = [f"# {plan.title}", ""]
    if plan.hook:
        parts += [plan.hook.strip(), ""]

    outline_context = "\n".join(f"{i}. {s.heading} — {s.point}" for i, s in enumerate(plan.sections, 1))

    for index, section in enumerate(plan.sections, start=1):
        await emit(
            {
                "type": "stage",
                "stage": "writing",
                "detail": f"Writing section {index} of {len(plan.sections)}: {section.heading}",
                "progress": {"current": index, "total": len(plan.sections)},
            }
        )

        # The accumulated draft is deliberately NOT sent — only the outline.
        # Cost per section stays flat instead of growing with the essay.
        user_prompt = (
            f"Essay title: {plan.title}\n"
            f"Full outline (for context — do NOT write these other sections):\n{outline_context}\n\n"
            f"Write section {index}: \"{section.heading}\"\n"
            f"The single point this section makes: {section.point}\n"
            f"Target length: about {1250 // len(plan.sections)} words.\n\n"
            f"Sources:\n\n{sources_block}"
        )

        buf = ""
        async for delta, _ in chat_stream_with_fallback(
            [
                Message(role="system", content=f"{SECTION_SYSTEM}\n\n---\nStyle reference:\n{skill_rules[:2500]}"),
                Message(role="user", content=user_prompt),
            ],
            provider_name=provider_name,
            temperature=0.7,
            max_tokens=700,
            settings=settings,
        ):
            if delta.text:
                buf += delta.text
                await emit({"type": "token", "text": delta.text})

        section_text = buf.strip()
        # Models sometimes omit the heading or re-emit it at the wrong level.
        if not re.match(r"^##\s", section_text):
            section_text = f"## {section.heading}\n\n{section_text}"
        parts += [section_text, ""]

    parts.append(_sources_footer(citations))
    essay = "\n".join(parts).strip() + "\n"

    report = validate(essay)
    log.info(
        "ship30.generated",
        words=report.word_count,
        sections=report.section_count,
        citations=report.citation_count,
        passed=report.passed,
        provider=provider_name,
    )
    await emit({"type": "validation", "report": report.to_dict()})

    return {
        "grounded": True,
        "title": plan.title,
        "essay": essay,
        "validation": report.to_dict(),
        "validation_summary": report.summary_line(),
        "provider": provider_name,
    }


async def _write_ship30_essay(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    topic = str(args.get("topic") or "").strip()
    if not topic:
        return {"error": "topic is required"}

    result = await generate_essay(topic, ctx=ctx, on_event=None)
    if not result.get("grounded"):
        return result

    from app.artifacts.sanitize import sanitize_artifact

    sanitized, report = sanitize_artifact("markdown", result["essay"])
    ctx.collected_artifacts.append(
        {
            "kind": "markdown",
            "title": result["title"],
            "raw_content": result["essay"],
            "sanitized_content": sanitized,
            "sanitizer_report": report,
        }
    )

    return {
        "created": True,
        "title": result["title"],
        "validation": result["validation_summary"],
        "note": (
            "The essay is now open in the artifact viewer. Do not repeat it in your reply — "
            "say what you wrote and mention the validation result."
        ),
    }


WRITE_SHIP30_ESSAY = register(
    Tool(
        name="write_ship30_essay",
        description=(
            "Write a ~1,250-word Ship 30 for 30 style essay grounded in the podcast transcripts, "
            "and open it in the artifact viewer. Use when the user asks for an essay, article, "
            "post, newsletter piece, or to 'write up' a topic. Handles its own research — pass "
            "the topic, not the sources."
        ),
        parameters={
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "The essay topic, as a standalone phrase resolved from the conversation.",
                }
            },
            "required": ["topic"],
        },
        handler=_write_ship30_essay,
        read_only=False,
    )
)
