# Design

## Principles

**1. The citation is the product, not a footnote.**
This user needs to defend a decision, not just make one. So citations are not collapsed behind a "sources" disclosure — they sit directly under the answer, expand to show the actual transcript passage, and link to the exact second of the episode. Verification is two clicks. An assistant that cites but cannot be checked is a more confident hallucination.

**2. Say what you don't know, loudly.**
Refusal is a first-class state with its own visual treatment, not an error. When the archive does not cover a question, the UI says so before the text arrives. A product whose trust story is grounding cannot treat "I don't know" as a failure path.

**3. Slowness must look like progress.**
On CPU a good answer takes 15 seconds and an essay takes minutes. That is a real constraint, not a bug to hide behind a spinner. Every stage names itself — "Searching transcripts", "Writing section 3 of 5" — so the wait reads as work rather than a hang.

**4. Show the machinery.**
The active provider, model, runtime and corpus size are visible without opening a menu. This is an internal tool for a technical team, and the first question anyone asks about a local-model demo is "what is it actually running?"

**5. Never render what you haven't checked.**
When the sanitizer removes something, the UI says what and offers the original in the source tab. Silent modification is the wrong default for content a user may publish.

---

## Information architecture

```
┌──────────┬─────────────────────────────┬──────────────────┐
│ Sessions │  Conversation               │  Artifact        │
│          │                             │                  │
│ + New    │  ┌───────────────────────┐  │  title · kind    │
│          │  │ user message      ──► │  │  ────────────    │
│ Pricing… │  └───────────────────────┘  │  validation      │
│ Retention│                             │  ────────────    │
│ Growth…  │  assistant answer           │  sanitizer note  │
│          │  🛡 Grounded in 4 passages   │  ────────────    │
│          │  [S1] Guest · Episode 12:04 │                  │
│──────────│  [S2] Guest · Episode 41:30 │  rendered ⇄ src  │
│ ● ok     │  ollama · llama3.2 · 14.8s  │  copy · download │
│ ollama   │                             │                  │
│ llama3.2 │  ┌───────────────────────┐  │                  │
│ 40 eps   │  │ ask something…  [Send]│  │                  │
└──────────┴─────────────────────────────┴──────────────────┘
   240px              flexible                 ≤46%
```

Three regions, ordered by how often they are touched: navigation (rarely), conversation (constantly), artifact (on demand, and dismissible).

The artifact pane is not a modal. A user comparing an essay against the answer that produced it needs both at once — that is the entire reason Claude Artifacts works, and a modal would break it.

**System status lives at the bottom of the sidebar**, not in a settings screen. It is reference information consulted mid-task, not configuration to be changed.

---

## Key interaction states

### Empty

Not a blank canvas. Four seeded questions that demonstrate the four capabilities — a grounded answer, a retention question, a PMF question, and an essay request — so the first interaction is a success rather than a guess about what the thing accepts.

### Streaming

The stage label is the design work here. Generic "Thinking…" for fifteen seconds reads as broken; a named stage reads as progress:

| Backend stage | Shown as |
|---|---|
| `routing` | Reading your question |
| `condensed` | Searching for: *rewritten query* |
| `retrieving` | Searching transcripts |
| `generating` | Writing the answer |
| `outlining` | Planning the structure |
| `writing` | Writing section 3 of 5 |

The condensed query is shown deliberately. When a follow-up is rewritten, the user should see what was actually searched for — otherwise a wrong rewrite produces a baffling answer with no visible cause.

**Stop** replaces **Send** while streaming. On a model that can take four minutes, the ability to abandon a generation is not a nicety.

**Autoscroll yields to the user.** The view follows the stream only while the user is within 120px of the bottom. Yanking the viewport back down while someone is reading an earlier citation is the most annoying thing a streaming chat UI can do.

### Grounded answer

Citations render as a full-width stack rather than inline chips — the guest and episode title need room to be legible, and truncating them to fit a chip defeats the purpose. Each row expands in place to reveal the passage, the speaker, the similarity score, and a "Watch at 12:04 →" link.

The similarity score is shown. It is jargon, but the audience is technical and it is the fastest way to judge whether a marginal citation is worth opening.

### Refusal

Amber pill above the text: *"Not covered by the transcript archive."* Zero citations shown — an empty citation area would suggest a rendering failure. The message names what the archive does cover and invites a rephrase.

### Sanitizer intervention

Amber banner in the artifact header, listing exactly what was removed, with a pointer to the source tab. Never silent.

### Validation

The Ship 30 scorecard is collapsed to a single line — *"Ship 30 spec: 7/9 checks · 1,240 words · 5 sections · 4 citations"* — expanding to the per-check detail. Green when it passes, amber when it does not.

Showing failures is the point. A skill with a validator that only reports success is a skill with no validator.

### Degraded

The sidebar dot turns amber within 20 seconds of a component failing, and names it. Config is polled rather than fetched once, so stopping Ollama mid-session surfaces without a reload — which is exactly the scenario the demo walks through.

### Error

Errors render inside the assistant turn, not as a toast. They carry the `hint` from the API, because "Ollama is not reachable. Start it with `ollama serve`" is actionable and "Request failed (503)" is not.

---

## Responsive behaviour

| Width | Layout |
|---|---|
| ≥1024px | Three panes. Artifact capped at 46% so the conversation stays readable. |
| 768–1023px | Sidebar visible; artifact replaces the conversation when open. |
| <768px | Sidebar becomes an overlay drawer; artifact takes the full width. |

The sidebar becomes a drawer rather than disappearing, because session switching is a primary action and hiding it entirely would strand a mobile user in one conversation.

The composer is a `textarea` that grows to a 180px cap. Enter sends, Shift+Enter newlines — the convention users already have.

---

## Accessibility

- **Keyboard.** Every control is reachable and operable. Focus is visible everywhere via `:focus-visible` with a 2px accent outline, never suppressed.
- **Semantics.** `<nav aria-label="Chat sessions">`, `<main>`, `<article>` per turn, `role="tablist"` on the view toggle with `aria-selected`, `aria-expanded` on every disclosure.
- **Live regions.** The stage indicator is `role="status" aria-live="polite"`, so a screen reader announces progress without interrupting.
- **Colour is never the only signal.** Grounded state carries a shield icon and the words "Grounded in N passages". Validation checks show ✓/✗ glyphs alongside colour. The degraded dot is paired with text.
- **Contrast.** Body text `--color-ink-900` on white is ~14:1. The lightest text in use, `--color-ink-500`, is ~4.6:1 — above AA for its size. Amber and green are used for fills behind dark text, never as text on white.
- **Motion.** `prefers-reduced-motion` disables the streaming dots and all transitions.
- **Labels.** The composer has a visually-hidden `<label>`; icon-only buttons carry `aria-label`.

---

## Artifact security policy

What the viewer permits, blocks, and why. Two independent layers — the sanitizer strips, the sandbox contains — and neither is relied on alone.

### Allowed

| Category | Elements |
|---|---|
| Structure | `html` `head` `body` `div` `span` `section` `article` `header` `footer` `main` `aside` `nav` |
| Text | `h1`–`h6` `p` `br` `hr` `blockquote` `pre` `code` `em` `strong` `b` `i` `u` `s` `small` `sub` `sup` `mark` `abbr` `cite` `q` `time` |
| Lists | `ul` `ol` `li` `dl` `dt` `dd` |
| Tables | `table` `thead` `tbody` `tfoot` `tr` `th` `td` `caption` `colgroup` `col` |
| Media | `a` (http/https/mailto/relative), `img` (`data:image/*` or `https:`) |
| Styling | `<style>` blocks, `style` attributes |
| Graphics | inline `svg` and its shape elements |

### Blocked

| Blocked | Why |
|---|---|
| `<script>` — tag **and body** | Arbitrary execution. The body goes too, so nothing is left as visible text. |
| `on*` attributes | Event-handler execution. |
| `javascript:` URLs | Execution via navigation. |
| `data:text/html` in `href` | Navigation-based XSS. Note `data:image/*` in `src` **is** allowed — the same scheme is safe in one attribute and dangerous in another. |
| `<iframe>` `<object>` `<embed>` `<applet>` | Nested browsing contexts and plugin execution. |
| `<form>` | Credential phishing against a real-looking page. |
| `<meta http-equiv="refresh">` | Redirect to an attacker page. |
| `<base>` | Rewrites every relative URL in the document. |
| `<link>` | External resource loading. |
| CSS `@import` | Remote stylesheet; can exfiltrate via attribute selectors. |
| CSS `expression()`, `behavior:`, `-moz-binding` | Legacy script execution vectors. |
| CSS `url()` to anything but `data:`/`https:` | Remote fetch and beaconing. |
| Remote `img` sources | Blocked by CSP. A remote image URL is an exfiltration channel — the URL itself carries the data. |
| HTML comments | Can hide payloads from review. |

### Isolation

```html
<iframe sandbox="" srcdoc="…">
```

`sandbox=""` is the maximally restrictive value: no scripts, no same-origin, no forms, no popups, no top-level navigation. Combined with:

```
default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'
```

**An artifact cannot make a single outbound network request.** Not a script, not a stylesheet, not an image, not a font. Even a complete sanitizer bypass lands in a context with no execution and no exfiltration path.

The same CSP is set as response headers on `GET /api/artifacts/{id}/render`, so opening an artifact URL directly is protected too — the `srcdoc` iframe has no response headers of its own, which is why the policy appears in both places.

**The trade-off, accepted deliberately:** interactive HTML artifacts do not work. A chart that responds to clicks, a form, a live calculator — none of it runs. For an assistant whose output is documents and one-pagers, refusing to execute untrusted JavaScript is the right call, and it makes the policy explainable in one sentence: **artifacts are documents, not programs.**

Markdown takes a different route to the same place: rendered through `react-markdown` **without** `rehype-raw`, so embedded HTML is inert by construction rather than by filtering. The server-side pass additionally rewrites unsafe link schemes and escapes raw HTML blocks so they display as text.

---

## Design decisions and their trade-offs

**Retrieve 8, prompt 4.** Citations are cheap to display and expensive to feed a model. Measured on a Ryzen 7 7730U with no GPU: 8 passages cost ~22s to first token, 4 cost ~11s. The user sees more evidence than the model reads — which is fine, because the citations shown are filtered to those the answer actually referenced.

**Keyword routing, not a classifier.** A regex that is right ~90% of the time and costs 0 ms beats a model call that is right ~92% and costs ten seconds of prefill. The failure mode is benign: `chat` is the default and `chat` always retrieves, so a misrouted essay request produces a grounded answer rather than nothing. On a frontier model, letting the model choose its own tools would be the better design.

**Essay sections do not see previous sections.** Each generation receives the outline and the sources, never the accumulated draft. Otherwise prefill grows with the essay and section five costs four times section one. Coherence comes from the outline telling each section what its neighbours cover.

**Vector-only retrieval, hybrid deferred.** The `tsvector` column and GIN index ship, and lexical search runs as a fallback when embeddings are down. RRF fusion does not, because tuning it needs an evaluation set and an untuned hybrid can rank *worse* than plain vector search. Shipping an uncalibrated knob and calling it hybrid retrieval would be worse than shipping without it.

**No dark mode.** A real cost — it is the first thing a developer audience notices. Traded for the artifact viewer, the validation scorecard, and the degraded-state handling, all of which the brief explicitly asks for. `color-scheme: light dark` is declared so form controls behave, and the token layer means adding it later is a variable swap rather than a rewrite.

**A `<textarea>`, not a rich editor.** Nothing in the input needs formatting.

**Polled config, not websockets.** A 20-second poll is enough to surface a provider going down, and it costs one endpoint instead of a second transport.
