"""Minimal forward-only migration runner.

    python -m app.db.migrate

Applies every `NNN_*.sql` in `migrations/` in filename order, once, tracking
what it has applied in `schema_migrations`. No rollback, no autogeneration —
for a schema this size Alembic is more machinery than the problem needs, and a
client engineer can read a .sql file without learning a tool.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import asyncpg

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.pool import _normalize_dsn

log = get_logger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename   TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def _render(sql: str, embed_dim: int) -> str:
    """Substitute build-time constants that belong to config, not to the schema."""
    return sql.replace("${EMBED_DIM}", str(embed_dim))


async def run_migrations() -> int:
    settings = get_settings()
    conn = await asyncpg.connect(_normalize_dsn(settings.database_url))
    applied_count = 0
    try:
        await conn.execute(TRACKING_TABLE)
        already = {r["filename"] for r in await conn.fetch("SELECT filename FROM schema_migrations")}

        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in already:
                log.debug("migrate.skip", file=path.name)
                continue
            sql = _render(path.read_text(encoding="utf-8"), settings.embed_dim)
            log.info("migrate.apply", file=path.name)
            # Not wrapped in a transaction: CREATE EXTENSION and CREATE INDEX
            # behave better outside one, and these files are idempotent.
            await conn.execute(sql)
            await conn.execute("INSERT INTO schema_migrations (filename) VALUES ($1)", path.name)
            applied_count += 1

        log.info("migrate.done", applied=applied_count, total=len(already) + applied_count)
        return applied_count
    finally:
        await conn.close()


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    try:
        applied = asyncio.run(run_migrations())
    except Exception as exc:  # noqa: BLE001
        log.error("migrate.failed", error=str(exc))
        print(
            "\nMigration failed. Common causes:\n"
            "  * DATABASE_URL is wrong or the project is paused (Supabase free tier auto-pauses).\n"
            "  * The `vector` extension is unavailable. On Supabase, enable it under\n"
            "    Database > Extensions, or run: CREATE EXTENSION IF NOT EXISTS vector;\n",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    print(f"Applied {applied} migration(s).")


if __name__ == "__main__":
    main()
