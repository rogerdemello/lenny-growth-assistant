# Coding agent transcripts

This project was built in one working day with Claude Code driving most of the implementation. `session-01-*.md` is the full, unedited record — regenerate it any time with:

```bash
python scripts/export_agent_transcript.py
```

Secrets are scrubbed automatically by that script, which also refuses to finish if a known credential shape survives the pass.

The rest of this file is the part worth reading: **what went wrong, and how it was caught.** The transcript is long; these are the moments that changed the code.

---

## Failures caught by tests

### 1. The sanitizer left `@import` alive inside `<style>`

**What happened.** The XSS payload table passed 12 of 13 cases. The failure was `<style>@import url('https://evil.example/x.css');</style>`. `<style>` is deliberately *allowed* — the brief asks for HTML **and CSS** — and nh3 sanitizes markup, not stylesheet contents. The CSS passed straight through.

**Why it mattered.** `@import` pulls a remote stylesheet, and CSS attribute selectors can exfiltrate page content. The CSP would have blocked it, but that makes a single control load-bearing for an entire attack class.

**The fix.** A separate CSS pass over the contents of every `<style>` block, stripping `@import`, `expression()`, `behavior:`, `-moz-binding`, and any `url()` that is not `data:` or `https:`. Now two independent layers block it.

**The lesson.** "I used a sanitizer library" is not the same as "the content is sanitized". Knowing what the library does *not* cover is the actual work.

---

### 2. Stripping `<script>` left `alert(1)` as visible text

**What happened.** `<script>alert(1)</script>` sanitized to the literal text `alert(1)`. Harmless — it cannot execute — but it renders in the document and looks exactly like a bypass.

**The fix.** `clean_content_tags={"script", "iframe", "object", "embed", "applet", "form", "noscript"}` so the *body* is removed with the tag.

**The lesson.** A security control that looks broken to a reviewer costs the same trust as one that is broken.

---

### 3. `data:` URIs were handled inconsistently

**What happened.** A test asserted that `<img src="data:image/png;base64,...">` survives. It did not — `data:` was absent from the allowed URL schemes. But the CSP said `img-src data: https:`. The sanitizer and the CSP disagreed about the policy.

**The fix, after actually deciding what the policy should be.** `data:` is now allowed at the scheme level and narrowed **per attribute**: `data:image/*` is fine in `<img src>`, `data:text/html` in `<a href>` is a navigation XSS vector and is dropped. Separately, `https:` was removed from the CSP's `img-src` entirely, so an artifact can now make **no outbound request at all** — closing the channel where generated content encodes data into a remote image URL.

**The lesson.** The bug was not a missing scheme. It was that two places implemented a policy nobody had written down. Writing the allow/block table in `design.md` is what made the inconsistency obvious.

---

### 4. A regex that was too literal

**What happened.** `test_artifact_requests` failed on "build a table comparing those". The router matched `make me a table` and `create me a table` but not `build`, because the verbs and nouns were spelled out as one alternation per verb.

**The fix.** Separate the verb list from the noun list so adding either does not require re-spelling the other.

---

### 5. A test that was wrong, not the code

**What happened.** `test_no_duplicate_chunks` failed: six chunks, two distinct texts. The obvious read was a chunker bug — the overlap logic re-emitting windows.

**What it actually was.** The synthetic fixture generated every turn with identical text (`"word " * 100`) and only the speaker alternating. Chunks built from identical turns are *correctly* identical. The chunker was fine; the fixture was degenerate.

**The fix.** Make each synthetic turn's text unique.

**The lesson.** A failing test is a hypothesis, not a verdict. Reading the assertion output rather than jumping to the implementation saved a pointless "fix" to working code.

---

### 6. A false alarm worth recording

**What happened.** A startup check reported only **1 API route registered** out of 13. That looked like `include_router` silently failing.

**What it actually was.** The check filtered `isinstance(r, APIRoute)`. Current FastAPI keeps included routers nested as `_IncludedRouter` objects rather than flattening them into the parent's route list. All 13 routes were registered; the diagnostic was wrong.

**The lesson.** Verify the instrument before believing the reading.

---

### 7. The most expensive bug: a regex that silently dropped 10% of the corpus

**What happened.** A `--dry-run` ingestion against all 303 real episodes logged three warnings: `ingest.pin_empty` for `casey-winters`, `teresa-torres` and `gibson-biddle` — three of the twenty hand-pinned episodes the assistant is supposed to be *best* at.

**The cause.** The speaker-turn regex required `HH:MM:SS`. Older episodes use `MM:SS` (`Casey Winters (00:12):`), and many use bare continuation markers (`(00:13):`) for the same speaker continuing.

**The scale, once measured.** 30 of 303 episodes parsed to **zero turns**. They were then dropped by corpus selection with no error, because "episode has no turns" is indistinguishable from "episode was not selected". Another 17,420 continuation turns across the corpus would have been left unattributed.

**Why it is the worst kind of bug.** Nothing crashed. Nothing logged an error. The only symptom would have been a user asking about Casey Winters — one of the most-cited growth voices on the podcast — and being told the archive does not cover him. A refusal that is *wrong* is far more corrosive to this product than an error, because it looks like correct behaviour.

**A second bug found while fixing the first.** The new regex used `\s*` between the speaker and the timestamp. `\s` matches newlines, so the speaker group could begin on the previous line, capture an entire paragraph as a speaker name, and swallow the following header. A test with mixed formats in one transcript caught it; `[ \t]*` fixed it.

**The outcome.** 302 of 303 episodes now parse, up from 273. The remaining one has no timestamps at all and is rejected deliberately — every citation would deep-link to 0:00, which looks authoritative and points at the wrong place — with `parser.unsupported_format` logged so it is a known gap rather than a silent one.

**The lesson.** Unit tests against a single fixture passed the whole time. What found this was running the real pipeline over the real corpus in dry-run mode and *reading the warnings*. A fixture proves the parser handles the data you thought about; only the corpus tells you what you did not.

---

## Environment problems

### 8. Ollama installed to the wrong drive

`winget install Ollama.Ollama` defaults to `%LOCALAPPDATA%` on C:. The install is ~2.8 GB and the cached models already lived on E:. The winget job was killed mid-download, the installer fetched directly, and run with `/DIR=E:\ML\Ollama`. It picked up the existing `llama3.2` blob via the pre-set `OLLAMA_MODELS`, saving a 2 GB re-download.

### 9. A silent hang on install

`Start-Process -Wait` on the Ollama installer never returned. The installer had already finished — it launched the Ollama tray app as a child process, and `-Wait` was waiting on *that*. Checking for `ollama.exe` on disk while the command was still "running" is what revealed it.

### 10. Mojibake from a PowerShell round-trip

Renumbering these very sections with `Get-Content -Raw | ... | Set-Content -Encoding utf8` corrupted every em-dash in the file. PowerShell 5.1's `Get-Content` defaults to the system ANSI codepage, so a UTF-8 file is read as mojibake and then written back out as double-encoded UTF-8. The file was rewritten from source rather than patched again.

---

## Measurements that changed the design

The most useful thing the agent did was **stop and measure before committing to an approach.**

### 11. Prefill cost split retrieval in two

The plan assumed `RETRIEVAL_TOP_K=8`. A benchmark against the real model showed:

| Passages | Prompt tokens | Time to first token |
|---|---|---|
| 2 | ~535 | 11.0 s |
| 4 | ~1,070 | 10.8 s |
| 8 | ~2,141 | 21.7 s |

Generation ran ~7–9 tok/s throughout. So eight passages doubled the wait before a single word appeared.

**The change.** `RETRIEVAL_TOP_K` (what the user sees as citations) was separated from `PROMPT_TOP_K` (what the model reads). Showing eight sources is free; feeding the model eight is not. That split does not exist in the original plan — it exists because of a measurement.

### 12. Embedding throughput sized the corpus

Measured at ~1.45 chunks/sec on CPU. A real episode produced 33 chunks, and a dry run over the selected 40 episodes produced 1,464. That is roughly 17 minutes of embedding — which is what confirmed `INGEST_MAX_EPISODES=40` rather than leaving it a guess.

---

## A plan assumption that was wrong

The initial plan asserted that the Claude Agent SDK is locked to an Anthropic API key, and concluded the SDK runtime could only ever be mock-tested.

**That was incorrect.** The SDK honours `ANTHROPIC_BASE_URL` against any gateway speaking the Anthropic Messages format — so a LiteLLM proxy in front of Azure OpenAI could drive it. A review pass caught this before implementation started.

The plan changed: the SDK runtime is a real implementation, and the reason it was not demonstrated is narrower and more honest — no Anthropic credentials were available on this machine, and pointing the SDK at a 3B local model is independently unworkable because its bundled agent binary sends a 10–15k token system prompt. Documented in `docs/architecture.md#agent-runtimes` rather than glossed.

---

## How the work was directed

Patterns that produced better output than a single long prompt:

- **Plan before code, then attack the plan.** A separate review pass over the plan caught the Agent SDK error above and forced a cut list before any time was sunk.
- **Measure the constraint, do not assume it.** Every latency decision traces to a benchmark run against the actual hardware.
- **Write the adversarial test first for security code.** The XSS payload table was written before the sanitizer was trusted, and it found three real defects.
- **Run the real pipeline over the real data early.** The dry run found a bug that 150 passing unit tests did not.
- **Prefer honest documentation to a plausible demo.** The unverified `docker-compose.yml` and the unrun SDK runtime are both labelled as such, in the files themselves and in the README.
