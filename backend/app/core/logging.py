"""Structured logging.

Every log line carries a `request_id` so a single chat turn can be traced
across retrieval, the model call, and persistence. The stage timers are the
reason this exists: when someone reports "the assistant is slow", the logs
should say whether it was retrieval, the first token, or the database.
"""

from __future__ import annotations

import logging
import sys
import time
import uuid
from contextvars import ContextVar

import structlog

_request_id: ContextVar[str] = ContextVar("request_id", default="-")


def configure_logging(level: str = "INFO", fmt: str = "console") -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=getattr(logging, level.upper(), logging.INFO))

    processors: list = [
        structlog.contextvars.merge_contextvars,
        _add_request_id,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if fmt == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=False))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper(), logging.INFO)),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def _add_request_id(_logger, _name, event_dict):  # noqa: ANN001
    event_dict.setdefault("request_id", _request_id.get())
    return event_dict


def set_request_id(value: str | None = None) -> str:
    rid = value or uuid.uuid4().hex[:12]
    _request_id.set(rid)
    return rid


def get_request_id() -> str:
    return _request_id.get()


def get_logger(name: str):  # noqa: ANN201
    return structlog.get_logger(name)


class Stage:
    """Times a named stage and logs its duration on exit.

    Accumulates into `sink` so a turn can report `retrieve_ms`, `llm_ms` and
    `total_ms` together in one structured event rather than three scattered ones.
    """

    def __init__(self, name: str, sink: dict[str, float] | None = None, **fields: object) -> None:
        self.name = name
        self.sink = sink
        self.fields = fields
        self._start = 0.0
        self.elapsed_ms = 0.0

    def __enter__(self) -> Stage:
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        self.elapsed_ms = round((time.perf_counter() - self._start) * 1000, 2)
        if self.sink is not None:
            self.sink[f"{self.name}_ms"] = self.elapsed_ms
        get_logger("stage").debug(
            "stage.complete",
            stage=self.name,
            duration_ms=self.elapsed_ms,
            failed=exc_type is not None,
            **self.fields,
        )
