"""Objective checks on a Ship 30 essay.

The point of encoding a writing style as a skill rather than a prompt is that
you can then *check* the output against it. Without this module the skill is
still just a prompt with better formatting — the model claims it followed the
rules and nobody verifies.

Every check here maps to a rule in `.claude/skills/ship30/SKILL.md`. The result
is shown to the user next to the essay, so a failure is visible rather than
silently shipped. Nothing here rejects an essay: a 1,090-word draft is not
worthless, and on a small local model a hard gate would mostly produce retries.
It reports, honestly, what the draft got right and wrong.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

WORD_MIN, WORD_MAX = 1_150, 1_350
SECTION_MIN, SECTION_MAX = 5, 6
MIN_CITATIONS = 3
MAX_SENTENCES_PER_PARAGRAPH = 5

H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
H2_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)
CITATION_RE = re.compile(r"\[S(\d+)\]")
BULLET_RE = re.compile(r"^\s*[-*+]\s+\S", re.MULTILINE)
SENTENCE_RE = re.compile(r"[.!?](?:\s|$)")
CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)

# Phrases that mark generated prose. Their presence is not fatal, but the skill
# explicitly forbids them, so the check has to notice.
BANNED_PHRASES = [
    "delve", "tapestry", "in the ever-evolving", "it's worth noting",
    "in today's fast-paced", "in today's fast-moving", "navigate the complexities",
    "unlock the power", "game-changer", "at the end of the day, it",
]


@dataclass(slots=True)
class Check:
    name: str
    passed: bool
    detail: str


@dataclass(slots=True)
class ValidationReport:
    word_count: int
    section_count: int
    citation_count: int
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def score(self) -> str:
        ok = sum(1 for c in self.checks if c.passed)
        return f"{ok}/{len(self.checks)}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "score": self.score,
            "word_count": self.word_count,
            "section_count": self.section_count,
            "citation_count": self.citation_count,
            "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in self.checks],
        }

    def summary_line(self) -> str:
        state = "meets the Ship 30 spec" if self.passed else "deviates from the Ship 30 spec"
        return (
            f"{self.word_count} words · {self.section_count} sections · "
            f"{self.citation_count} citations — {state} ({self.score} checks)"
        )


def _strip_code(text: str) -> str:
    return CODE_FENCE_RE.sub("", text)


def _paragraphs(body: str) -> list[str]:
    """Prose paragraphs only — headings and list blocks follow different rules."""
    out = []
    for block in re.split(r"\n\s*\n", body):
        block = block.strip()
        if not block or block.startswith("#") or BULLET_RE.match(block) or block.startswith(">"):
            continue
        out.append(block)
    return out


def _count_sentences(paragraph: str) -> int:
    return max(1, len(SENTENCE_RE.findall(paragraph.strip())))


def _word_count(text: str) -> int:
    prose = _strip_code(text)
    prose = re.sub(r"^#+\s*", "", prose, flags=re.MULTILINE)
    prose = CITATION_RE.sub("", prose)
    return len([w for w in re.split(r"\s+", prose) if w.strip()])


def validate(essay: str) -> ValidationReport:
    body = essay.strip()

    # The Sources block is reference material, not prose; counting it would let
    # a long citation list pad the essay to length.
    prose_body = re.split(r"^##\s*Sources\b", body, maxsplit=1, flags=re.MULTILINE | re.IGNORECASE)[0]

    words = _word_count(prose_body)
    sections = H2_RE.findall(prose_body)
    section_count = len(sections)
    citation_labels = set(CITATION_RE.findall(body))
    citation_count = len(citation_labels)

    checks: list[Check] = []

    checks.append(
        Check(
            "word count",
            WORD_MIN <= words <= WORD_MAX,
            f"{words} words (target {WORD_MIN}–{WORD_MAX})",
        )
    )
    checks.append(
        Check(
            "section count",
            SECTION_MIN <= section_count <= SECTION_MAX,
            f"{section_count} H2 sections (target {SECTION_MIN}–{SECTION_MAX})",
        )
    )
    checks.append(
        Check(
            "has a title",
            bool(H1_RE.search(body)),
            "H1 headline present" if H1_RE.search(body) else "no H1 headline",
        )
    )

    # The hook: the first prose paragraph before any H2 must be one sentence.
    intro = prose_body.split("\n##")[0]
    intro_paras = _paragraphs(re.sub(r"^#\s+.+$", "", intro, count=1, flags=re.MULTILINE))
    if intro_paras:
        hook_sentences = _count_sentences(intro_paras[0])
        checks.append(
            Check(
                "single-sentence hook",
                hook_sentences == 1,
                f"opening paragraph is {hook_sentences} sentence(s)",
            )
        )
    else:
        checks.append(Check("single-sentence hook", False, "no opening paragraph found"))

    checks.append(
        Check(
            "citations",
            citation_count >= MIN_CITATIONS,
            f"{citation_count} distinct [S#] citations (minimum {MIN_CITATIONS})",
        )
    )

    bullets = len(BULLET_RE.findall(prose_body))
    checks.append(Check("bulleted list", bullets > 0, f"{bullets} bullet item(s)"))

    long_paras = [p for p in _paragraphs(prose_body) if _count_sentences(p) > MAX_SENTENCES_PER_PARAGRAPH]
    checks.append(
        Check(
            "paragraph length",
            not long_paras,
            "all paragraphs ≤5 sentences"
            if not long_paras
            else f"{len(long_paras)} paragraph(s) exceed {MAX_SENTENCES_PER_PARAGRAPH} sentences",
        )
    )

    takeaway = bool(
        re.search(r"^##\s*.*(takeaway|do this|start here|what to do|try this)", prose_body, re.MULTILINE | re.IGNORECASE)
    )
    checks.append(Check("takeaway section", takeaway, "present" if takeaway else "no takeaway-style section found"))

    lowered = body.lower()
    found_banned = [p for p in BANNED_PHRASES if p in lowered]
    checks.append(
        Check(
            "voice",
            not found_banned,
            "no banned phrases" if not found_banned else f"found: {', '.join(found_banned)}",
        )
    )

    return ValidationReport(
        word_count=words,
        section_count=section_count,
        citation_count=citation_count,
        checks=checks,
    )
