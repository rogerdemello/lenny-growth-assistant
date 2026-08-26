"""The local agent runtime.

Runs against any OpenAI-compatible endpoint — Ollama for the local demo, Azure
OpenAI for the cloud path. It is the runtime that powers the shipped demo.

The design choice worth defending: **this is an orchestrated pipeline, not a
free-running tool loop.** A frontier model can be handed a tool list and
trusted to sequence its own work. `llama3.2` at 3B cannot — it skips retrieval
when it thinks it already knows the answer, which is precisely the failure the
grounding requirement exists to prevent.

So the sequence is fixed:

    classify -> condense -> retrieve -> (refuse | generate) -> post-process

The model's judgement is used where it is good (writing prose from sources)
and not where it is unreliable (deciding whether to look things up). Tool
*calling* is still offered on top for artifact creation, where a wrong call is
cheap and recoverable.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any

from app.agent import prompts
from app.agent.router import Intent, classify
from app.agent.runtime import AgentEvent, AgentRequest, AgentRuntime
from app.agent.tools import ToolContext, execute, openai_schemas
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import Stage, get_logger
from app.providers.base import Message
from app.providers.registry import chat_stream_with_fallback, get_provider
from app.rag.retrieval import format_sources_block, search

log = get_logger(__name__)

MAX_TOOL_ROUNDS = 3
CITATION_RE = re.compile(r"\[S(\d+)\]")

# Markers of a question that cannot stand on its own: it refers to something
# established in an earlier turn.
FOLLOWUP_RE = re.compile(
    r"^\s*(what about|how about|and |but |what if|why |ok |okay |also )"
    r"|\b(that|those|these|this|it|they|them|he|she|his|her|their|its|the same|instead)\b",
    re.IGNORECASE,
)
_NORMALISE_RE = re.compile(r"[^a-z0-9 ]+")


def _normalise(text: str) -> str:
    return " ".join(_NORMALISE_RE.sub(" ", text.lower()).split())


def _is_echo(condensed: str, original: str) -> bool:
    """Did the model just hand the question back with the punctuation removed?"""
    return _normalise(condensed) == _normalise(original)


def _looks_like_followup(message: str) -> bool:
    """Short, or carrying an unresolved reference to an earlier turn."""
    return len(message.split()) <= 10 or bool(FOLLOWUP_RE.search(message))


class LocalToolLoopRuntime(AgentRuntime):
    name = "local"

    async def describe(self) -> dict[str, Any]:
        settings = get_settings()
        provider = get_provider(settings.llm_provider, settings)
        return {
            "runtime": self.name,
            "provider": provider.info.name,
            "model": provider.info.model,
            "tools": sorted(t["function"]["name"] for t in openai_schemas()),
            "strategy": "deterministic router + mandatory retrieval + optional tool calls",
        }

    async def run(self, request: AgentRequest) -> AsyncIterator[AgentEvent]:  # noqa: C901
        settings = get_settings()
        timings: dict[str, float] = {}
        ctx = ToolContext(session_id=request.session_id, settings=settings)
        provider_used = settings.llm_provider

        try:
            intent = classify(request.message)
            log.info("agent.intent", intent=str(intent), message=request.message[:120])
            yield AgentEvent("stage", {"stage": "routing", "detail": f"Intent: {intent}", "intent": str(intent)})

            # ---- smalltalk: no retrieval, no citations ------------------
            if intent is Intent.SMALLTALK:
                async for event in self._plain_reply(
                    prompts.SMALLTALK_SYSTEM, request, settings, max_tokens=180
                ):
                    yield event
                yield AgentEvent("done", {"intent": str(intent), "citations": [], "provider": provider_used})
                return

            # ---- essay: the skill owns its own retrieval and streaming --
            if intent is Intent.ESSAY:
                async for event in self._run_essay(request, ctx, settings):
                    yield event
                return

            # ---- condense the question for retrieval --------------------
            with Stage("condense", timings):
                query = await self._condense(request, settings)
            if query != request.message:
                log.info("agent.condensed", original=request.message[:80], query=query[:80])
                yield AgentEvent("stage", {"stage": "condensed", "detail": f"Searching for: {query}"})

            # ---- retrieve (always, for grounded intents) ----------------
            yield AgentEvent("stage", {"stage": "retrieving", "detail": "Searching transcripts"})
            with Stage("retrieve", timings):
                result = await search(query, settings=settings)

            if not result.grounded:
                log.info("agent.no_grounding", reason=result.reason, query=query[:80])
                yield AgentEvent("stage", {"stage": "no_grounding", "detail": result.reason or "No relevant passages"})
                async for event in self._plain_reply(
                    prompts.NO_GROUNDING_SYSTEM, request, settings, max_tokens=200
                ):
                    yield event
                yield AgentEvent(
                    "done",
                    {
                        "intent": str(intent),
                        "citations": [],
                        "grounded": False,
                        "reason": result.reason,
                        "provider": provider_used,
                        "timings": timings,
                    },
                )
                return

            citations = result.citations
            yield AgentEvent("citations", {"citations": [c.to_dict() for c in citations]})

            # Retrieve broadly, ground narrowly: the user sees every citation,
            # the model reads only the top few. Prefill is the dominant cost on
            # CPU and it scales with what we put in the prompt, not with what we
            # display.
            prompt_citations = citations[: settings.prompt_top_k]
            sources_block = format_sources_block(prompt_citations)

            tools = openai_schemas(["create_artifact"]) if intent is Intent.ARTIFACT else None

            messages = [Message(role="system", content=prompts.ASSISTANT_SYSTEM)]
            messages += self._history_messages(request, limit=4)
            messages.append(
                Message(
                    role="user",
                    content=prompts.build_grounded_prompt(request.message, sources_block),
                )
            )

            yield AgentEvent("stage", {"stage": "generating", "detail": "Writing answer"})

            answer = ""
            with Stage("generate", timings):
                async for event, text, used in self._tool_loop(messages, tools, ctx, settings):
                    provider_used = used or provider_used
                    answer += text
                    if event is not None:
                        yield event

            # Only cite what the model actually referenced, so the citation
            # list under an answer matches the answer.
            used_labels = {int(n) for n in CITATION_RE.findall(answer)}
            if used_labels:
                referenced = [
                    c.to_dict() for i, c in enumerate(prompt_citations, start=1) if i in used_labels
                ]
                if referenced:
                    yield AgentEvent("citations", {"citations": referenced, "final": True})

            for artifact in ctx.collected_artifacts:
                yield AgentEvent("artifact", artifact)

            yield AgentEvent(
                "done",
                {
                    "intent": str(intent),
                    "grounded": True,
                    "citations": [c.to_dict() for c in prompt_citations],
                    "provider": provider_used,
                    "timings": timings,
                    "retrieval_strategy": result.strategy,
                },
            )

        except AppError as exc:
            log.error("agent.app_error", code=exc.code, error=exc.message)
            yield AgentEvent("error", {"code": exc.code, "message": exc.message, "hint": exc.hint})
        except Exception as exc:  # noqa: BLE001
            log.exception("agent.unexpected_error")
            yield AgentEvent("error", {"code": "internal_error", "message": str(exc)})

    # -- helpers -----------------------------------------------------------

    def _history_messages(self, request: AgentRequest, *, limit: int) -> list[Message]:
        """Recent turns only.

        Full history would be more faithful, but every extra token is prefill
        time on CPU. Four turns is enough for the follow-up behaviour that
        matters, and the condensation step carries the rest of the context into
        the retrieval query.
        """
        out: list[Message] = []
        for item in request.history[-limit:]:
            role = item.get("role")
            if role not in ("user", "assistant"):
                continue
            content = (item.get("content") or "").strip()
            if content:
                out.append(Message(role=role, content=content[:1500]))
        return out

    async def _condense(self, request: AgentRequest, settings) -> str:  # noqa: ANN001
        """Rewrite a follow-up into a standalone search query.

        Skipped when there is no history — the first message is already
        standalone, and skipping saves ~10s of prefill.
        """
        recent = [h for h in request.history if h.get("role") in ("user", "assistant")]
        if not recent:
            return request.message

        history_text = "\n".join(
            f"{h['role']}: {(h.get('content') or '')[:300]}" for h in recent[-4:]
        )
        buf = ""
        try:
            async for delta, _ in chat_stream_with_fallback(
                [
                    Message(role="system", content=prompts.CONDENSE_SYSTEM),
                    Message(
                        role="user",
                        content=f"History:\n{history_text}\n\nLatest message: {request.message}\n\nStandalone query:",
                    ),
                ],
                temperature=0.0,
                max_tokens=60,
                settings=settings,
            ):
                buf += delta.text
        except AppError as exc:
            log.warning("agent.condense_failed", error=str(exc))
            return request.message

        query = buf.strip().strip('"').split("\n")[0].strip()

        # Guard against a model that explains instead of answering.
        if not query or len(query) > 300 or len(query) < 3:
            query = request.message

        # A 3B model frequently "condenses" by echoing the input with the
        # punctuation removed — observed: "What about for PLG?" -> "what about
        # for PLG", which resolves nothing. When that happens, fall back to a
        # deterministic rewrite: concatenate the previous user turn. It is
        # cruder than a real rewrite but it reliably puts the missing subject
        # into the query, which is the only thing retrieval needs.
        if _is_echo(query, request.message) and _looks_like_followup(request.message):
            previous = next(
                (h.get("content", "") for h in reversed(recent) if h.get("role") == "user"), ""
            )
            if previous:
                query = f"{previous.strip()[:200]} {request.message.strip()}"
                log.info("agent.condense_fallback", query=query[:120])

        return query

    async def _plain_reply(self, system: str, request: AgentRequest, settings, *, max_tokens: int):  # noqa: ANN001, ANN201
        messages = [Message(role="system", content=system)]
        messages += self._history_messages(request, limit=2)
        messages.append(Message(role="user", content=request.message))

        async for delta, _used in chat_stream_with_fallback(
            messages, temperature=0.3, max_tokens=max_tokens, settings=settings
        ):
            if delta.text:
                yield AgentEvent("token", {"text": delta.text})

    async def _tool_loop(self, messages: list[Message], tools, ctx: ToolContext, settings):  # noqa: ANN001, ANN201
        """Stream a reply, servicing tool calls, bounded by MAX_TOOL_ROUNDS.

        Yields `(event | None, text, provider_name)`. Text is yielded separately
        from events so the caller can accumulate the answer for citation
        parsing without re-reading the event stream.
        """
        rounds = 0
        while rounds < MAX_TOOL_ROUNDS:
            rounds += 1
            buf = ""
            calls: list[dict[str, Any]] = []
            provider_used = None

            async for delta, used in chat_stream_with_fallback(
                messages, tools=tools, temperature=0.3, max_tokens=1400, settings=settings
            ):
                provider_used = used
                if delta.text:
                    buf += delta.text
                    yield AgentEvent("token", {"text": delta.text}), delta.text, used
                if delta.tool_calls:
                    calls.extend(delta.tool_calls)

            if not calls:
                return

            messages.append(
                Message(role="assistant", content=buf, tool_calls=[c for c in calls if c.get("id")])
            )

            for call in calls:
                fn = call.get("function") or {}
                name = fn.get("name") or ""
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                    log.warning("agent.bad_tool_args", tool=name, raw=(fn.get("arguments") or "")[:200])

                log.info("agent.tool_call", tool=name, round=rounds)
                yield AgentEvent("tool_call", {"name": name, "arguments": args}), "", provider_used

                output = await execute(name, args, ctx)
                messages.append(
                    Message(
                        role="tool",
                        content=json.dumps(output)[:4000],
                        tool_call_id=call.get("id") or name,
                        name=name,
                    )
                )

        log.warning("agent.tool_rounds_exhausted", rounds=rounds)

    async def _run_essay(self, request: AgentRequest, ctx: ToolContext, settings):  # noqa: ANN001, ANN201
        """Drive the Ship 30 skill directly.

        The essay is not routed through tool-calling: on a 3B model, asking it
        to emit a correct tool call and *then* orchestrate five generations is
        a chain of failure points. The router already established intent, so
        the skill is invoked directly and streams its own progress.
        """
        from app.skills.ship30 import generate_essay

        queue: list[AgentEvent] = []

        async def on_event(payload: dict[str, Any]) -> None:
            kind = payload.pop("type", "stage")
            queue.append(AgentEvent(kind, payload))

        topic = self._essay_topic(request)
        yield AgentEvent("stage", {"stage": "essay", "detail": f"Writing a Ship 30 essay on: {topic}"})

        # generate_essay is a coroutine that emits through the callback; drain
        # the queue as it fills so events reach the client while it runs.
        import asyncio

        task = asyncio.create_task(generate_essay(topic, ctx=ctx, on_event=on_event))
        while not task.done() or queue:
            while queue:
                yield queue.pop(0)
            if not task.done():
                await asyncio.sleep(0.05)

        result = await task

        if not result.get("grounded"):
            yield AgentEvent("token", {"text": result.get("message", "I couldn't ground that essay.")})
            yield AgentEvent("done", {"intent": "essay", "grounded": False, "reason": result.get("reason")})
            return

        from app.artifacts.sanitize import sanitize_artifact

        sanitized, report = sanitize_artifact("markdown", result["essay"])
        yield AgentEvent(
            "artifact",
            {
                "kind": "markdown",
                "title": result["title"],
                "raw_content": result["essay"],
                "sanitized_content": sanitized,
                "sanitizer_report": report,
                "validation": result["validation"],
            },
        )
        yield AgentEvent(
            "token",
            {
                "text": (
                    f"I've written **{result['title']}** and opened it in the viewer.\n\n"
                    f"{result['validation_summary']}"
                )
            },
        )
        yield AgentEvent(
            "done",
            {
                "intent": "essay",
                "grounded": True,
                "citations": ctx.collected_citations,
                "provider": result.get("provider"),
                "validation": result["validation"],
            },
        )

    def _essay_topic(self, request: AgentRequest) -> str:
        """Work out what the essay is about.

        "Turn that into an essay" carries no topic of its own — the subject is
        in the previous turn, so fall back to the last user message.
        """
        text = request.message.strip()
        stripped = re.sub(
            r"\b(write|create|make|turn|draft|please|me|a|an|the|this|that|it|into|up|as|"
            r"ship\s*30|atomic|essay|article|post|piece|blog|newsletter|about|on)\b",
            " ",
            text,
            flags=re.IGNORECASE,
        )
        if len(stripped.split()) >= 3:
            return re.sub(r"\s+", " ", stripped).strip(" .,:;!?")

        for item in reversed(request.history):
            if item.get("role") == "user" and (item.get("content") or "").strip() != text:
                return (item["content"] or "").strip()[:300]
        return text
