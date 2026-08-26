#!/usr/bin/env python3
"""Export this project's coding-agent transcript to readable, scrubbed Markdown.

    python scripts/export_agent_transcript.py

Claude Code records every session as JSONL under
`~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`. That file is the raw
record of how this project was built — including the parts that went wrong.
This script turns it into something a reviewer can actually read, and removes
anything that should not be published.

**Scrubbing is deliberately aggressive.** A transcript of a build session is one
of the easiest ways to leak a credential, because keys get pasted into it in the
course of normal work. The patterns below cover the providers this project
touches plus the generic shapes; anything matched is replaced with a labelled
placeholder rather than deleted, so the reader can see that a value was present
without seeing the value.

Run it again at any time — the output is regenerated from scratch.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "agent-transcripts"

# Order matters: the more specific patterns run first so a generic rule does not
# swallow a provider-specific one and lose the label.
SCRUB_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"), "<ANTHROPIC_KEY_REDACTED>"),
    (re.compile(r"\bsk-proj-[A-Za-z0-9_\-]{20,}"), "<OPENAI_KEY_REDACTED>"),
    (re.compile(r"\bsk-[A-Za-z0-9]{32,}"), "<OPENAI_KEY_REDACTED>"),
    (re.compile(r"\bnvapi-[A-Za-z0-9_\-]{20,}"), "<NVIDIA_KEY_REDACTED>"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "<GITHUB_TOKEN_REDACTED>"),
    (re.compile(r"\bey[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}"), "<JWT_REDACTED>"),
    # Connection strings: keep the shape, lose the password and host.
    (
        re.compile(r"(postgres(?:ql)?://[^:@\s]+:)[^@\s]+(@)[^\s/]+", re.IGNORECASE),
        r"\1<DB_PASSWORD_REDACTED>\2<DB_HOST_REDACTED>",
    ),
    (re.compile(r"https://[a-z0-9-]+\.openai\.azure\.com", re.IGNORECASE), "https://<AZURE_RESOURCE_REDACTED>.openai.azure.com"),
    (re.compile(r"https://[a-z0-9]{20}\.supabase\.co", re.IGNORECASE), "https://<SUPABASE_PROJECT_REDACTED>.supabase.co"),
    # Generic assignments, e.g. `AZURE_OPENAI_API_KEY=abc123` or `"api_key": "abc"`.
    (
        re.compile(r"([A-Z0-9_]*(?:API_KEY|SECRET|TOKEN|PASSWORD)[A-Z0-9_]*\s*[=:]\s*[\"']?)([^\s\"',}]{8,})", re.IGNORECASE),
        r"\1<REDACTED>",
    ),
]

# Tool results are frequently thousands of lines of file content. Truncating
# keeps the transcript readable without losing what the agent actually did.
MAX_TOOL_RESULT_CHARS = 1_200
MAX_TOOL_INPUT_CHARS = 2_000


def scrub(text: str) -> str:
    for pattern, replacement in SCRUB_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n… [{len(text) - limit:,} more characters truncated]"


@dataclass
class Stats:
    user_turns: int = 0
    assistant_turns: int = 0
    tool_calls: int = 0
    errors: int = 0
    scrubbed: int = 0


def find_session_files() -> list[Path]:
    """Locate this project's transcripts.

    Claude Code encodes the absolute cwd by replacing every non-alphanumeric
    character with a dash.
    """
    encoded = re.sub(r"[^A-Za-z0-9]", "-", str(REPO_ROOT))
    base = Path(os.environ.get("CLAUDE_PROJECTS_DIR", Path.home() / ".claude" / "projects"))
    candidates = [base / encoded]

    # The project may have been opened from a different path spelling.
    if base.exists():
        candidates += [d for d in base.iterdir() if d.is_dir() and REPO_ROOT.name.lower() in d.name.lower()]

    files: list[Path] = []
    for directory in candidates:
        if directory.exists():
            files.extend(sorted(directory.glob("*.jsonl")))
    return sorted(set(files))


def render_content(blocks: object, stats: Stats) -> list[str]:
    """Turn one message's content into Markdown lines."""
    lines: list[str] = []

    if isinstance(blocks, str):
        return [scrub(blocks)]
    if not isinstance(blocks, list):
        return lines

    for block in blocks:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")

        if kind == "text":
            text = scrub(block.get("text", "")).strip()
            if text:
                lines.append(text)

        elif kind == "thinking":
            # Reasoning is where the corrections actually happen, so it is worth
            # keeping — folded away so it does not dominate the page.
            thought = scrub(block.get("thinking", "")).strip()
            if thought:
                lines.append(f"<details><summary>Reasoning</summary>\n\n{truncate(thought, 3000)}\n\n</details>")

        elif kind == "tool_use":
            stats.tool_calls += 1
            name = block.get("name", "?")
            payload = scrub(json.dumps(block.get("input", {}), indent=2, ensure_ascii=False))
            lines.append(f"**Tool: `{name}`**\n\n```json\n{truncate(payload, MAX_TOOL_INPUT_CHARS)}\n```")

        elif kind == "tool_result":
            content = block.get("content")
            if isinstance(content, list):
                content = "\n".join(
                    part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"
                )
            body = scrub(str(content or "")).strip()
            if block.get("is_error"):
                stats.errors += 1
                lines.append(f"**Result — ERROR**\n\n```\n{truncate(body, MAX_TOOL_RESULT_CHARS)}\n```")
            elif body:
                lines.append(f"<details><summary>Result</summary>\n\n```\n{truncate(body, MAX_TOOL_RESULT_CHARS)}\n```\n\n</details>")

    return lines


def convert(path: Path) -> tuple[str, Stats]:
    stats = Stats()
    out: list[str] = []

    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            entry = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        entry_type = entry.get("type")
        message = entry.get("message") or {}
        content = message.get("content")

        if entry_type == "user":
            # Tool results are recorded as user turns; only count real input.
            is_tool_result = isinstance(content, list) and any(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in content
            )
            rendered = render_content(content, stats)
            if not rendered:
                continue
            if is_tool_result:
                out.append("\n".join(rendered))
            else:
                stats.user_turns += 1
                out.append(f"\n---\n\n### 👤 User\n\n" + "\n\n".join(rendered))

        elif entry_type == "assistant":
            rendered = render_content(content, stats)
            if not rendered:
                continue
            stats.assistant_turns += 1
            out.append("#### 🤖 Assistant\n\n" + "\n\n".join(rendered))

    return "\n\n".join(out), stats


def main() -> None:
    files = find_session_files()
    if not files:
        print("No session transcripts found.", file=sys.stderr)
        print("Expected under ~/.claude/projects/<encoded-repo-path>/", file=sys.stderr)
        raise SystemExit(1)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    written: list[tuple[Path, Stats, Path]] = []

    for index, path in enumerate(files, start=1):
        body, stats = convert(path)
        if not body.strip():
            continue

        target = OUTPUT_DIR / f"session-{index:02d}-{path.stem[:8]}.md"
        header = (
            f"# Coding agent transcript — session {index}\n\n"
            f"Source: `{path.name}` · exported {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
            f"{stats.user_turns} user turns · {stats.assistant_turns} assistant turns · "
            f"{stats.tool_calls} tool calls · {stats.errors} tool errors\n\n"
            "> Secrets have been scrubbed automatically — see `scripts/export_agent_transcript.py`.\n"
            "> Tool inputs and results are truncated for readability.\n"
        )
        target.write_text(header + "\n" + body + "\n", encoding="utf-8")
        written.append((path, stats, target))
        print(f"  {target.relative_to(REPO_ROOT)}  ({target.stat().st_size / 1024:.0f} KB)")

    # A last-resort check: if a known secret shape survived, say so loudly
    # rather than letting it reach a public repository.
    leaked = []
    for _, _, target in written:
        text = target.read_text(encoding="utf-8")
        for pattern, _ in SCRUB_PATTERNS[:6]:
            if pattern.search(text):
                leaked.append((target.name, pattern.pattern[:40]))

    if leaked:
        print("\nWARNING: possible unscrubbed secrets:", file=sys.stderr)
        for name, pattern in leaked:
            print(f"  {name}: matched /{pattern}/", file=sys.stderr)
        raise SystemExit(2)

    print(f"\nExported {len(written)} transcript(s) to {OUTPUT_DIR.relative_to(REPO_ROOT)}/ — no secrets detected.")


if __name__ == "__main__":
    main()
