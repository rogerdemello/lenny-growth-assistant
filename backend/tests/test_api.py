"""API contracts, session isolation, and degradation behaviour.

These run without a database or a model. Persistence is exercised against an
in-memory fake that enforces the same session-scoping rule the real repository
does, which is what makes the isolation test meaningful rather than tautological
— the fake would happily leak across sessions if the API asked it to.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.agent.runtime import AgentEvent
from app.db.repository import SessionRow
from app.main import app


# --------------------------------------------------------------------------
# In-memory persistence
# --------------------------------------------------------------------------


class FakeStore:
    """Minimal stand-in for the repository.

    Reads are scoped by session_id exactly as the real SQL is. If the API ever
    fetched without scoping, this would return everything and the isolation
    test would fail — which is the point.
    """

    def __init__(self) -> None:
        self.sessions: dict[UUID, SessionRow] = {}
        self.messages: dict[UUID, list[dict[str, Any]]] = {}
        self.artifacts: dict[UUID, list[dict[str, Any]]] = {}

    async def create_session(self, **kwargs: Any) -> SessionRow:
        now = datetime.now(UTC)
        row = SessionRow(
            id=uuid4(),
            title=kwargs.get("title", "New chat"),
            user_id=kwargs.get("user_id", "anonymous"),
            client_metadata=kwargs.get("client_metadata", {}),
            provider=kwargs.get("provider"),
            model=kwargs.get("model"),
            created_at=now,
            updated_at=now,
        )
        self.sessions[row.id] = row
        self.messages[row.id] = []
        self.artifacts[row.id] = []
        return row

    async def get_session(self, session_id: UUID) -> SessionRow:
        from app.core.errors import NotFoundError

        if session_id not in self.sessions:
            raise NotFoundError(f"Session {session_id} does not exist.")
        return self.sessions[session_id]

    async def list_sessions(self, **_: Any) -> list[SessionRow]:
        return sorted(self.sessions.values(), key=lambda s: s.updated_at, reverse=True)

    async def delete_session(self, session_id: UUID) -> bool:
        existed = self.sessions.pop(session_id, None) is not None
        self.messages.pop(session_id, None)
        self.artifacts.pop(session_id, None)
        return existed

    async def touch_session(self, session_id: UUID, *, title: str | None = None) -> None:
        row = self.sessions.get(session_id)
        if row:
            row.updated_at = datetime.now(UTC)
            if title:
                row.title = title

    async def add_message(self, session_id: UUID, **kwargs: Any) -> dict[str, Any]:
        record = {
            "id": str(uuid4()),
            "session_id": str(session_id),
            "role": kwargs["role"],
            "content": kwargs.get("content", ""),
            "tool_calls": kwargs.get("tool_calls") or [],
            "citations": kwargs.get("citations") or [],
            "provider": kwargs.get("provider"),
            "model": kwargs.get("model"),
            "latency_ms": kwargs.get("latency_ms"),
            "token_usage": kwargs.get("token_usage") or {},
            "created_at": datetime.now(UTC).isoformat(),
        }
        self.messages.setdefault(session_id, []).append(record)
        return record

    async def get_messages(self, session_id: UUID, **_: Any) -> list[dict[str, Any]]:
        return list(self.messages.get(session_id, []))

    async def list_artifacts(self, session_id: UUID) -> list[dict[str, Any]]:
        return list(self.artifacts.get(session_id, []))

    async def create_artifact(self, session_id: UUID, **kwargs: Any) -> dict[str, Any]:
        record = {
            "id": str(uuid4()),
            "session_id": str(session_id),
            "message_id": str(kwargs.get("message_id") or ""),
            "kind": kwargs["kind"],
            "title": kwargs["title"],
            "raw_content": kwargs["raw_content"],
            "sanitized_content": kwargs["sanitized_content"],
            "sanitizer_report": kwargs.get("sanitizer_report") or {},
            "version": 1,
            "created_at": datetime.now(UTC).isoformat(),
        }
        self.artifacts.setdefault(session_id, []).append(record)
        return record

    async def corpus_stats(self) -> dict[str, Any]:
        return {"episodes": 40, "chunks": 1300, "embedded_chunks": 1300, "last_ingested_at": None}


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore()
    import app.api.chat as chat_module
    import app.api.system as system_module

    for module in (chat_module, system_module):
        for name in (
            "create_session", "get_session", "list_sessions", "delete_session",
            "touch_session", "add_message", "get_messages", "list_artifacts",
            "create_artifact", "corpus_stats",
        ):
            if hasattr(module.repo, name):
                monkeypatch.setattr(module.repo, name, getattr(fake, name), raising=False)
    return fake


@pytest.fixture
def client(store):  # noqa: ARG001 — the fixture patches, the test uses the client
    # Without this, every test pays a full TCP connect timeout against the
    # placeholder DATABASE_URL during lifespan startup, and a provider probe
    # against a possibly-absent Ollama. Both are covered explicitly by their own
    # tests; paying for them 30 times over is just a slow suite.
    with (
        patch("app.main.init_pool", AsyncMock(return_value=None)),
        patch("app.main.close_pool", AsyncMock(return_value=None)),
        patch(
            "app.providers.openai_compat.OpenAICompatProvider.ping",
            AsyncMock(return_value={"ok": True, "status": 200}),
        ),
    ):
        with TestClient(app) as test_client:
            yield test_client


def _fake_runtime(events: list[AgentEvent]):
    """A runtime that replays a scripted event sequence."""

    class Scripted:
        name = "fake"

        async def run(self, _request):  # noqa: ANN001
            for event in events:
                yield event

        async def describe(self):
            return {"runtime": "fake"}

    return Scripted()


# --------------------------------------------------------------------------
# Health and config
# --------------------------------------------------------------------------


class TestHealth:
    def test_health_reports_components_separately(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] in ("ok", "degraded")
        assert "database" in body["components"]
        assert "llm_provider" in body["components"]

    def test_health_degrades_rather_than_failing(self, client):
        """A dead provider must not turn /health into a 500."""
        with patch(
            "app.providers.openai_compat.OpenAICompatProvider.ping",
            AsyncMock(return_value={"ok": False, "reason": "connection refused"}),
        ):
            response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "degraded"

    def test_config_exposes_the_toggle(self, client):
        body = client.get("/api/config").json()
        for field in ("active_provider", "active_model", "agent_runtime", "embed_model", "retrieval"):
            assert field in body

    def test_config_never_returns_secrets(self, client):
        """Asserted here as well as in test_agent.py — it is worth two tests."""
        raw = client.get("/api/config").text
        for marker in ("sk-ant-", "nvapi-", "api_key", "AZURE_OPENAI_API_KEY"):
            assert marker not in raw

    def test_root_is_a_pointer(self, client):
        body = client.get("/").json()
        assert body["docs"] == "/docs"
        assert body["health"] == "/health"


# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------


class TestSessions:
    def test_create_returns_201_with_the_session(self, client):
        response = client.post("/api/sessions", json={"title": "Pricing research"})
        assert response.status_code == 201
        body = response.json()
        assert body["title"] == "Pricing research"
        assert UUID(body["id"])

    def test_create_captures_client_metadata(self, client):
        """The brief asks for user metadata; this is what we can honestly record."""
        response = client.post(
            "/api/sessions",
            json={"title": "x", "client_metadata": {"timezone": "Asia/Kolkata"}},
            headers={"user-agent": "pytest-agent/1.0"},
        )
        metadata = response.json()["client_metadata"]
        assert metadata["timezone"] == "Asia/Kolkata"
        assert "pytest-agent" in metadata["user_agent"]

    def test_create_records_the_active_provider(self, client):
        body = client.post("/api/sessions", json={}).json()
        assert body["provider"]
        assert body["model"]

    def test_list_is_newest_first(self, client):
        first = client.post("/api/sessions", json={"title": "first"}).json()
        second = client.post("/api/sessions", json={"title": "second"}).json()
        ids = [s["id"] for s in client.get("/api/sessions").json()]
        assert ids.index(second["id"]) < ids.index(first["id"])

    def test_get_unknown_session_is_404_with_the_error_envelope(self, client):
        response = client.get(f"/api/sessions/{uuid4()}")
        assert response.status_code == 404
        error = response.json()["error"]
        assert error["code"] == "not_found"
        assert error["request_id"]

    def test_delete_removes_then_404s(self, client):
        session_id = client.post("/api/sessions", json={}).json()["id"]
        assert client.delete(f"/api/sessions/{session_id}").status_code == 204
        assert client.get(f"/api/sessions/{session_id}").status_code == 404

    def test_delete_unknown_session_is_404(self, client):
        assert client.delete(f"/api/sessions/{uuid4()}").status_code == 404

    def test_malformed_uuid_is_422(self, client):
        assert client.get("/api/sessions/not-a-uuid").status_code == 422


# --------------------------------------------------------------------------
# Session isolation — an explicit requirement
# --------------------------------------------------------------------------


class TestSessionIsolation:
    def test_messages_do_not_leak_between_sessions(self, client, store):
        alpha = client.post("/api/sessions", json={"title": "alpha"}).json()["id"]
        beta = client.post("/api/sessions", json={"title": "beta"}).json()["id"]

        events = [AgentEvent("token", {"text": "answer"}), AgentEvent("done", {"grounded": True})]
        with patch("app.api.chat.build_runtime", return_value=_fake_runtime(events)):
            client.post(f"/api/sessions/{alpha}/messages", json={"message": "alpha question", "stream": False})

        alpha_messages = client.get(f"/api/sessions/{alpha}/messages").json()
        beta_messages = client.get(f"/api/sessions/{beta}/messages").json()

        assert len(alpha_messages) == 2  # user + assistant
        assert beta_messages == []
        assert all(m["session_id"] == alpha for m in alpha_messages)
        assert "alpha question" not in json.dumps(beta_messages)

    def test_history_passed_to_the_agent_is_session_scoped(self, client, store):
        alpha = client.post("/api/sessions", json={}).json()["id"]
        beta = client.post("/api/sessions", json={}).json()["id"]

        events = [AgentEvent("token", {"text": "ok"}), AgentEvent("done", {})]
        with patch("app.api.chat.build_runtime", return_value=_fake_runtime(events)):
            client.post(f"/api/sessions/{alpha}/messages", json={"message": "secret alpha", "stream": False})

            captured: dict[str, Any] = {}

            class Capturing:
                name = "capture"

                async def run(self, request):  # noqa: ANN001
                    captured["history"] = request.history
                    yield AgentEvent("token", {"text": "ok"})
                    yield AgentEvent("done", {})

                async def describe(self):
                    return {}

            with patch("app.api.chat.build_runtime", return_value=Capturing()):
                client.post(f"/api/sessions/{beta}/messages", json={"message": "beta question", "stream": False})

        assert "secret alpha" not in json.dumps(captured["history"])

    def test_deleting_a_session_does_not_touch_others(self, client):
        alpha = client.post("/api/sessions", json={"title": "keep"}).json()["id"]
        beta = client.post("/api/sessions", json={"title": "drop"}).json()["id"]
        client.delete(f"/api/sessions/{beta}")
        assert client.get(f"/api/sessions/{alpha}").status_code == 200


# --------------------------------------------------------------------------
# Chat contract
# --------------------------------------------------------------------------


class TestChat:
    def test_non_streaming_returns_text_and_citations(self, client):
        citation = {"chunk_id": "c1", "guest": "Brian Balfour", "episode_title": "Growth", "timestamp": "2:35"}
        events = [
            AgentEvent("citations", {"citations": [citation]}),
            AgentEvent("token", {"text": "Retention "}),
            AgentEvent("token", {"text": "is the signal."}),
            AgentEvent("done", {"grounded": True, "provider": "ollama"}),
        ]
        with patch("app.api.chat.build_runtime", return_value=_fake_runtime(events)):
            session_id = client.post("/api/sessions", json={}).json()["id"]
            body = client.post(
                f"/api/sessions/{session_id}/messages", json={"message": "retention?", "stream": False}
            ).json()

        assert body["message"] == "Retention is the signal."
        assert body["citations"][0]["guest"] == "Brian Balfour"
        assert body["request_id"]

    def test_streaming_emits_sse_frames_and_terminates(self, client):
        events = [
            AgentEvent("stage", {"stage": "retrieving"}),
            AgentEvent("token", {"text": "Hello"}),
            AgentEvent("done", {"grounded": True}),
        ]
        with patch("app.api.chat.build_runtime", return_value=_fake_runtime(events)):
            session_id = client.post("/api/sessions", json={}).json()["id"]
            with client.stream(
                "POST", f"/api/sessions/{session_id}/messages", json={"message": "hi", "stream": True}
            ) as response:
                assert response.status_code == 200
                assert "text/event-stream" in response.headers["content-type"]
                frames = [line for line in response.iter_lines() if line.startswith("data:")]

        payloads = [f[5:].strip() for f in frames]
        assert payloads[-1] == "[DONE]"
        types = [json.loads(p)["type"] for p in payloads if p != "[DONE]"]
        assert "stage" in types and "token" in types and "done" in types

    def test_the_turn_is_persisted(self, client, store):
        events = [AgentEvent("token", {"text": "answer text"}), AgentEvent("done", {"provider": "ollama"})]
        with patch("app.api.chat.build_runtime", return_value=_fake_runtime(events)):
            session_id = client.post("/api/sessions", json={}).json()["id"]
            client.post(f"/api/sessions/{session_id}/messages", json={"message": "q", "stream": False})

        messages = client.get(f"/api/sessions/{session_id}/messages").json()
        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert messages[1]["content"] == "answer text"
        assert messages[1]["provider"] == "ollama"
        assert messages[1]["latency_ms"] is not None

    def test_first_message_titles_the_session(self, client):
        events = [AgentEvent("token", {"text": "a"}), AgentEvent("done", {})]
        with patch("app.api.chat.build_runtime", return_value=_fake_runtime(events)):
            session_id = client.post("/api/sessions", json={}).json()["id"]
            client.post(
                f"/api/sessions/{session_id}/messages",
                json={"message": "How do I price a B2B product?", "stream": False},
            )
        assert client.get(f"/api/sessions/{session_id}").json()["title"].startswith("How do I price")

    def test_agent_error_becomes_a_structured_503(self, client):
        events = [AgentEvent("error", {"code": "provider_unavailable", "message": "Ollama down", "hint": "Start it."})]
        with patch("app.api.chat.build_runtime", return_value=_fake_runtime(events)):
            session_id = client.post("/api/sessions", json={}).json()["id"]
            response = client.post(
                f"/api/sessions/{session_id}/messages", json={"message": "q", "stream": False}
            )
        assert response.status_code == 503
        error = response.json()["error"]
        assert error["code"] == "provider_unavailable"
        assert error["hint"] == "Start it."

    def test_failed_turns_do_not_persist_an_empty_assistant_message(self, client):
        events = [AgentEvent("error", {"code": "provider_unavailable", "message": "down"})]
        with patch("app.api.chat.build_runtime", return_value=_fake_runtime(events)):
            session_id = client.post("/api/sessions", json={}).json()["id"]
            client.post(f"/api/sessions/{session_id}/messages", json={"message": "q", "stream": False})

        roles = [m["role"] for m in client.get(f"/api/sessions/{session_id}/messages").json()]
        assert roles == ["user"]

    def test_empty_message_is_rejected(self, client):
        session_id = client.post("/api/sessions", json={}).json()["id"]
        response = client.post(f"/api/sessions/{session_id}/messages", json={"message": "", "stream": False})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"

    def test_oversized_message_is_rejected(self, client):
        session_id = client.post("/api/sessions", json={}).json()["id"]
        response = client.post(
            f"/api/sessions/{session_id}/messages", json={"message": "x" * 9000, "stream": False}
        )
        assert response.status_code == 422

    def test_posting_to_an_unknown_session_is_404(self, client):
        response = client.post(f"/api/sessions/{uuid4()}/messages", json={"message": "hi", "stream": False})
        assert response.status_code == 404


# --------------------------------------------------------------------------
# Cross-cutting
# --------------------------------------------------------------------------


class TestRequestContext:
    def test_every_response_carries_a_request_id(self, client):
        assert client.get("/health").headers.get("X-Request-Id")

    def test_an_inbound_request_id_is_honoured(self, client):
        response = client.get("/health", headers={"X-Request-Id": "trace-me-123"})
        assert response.headers["X-Request-Id"] == "trace-me-123"

    def test_error_bodies_carry_the_same_id(self, client):
        response = client.get(f"/api/sessions/{uuid4()}", headers={"X-Request-Id": "trace-me-456"})
        assert response.json()["error"]["request_id"] == "trace-me-456"


class TestIngestEndpoint:
    def test_disabled_when_no_admin_token_is_set(self, client):
        """An open endpoint that can saturate the machine is not a safe default."""
        response = client.post("/api/ingest")
        assert response.status_code == 403

    def test_wrong_token_is_rejected(self, client):
        from app.core.config import get_settings

        settings = get_settings()
        original = settings.ingest_admin_token
        settings.ingest_admin_token = "correct-token"
        try:
            assert client.post("/api/ingest", headers={"x-admin-token": "wrong"}).status_code == 401
        finally:
            settings.ingest_admin_token = original
