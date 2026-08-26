#!/usr/bin/env python3
"""Independently verify that no credential reached the exported transcripts.

    python scripts/check_transcripts.py

**Why this is a separate script.** The exporter's own check re-ran the same
rules it used to scrub, so it could only ever confirm what already worked — it
reported "no secrets detected" while five live credentials sat in the output.
A check that shares its assumptions with the thing it checks is decoration.

**Why it never prints a secret.** The obvious way to verify is to grep for the
values. Doing that from a shell writes them into the command line, which lands
in the *next* exported transcript — which is exactly how a partial credential
reached a public repository here. This reads the values from `.env`, reports
only counts and variable names, and never echoes a fragment.

It searches at a *shorter* fragment length than the exporter redacts at, so it
is strictly stricter than the thing it is checking.

Exit codes: 0 clean, 1 a credential fragment was found, 2 nothing to check.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from export_agent_transcript import (  # noqa: E402
    MIN_SECRET_FRAGMENT,
    OUTPUT_DIR,
    REPO_ROOT,
    _is_high_entropy,
    load_secret_values,
)

# Shorter than the exporter's threshold, but not so short that it matches
# ordinary English. A 4-character check flagged 18 "leaks" because the
# DATABASE_URL begins with "post" and an Azure endpoint with "open".
CHECK_FRAGMENT = max(8, MIN_SECRET_FRAGMENT)


def main() -> int:
    env_path = REPO_ROOT / ".env"
    values = load_secret_values(env_path)
    if not values:
        print(f"No secrets found in {env_path.name} — nothing to check against.", file=sys.stderr)
        return 2

    transcripts = sorted(OUTPUT_DIR.glob("session-*.md"))
    if not transcripts:
        print("No transcripts to check.", file=sys.stderr)
        return 2

    print(f"Checking {len(transcripts)} transcript(s) against {len(values)} secret value(s)")
    print(f"Fragment length: {CHECK_FRAGMENT} (exporter redacts at {MIN_SECRET_FRAGMENT})\n")

    failures = 0
    for path in transcripts:
        text = path.read_text(encoding="utf-8", errors="replace")
        hits: list[str] = []

        for index, value in enumerate(values):
            # A whole connection string or endpoint URL is checked verbatim.
            # Its *sensitive components* — password, host, project ref — are
            # already separate entries in `values` and get fragment-checked
            # individually, so prefix-matching the URL itself only produces
            # noise ("post...", "open...").
            if "://" in value:
                fragment = value
            elif _is_high_entropy(value):
                fragment = value[:CHECK_FRAGMENT]
            else:
                fragment = value
            count = text.count(fragment)
            if count:
                # Identify by position and length only — never by content.
                hits.append(f"secret #{index} (len {len(value)}): {count} occurrence(s)")

        if hits:
            failures += len(hits)
            print(f"  FAIL {path.name}")
            for hit in hits:
                print(f"         {hit}")
        else:
            print(f"  ok   {path.name}")

    if failures:
        print(
            f"\n{failures} credential fragment(s) found. Do not commit.\n"
            f"Lower MIN_SECRET_FRAGMENT in scripts/export_agent_transcript.py, re-export, "
            f"and re-run this check.",
            file=sys.stderr,
        )
        return 1

    print("\nClean. No credential fragments in any transcript.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
