"""Intent routing.

A deliberately boring keyword dispatcher rather than an LLM classifier.

The reasoning: `llama3.2` at 3B is unreliable at tool selection, and every
classification call costs ~10 seconds of prefill on CPU before the real work
starts. A regex that is right 90% of the time and takes 0ms beats a model call
that is right 92% of the time and costs ten seconds — especially when the
failure mode is benign. `chat` is the default, `chat` always retrieves, and a
misrouted essay request just produces a grounded answer instead of an essay.

The trade-off is stated in docs/design.md. If this ran on a frontier model, the
right call would be to let the model choose its own tools; the router exists
because of what is actually running the demo.
"""

from __future__ import annotations

import re
from enum import StrEnum

from app.core.logging import get_logger

log = get_logger(__name__)


class Intent(StrEnum):
    CHAT = "chat"
    ESSAY = "essay"
    ARTIFACT = "artifact"
    SMALLTALK = "smalltalk"


ESSAY_RE = re.compile(
    r"\b(ship\s*30|atomic essay|write (?:me )?(?:an?|the) (?:essay|article|post|piece|blog)"
    r"|turn (?:that|this|it) into an? (?:essay|article|post)"
    r"|write (?:that|this|it) up|essay about|essay on|newsletter (?:piece|post))\b",
    re.IGNORECASE,
)

ARTIFACT_RE = re.compile(
    r"\b(one[- ]pager|onepager|html|web ?page|landing page"
    # "<verb> me a <noun>" — the verb and noun lists are separate so adding
    # either does not require re-spelling the other.
    r"|(?:make|create|build|generate|draft|give|put together)\s+(?:me\s+)?(?:an?|the)\s+"
    r"(?:document|doc|page|table|checklist|summary|brief|cheat\s?sheet|outline|report|memo)"
    r"|as (?:a )?markdown|render (?:it|that|this)|slide|deck|template)\b",
    re.IGNORECASE,
)

SMALLTALK_RE = re.compile(
    r"\A\s*(hi|hey|hello|yo|thanks|thank you|thx|ok|okay|cool|nice|got it|bye|good morning|good evening)"
    r"[\s!.,?]*\Z",
    re.IGNORECASE,
)

# Explicitly asking what the assistant is or can do — answering this from
# transcripts would be nonsense.
META_RE = re.compile(
    r"\b(what can you do|who are you|what are you|how do you work|what do you know"
    r"|which episodes|what episodes|your (?:corpus|sources|knowledge))\b",
    re.IGNORECASE,
)


def classify(message: str) -> Intent:
    text = (message or "").strip()
    if not text:
        return Intent.SMALLTALK

    if SMALLTALK_RE.match(text):
        return Intent.SMALLTALK
    if META_RE.search(text):
        return Intent.SMALLTALK

    # Essay is checked before artifact: "write this up as a one-pager essay"
    # is an essay request that happens to mention a format.
    if ESSAY_RE.search(text):
        return Intent.ESSAY
    if ARTIFACT_RE.search(text):
        return Intent.ARTIFACT

    return Intent.CHAT


def needs_retrieval(intent: Intent) -> bool:
    """Retrieval is not left to the model's discretion for grounded intents."""
    return intent in (Intent.CHAT, Intent.ESSAY, Intent.ARTIFACT)
