"""Application configuration.

Everything the evaluator can change lives here and is sourced from the
environment. No provider, model, or endpoint is hard-coded anywhere else in
the codebase — that is what makes the model toggle real rather than nominal.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]

ProviderName = Literal["ollama", "azure", "openai_compat", "anthropic"]
RuntimeName = Literal["local", "claude_sdk"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", REPO_ROOT / "backend" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- database ---------------------------------------------------------
    database_url: str = "postgresql://postgres:password@localhost:5432/lenny"

    # --- model selection --------------------------------------------------
    llm_provider: ProviderName = "ollama"
    llm_model: str = "llama3.2"
    llm_fallback_provider: ProviderName | None = None
    essay_provider: ProviderName | None = None
    agent_runtime: RuntimeName = "local"
    llm_timeout_seconds: float = 180.0

    # --- ollama -----------------------------------------------------------
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_chat_model: str = "llama3.2"
    ollama_embed_model: str = "nomic-embed-text"

    # --- azure openai -----------------------------------------------------
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_chat_deployment: str = ""
    azure_openai_embed_deployment: str = ""
    azure_openai_api_version: str = "2024-10-21"

    # --- generic openai-compatible ---------------------------------------
    openai_compat_base_url: str = ""
    openai_compat_api_key: str = ""
    openai_compat_model: str = ""

    # --- anthropic --------------------------------------------------------
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"
    anthropic_base_url: str = ""

    # --- embeddings -------------------------------------------------------
    embed_provider: ProviderName = "ollama"
    embed_model: str = "nomic-embed-text"
    embed_dim: int = 768

    # Asymmetric-retrieval task prefixes.
    #
    # nomic-embed-text is trained with `search_query:` / `search_document:`
    # prefixes and expects them at inference. Omitting them measurably degrades
    # calibration: on this corpus, an out-of-domain query ("best kubernetes
    # ingress controller") scored 0.44 while a legitimate in-domain one scored
    # 0.42 — the scores carried no usable signal, so no score floor could
    # separate them and the grounding guarantee silently failed.
    #
    # Leave blank for symmetric models (all-minilm, text-embedding-3-*, ada-002).
    # Set explicitly to override the model-name-based defaults below.
    embed_query_prefix: str | None = None
    embed_document_prefix: str | None = None

    # --- retrieval --------------------------------------------------------
    # Retrieve broadly, ground narrowly. `retrieval_top_k` is what we fetch and
    # show as citations; `prompt_top_k` is what actually enters the prompt.
    # They differ because prefill on a small local model is expensive: measured
    # on a Ryzen 7 7730U (CPU only), 8 chunks cost ~22s to first token while
    # 4 chunks cost ~11s. Showing the user 8 sources is free; feeding the model
    # 8 sources is not.
    retrieval_top_k: int = 8
    prompt_top_k: int = 4
    retrieval_score_floor: float = 0.35

    # --- ingestion --------------------------------------------------------
    transcripts_repo: str = "ChatPRD/lennys-podcast-transcripts"
    transcripts_ref: str = "main"
    ingest_min_duration_seconds: int = 1800
    ingest_max_episodes: int = 40
    chunk_target_tokens: int = 700
    chunk_overlap_tokens: int = 100
    ingest_admin_token: str = ""

    # --- app --------------------------------------------------------------
    app_env: str = "development"
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "console"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @field_validator("llm_fallback_provider", "essay_provider", mode="before")
    @classmethod
    def _blank_to_none(cls, v: object) -> object:
        """Treat an empty env var the same as an unset one.

        `LLM_FALLBACK_PROVIDER=` in a .env file is how people disable fallback,
        and pydantic would otherwise reject the empty string against the Literal.
        """
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def skills_dir(self) -> Path:
        return REPO_ROOT / ".claude" / "skills"

    @property
    def corpus_file(self) -> Path:
        return REPO_ROOT / "corpus.yml"

    @property
    def effective_essay_provider(self) -> ProviderName:
        return self.essay_provider or self.llm_provider

    # Known asymmetric embedding models and the prefixes they were trained with.
    _ASYMMETRIC_PREFIXES: ClassVar[dict[str, tuple[str, str]]] = {
        "nomic-embed": ("search_query: ", "search_document: "),
        "bge-": ("Represent this sentence for searching relevant passages: ", ""),
        "e5-": ("query: ", "passage: "),
        "multilingual-e5": ("query: ", "passage: "),
        "gte-": ("", ""),
    }

    def _default_prefixes(self) -> tuple[str, str]:
        model = self.embed_model.lower()
        for marker, prefixes in self._ASYMMETRIC_PREFIXES.items():
            if marker in model:
                return prefixes
        return ("", "")

    @property
    def query_prefix(self) -> str:
        if self.embed_query_prefix is not None:
            return self.embed_query_prefix
        return self._default_prefixes()[0]

    @property
    def document_prefix(self) -> str:
        if self.embed_document_prefix is not None:
            return self.embed_document_prefix
        return self._default_prefixes()[1]


@lru_cache
def get_settings() -> Settings:
    return Settings()
