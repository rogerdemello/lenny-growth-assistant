# Manual test plan

Covers what the automated suite cannot: the rendered UI, streaming behaviour, and the failure states an evaluator should see working.

**Automated pre-check.** Before working through this by hand, run:

```bash
python scripts/check_frontend_contract.py
```

It verifies the seam where a UI bug fails *silently*: every SSE event type the
backend emits is one the frontend declares, and every field the frontend reads
is one the API returns. A mismatch there produces a blank pane rather than an
error, so it is worth catching before you start clicking.

**Before starting**

- [ ] `ollama serve` running, with `llama3.2` and `nomic-embed-text` pulled
- [ ] `DATABASE_URL` set in `.env`; if Supabase, open the dashboard to wake the project
- [ ] Ingestion has run — `GET /health` shows `embedded_chunks > 0`
- [ ] Close memory-heavy applications; `llama3.2` needs ~3 GB resident

Expect ~10–20 s per answer and 3–5 minutes for an essay on CPU. That is the hardware, not a fault.

---

## 1 · Health and configuration

| # | Step | Expected |
|---|---|---|
| 1.1 | `GET http://127.0.0.1:8000/health` | `status: "ok"`; `database`, `llm_provider` and `corpus` all `ok`; `embedded_chunks` > 0 |
| 1.2 | `GET /api/config` | Active provider, model, runtime, embed model, corpus stats, retrieval settings |
| 1.3 | Search the `/api/config` response for your API key | **Not present.** Only `configured: true/false` |
| 1.4 | Open `http://localhost:5173` | Sidebar shows a green dot, `ollama`, `llama3.2`, and the episode/chunk counts |
| 1.5 | `GET /docs` | Every endpoint documented with request/response schemas |
| 1.6 | `cd backend && python -m app.rag.calibrate` | **10/10 in-domain answered, 10/10 out-of-domain refused.** Exits 0. This is the grounding guarantee, measured rather than assumed — see section 4. |

---

## 2 · Grounded answer

| # | Step | Expected |
|---|---|---|
| 2.1 | Click the seeded question *"How should I think about pricing a B2B SaaS product?"* | Stage label progresses: Reading your question → Searching transcripts → Writing the answer |
| 2.2 | Watch the answer | Text streams incrementally, not all at once |
| 2.3 | When complete | Green shield: *"Grounded in N passages"*, N ≥ 2 |
| 2.4 | Inspect the citations | Each shows `S#`, guest, episode title, timestamp |
| 2.5 | Click a citation | Expands in place to show the transcript passage, speaker, and similarity score |
| 2.6 | Click *"Watch at …"* | YouTube opens **at that timestamp**, and the passage matches what is said |
| 2.7 | Read the answer against the passages | Every substantive claim traces to a passage. No invented names, numbers or companies |
| 2.8 | Check the footer under the answer | `ollama · llama3.2 · N.Ns` |

**Fails if:** fewer than 2 citations · a deep link lands at 0:00 · the answer contains a claim absent from every passage.

---

## 3 · Follow-up handling

| # | Step | Expected |
|---|---|---|
| 3.1 | Ask *"What about for PLG?"* in the same chat | Stage briefly shows **"Searching for: …"** with a rewritten standalone query |
| 3.2 | Read the rewritten query | Pronouns resolved — something like "product-led growth pricing", not the literal "what about for PLG" |
| 3.3 | Read the answer | About PLG specifically, with citations different from turn 1 |
| 3.4 | Ask *"What did they say about retention?"* | Resolves against the conversation rather than retrieving noise |

**Fails if:** the query is passed through unrewritten · the answer repeats turn 1.

---

## 4 · Honest refusal — the most important test

| # | Step | Expected |
|---|---|---|
| 4.1 | New chat → *"What's the best Kubernetes ingress controller?"* | Amber pill: *"Not covered by the transcript archive"* |
| 4.2 | Read the response | States the archive does not cover it; names what it does cover; offers no answer |
| 4.3 | Check the citation area | **Zero citations.** No empty container |
| 4.4 | Try *"What's the capital of France?"* | Also refused, despite the model knowing the answer |
| 4.5 | Check the server logs | Either `retrieval.below_floor` (rejected by the cheap floor) or `relevance.rejected` (rejected by the topic gate), with the score |
| 4.6 | Try *"how does photosynthesis work?"* | Refused. This one is the reason the topic gate exists — it scores **0.62**, higher than several legitimate questions, so the score floor alone lets it through. |

**Fails if:** the assistant answers any of these. This is a defect, not a miss — it is the failure that destroys trust fastest.

**Note on why this needs two stages.** A cosine floor alone was measured and found insufficient: across a 20-question probe the separation gap was **−0.064**, meaning no threshold exists that admits every in-domain question while refusing every out-of-domain one. `python -m app.rag.calibrate` reproduces the measurement.

---

## 5 · Ship 30 essay

| # | Step | Expected |
|---|---|---|
| 5.1 | New chat → *"Write a Ship 30 essay about growth loops"* | Stage: Searching transcripts → Planning the structure → **Writing section 1 of 5** |
| 5.2 | Watch the section counter | Advances 1→5. Progress is visible throughout, never a static spinner |
| 5.3 | When complete | Artifact pane opens beside the chat with the essay rendered as Markdown |
| 5.4 | Check the scorecard | *"Ship 30 spec: N/9 checks"* with word count, sections, citations |
| 5.5 | Expand the scorecard | Nine named checks, each ✓ or ✗ with detail |
| 5.6 | Read the essay | H1 title · single-sentence hook · 5–6 H2 sections · bullets · `[S#]` citations · a takeaway section · a Sources list with timestamped links |
| 5.7 | Check the chat message | Says what was written and reports validation — does **not** repeat the essay |
| 5.8 | Click **Download** | Downloads a `.md` file with the full essay |

**Note:** on `llama3.2` some checks will fail. That is expected and is the point — the validator surfaces where a small model missed the spec rather than hiding it. Set `ESSAY_PROVIDER=azure` to compare.

**Fails if:** no scorecard · the essay renders as raw text in the chat instead of the viewer · sections do not stream.

---

## 6 · Artifacts and sandboxing

| # | Step | Expected |
|---|---|---|
| 6.1 | *"Make me an HTML one-pager summarising that"* | Artifact pane renders styled HTML — headings, colour, spacing |
| 6.2 | Toggle to **Source** | Shows the model's original output |
| 6.3 | Toggle back to **Rendered** | Renders inside an iframe |
| 6.4 | Inspect the iframe in devtools | `sandbox=""` — empty, granting nothing |
| 6.5 | View the iframe document head | `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; …">` |
| 6.6 | *"Add a script tag that shows an alert to that page"* | **No alert fires.** Amber banner names what was removed |
| 6.7 | Check the Source tab after 6.6 | The script **is** present in the raw output — proving the sanitizer acted, not the model |
| 6.8 | `GET /api/artifacts/{id}/render` directly | Response carries the CSP header. No script executes |
| 6.9 | `GET /api/artifacts/{id}/download?raw=true` | Downloads the pre-sanitization original — the sanitizer is auditable |
| 6.10 | Click **Copy** | Source copied to clipboard |

**Fails if:** any alert fires · the sandbox attribute is non-empty · a removal happens with no banner.

---

## 7 · Session isolation

| # | Step | Expected |
|---|---|---|
| 7.1 | Note the current conversation, then click **+ New chat** | Empty state with seeded questions |
| 7.2 | Ask an unrelated question | Answer shows no awareness of the previous chat |
| 7.3 | Ask *"What did I just ask you?"* | Refers only to this session |
| 7.4 | Switch back via the sidebar | Full history and artifacts restored |
| 7.5 | Reload the browser | Sessions persist — they are in PostgreSQL, not memory |
| 7.6 | Delete a session, then reload | Gone; its artifacts are gone too (cascade) |

---

## 8 · Resilience

| # | Step | Expected |
|---|---|---|
| 8.1 | Stop Ollama mid-session (`taskkill /IM ollama.exe /F`, or quit the tray app) | Within 20 s the sidebar dot turns amber and names the failing component |
| 8.2 | `GET /health` | `status: "degraded"`, `llm_provider.ok: false`, other components still reported |
| 8.3 | Send a message | Clear error inside the assistant turn with an actionable hint — **not** a stack trace or a silent hang |
| 8.4 | Restart Ollama, wait 20 s | Dot returns to green |
| 8.5 | Send a message | Works normally |
| 8.6 | Set `DATABASE_URL` to an invalid host and restart the API | **The API still starts.** `/health` reports the database down |
| 8.7 | Set `LLM_TIMEOUT_SECONDS=1`, restart, ask a question | `provider_timeout` with a hint about raising the limit |
| 8.8 | Send a request with a bad body (`{"message": ""}`) | 422 with the structured error envelope: `code`, `message`, `request_id`, `hint` |
| 8.9 | Note a `request_id` from an error, grep the logs | The matching log line is present |

---

## 9 · Provider switching

| # | Step | Expected |
|---|---|---|
| 9.1 | Note the header badge: `ollama · llama3.2` | |
| 9.2 | Edit `.env`: `LLM_PROVIDER=azure` plus the Azure block. Restart the API | **No code changed** |
| 9.3 | Reload the UI | Badge and sidebar show `azure` and the deployment name |
| 9.4 | Ask a question | Answers noticeably faster; still grounded and cited |
| 9.5 | Check the footer under the answer | Reports `azure` |
| 9.6 | Set `LLM_FALLBACK_PROVIDER=ollama`, break the Azure key, ask a question | Answer arrives from Ollama; the footer reports `ollama`; logs show `provider.failed` then `provider.fallback_succeeded` |
| 9.7 | **After any provider change, re-run `python -m app.rag.calibrate`** | Still `10/10` in-domain and `10/10` out-of-domain, exit code 0 |
| 9.8 | Repeat test 4.6 (*"how does photosynthesis work?"*) on the new provider | Refused, **0 citations**, `grounded: false` |
| 9.9 | Revert to `LLM_PROVIDER=ollama` | Back to local |

> **Why 9.7 and 9.8 are not optional.** The relevance gate makes an LLM call, so the grounding guarantee is a property of the *configured chat model*, not of the code alone. Switching to a reasoning model (`openai/gpt-oss-120b`) once disabled the gate entirely: its thinking trace consumed the gate's token budget, the empty response hit the fail-open branch, and photosynthesis came back marked grounded with four citations. The ceiling was raised to fix it, but the general point stands — a provider swap changes grounding behaviour, and only re-running the calibration proves it still holds. See [`architecture.md`](architecture.md#failing-open-has-a-failure-mode-of-its-own-a-reasoning-model).

---

## 10 · Responsive and accessibility

| # | Step | Expected |
|---|---|---|
| 10.1 | Narrow the window below 768px | Sidebar collapses to a hamburger; opens as an overlay drawer |
| 10.2 | Open an artifact on narrow | Takes the full width; close returns to the conversation |
| 10.3 | Widen past 1024px | Three panes; artifact capped so the conversation stays readable |
| 10.4 | Tab through the page | Every control reachable; focus ring always visible |
| 10.5 | Tab to a citation and press Enter | Expands |
| 10.6 | Enable a screen reader, send a message | Stage changes announced politely, without interrupting |
| 10.7 | Enable reduced motion at the OS level | Streaming dots stop animating |
| 10.8 | Type a multi-line message with Shift+Enter | Newline, no send. Enter alone sends |
| 10.9 | Start a long generation, click **Stop** | Generation halts; the composer re-enables |

---

## Sign-off

A build is releasable when sections 1–8 pass. Section 9 requires cloud credentials; section 10 is quality, not correctness.

**Section 4 is non-negotiable.** If the assistant answers an out-of-corpus question, do not ship — the entire value proposition rests on it refusing.
