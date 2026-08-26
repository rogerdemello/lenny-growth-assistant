"""One error shape for the whole API.

Clients should never have to guess whether a failure body is `{"detail": ...}`,
`{"error": "..."}` or a stack trace. Everything that goes wrong leaves through
here as:

    {"error": {"code": ..., "message": ..., "request_id": ..., "hint": ...}}

`hint` is deliberately actionable — "Ollama is not reachable at
http://localhost:11434/v1. Start it with `ollama serve`." beats "Connection
refused" for the client engineer who inherits this.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base for every error we raise deliberately."""

    code = "internal_error"
    status_code = 500
    hint: str | None = None

    def __init__(self, message: str, *, hint: str | None = None, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        if hint is not None:
            self.hint = hint
        self.details = details or {}


class NotFoundError(AppError):
    code = "not_found"
    status_code = 404


class ValidationError(AppError):
    code = "validation_error"
    status_code = 422


class DatabaseUnavailableError(AppError):
    code = "database_unavailable"
    status_code = 503
    hint = "Check DATABASE_URL and that the database accepts connections. Supabase free projects auto-pause after inactivity — open the dashboard to wake it."


class ProviderUnavailableError(AppError):
    code = "provider_unavailable"
    status_code = 503
    hint = "The configured LLM provider did not respond. If using Ollama, confirm `ollama serve` is running; if using a cloud provider, confirm the API key and endpoint."


class ProviderTimeoutError(AppError):
    code = "provider_timeout"
    status_code = 504
    hint = "The model took too long. Small models on CPU are slow — raise LLM_TIMEOUT_SECONDS, or switch LLM_PROVIDER to a cloud provider."


class MissingCredentialsError(AppError):
    code = "missing_credentials"
    status_code = 503


class NoGroundingError(AppError):
    """Retrieval found nothing above the score floor.

    Not really an error — it is a product behaviour. It is modelled as one so
    that every path which would otherwise invent an answer has to handle it.
    """

    code = "no_grounding"
    status_code = 200
    hint = "Try rephrasing, or ask about a topic covered by the ingested episodes. See INGESTED.md for the corpus."


class IngestionError(AppError):
    code = "ingestion_error"
    status_code = 500


class ArtifactRejectedError(AppError):
    code = "artifact_rejected"
    status_code = 422
    hint = "The generated artifact could not be safely rendered. See docs/design.md for the sanitizer's allow/block policy."


def error_body(
    code: str,
    message: str,
    request_id: str,
    hint: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "message": message, "request_id": request_id}
    if hint:
        payload["hint"] = hint
    if details:
        payload["details"] = details
    return {"error": payload}
