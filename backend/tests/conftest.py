"""Shared test configuration.

The important thing here is hermeticity. `Settings` reads the repo's `.env` by
design, which means every test that constructs one was silently inheriting the
developer's local configuration. That surfaced the moment `ESSAY_PROVIDER` was
set to `azure` locally: a test asserting the default behaviour failed, on a
machine where nothing about the code had changed.

A test suite whose result depends on an untracked file is not a test suite. The
autouse fixture below detaches `Settings` from `.env` so every test sees the
declared defaults plus whatever it passes explicitly.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _isolate_settings_from_dotenv(monkeypatch):
    """Make Settings ignore the repo's .env for the duration of each test."""
    config = dict(Settings.model_config)
    config["env_file"] = None
    monkeypatch.setattr(Settings, "model_config", config)

    # Clear any provider variables that may be exported in the developer's
    # shell — Ollama's OLLAMA_* vars are set globally on the dev machine.
    for name in (
        "LLM_PROVIDER", "LLM_MODEL", "ESSAY_PROVIDER", "LLM_FALLBACK_PROVIDER",
        "AGENT_RUNTIME", "EMBED_PROVIDER", "EMBED_MODEL", "DATABASE_URL",
        "RETRIEVAL_SCORE_FLOOR", "RETRIEVAL_CONFIDENT_SCORE",
        "ANTHROPIC_API_KEY", "AZURE_OPENAI_API_KEY", "OPENAI_COMPAT_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
