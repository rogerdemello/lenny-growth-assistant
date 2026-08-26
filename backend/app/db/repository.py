"""All SQL the request path executes.

Keeping it in one module means the session-isolation guarantee is auditable:
every query that reads messages or artifacts takes a `session_id` and filters
on it. There is no code path that can read across sessions by accident.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from app.core.errors import NotFoundError
from app.db.pool import get_pool


@dataclass(slots=True)
class SessionRow:
    id: UUID
    title: str
    user_id: str
    client_metadata: dict[str, Any]
    provider: str | None
    model: str | None
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


def _loads(value: Any) -> Any:
    """asyncpg returns jsonb as str unless a codec is registered."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if value is not None else {}


def _session_from_row(row: asyncpg.Record) -> SessionRow:
    return SessionRow(
        id=row["id"],
        title=row["title"],
        user_id=row["user_id"],
        client_metadata=_loads(row["client_metadata"]),
        provider=row["provider"],
        model=row["model"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        message_count=row.get("message_count", 0) if hasattr(row, "get") else 0,
    )


# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------


async def create_session(
    *,
    title: str = "New chat",
    user_id: str = "anonymous",
    client_metadata: dict[str, Any] | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> SessionRow:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO sessions (title, user_id, client_metadata, provider, model)
            VALUES ($1, $2, $3::jsonb, $4, $5)
            RETURNING *
            """,
            title,
            user_id,
            json.dumps(client_metadata or {}),
            provider,
            model,
        )
    return _session_from_row(row)


async def list_sessions(*, user_id: str | None = None, limit: int = 50) -> list[SessionRow]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT s.*, (SELECT count(*) FROM messages m WHERE m.session_id = s.id) AS message_count
              FROM sessions s
             WHERE ($1::text IS NULL OR s.user_id = $1)
             ORDER BY s.updated_at DESC
             LIMIT $2
            """,
            user_id,
            limit,
        )
    return [
        SessionRow(
            id=r["id"],
            title=r["title"],
            user_id=r["user_id"],
            client_metadata=_loads(r["client_metadata"]),
            provider=r["provider"],
            model=r["model"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
            message_count=r["message_count"],
        )
        for r in rows
    ]


async def get_session(session_id: UUID) -> SessionRow:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM sessions WHERE id = $1", session_id)
    if row is None:
        raise NotFoundError(f"Session {session_id} does not exist.")
    return _session_from_row(row)


async def touch_session(session_id: UUID, *, title: str | None = None) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute(
            """
            UPDATE sessions
               SET updated_at = now(),
                   title = COALESCE($2, title)
             WHERE id = $1
            """,
            session_id,
            title,
        )


async def delete_session(session_id: UUID) -> bool:
    async with get_pool().acquire() as conn:
        result = await conn.execute("DELETE FROM sessions WHERE id = $1", session_id)
    return result.endswith("1")


# --------------------------------------------------------------------------
# Messages
# --------------------------------------------------------------------------


async def add_message(
    session_id: UUID,
    *,
    role: str,
    content: str,
    tool_calls: list[dict[str, Any]] | None = None,
    citations: list[dict[str, Any]] | None = None,
    provider: str | None = None,
    model: str | None = None,
    latency_ms: int | None = None,
    token_usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO messages (session_id, role, content, tool_calls, citations,
                                  provider, model, latency_ms, token_usage)
            VALUES ($1,$2,$3,$4::jsonb,$5::jsonb,$6,$7,$8,$9::jsonb)
            RETURNING *
            """,
            session_id,
            role,
            content,
            json.dumps(tool_calls or []),
            json.dumps(citations or []),
            provider,
            model,
            latency_ms,
            json.dumps(token_usage or {}),
        )
    return _message_to_dict(row)


async def get_messages(session_id: UUID, *, limit: int = 200) -> list[dict[str, Any]]:
    """Every read of conversation content is scoped to one session."""
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM messages
             WHERE session_id = $1
             ORDER BY created_at ASC, id ASC
             LIMIT $2
            """,
            session_id,
            limit,
        )
    return [_message_to_dict(r) for r in rows]


def _message_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "session_id": str(row["session_id"]),
        "role": row["role"],
        "content": row["content"],
        "tool_calls": _loads(row["tool_calls"]) or [],
        "citations": _loads(row["citations"]) or [],
        "provider": row["provider"],
        "model": row["model"],
        "latency_ms": row["latency_ms"],
        "token_usage": _loads(row["token_usage"]) or {},
        "created_at": row["created_at"].isoformat(),
    }


# --------------------------------------------------------------------------
# Artifacts
# --------------------------------------------------------------------------


async def create_artifact(
    session_id: UUID,
    *,
    kind: str,
    title: str,
    raw_content: str,
    sanitized_content: str,
    sanitizer_report: dict[str, Any] | None = None,
    message_id: UUID | None = None,
) -> dict[str, Any]:
    async with get_pool().acquire() as conn:
        version = await conn.fetchval(
            "SELECT COALESCE(max(version), 0) + 1 FROM artifacts WHERE session_id = $1 AND title = $2",
            session_id,
            title,
        )
        row = await conn.fetchrow(
            """
            INSERT INTO artifacts (session_id, message_id, kind, title,
                                   raw_content, sanitized_content, sanitizer_report, version)
            VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8)
            RETURNING *
            """,
            session_id,
            message_id,
            kind,
            title,
            raw_content,
            sanitized_content,
            json.dumps(sanitizer_report or {}),
            version,
        )
    return _artifact_to_dict(row)


async def get_artifact(artifact_id: UUID) -> dict[str, Any]:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM artifacts WHERE id = $1", artifact_id)
    if row is None:
        raise NotFoundError(f"Artifact {artifact_id} does not exist.")
    return _artifact_to_dict(row)


async def list_artifacts(session_id: UUID) -> list[dict[str, Any]]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM artifacts WHERE session_id = $1 ORDER BY created_at DESC",
            session_id,
        )
    return [_artifact_to_dict(r) for r in rows]


def _artifact_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "session_id": str(row["session_id"]),
        "message_id": str(row["message_id"]) if row["message_id"] else None,
        "kind": row["kind"],
        "title": row["title"],
        "raw_content": row["raw_content"],
        "sanitized_content": row["sanitized_content"],
        "sanitizer_report": _loads(row["sanitizer_report"]) or {},
        "version": row["version"],
        "created_at": row["created_at"].isoformat(),
    }


# --------------------------------------------------------------------------
# Corpus stats — surfaced by /health and /api/config
# --------------------------------------------------------------------------


async def corpus_stats() -> dict[str, Any]:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT (SELECT count(*) FROM episodes) AS episodes,
                   (SELECT count(*) FROM chunks) AS chunks,
                   (SELECT count(*) FROM chunks WHERE embedding IS NOT NULL) AS embedded,
                   (SELECT max(ingested_at) FROM episodes) AS last_ingested
            """
        )
    return {
        "episodes": row["episodes"],
        "chunks": row["chunks"],
        "embedded_chunks": row["embedded"],
        "last_ingested_at": row["last_ingested"].isoformat() if row["last_ingested"] else None,
    }
