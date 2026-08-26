"""The ingestion pipeline: fetch, select, parse, chunk, embed, store.

    python -m app.ingest.pipeline              # the configured subset
    python -m app.ingest.pipeline --all        # every qualifying episode
    python -m app.ingest.pipeline --limit 5    # a quick smoke run
    python -m app.ingest.pipeline --dry-run    # parse and chunk, touch nothing

Three properties this is built for, because the brief asks how transcripts are
"loaded, chunked or selected, indexed, refreshed, and traced back to source":

  * **Refreshable.** Every episode carries a content hash. Re-running skips
    unchanged episodes, so a refresh costs only what actually changed.
  * **Resumable.** Embeddings are written per batch, not at the end. An
    interrupted run picks up where it stopped instead of starting over — which
    matters when a full CPU-bound run takes over an hour.
  * **Traceable.** Every run records the upstream commit SHA and writes
    INGESTED.md, so what the assistant knows is a readable artifact.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from dataclasses import dataclass
from pathlib import Path

import asyncpg

from app.core.config import REPO_ROOT, Settings, get_settings
from app.core.errors import IngestionError
from app.core.logging import Stage, configure_logging, get_logger
from app.ingest.chunker import chunk_episode
from app.ingest.parser import Episode, parse_episode
from app.ingest.source import CorpusPolicy, RawTranscript, download_transcripts, resolve_commit_sha
from app.providers.registry import get_embedding_provider

log = get_logger(__name__)

EMBED_BATCH = 32
INSERT_BATCH = 200


@dataclass
class IngestStats:
    considered: int = 0
    selected: int = 0
    skipped_unchanged: int = 0
    episodes_written: int = 0
    chunks_written: int = 0
    chunks_embedded: int = 0
    errors: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


def select_episodes(
    episodes: list[Episode],
    policy: CorpusPolicy,
    *,
    min_duration: int,
    max_episodes: int | None,
) -> list[Episode]:
    """Apply the three-pass selection documented in corpus.yml.

    Pinned episodes bypass the duration filter deliberately: if a pin is short,
    that was a considered choice, and silently dropping it would make the
    corpus file lie about what got ingested.
    """
    by_slug = {e.slug: e for e in episodes}

    eligible = [
        e
        for e in episodes
        if e.slug not in policy.exclude
        and e.turns
        and (e.duration_seconds is None or e.duration_seconds >= min_duration)
    ]

    selected: list[Episode] = []
    seen: set[str] = set()

    for slug in policy.pinned:
        episode = by_slug.get(slug)
        if episode is None:
            log.warning("ingest.pin_not_found", slug=slug)
            continue
        if not episode.turns:
            log.warning("ingest.pin_empty", slug=slug)
            continue
        selected.append(episode)
        seen.add(slug)

    # Most recent first; episodes with no publish date sort last.
    remainder = sorted(
        (e for e in eligible if e.slug not in seen),
        key=lambda e: (e.publish_date is not None, e.publish_date),
        reverse=True,
    )

    if max_episodes is None:
        selected.extend(remainder)
    else:
        room = max(0, max_episodes - len(selected))
        selected.extend(remainder[:room])

    return selected


async def _existing_hashes(conn: asyncpg.Connection) -> dict[str, str]:
    rows = await conn.fetch("SELECT slug, content_hash FROM episodes")
    return {r["slug"]: r["content_hash"] for r in rows}


async def _upsert_episode(conn: asyncpg.Connection, episode: Episode, source_sha: str | None) -> str:
    """Insert or replace an episode, returning its id.

    Replacing cascades to `chunks`, which is intentional: a changed transcript
    invalidates every chunk and embedding derived from it.
    """
    return await conn.fetchval(
        """
        INSERT INTO episodes (
            slug, guest, title, youtube_url, video_id, publish_date,
            duration_seconds, view_count, keywords, content_hash, source_sha, ingested_at
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11, now())
        ON CONFLICT (slug) DO UPDATE SET
            guest = EXCLUDED.guest,
            title = EXCLUDED.title,
            youtube_url = EXCLUDED.youtube_url,
            video_id = EXCLUDED.video_id,
            publish_date = EXCLUDED.publish_date,
            duration_seconds = EXCLUDED.duration_seconds,
            view_count = EXCLUDED.view_count,
            keywords = EXCLUDED.keywords,
            content_hash = EXCLUDED.content_hash,
            source_sha = EXCLUDED.source_sha,
            ingested_at = now()
        RETURNING id
        """,
        episode.slug,
        episode.guest,
        episode.title,
        episode.youtube_url,
        episode.video_id,
        episode.publish_date,
        episode.duration_seconds,
        episode.view_count,
        episode.keywords,
        episode.content_hash,
        source_sha,
    )


def _vector_literal(values: list[float]) -> str:
    """pgvector accepts a bracketed string; asyncpg has no native codec for it."""
    return "[" + ",".join(f"{v:.7g}" for v in values) + "]"


async def ingest(
    *,
    settings: Settings,
    limit: int | None = None,
    ingest_all: bool = False,
    dry_run: bool = False,
    force: bool = False,
) -> IngestStats:
    stats = IngestStats()
    policy = CorpusPolicy.load(settings.corpus_file)
    timings: dict[str, float] = {}

    with Stage("download", timings):
        raw: list[RawTranscript] = await download_transcripts(
            settings.transcripts_repo, settings.transcripts_ref, cache_dir=REPO_ROOT / "data" / "raw"
        )
    source_sha = await resolve_commit_sha(settings.transcripts_repo, settings.transcripts_ref)

    with Stage("parse", timings):
        parsed = [parse_episode(item.slug, item.text) for item in raw]
    stats.considered = len(parsed)

    max_episodes = None if ingest_all else (limit if limit is not None else settings.ingest_max_episodes)
    selected = select_episodes(
        parsed,
        policy,
        min_duration=settings.ingest_min_duration_seconds,
        max_episodes=max_episodes,
    )
    stats.selected = len(selected)
    log.info(
        "ingest.selected",
        considered=stats.considered,
        selected=stats.selected,
        pinned=len(policy.pinned),
        max_episodes=max_episodes,
    )

    if dry_run:
        total_chunks = 0
        for episode in selected:
            chunks = chunk_episode(
                episode,
                target_tokens=settings.chunk_target_tokens,
                overlap_tokens=settings.chunk_overlap_tokens,
            )
            total_chunks += len(chunks)
            log.info("ingest.dry_run_episode", slug=episode.slug, turns=len(episode.turns), chunks=len(chunks))
        stats.chunks_written = total_chunks
        log.info("ingest.dry_run_done", episodes=len(selected), chunks=total_chunks)
        return stats

    from app.db.pool import _normalize_dsn

    conn = await asyncpg.connect(_normalize_dsn(settings.database_url))
    embedder = get_embedding_provider(settings)
    run_id = None

    try:
        run_id = await conn.fetchval(
            "INSERT INTO ingest_runs (source_sha, embed_model, status) VALUES ($1,$2,'running') RETURNING id",
            source_sha,
            settings.embed_model,
        )

        known = {} if force else await _existing_hashes(conn)

        for index, episode in enumerate(selected, start=1):
            if known.get(episode.slug) == episode.content_hash:
                stats.skipped_unchanged += 1
                log.debug("ingest.skip_unchanged", slug=episode.slug)
                continue

            started = time.perf_counter()
            chunks = chunk_episode(
                episode,
                target_tokens=settings.chunk_target_tokens,
                overlap_tokens=settings.chunk_overlap_tokens,
            )
            if not chunks:
                log.warning("ingest.no_chunks", slug=episode.slug)
                continue

            try:
                episode_id = await _upsert_episode(conn, episode, source_sha)
                await conn.execute("DELETE FROM chunks WHERE episode_id = $1", episode_id)

                # Embed and insert in batches so an interruption leaves a
                # partially-but-correctly ingested episode rather than nothing.
                for start in range(0, len(chunks), EMBED_BATCH):
                    batch = chunks[start : start + EMBED_BATCH]
                    vectors = await embedder.embed([c.text for c in batch])
                    if len(vectors) != len(batch):
                        raise IngestionError(
                            f"Embedder returned {len(vectors)} vectors for {len(batch)} chunks."
                        )
                    if vectors and len(vectors[0]) != settings.embed_dim:
                        raise IngestionError(
                            f"Embedding dimension mismatch: model returned {len(vectors[0])}, "
                            f"schema expects {settings.embed_dim}.",
                            hint="Set EMBED_DIM to match the model and re-run migrations, or use a different EMBED_MODEL.",
                        )

                    await conn.executemany(
                        """
                        INSERT INTO chunks (episode_id, ord, speaker, start_seconds, end_seconds,
                                            text, token_count, embedding)
                        VALUES ($1,$2,$3,$4,$5,$6,$7,$8::vector)
                        ON CONFLICT (episode_id, ord) DO UPDATE SET
                            text = EXCLUDED.text, embedding = EXCLUDED.embedding
                        """,
                        [
                            (
                                episode_id,
                                c.ord,
                                c.speaker,
                                c.start_seconds,
                                c.end_seconds,
                                c.text,
                                c.token_count,
                                _vector_literal(vec),
                            )
                            for c, vec in zip(batch, vectors, strict=True)
                        ],
                    )
                    stats.chunks_embedded += len(batch)

                stats.episodes_written += 1
                stats.chunks_written += len(chunks)
                elapsed = time.perf_counter() - started
                log.info(
                    "ingest.episode_done",
                    slug=episode.slug,
                    progress=f"{index}/{len(selected)}",
                    chunks=len(chunks),
                    seconds=round(elapsed, 1),
                    chunks_per_sec=round(len(chunks) / elapsed, 2) if elapsed else None,
                )

            except Exception as exc:  # noqa: BLE001 — one bad episode must not end the run
                stats.errors.append(f"{episode.slug}: {exc}")
                log.error("ingest.episode_failed", slug=episode.slug, error=str(exc))

        await conn.execute(
            """
            UPDATE ingest_runs
               SET status = $2, episodes_count = $3, chunks_count = $4, error = $5, finished_at = now()
             WHERE id = $1
            """,
            run_id,
            "completed" if not stats.errors else "failed",
            stats.episodes_written,
            stats.chunks_written,
            "; ".join(stats.errors[:5]) or None,
        )

        await write_manifest(conn)

    except Exception as exc:
        if run_id is not None:
            await conn.execute(
                "UPDATE ingest_runs SET status='failed', error=$2, finished_at=now() WHERE id=$1",
                run_id,
                str(exc)[:1000],
            )
        raise
    finally:
        await conn.close()

    log.info(
        "ingest.complete",
        episodes=stats.episodes_written,
        chunks=stats.chunks_written,
        skipped=stats.skipped_unchanged,
        errors=len(stats.errors),
    )
    return stats


async def write_manifest(conn: asyncpg.Connection, path: Path | None = None) -> Path:
    """Write INGESTED.md — the human-readable record of the knowledge base."""
    path = path or (REPO_ROOT / "INGESTED.md")
    rows = await conn.fetch(
        """
        SELECT e.slug, e.guest, e.title, e.publish_date, e.duration_seconds,
               e.content_hash, e.source_sha, e.ingested_at,
               count(c.id) AS chunk_count
          FROM episodes e
          LEFT JOIN chunks c ON c.episode_id = e.id
         GROUP BY e.id
         ORDER BY e.guest, e.slug
        """
    )
    total_chunks = sum(r["chunk_count"] for r in rows)
    shas = {r["source_sha"] for r in rows if r["source_sha"]}

    lines = [
        "# Ingested corpus",
        "",
        "Generated by `python -m app.ingest.pipeline`. This is the record of what",
        "the assistant actually knows — if a question is not covered here, the",
        "assistant should say so rather than answer it.",
        "",
        f"- **Episodes:** {len(rows)}",
        f"- **Chunks:** {total_chunks}",
        f"- **Source revision:** {', '.join(sorted(shas)) if shas else 'unknown'}",
        "",
        "Transcripts are sourced from [ChatPRD/lennys-podcast-transcripts]"
        "(https://github.com/ChatPRD/lennys-podcast-transcripts) and are not redistributed in this repository.",
        "",
        "| Guest | Episode | Published | Duration | Chunks | Hash |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        duration = f"{int(r['duration_seconds'] // 60)} min" if r["duration_seconds"] else "—"
        title = (r["title"] or "").replace("|", "\\|")
        lines.append(
            f"| {r['guest'] or '—'} | {title[:90]} | {r['publish_date'] or '—'} "
            f"| {duration} | {r['chunk_count']} | `{(r['content_hash'] or '')[:12]}` |"
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info("ingest.manifest_written", path=str(path), episodes=len(rows))
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Lenny's Podcast transcripts.")
    parser.add_argument("--all", action="store_true", help="Ingest every qualifying episode (slow: 1-2h on CPU).")
    parser.add_argument("--limit", type=int, default=None, help="Override INGEST_MAX_EPISODES.")
    parser.add_argument("--dry-run", action="store_true", help="Parse and chunk only; write nothing.")
    parser.add_argument("--force", action="store_true", help="Re-embed even if the content hash is unchanged.")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)

    try:
        stats = asyncio.run(
            ingest(
                settings=settings,
                limit=args.limit,
                ingest_all=args.all,
                dry_run=args.dry_run,
                force=args.force,
            )
        )
    except IngestionError as exc:
        log.error("ingest.failed", error=exc.message, hint=exc.hint)
        raise SystemExit(1) from exc

    print(
        f"\nEpisodes written: {stats.episodes_written}  "
        f"chunks: {stats.chunks_written}  skipped (unchanged): {stats.skipped_unchanged}"
    )
    if stats.errors:
        print(f"Errors on {len(stats.errors)} episode(s):")
        for err in stats.errors[:10]:
            print(f"  - {err}")


if __name__ == "__main__":
    main()
