"""Load skills from disk.

Skills live in `.claude/skills/<name>/SKILL.md` — the layout the Claude Agent
SDK expects, so the same files serve both runtimes without duplication or a
build step. The local runtime reads the body as a system prompt; the SDK
runtime lets its own skill loader pick up the same directory.

That shared location is the point. A change to the Ship 30 rubric takes effect
in both runtimes, and there is exactly one answer to "where is the skill
defined?"
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass(slots=True)
class Skill:
    name: str
    description: str
    body: str
    allowed_tools: list[str]
    path: Path


def _parse(path: Path) -> Skill | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("skill.unreadable", path=str(path), error=str(exc))
        return None

    match = FRONTMATTER_RE.match(raw)
    meta: dict = {}
    body = raw
    if match:
        try:
            meta = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError as exc:
            log.warning("skill.bad_frontmatter", path=str(path), error=str(exc))
        body = raw[match.end() :]

    name = str(meta.get("name") or path.parent.name)
    tools = meta.get("allowed-tools") or meta.get("allowed_tools") or []
    if isinstance(tools, str):
        tools = [t.strip() for t in tools.split(",") if t.strip()]

    return Skill(
        name=name,
        description=str(meta.get("description") or ""),
        body=body.strip(),
        allowed_tools=[str(t) for t in tools],
        path=path,
    )


@lru_cache(maxsize=1)
def load_skills() -> dict[str, Skill]:
    skills_dir = get_settings().skills_dir
    if not skills_dir.exists():
        log.warning("skill.dir_missing", path=str(skills_dir))
        return {}

    found: dict[str, Skill] = {}
    for path in sorted(skills_dir.glob("*/SKILL.md")):
        skill = _parse(path)
        if skill:
            found[skill.name] = skill
            log.info("skill.loaded", name=skill.name, tools=skill.allowed_tools, bytes=len(skill.body))
    return found


def get_skill(name: str) -> Skill | None:
    return load_skills().get(name)


def reset_cache() -> None:
    load_skills.cache_clear()
