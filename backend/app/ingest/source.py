"""Fetch transcripts from the source repository.

A tarball rather than a `git clone`: no git dependency in the container, one
HTTP request instead of hundreds, and the response headers give us the exact
commit SHA — which is what makes every citation traceable to a source revision.

Nothing here is committed to our repo. The transcripts belong to
ChatPRD/lennys-podcast-transcripts and are fetched at ingest time; the README
records the attribution.
"""

from __future__ import annotations

import io
import re
import tarfile
from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml

from app.core.errors import IngestionError
from app.core.logging import get_logger

log = get_logger(__name__)

TRANSCRIPT_PATH_RE = re.compile(r"^[^/]+/episodes/(?P<slug>[^/]+)/transcript\.md$")


@dataclass(slots=True)
class RawTranscript:
    slug: str
    text: str


@dataclass(slots=True)
class CorpusPolicy:
    pinned: list[str]
    exclude: set[str]

    @classmethod
    def load(cls, path: Path) -> CorpusPolicy:
        if not path.exists():
            log.warning("corpus.policy_missing", path=str(path))
            return cls(pinned=[], exclude=set())
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(
            pinned=[str(s).strip() for s in (data.get("pinned") or [])],
            exclude={str(s).strip() for s in (data.get("exclude") or [])},
        )


async def resolve_commit_sha(repo: str, ref: str = "main") -> str | None:
    """The revision every ingested chunk can be traced back to."""
    url = f"https://api.github.com/repos/{repo}/commits/{ref}"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, headers={"User-Agent": "lenny-growth-assistant"})
            if resp.status_code >= 400:
                log.warning("source.sha_unavailable", status=resp.status_code)
                return None
            return resp.json().get("sha")
    except httpx.HTTPError as exc:
        log.warning("source.sha_failed", error=str(exc))
        return None


async def download_transcripts(repo: str, ref: str = "main", cache_dir: Path | None = None) -> list[RawTranscript]:
    """Download and unpack every `episodes/*/transcript.md`.

    The tarball is cached on disk so re-running ingestion during development
    does not re-download ~15MB each time.
    """
    url = f"https://codeload.github.com/{repo}/tar.gz/{ref}"
    blob: bytes

    cache_path = (cache_dir / f"{repo.replace('/', '_')}_{ref}.tar.gz") if cache_dir else None
    if cache_path and cache_path.exists():
        log.info("source.cache_hit", path=str(cache_path))
        blob = cache_path.read_bytes()
    else:
        log.info("source.download_start", url=url)
        try:
            async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
                resp = await client.get(url)
                if resp.status_code >= 400:
                    raise IngestionError(
                        f"Could not download transcripts: HTTP {resp.status_code}",
                        hint=f"Check that {repo}@{ref} exists and is public.",
                    )
                blob = resp.content
        except httpx.HTTPError as exc:
            raise IngestionError(
                f"Could not download transcripts from {url}: {exc}",
                hint="Check network access. Ingestion requires internet on first run.",
            ) from exc

        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(blob)
        log.info("source.download_done", bytes=len(blob))

    transcripts: list[RawTranscript] = []
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            match = TRANSCRIPT_PATH_RE.match(member.name)
            if not match:
                continue
            fh = tar.extractfile(member)
            if fh is None:
                continue
            transcripts.append(
                RawTranscript(slug=match.group("slug"), text=fh.read().decode("utf-8", errors="replace"))
            )

    log.info("source.extracted", transcripts=len(transcripts))
    if not transcripts:
        raise IngestionError(
            "The transcripts archive contained no episodes/*/transcript.md files.",
            hint="The upstream repository layout may have changed; check TRANSCRIPTS_REPO.",
        )
    return transcripts
