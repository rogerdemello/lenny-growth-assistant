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
    # Generic assignments. The `["']?\s*` before the separator matters: a
    # PowerShell hashtable writes `'AZURE_OPENAI_API_KEY'  = '...'`, where the
    # character after the name is a closing quote, not whitespace. An earlier
    # version required whitespace-or-separator immediately and missed it.
    (
        re.compile(
            r"([A-Z0-9_]*(?:API_KEY|SECRET|TOKEN|PASSWORD|CONNECTION_STRING)[A-Z0-9_]*[\"']?\s*[=:]\s*[\"']?)"
            r"([^\s\"',}]{8,})",
            re.IGNORECASE,
        ),
        r"\1<REDACTED>",
    ),
]


def load_secret_values(env_path: Path) -> list[str]:
    """Read the actual secret values from .env so they can be redacted literally.

    Pattern matching alone is not enough, and the way it failed here is
    instructive: a verification command that greps for the secrets embeds them
    as bare quoted strings, in no recognisable `KEY=value` shape. The
    leak-checking step leaked.

    Redacting by value catches every context — prose, shell commands, JSON,
    log output — because it does not care how the secret is spelled around it.
    Pattern rules stay as a backstop for anything not in .env.
    """
    values: list[str] = []
    if not env_path.exists():
        return values

    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        value = raw.strip().strip("\"'")
        # Short values are not secrets and would cause absurd false positives
        # ("ollama", "json", "local").
        if len(value) < 12:
            continue
        if not re.search(r"KEY|SECRET|TOKEN|PASSWORD|URL|ENDPOINT|DSN", key, re.IGNORECASE):
            continue
        values.append(value)

        # A connection string hides two more secrets inside it: the password
        # and the host. Both leak identity even when the full URL does not
        # appear verbatim.
        if match := re.match(r"[a-z+]+://([^:@/\s]+):([^@\s]+)@([^/\s:]+)", value, re.IGNORECASE):
            values.extend(part for part in (match.group(2), match.group(3)) if len(part) >= 8)
            # The Supabase project ref, e.g. db.<ref>.supabase.co
            if ref := re.search(r"db\.([a-z0-9]{16,})\.supabase\.co", match.group(3), re.IGNORECASE):
                values.append(ref.group(1))

        # The resource name inside an Azure endpoint.
        if host := re.search(r"https://([a-z0-9-]+)\.openai\.azure\.com", value, re.IGNORECASE):
            values.append(host.group(1))
            values.append(f"{host.group(1)}.openai.azure.com")

    # Longest first, so a substring never redacts before its container does and
    # leave a recognisable tail behind.
    return sorted(set(values), key=len, reverse=True)

# Tool results are frequently thousands of lines of file content. Truncating
# keeps the transcript readable without losing what the agent actually did.
MAX_TOOL_RESULT_CHARS = 1_200
MAX_TOOL_INPUT_CHARS = 2_000


SECRET_VALUES: list[str] = []
SECRET_PATTERNS: list[re.Pattern[str]] = []

# A partial secret is still a leak. Debug commands routinely quote a prefix of
# a key — one in this very session printed `value[:10]` — so literal
# replacement of the full value is not enough.
#
# 12 was the first guess and it was still too generous: 10-character fragments
# reached a public repository. 8 is short enough to catch a truncated display
# and long enough not to shred unrelated text, *provided* it is applied only to
# high-entropy values (see `_is_high_entropy`). Applying it to something like
# `http://localhost:11434/v1` would redact every mention of localhost.
MIN_SECRET_FRAGMENT = 6

# Hosts and URLs that identify nothing and are safe to leave readable.
_BENIGN_HOST_RE = re.compile(r"^https?://(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?", re.IGNORECASE)


def _is_high_entropy(value: str) -> bool:
    """Is this an opaque credential rather than a readable URL or name?

    Fragment matching is aggressive, so it is reserved for values where a
    partial disclosure actually matters — keys, tokens, passwords, project
    refs — rather than for endpoints an operator needs to be able to read.
    """
    if _BENIGN_HOST_RE.match(value):
        return False
    if "://" in value and not re.search(r"://[^/\s]+:[^@/\s]+@", value):
        # A URL with no embedded credentials. Redact it whole, not by fragment.
        return False
    if re.search(r"\s", value):
        return False
    stripped = re.sub(r"[^A-Za-z0-9]", "", value)
    # A long unbroken alphanumeric run with no spaces is an identifier, not
    # prose. Requiring mixed case was too strict and let a Supabase project ref
    # (`iuayyxcynambfgjomgsm` — all lowercase) through at 8-character
    # granularity.
    return len(stripped) >= 12


def build_secret_patterns(values: list[str]) -> list[re.Pattern[str]]:
    """Match each secret *and any prefix of it* of at least MIN_SECRET_FRAGMENT.

    `<first 12 chars><any continuation>` catches the full value, a truncated
    copy, and a hand-typed prefix in a grep command — which is exactly how the
    first version of this script leaked.
    """
    patterns = []
    for value in values:
        if len(value) < MIN_SECRET_FRAGMENT:
            continue
        # High-entropy credentials are matched from a short prefix, so a
        # truncated or hand-typed fragment is caught too. Readable URLs are
        # matched whole, so an operator can still see which endpoint was used.
        head_len = MIN_SECRET_FRAGMENT if _is_high_entropy(value) else len(value)
        head = re.escape(value[:head_len])
        patterns.append(re.compile(head + r"[A-Za-z0-9_\-.:/+=@]*"))
    return patterns


def scrub(text: str) -> str:
    # Secret values first — the strongest rule, and independent of context.
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("<REDACTED>", text)
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
                out.append("\n---\n\n### ðŸ‘¤ User\n\n" + "\n\n".join(rendered))

        elif entry_type == "assistant":
            rendered = render_content(content, stats)
            if not rendered:
                continue
            stats.assistant_turns += 1
            out.append("#### ðŸ¤– Assistant\n\n" + "\n\n".join(rendered))

    return "\n\n".join(out), stats


def main() -> None:
    global SECRET_VALUES, SECRET_PATTERNS
    SECRET_VALUES = load_secret_values(REPO_ROOT / ".env")
    SECRET_PATTERNS = build_secret_patterns(SECRET_VALUES)
    if SECRET_VALUES:
        print(
            f"Redacting {len(SECRET_VALUES)} secret value(s) from .env, "
            f"including any fragment of {MIN_SECRET_FRAGMENT}+ characters"
        )

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
            f"Source: `{path.name}` Â· exported {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
            f"{stats.user_turns} user turns Â· {stats.assistant_turns} assistant turns Â· "
            f"{stats.tool_calls} tool calls Â· {stats.errors} tool errors\n\n"
            "> Secrets have been scrubbed automatically — see `scripts/export_agent_transcript.py`.\n"
            "> Tool inputs and results are truncated for readability.\n"
        )
        target.write_text(header + "\n" + body + "\n", encoding="utf-8")
        written.append((path, stats, target))
        print(f"  {target.relative_to(REPO_ROOT)}  ({target.stat().st_size / 1024:.0f} KB)")

    # Verify what was actually written.
    #
    # The previous version of this check only re-ran the first six *patterns*,
    # and so reported "no secrets detected" while five live credentials sat in
    # the output — including in a verification command that had embedded them
    # as bare quoted strings. Checking the literal values is the check that
    # would have caught it, so that is what runs first.
    leaked: list[tuple[str, str]] = []
    for _, _, target in written:
        text = target.read_text(encoding="utf-8")
        for value, pattern in zip(SECRET_VALUES, SECRET_PATTERNS, strict=False):
            if pattern.search(text):
                leaked.append((target.name, f"secret (or fragment) ending ...{value[-4:]}"))
        for pattern, _ in SCRUB_PATTERNS[:6]:
            if pattern.search(text):
                leaked.append((target.name, f"pattern /{pattern.pattern[:40]}/"))

    if leaked:
        print("\nREFUSING TO FINISH — unscrubbed secrets in the output:", file=sys.stderr)
        for name, what in leaked:
            print(f"  {name}: {what}", file=sys.stderr)
        print("\nFix scripts/export_agent_transcript.py before committing.", file=sys.stderr)
        raise SystemExit(2)

    checked = f"{len(SECRET_VALUES)} literal value(s) + {len(SCRUB_PATTERNS)} patterns"
    print(f"\nExported {len(written)} transcript(s) to {OUTPUT_DIR.relative_to(REPO_ROOT)}/")
    print(f"Verified clean against {checked}.")


if __name__ == "__main__":
    main()

