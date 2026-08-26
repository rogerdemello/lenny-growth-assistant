"""Retrieval over the transcript corpus.

Vector search with a score floor, plus a lexical fallback.

Two design notes worth stating out loud, because they are the difference
between a demo and something a team can trust:

**The score floor is a product decision, not a tuning knob.** Below it,
`search` returns nothing rather than the least-bad match. An assistant that
answers every question from whatever it found is exactly the hallucination
failure mode the brief calls out. Returning nothing is what lets the layer
above say "the transcripts don't cover this".

**Hybrid retrieval is deliberately deferred, not missing.** The `tsv` column
and its GIN index exist, and `lexical_search` uses them as a fallback when
embeddings are unavailable. Fusing the two rankings with RRF is a query
change, not a migration — but tuning fusion weights needs an evaluation set we
did not have time to build, and an untuned hybrid can retrieve *worse* than
plain vector search. See docs/design.md.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.core.config import Settings, get_settings
from app.core.errors import ProviderUnavailableError
from app.core.logging import get_logger
from app.db.pool import get_pool
from app.providers.registry import get_embedding_provider

log = get_logger(__name__)


@dataclass(slots=True)
class Citation:
    """One retrieved passage, in the form the UI and the model both consume."""

    chunk_id: str
    episode_slug: str
    guest: str
    episode_title: str
    speaker: str
    start_seconds: int
    text: str
    score: float
    youtube_url: str | None

    @property
    def timestamp(self) -> str:
        h, rem = divmod(self.start_seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["timestamp"] = self.timestamp
        return data


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{v:.7g}" for v in values) + "]"


def _row_to_citation(row: Any, score: float) -> Citation:
    video_id = row["video_id"]
    start = int(row["start_seconds"])
    if video_id:
        url = f"https://www.youtube.com/watch?v={video_id}&t={start}s"
    else:
        url = row["youtube_url"]
    return Citation(
        chunk_id=str(row["id"]),
        episode_slug=row["slug"],
        guest=row["guest"],
        episode_title=row["title"],
        speaker=row["speaker"] or "",
        start_seconds=start,
        text=row["text"],
        score=round(float(score), 4),
        youtube_url=url,
    )


async def vector_search(query: str, *, top_k: int, settings: Settings) -> list[Citation]:
    embedder = get_embedding_provider(settings)
    vectors = await embedder.embed([query])
    if not vectors:
        return []

    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.id, c.speaker, c.start_seconds, c.text,
                   e.slug, e.guest, e.title, e.video_id, e.youtube_url,
                   1 - (c.embedding <=> $1::vector) AS score
              FROM chunks c
              JOIN episodes e ON e.id = c.episode_id
             WHERE c.embedding IS NOT NULL
             ORDER BY c.embedding <=> $1::vector
             LIMIT $2
            """,
            _vector_literal(vectors[0]),
            top_k,
        )
    return [_row_to_citation(r, r["score"]) for r in rows]


async def lexical_search(query: str, *, top_k: int) -> list[Citation]:
    """Postgres full-text search — the fallback when embeddings are unusable.

    `ts_rank_cd` is not on the same scale as cosine similarity, so results from
    here are marked with their own scores and the caller must not compare them
    against the vector score floor.
    """
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.id, c.speaker, c.start_seconds, c.text,
                   e.slug, e.guest, e.title, e.video_id, e.youtube_url,
                   ts_rank_cd(c.tsv, plainto_tsquery('english', $1)) AS score
              FROM chunks c
              JOIN episodes e ON e.id = c.episode_id
             WHERE c.tsv @@ plainto_tsquery('english', $1)
             ORDER BY score DESC
             LIMIT $2
            """,
            query,
            top_k,
        )
    return [_row_to_citation(r, r["score"]) for r in rows]


@dataclass(slots=True)
class RetrievalResult:
    citations: list[Citation]
    grounded: bool
    strategy: str
    best_score: float = 0.0
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "citations": [c.to_dict() for c in self.citations],
            "grounded": self.grounded,
            "strategy": self.strategy,
            "best_score": self.best_score,
            "reason": self.reason,
        }


async def search(
    query: str,
    *,
    top_k: int | None = None,
    settings: Settings | None = None,
) -> RetrievalResult:
    """Retrieve passages for a query, or report honestly that we cannot."""
    settings = settings or get_settings()
    top_k = top_k or settings.retrieval_top_k
    query = (query or "").strip()

    if not query:
        return RetrievalResult([], grounded=False, strategy="none", reason="empty query")

    strategy = "vector"
    try:
        citations = await vector_search(query, top_k=top_k, settings=settings)
    except ProviderUnavailableError as exc:
        # The embedding model is down but the corpus is still searchable
        # lexically. Degrading to keyword search beats refusing to answer.
        log.warning("retrieval.vector_unavailable", error=str(exc))
        strategy = "lexical_fallback"
        citations = await lexical_search(query, top_k=top_k)
        if not citations:
            return RetrievalResult(
                [], grounded=False, strategy=strategy, reason="embeddings unavailable and no lexical match"
            )
        return RetrievalResult(citations, grounded=True, strategy=strategy, best_score=citations[0].score)

    if not citations:
        return RetrievalResult([], grounded=False, strategy=strategy, reason="no chunks in corpus")

    best = citations[0].score
    kept = [c for c in citations if c.score >= settings.retrieval_score_floor]

    if not kept:
        log.info(
            "retrieval.below_floor",
            best_score=best,
            floor=settings.retrieval_score_floor,
            query=query[:120],
        )
        return RetrievalResult(
            [],
            grounded=False,
            strategy=strategy,
            best_score=best,
            reason=f"best match scored {best:.3f}, below the {settings.retrieval_score_floor} floor",
        )

    log.info("retrieval.hit", results=len(kept), best_score=best, strategy=strategy)
    return RetrievalResult(kept, grounded=True, strategy=strategy, best_score=best)


def format_sources_block(citations: list[Citation]) -> str:
    """Render passages for the prompt with stable [S1]-style labels.

    The labels are what the model cites and what we parse back out, so they
    must be short, unambiguous, and identical in both directions.
    """
    blocks = []
    for i, c in enumerate(citations, start=1):
        header = f"[S{i}] {c.guest} — \"{c.episode_title}\" at {c.timestamp}"
        blocks.append(f"{header}\n{c.text}")
    return "\n\n---\n\n".join(blocks)
