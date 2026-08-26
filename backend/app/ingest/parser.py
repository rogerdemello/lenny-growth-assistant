"""Parse one `transcript.md` from the Lenny's Podcast transcripts repo.

The files look like this:

    ---
    guest: Ada Chen Rekhi
    title: Feeling stuck? Here's how to know when it's time to leave your job | Ada Chen
      Rekhi
    youtube_url: https://www.youtube.com/watch?v=l-T8sNRcWQk
    video_id: l-T8sNRcWQk
    publish_date: 2023-04-21
    duration_seconds: 230.0
    ...
    ---

    # <title>

    ## Transcript

    Ada Chen Rekhi (00:00:00):
    It's a terrible outcome to wake up one day and be late career...

    Lenny (00:00:36):
    Welcome to Lenny's Podcast, where I interview...

Two things about the real data that the naive parser gets wrong:

  * The frontmatter is PyYAML-dumped, so `title` wraps across lines and
    apostrophes are doubled. `yaml.safe_load` handles both; hand-rolling a
    `key: value` split does not.
  * The corpus mixes full interviews with 4-minute YouTube shorts. The shorts
    are citation-poor and drag down retrieval precision, so callers filter on
    `duration_seconds`.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import yaml

from app.core.logging import get_logger

log = get_logger(__name__)

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# "Speaker Name (00:12:34):" on its own line. The speaker part is non-greedy so
# a name containing parentheses does not swallow the timestamp.
TURN_RE = re.compile(r"^(?P<speaker>.{1,80}?)\s*\((?P<ts>\d{1,2}:\d{2}:\d{2})\):\s*$", re.MULTILINE)

# Transcription artefacts like "[inaudible 00:00:42]" add no meaning and waste
# tokens in both the embedding and the prompt.
INAUDIBLE_RE = re.compile(r"\[(?:inaudible|crosstalk|silence)[^\]]*\]", re.IGNORECASE)


@dataclass(slots=True)
class Turn:
    speaker: str
    start_seconds: int
    text: str


@dataclass(slots=True)
class Episode:
    slug: str
    guest: str
    title: str
    youtube_url: str | None
    video_id: str | None
    publish_date: date | None
    duration_seconds: float | None
    view_count: int | None
    keywords: list[str] = field(default_factory=list)
    turns: list[Turn] = field(default_factory=list)
    content_hash: str = ""

    @property
    def is_short(self) -> bool:
        """YouTube clips rather than full interviews."""
        return self.duration_seconds is not None and self.duration_seconds < 1800

    def youtube_link_at(self, seconds: int) -> str | None:
        if not self.video_id:
            return self.youtube_url
        return f"https://www.youtube.com/watch?v={self.video_id}&t={max(seconds, 0)}s"


def timestamp_to_seconds(ts: str) -> int:
    parts = [int(p) for p in ts.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, s = parts[-3], parts[-2], parts[-1]
    return h * 3600 + m * 60 + s


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def parse_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    """Split YAML frontmatter from the body. Returns ({}, raw) if absent."""
    match = FRONTMATTER_RE.match(raw)
    if not match:
        return {}, raw
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        log.warning("parser.bad_frontmatter", error=str(exc))
        return {}, raw[match.end() :]
    if not isinstance(meta, dict):
        return {}, raw[match.end() :]
    return meta, raw[match.end() :]


def parse_turns(body: str) -> list[Turn]:
    """Extract speaker turns.

    Text between one `Speaker (ts):` header and the next belongs to that turn.
    Anything before the first header (the `# Title` / `## Transcript` preamble)
    is dropped.
    """
    turns: list[Turn] = []
    matches = list(TURN_RE.finditer(body))

    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        text = _clean(body[start:end])
        if not text:
            continue
        turns.append(
            Turn(
                speaker=match.group("speaker").strip(),
                start_seconds=timestamp_to_seconds(match.group("ts")),
                text=text,
            )
        )
    return turns


def _clean(text: str) -> str:
    text = INAUDIBLE_RE.sub("", text)
    # Collapse the blank lines left behind by the turn split, but keep
    # paragraph structure inside a single turn.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_episode(slug: str, raw: str) -> Episode:
    meta, body = parse_frontmatter(raw)
    turns = parse_turns(body)

    keywords = meta.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [keywords]

    return Episode(
        slug=slug,
        guest=str(meta.get("guest") or "").strip(),
        # Frontmatter titles wrap across lines; PyYAML rejoins them with a
        # newline, which would otherwise end up in the UI.
        title=" ".join(str(meta.get("title") or "").split()),
        youtube_url=(str(meta["youtube_url"]).strip() if meta.get("youtube_url") else None),
        video_id=(str(meta["video_id"]).strip() if meta.get("video_id") else None),
        publish_date=_coerce_date(meta.get("publish_date")),
        duration_seconds=_coerce_float(meta.get("duration_seconds")),
        view_count=_coerce_int(meta.get("view_count")),
        keywords=[str(k).strip() for k in keywords if str(k).strip()],
        turns=turns,
        content_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32],
    )
