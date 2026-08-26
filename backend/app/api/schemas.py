"""Request and response contracts.

Explicit models rather than loose dicts, so the API has a published shape,
validation happens at the boundary, and /docs is accurate without being
hand-maintained.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class CreateSessionRequest(BaseModel):
    title: str = Field(default="New chat", max_length=200)
    user_id: str = Field(default="anonymous", max_length=128)
    client_metadata: dict[str, Any] = Field(default_factory=dict)


class SessionResponse(BaseModel):
    id: UUID
    title: str
    user_id: str
    client_metadata: dict[str, Any]
    provider: str | None
    model: str | None
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


class CitationModel(BaseModel):
    chunk_id: str
    episode_slug: str
    guest: str
    episode_title: str
    speaker: str
    start_seconds: int
    timestamp: str
    text: str
    score: float
    youtube_url: str | None


class MessageResponse(BaseModel):
    id: str
    session_id: str
    role: str
    content: str
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    latency_ms: int | None = None
    token_usage: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class SessionDetailResponse(SessionResponse):
    messages: list[MessageResponse] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    # The client may stream or not; both go through the same pipeline so the
    # non-streaming path cannot drift from the streaming one.
    stream: bool = True


class ArtifactResponse(BaseModel):
    id: str
    session_id: str
    message_id: str | None
    kind: Literal["markdown", "html"]
    title: str
    raw_content: str
    sanitized_content: str
    sanitizer_report: dict[str, Any]
    version: int
    created_at: str


class HealthComponent(BaseModel):
    ok: bool
    detail: dict[str, Any] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    # `degraded` rather than a 500: a dead embedding model does not mean the
    # API is down, and an evaluator needs to see *which* part is broken.
    status: Literal["ok", "degraded"]
    version: str
    components: dict[str, HealthComponent]


class ConfigResponse(BaseModel):
    active_provider: str
    active_model: str | None
    fallback_provider: str | None
    essay_provider: str
    agent_runtime: str
    embed_provider: str
    embed_model: str
    embed_dim: int
    available: list[dict[str, Any]]
    runtime: dict[str, Any] = Field(default_factory=dict)
    corpus: dict[str, Any] = Field(default_factory=dict)
    retrieval: dict[str, Any] = Field(default_factory=dict)


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str
    hint: str | None = None
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
