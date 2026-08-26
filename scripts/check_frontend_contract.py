"""Does the frontend's contract match what the backend actually emits?

I cannot click through the UI in this environment, so the next best thing is to
check the seam where a mismatch would fail silently: SSE event names the
frontend switches on, and the response fields it reads. A typo either side
produces a blank pane, not an error.
"""

import json
import re
import sys
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000"
FRONTEND = Path(r"E:\assignment\oogwayLabsFDE\frontend\src")


def frontend_handled_events() -> set[str]:
    """Event names App.tsx switches on."""
    app = (FRONTEND / "App.tsx").read_text(encoding="utf-8")
    block = app.split("const handle = (event: StreamEvent)")[1]
    return set(re.findall(r"case '([a-z_]+)':", block))


def declared_event_types() -> set[str]:
    api = (FRONTEND / "lib" / "api.ts").read_text(encoding="utf-8")
    block = api.split("export type StreamEvent")[1].split("async function request")[0]
    return set(re.findall(r"type:\s*'([a-z_]+)'", block))


def main() -> int:
    problems: list[str] = []

    handled = frontend_handled_events()
    declared = declared_event_types()
    print(f"frontend handles : {sorted(handled)}")
    print(f"frontend declares: {sorted(declared)}")

    undeclared = handled - declared
    if undeclared:
        problems.append(f"handled but not declared in StreamEvent: {sorted(undeclared)}")

    with httpx.Client() as client:
        # --- config contract -------------------------------------------
        cfg = client.get(f"{BASE}/api/config", timeout=30).json()
        api_ts = (FRONTEND / "lib" / "api.ts").read_text(encoding="utf-8")
        cfg_block = api_ts.split("export type AppConfig = {")[1].split("}")[0]
        for field in re.findall(r"^\s*(\w+)", cfg_block, re.MULTILINE):
            if field not in cfg:
                problems.append(f"AppConfig declares '{field}' but /api/config does not return it")
        print(f"\n/api/config keys : {sorted(cfg)}")

        # --- session contract ------------------------------------------
        sid = client.post(f"{BASE}/api/sessions", json={"title": "contract"}, timeout=30).json()
        sess_block = api_ts.split("export type Session = {")[1].split("}")[0]
        for field in re.findall(r"^\s*(\w+)", sess_block, re.MULTILINE):
            if field not in sid:
                problems.append(f"Session declares '{field}' but POST /api/sessions does not return it")

        # --- live SSE stream -------------------------------------------
        seen: set[str] = set()
        with client.stream(
            "POST", f"{BASE}/api/sessions/{sid['id']}/messages",
            json={"message": "hi", "stream": True}, timeout=300.0,
        ) as r:
            for line in r.iter_lines():
                if line.startswith("data:"):
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        seen.add(json.loads(payload)["type"])
                    except (json.JSONDecodeError, KeyError):
                        pass

        print(f"backend emitted  : {sorted(seen)}")
        unknown = seen - declared
        if unknown:
            problems.append(f"backend emits event types the frontend does not declare: {sorted(unknown)}")

        # A citation is what the UI renders; check its shape end to end.
        cite_block = api_ts.split("export type Citation = {")[1].split("}")[0]
        cite_fields = set(re.findall(r"^\s*(\w+)", cite_block, re.MULTILINE))
        grounded = client.post(
            f"{BASE}/api/sessions/{sid['id']}/messages",
            json={"message": "what drives retention", "stream": False},
            timeout=600.0,
        ).json()
        cites = grounded.get("citations") or []
        print(f"\ncitations returned: {len(cites)}")
        if cites:
            missing = cite_fields - set(cites[0])
            if missing:
                problems.append(f"Citation type declares fields the API omits: {sorted(missing)}")
            else:
                print(f"citation shape   : all {len(cite_fields)} declared fields present")
        else:
            problems.append("no citations returned for an in-domain question")

    print("\n" + "=" * 60)
    if problems:
        print("CONTRACT MISMATCHES:")
        for p in problems:
            print("  -", p)
        return 1
    print("FRONTEND/BACKEND CONTRACT CONSISTENT")
    return 0


sys.exit(main())
