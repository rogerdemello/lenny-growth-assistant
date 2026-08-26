"""Second-stage relevance gate.

**Why this exists.** The original design assumed a cosine score floor was
enough to enforce the grounding guarantee. Calibrating against the real corpus
proved it is not:

    IN-DOMAIN   0.5575  how do I run continuous product discovery
    OUT-DOMAIN  0.6216  how does photosynthesis work

The out-of-domain question scored *higher* than a legitimate one. The failure
is structural, not a tuning problem: `nomic-embed-text` partly matches on
question shape ("how does X work") rather than topic, so a well-formed question
about anything lands near well-formed questions about product management. No
single threshold can separate those two sets — verified across a 20-question
probe with a separation gap of **-0.064**.

So retrieval keeps a low floor to discard obvious junk cheaply, and anything
that survives but is not confidently relevant gets one small LLM call asking
the only question that actually matters:

    Do these passages contain information that answers this question?

**Cost control.** The gate is skipped entirely above `retrieval_confident_score`,
so the common case — a clearly in-domain question — pays nothing. It only runs
in the ambiguous band, where being wrong is expensive.

**Failure direction.** If the gate itself errors or returns something
unparseable, it fails *open* — the passages are kept. A retrieval layer that
refuses whenever the model hiccups would be worse than one that occasionally
passes weak sources, because the answer prompt still instructs the model to say
when its sources do not cover the question. The gate is a second line of
defence, not the only one.
"""

from __future__ import annotations

import re

from app.core.config import Settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.providers.base import Message
from app.providers.registry import chat_stream_with_fallback
from app.rag.retrieval import Citation

log = get_logger(__name__)

# The gate asks about the QUESTION's topic, not about whether the passages
# answer it.
#
# The first version asked "do these passages answer this question?" That is the
# question you actually care about, but it is too hard for a 3B model: it
# rejected 4 of 10 legitimate questions, including one scoring 0.70, because
# transcript passages are rambling and conversational and rarely look like a
# tidy answer to anything.
#
# Topic classification is a much easier judgement, and it targets the actual
# failure mode — the leaks were all out-of-DOMAIN questions (photosynthesis,
# football, sourdough), not in-domain questions with weak passages. Weak
# passages are already handled: the answer prompt instructs the model to say
# when its sources do not cover the question.
GATE_SYSTEM = """You classify whether a question is about business, startups, product management, or growth.

Answer with exactly one word: YES or NO.

YES — product management, growth, marketing, pricing, positioning, strategy, fundraising, hiring, leadership, company building, careers in tech.
NO — anything else: science, sport, cooking, weather, geography, personal finance, general programming or infrastructure, health, entertainment.

Examples:
Q: how do I run continuous product discovery -> YES
Q: what actually drives retention -> YES
Q: how should a PM prioritise a roadmap -> YES
Q: when should I hire a growth team -> YES
Q: how do I position against an incumbent -> YES
Q: how does photosynthesis work -> NO
Q: best kubernetes ingress controller -> NO
Q: what is the offside rule in football -> NO
Q: how do I bake sourdough bread -> NO
Q: what is the capital of France -> NO"""

# The verdict is one word, so a tiny ceiling looks like free money. It is not.
#
# Reasoning models (gpt-oss, nemotron, o-series) emit a thinking trace before any
# `content` at all. At max_tokens=5 the whole budget is spent thinking and the
# response carries an empty `content` — so the gate read "", fell through to the
# unparseable branch, and failed open. Measured against `openai/gpt-oss-120b`
# with "how does photosynthesis work":
#
#     max_tokens=5     ""     -> gate fails open, question leaks through
#     max_tokens=20    ""     -> gate fails open, question leaks through
#     max_tokens=100   "NO"   -> correctly refused
#
# The model knew the answer at every ceiling; below 100 it never got to say it.
# That turned the grounding guarantee off entirely for a whole class of models,
# silently, with only a warning log to show for it.
#
# A ceiling is not a cost. A non-reasoning model still stops after one token, so
# this is free for `llama3.2`; a reasoning model now pays for its trace, which is
# the actual price of choosing one.
GATE_MAX_TOKENS = 256


def _parse_verdict(raw: str) -> bool | None:
    """YES / NO / None if the model said neither.

    Reasoning models put the verdict last, plain ones put it first, and either
    may wrap it in punctuation or markdown. Checking the last standalone token
    handles all three without inferring a verdict from prose that merely
    contains the word.
    """
    words = re.findall(r"[A-Z]+", raw.upper())
    for word in reversed(words):
        if word in ("YES", "NO"):
            return word == "YES"
    return None


def _build_prompt(question: str, citations: list[Citation], max_chars: int = 400) -> str:  # noqa: ARG001
    return f"Q: {question} ->"


async def is_relevant(
    question: str,
    citations: list[Citation],
    *,
    settings: Settings,
) -> bool:
    """One cheap LLM call. Fails open — see the module docstring."""
    if not citations:
        return False

    buf = ""
    try:
        async for delta, _ in chat_stream_with_fallback(
            [
                Message(role="system", content=GATE_SYSTEM),
                Message(role="user", content=_build_prompt(question, citations)),
            ],
            temperature=0.0,
            max_tokens=GATE_MAX_TOKENS,
            settings=settings,
        ):
            buf += delta.text
    except AppError as exc:
        log.warning("relevance.gate_failed", error=str(exc))
        return True

    verdict = _parse_verdict(buf)
    if verdict is False:
        log.info("relevance.rejected", question=question[:100], best_score=citations[0].score)
        return False
    if verdict is True:
        return True

    # Still fails open, deliberately — see the module docstring. But an empty
    # `raw` here means the model produced no content at all, which is the
    # reasoning-model symptom above rather than a one-off hiccup, so say which.
    log.warning(
        "relevance.unparseable",
        raw=buf[:80],
        empty=not buf.strip(),
        hint=(
            "The model returned no content. If it is a reasoning model, its trace "
            "consumed the token budget; raise GATE_MAX_TOKENS."
        )
        if not buf.strip()
        else None,
    )
    return True


async def gate(
    question: str,
    citations: list[Citation],
    best_score: float,
    *,
    settings: Settings,
) -> tuple[bool, str]:
    """Apply the gate only where it earns its latency.

    Returns `(keep, reason)`.
    """
    if best_score >= settings.retrieval_confident_score:
        return True, "confident"

    relevant = await is_relevant(question, citations, settings=settings)
    if relevant:
        return True, "verified"
    return False, (
        f"the best passage scored {best_score:.3f} and a topic check judged the question "
        f"to be outside what the podcast archive covers"
    )
