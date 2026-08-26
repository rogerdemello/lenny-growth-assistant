"""Sessions and the chat turn.

The streaming and non-streaming paths share one generator, so they cannot
diverge: `stream=false` drains the same events the SSE path emits and folds
them into a single JSON response. Persistence happens once, at the end of the
turn, from the same accumulated state either way.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse

from app.agent.runtime import AgentRequest, build_runtime
from app.api.schemas import (
    ChatRequest,
    CreateSessionRequest,
    MessageResponse,
    SessionDetailResponse,
    SessionResponse,
)
from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.core.logging import get_logger, get_request_id
from app.db import repository as repo

log = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["chat"])


def _session_payload(row: repo.SessionRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "user_id": row.user_id,
        "client_metadata": row.client_metadata,
        "provider": row.provider,
        "model": row.model,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "message_count": row.message_count,
    }


@router.post("/sessions", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(body: CreateSessionRequest, request: Request) -> Any:
    settings = get_settings()

    # Captured because the brief asks for user metadata, and this is what we
    # can honestly record without inventing an auth system.
    metadata = {
        **body.client_metadata,
        "user_agent": request.headers.get("user-agent", "")[:300],
        "accept_language": request.headers.get("accept-language", "")[:100],
    }

    row = await repo.create_session(
        title=body.title,
        user_id=body.user_id,
        client_metadata=metadata,
        provider=settings.llm_provider,
        model=settings.llm_model,
    )
    log.info("session.created", session_id=str(row.id), user_id=row.user_id)
    return _session_payload(row)


@router.get("/sessions", response_model=list[SessionResponse])
async def list_sessions(user_id: str | None = None, limit: int = 50) -> Any:
    rows = await repo.list_sessions(user_id=user_id, limit=min(limit, 200))
    return [_session_payload(r) for r in rows]


@router.get("/sessions/{session_id}", response_model=SessionDetailResponse)
async def get_session(session_id: UUID) -> Any:
    row = await repo.get_session(session_id)
    messages = await repo.get_messages(session_id)
    artifacts = await repo.list_artifacts(session_id)
    return {
        **_session_payload(row),
        "message_count": len(messages),
        "messages": messages,
        "artifacts": artifacts,
    }


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(session_id: UUID) -> Response:
    if not await repo.delete_session(session_id):
        raise NotFoundError(f"Session {session_id} does not exist.")
    log.info("session.deleted", session_id=str(session_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/sessions/{session_id}/messages", response_model=list[MessageResponse])
async def get_messages(session_id: UUID) -> Any:
    await repo.get_session(session_id)  # 404 rather than an empty list for an unknown id
    return await repo.get_messages(session_id)


async def _run_turn(session_id: UUID, message: str) -> AsyncIterator[dict[str, Any]]:
    """Execute one turn, emitting SSE-shaped events and persisting the result.

    Persistence is deliberately inside the generator: if the client disconnects
    mid-stream the turn still completes and is saved, so a dropped connection
    does not silently lose the assistant's reply.
    """
    started = time.perf_counter()
    settings = get_settings()

    await repo.get_session(session_id)
    history = await repo.get_messages(session_id)

    await repo.add_message(session_id, role="user", content=message)

    # First real message names the chat, so the sidebar is readable.
    if not history:
        await repo.touch_session(session_id, title=message[:60].strip() or "New chat")
    else:
        await repo.touch_session(session_id)

    runtime = build_runtime()
    answer_parts: list[str] = []
    citations: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    provider_used = settings.llm_provider
    error: dict[str, Any] | None = None

    agent_request = AgentRequest(
        session_id=session_id,
        message=message,
        history=[{"role": m["role"], "content": m["content"]} for m in history],
    )

    async for event in runtime.run(agent_request):
        payload = event.to_sse()

        if event.type == "token":
            answer_parts.append(event.data.get("text", ""))
        elif event.type == "citations":
            if event.data.get("final") or not citations:
                citations = event.data.get("citations", [])
        elif event.type == "tool_call":
            tool_calls.append(event.data)
        elif event.type == "artifact":
            artifacts.append(event.data)
        elif event.type == "done":
            provider_used = event.data.get("provider") or provider_used
        elif event.type == "error":
            error = event.data

        yield payload

    content = "".join(answer_parts).strip()
    latency_ms = int((time.perf_counter() - started) * 1000)

    if error is not None:
        log.error("chat.turn_failed", session_id=str(session_id), **error)
        return

    stored = await repo.add_message(
        session_id,
        role="assistant",
        content=content,
        tool_calls=tool_calls,
        citations=citations,
        provider=provider_used,
        model=settings.llm_model,
        latency_ms=latency_ms,
    )

    for artifact in artifacts:
        saved = await repo.create_artifact(
            session_id,
            kind=artifact.get("kind", "markdown"),
            title=artifact.get("title", "Untitled artifact"),
            raw_content=artifact.get("raw_content", ""),
            sanitized_content=artifact.get("sanitized_content", ""),
            sanitizer_report=artifact.get("sanitizer_report", {}),
            message_id=UUID(stored["id"]),
        )
        # Re-emit with the persisted id so the client can link to it.
        yield {"type": "artifact_saved", **saved}

    log.info(
        "chat.turn_complete",
        session_id=str(session_id),
        latency_ms=latency_ms,
        chars=len(content),
        citations=len(citations),
        artifacts=len(artifacts),
        provider=provider_used,
    )

    yield {"type": "saved", "message_id": stored["id"], "latency_ms": latency_ms}


@router.post("/sessions/{session_id}/messages")
async def post_message(session_id: UUID, body: ChatRequest) -> Response:
    request_id = get_request_id()

    if not body.stream:
        events: list[dict[str, Any]] = []
        async for event in _run_turn(session_id, body.message):
            events.append(event)

        text = "".join(e.get("text", "") for e in events if e.get("type") == "token")
        citations = next(
            (e.get("citations", []) for e in reversed(events) if e.get("type") == "citations"), []
        )
        artifacts = [e for e in events if e.get("type") == "artifact_saved"]
        failure = next((e for e in events if e.get("type") == "error"), None)

        if failure:
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": failure.get("code", "agent_error"),
                        "message": failure.get("message", "The agent failed."),
                        "request_id": request_id,
                        "hint": failure.get("hint"),
                    }
                },
            )

        return JSONResponse(
            content={
                "message": text,
                "citations": citations,
                "artifacts": artifacts,
                "request_id": request_id,
            }
        )

    async def event_stream() -> AsyncIterator[str]:
        try:
            async for event in _run_turn(session_id, body.message):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as exc:  # noqa: BLE001
            # The response has already started, so an exception handler cannot
            # change the status code. The only honest thing left is to tell the
            # client in-band.
            log.exception("chat.stream_failed", session_id=str(session_id))
            yield f"data: {json.dumps({'type': 'error', 'code': 'stream_failed', 'message': str(exc), 'request_id': request_id})}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # nginx and friends buffer SSE by default, which makes streaming
            # look like a hang.
            "X-Accel-Buffering": "no",
            "X-Request-Id": request_id,
        },
    )
