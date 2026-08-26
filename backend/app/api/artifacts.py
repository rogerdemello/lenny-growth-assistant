"""Artifact retrieval, rendering, and export.

`/render` is the second half of the security story. It serves an artifact as a
standalone HTML document with restrictive headers, so even if someone opens it
directly — outside the sandboxed iframe the UI uses — the browser still refuses
to run scripts, load remote resources, or let it be framed by a third party.
"""

from __future__ import annotations

import html as html_lib
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, PlainTextResponse

from app.api.schemas import ArtifactResponse
from app.artifacts.sanitize import CSP
from app.core.logging import get_logger
from app.db import repository as repo

log = get_logger(__name__)
router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])

SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-site",
}


@router.get("/{artifact_id}", response_model=ArtifactResponse)
async def get_artifact(artifact_id: UUID) -> Any:
    return await repo.get_artifact(artifact_id)


@router.get("/{artifact_id}/render", response_class=HTMLResponse)
async def render_artifact(artifact_id: UUID) -> HTMLResponse:
    """Serve the sanitized artifact as a standalone document."""
    artifact = await repo.get_artifact(artifact_id)
    content = artifact["sanitized_content"]

    if artifact["kind"] == "markdown":
        # Markdown is rendered by the client, which owns the markdown pipeline.
        # Serving it as escaped preformatted text here keeps this endpoint from
        # needing a second, differently-configured renderer that could disagree
        # with the first about what is safe.
        body = f"<pre>{html_lib.escape(content)}</pre>"
    else:
        body = content

    document = (
        "<!doctype html>"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<meta http-equiv="Content-Security-Policy" content="{CSP}">'
        f"<title>{html_lib.escape(artifact['title'])}</title>"
        "</head><body>"
        f"{body}"
        "</body></html>"
    )
    return HTMLResponse(content=document, headers=SECURITY_HEADERS)


@router.get("/{artifact_id}/download")
async def download_artifact(
    artifact_id: UUID,
    raw: bool = Query(False, description="Return the model's original output instead of the sanitized version."),
) -> PlainTextResponse:
    """Export an artifact as a file.

    `raw=true` exists so the sanitizer is auditable — a reviewer can diff what
    the model wrote against what we serve. It is served as text/plain with an
    attachment disposition so a browser downloads it rather than rendering it.
    """
    artifact = await repo.get_artifact(artifact_id)
    extension = "md" if artifact["kind"] == "markdown" else "html"
    safe_title = "".join(c if c.isalnum() or c in "-_ " else "_" for c in artifact["title"]).strip()[:80]
    filename = f"{safe_title or 'artifact'}.{extension}"

    content = artifact["raw_content"] if raw else artifact["sanitized_content"]
    log.info("artifact.downloaded", artifact_id=str(artifact_id), raw=raw, kind=artifact["kind"])

    return PlainTextResponse(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            **SECURITY_HEADERS,
        },
    )
