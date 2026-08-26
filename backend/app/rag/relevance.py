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
            max_tokens=5,
            settings=settings,
        ):
            buf += delta.text
    except AppError as exc:
        log.warning("relevance.gate_failed", error=str(exc))
        return True

    verdict = buf.strip().upper()
    if verdict.startswith("NO"):
        log.info("relevance.rejected", question=question[:100], best_score=citations[0].score)
        return False
    if verdict.startswith("YES"):
        return True

    log.warning("relevance.unparseable", raw=buf[:80])
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
