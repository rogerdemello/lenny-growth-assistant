"""Health, configuration, and ingestion control.

`/health` is written to answer the question an on-call engineer actually has:
*which part is broken?* It probes the database, the chat provider, the
embedding provider, and the corpus independently, and reports `degraded`
rather than failing — a dead embedding model does not mean the API is down,
and a 500 here would hide which component is at fault.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Header, HTTPException, status

from app.api.schemas import ConfigResponse, HealthResponse
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db import pool as db_pool
from app.db import repository as repo
from app.providers.registry import describe_configuration, get_provider

log = get_logger(__name__)
router = APIRouter(tags=["system"])

VERSION = "0.1.0"


@router.get("/health", response_model=HealthResponse)
async def health() -> Any:
    settings = get_settings()
    components: dict[str, dict[str, Any]] = {}

    db_state = await db_pool.healthcheck()
    components["database"] = {"ok": db_state.pop("ok"), "detail": db_state}

    async def probe(name: str) -> dict[str, Any]:
        try:
            return await asyncio.wait_for(get_provider(name, settings).ping(), timeout=6.0)
        except TimeoutError:
            return {"ok": False, "reason": "probe timed out"}
        except Exception as exc:  # noqa: BLE001 — health must never raise
            return {"ok": False, "reason": str(exc)[:200]}

    chat_state = await probe(settings.llm_provider)
    components["llm_provider"] = {
        "ok": chat_state.pop("ok", False),
        "detail": {"provider": settings.llm_provider, "model": settings.llm_model, **chat_state},
    }

    if settings.embed_provider != settings.llm_provider:
        embed_state = await probe(settings.embed_provider)
        components["embedding_provider"] = {
            "ok": embed_state.pop("ok", False),
            "detail": {"provider": settings.embed_provider, "model": settings.embed_model, **embed_state},
        }

    # A reachable database with an empty corpus is a distinct failure from an
    # unreachable one, and it has a different fix.
    if components["database"]["ok"]:
        chunks = components["database"]["detail"].get("embedded_chunks", 0)
        components["corpus"] = {
            "ok": chunks > 0,
            "detail": {
                "embedded_chunks": chunks,
                "hint": None if chunks else "Run `python -m app.ingest.pipeline` to build the knowledge base.",
            },
        }

    overall = "ok" if all(c["ok"] for c in components.values()) else "degraded"
    return {"status": overall, "version": VERSION, "components": components}


@router.get("/api/config", response_model=ConfigResponse)
async def config() -> Any:
    """What the UI badge renders and what an evaluator checks first."""
    settings = get_settings()
    payload = describe_configuration(settings)

    try:
        from app.agent.runtime import build_runtime

        payload["runtime"] = await build_runtime().describe()
    except Exception as exc:  # noqa: BLE001
        payload["runtime"] = {"error": str(exc)[:200]}

    try:
        payload["corpus"] = await repo.corpus_stats()
    except Exception as exc:  # noqa: BLE001
        payload["corpus"] = {"error": str(exc)[:200]}

    payload["retrieval"] = {
        "top_k": settings.retrieval_top_k,
        "prompt_top_k": settings.prompt_top_k,
        "score_floor": settings.retrieval_score_floor,
    }
    return payload


@router.post("/api/ingest", status_code=status.HTTP_202_ACCEPTED)
async def trigger_ingest(
    limit: int | None = None,
    x_admin_token: str = Header(default=""),
) -> dict[str, Any]:
    """Kick off a re-index.

    Guarded by a shared token, and disabled entirely when that token is unset —
    an unauthenticated endpoint that can saturate the machine embedding 300
    episodes is not something to leave open by default.
    """
    settings = get_settings()
    if not settings.ingest_admin_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Ingestion endpoint is disabled. Set INGEST_ADMIN_TOKEN to enable it.",
        )
    if x_admin_token != settings.ingest_admin_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin token.")

    from app.ingest.pipeline import ingest

    async def _run() -> None:
        try:
            await ingest(settings=settings, limit=limit)
        except Exception:  # noqa: BLE001
            log.exception("ingest.background_failed")

    asyncio.create_task(_run())
    log.info("ingest.triggered", limit=limit)
    return {"status": "started", "limit": limit or settings.ingest_max_episodes}


@router.get("/api/ingest/status")
async def ingest_status() -> dict[str, Any]:
    try:
        async with db_pool.get_pool().acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM ingest_runs ORDER BY started_at DESC LIMIT 1"
            )
        stats = await repo.corpus_stats()
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:200]}

    if row is None:
        return {"last_run": None, "corpus": stats}

    return {
        "last_run": {
            "status": row["status"],
            "source_sha": row["source_sha"],
            "embed_model": row["embed_model"],
            "episodes": row["episodes_count"],
            "chunks": row["chunks_count"],
            "error": row["error"],
            "started_at": row["started_at"].isoformat(),
            "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
        },
        "corpus": stats,
    }
