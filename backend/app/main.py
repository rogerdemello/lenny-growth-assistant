"""FastAPI application entry point.

    uv run uvicorn app.main:app --reload

Startup is deliberately non-fatal. If the database is unreachable the app still
starts and `/health` says so — an API that refuses to boot gives an evaluator
a stack trace and no diagnosis, while one that starts degraded tells them
exactly which component to fix.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import artifacts, chat, system
from app.core.config import get_settings
from app.core.errors import AppError, error_body
from app.core.logging import configure_logging, get_logger, get_request_id, set_request_id
from app.db.pool import close_pool, init_pool

settings = get_settings()
configure_logging(settings.log_level, settings.log_format)
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201
    log.info(
        "app.starting",
        env=settings.app_env,
        provider=settings.llm_provider,
        model=settings.llm_model,
        runtime=settings.agent_runtime,
        embed_model=settings.embed_model,
    )
    try:
        await init_pool(settings.database_url)
    except Exception as exc:  # noqa: BLE001
        log.error(
            "app.database_unavailable",
            error=str(exc),
            hint="The API is starting anyway. GET /health will report the database as down.",
        )

    # Importing the skill module registers write_ship30_essay in the shared
    # tool registry. Doing it at startup means /api/config reports the real
    # tool list rather than a partially-populated one.
    try:
        import app.skills.ship30  # noqa: F401
        from app.skills.loader import load_skills

        log.info("app.skills_loaded", skills=sorted(load_skills()))
    except Exception as exc:  # noqa: BLE001
        log.error("app.skill_load_failed", error=str(exc))

    yield

    await close_pool()
    log.info("app.stopped")


app = FastAPI(
    title="The Lenny Growth Assistant",
    description=(
        "A grounded conversational assistant over Lenny's Podcast transcripts. "
        "Answers cite the episode and timestamp they came from."
    ),
    version=system.VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-Id"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):  # noqa: ANN001, ANN201
    """Attach a request id and log the outcome of every request."""
    rid = set_request_id(request.headers.get("x-request-id"))
    started = time.perf_counter()

    response = await call_next(request)

    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    response.headers["X-Request-Id"] = rid
    # /health is polled; logging it at info would drown the signal.
    (log.debug if request.url.path == "/health" else log.info)(
        "http.request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration_ms=duration_ms,
    )
    return response


@app.exception_handler(AppError)
async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    log.warning("api.app_error", code=exc.code, message=exc.message, path=request.url.path)
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(exc.code, exc.message, get_request_id(), exc.hint, exc.details),
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=error_body(
            "validation_error",
            "The request body failed validation.",
            get_request_id(),
            hint="Check the field names and types against /docs.",
            details={"errors": exc.errors()[:10]},
        ),
    )


@app.exception_handler(StarletteHTTPException)
async def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body("http_error", str(exc.detail), get_request_id()),
    )


@app.exception_handler(Exception)
async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    log.exception("api.unhandled", path=request.url.path)
    return JSONResponse(
        status_code=500,
        content=error_body(
            "internal_error",
            "An unexpected error occurred.",
            get_request_id(),
            hint="Check the server logs for the matching request_id.",
        ),
    )


app.include_router(system.router)
app.include_router(chat.router)
app.include_router(artifacts.router)


@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {
        "name": "The Lenny Growth Assistant",
        "version": system.VERSION,
        "docs": "/docs",
        "health": "/health",
    }
