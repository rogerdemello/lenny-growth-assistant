# Demo video script

**Target 2:45. Camera on. One take.**

Everything in **bold brackets** is a stage direction — don't read it. Everything else is meant to be said close to verbatim. It's written for speech, not for the page, so read it out loud once before recording and change any phrase that feels unnatural in your mouth.

Total spoken words: ~420, which is about 2:20 at a normal pace, leaving ~25 seconds of breathing room while things load.

---

## Before you press record

- [ ] **Close everything.** `llama3.2` needs ~3 GB resident and this machine has 15 GB total. Chrome with forty tabs will make the demo look worse than it is.
- [ ] **Warm the model** — ask one throwaway question and discard it. First-token latency drops from ~11s to ~2s once the model is resident.
- [ ] **Wake Supabase** — free projects auto-pause. Confirm `GET /health` shows `status: ok`.
- [ ] **Confirm `ESSAY_PROVIDER=azure`** in `.env`. Essays come back in ~37s and score 9/9. On local `llama3.2` the same essay takes 8–12 minutes and scores 7/9. You *will* mention this on camera — it's a decision, not a shortcut.
- [ ] **Two tabs:** the app at `localhost:5173`, and `127.0.0.1:8000/health`.
- [ ] **Delete old test sessions** so the sidebar looks deliberate.
- [ ] Have the answer to shot 3 already generated once, so you know it works before you're live.

---

## 1 · The problem — 0:00 to 0:22

**[Camera on you. No screen yet.]**

> Product managers treat Lenny's Podcast as a professional reference. But when you're about to make a pricing decision and you half-remember someone making a great argument about it eighteen months ago, you've got two bad options. Scrub through a ninety-minute video, or ask ChatGPT and get a confident answer you can't source.
>
> And if you have to defend that decision to your team, an uncited answer is worthless. So the thing I built treats the citation as the product — not as a footnote.

---

## 2 · It runs locally — 0:22 to 0:38

**[Share screen. App open. Point at the sidebar.]**

> Everything you're about to see runs on this laptop. No API key for the chat.
>
> That's Ollama serving Llama 3.2 — three billion parameters, on CPU, there's no usable GPU in this machine. Embeddings are nomic-embed-text, also local. The only thing that isn't local is Postgres, which is a free Supabase project with pgvector.

**[Point at the corpus count in the sidebar.]**

> Forty episodes, about fifteen hundred passages indexed.

---

## 3 · A grounded answer — 0:38 to 1:20

**[Click the seeded question: "How should I think about pricing a B2B SaaS product?"]**

**[Narrate while it streams — do not wait in silence.]**

> It tells you what it's doing. Searching transcripts, then writing. And here's the part that matters.

**[Citations appear. Click one to expand it.]**

> Every claim is tied to a real passage — the guest, the episode, the timestamp. This isn't the model recalling a podcast. This is the transcript.

**[Click the "Watch at…" link. Let YouTube load and land on the timestamp.]**

> And that goes to the exact second. Two clicks to verify anything it told me.
>
> That's the whole product. If a PM can't show where an idea came from, they're making an assertion. If they can, they're making a case.

---

## 4 · What it refuses — 1:20 to 1:42

**[New chat. Type: "What's the best Kubernetes ingress controller?"]**

> Now the one I actually care about.

**[Amber refusal appears. Point at it.]**

> Llama 3.2 definitely knows something about Kubernetes. But the transcripts don't cover it, so it says so.
>
> And that isn't a prompt asking it nicely. Retrieval hands the model *no sources at all* when nothing clears the bar — so there's nothing to answer from even if it wanted to. That's enforced in code, and it's tested.

---

## 5 · The skill and the artifact — 1:42 to 2:18

**[Type: "Write a Ship 30 essay about growth loops"]**

> It plans the structure first, then writes section by section.

**[Essay lands, ~37s. Artifact pane opens. Expand the validation scorecard.]**

> The Ship 30 style isn't a prompt I improvised — it's a skill file. The 1/3/1 rhythm, the six hook types, the headline formula, all encoded. And because it's encoded, I can *check* it. Nine automated checks against the spec, all green.
>
> This is also the one place I route out to the cloud, and I measured it before deciding: the local model takes eight to twelve minutes and scores seven out of nine. Everything else you've seen is still on Ollama.

**[Type: "Make me an HTML one-pager of that"]**

**[It renders in the split pane.]**

> Generated HTML renders beside the chat — in an iframe with an empty sandbox attribute and a policy that blocks every outbound request. It's untrusted input. It was written by a model that just read text I don't control.

---

## 6 · The trade-off — 2:18 to 2:45

**[Camera on you. Pick ONE of the two below. The first is stronger.]**

### Option A — grounding needed two stages *(recommended)*

> One trade-off worth calling out. My original design used a similarity threshold to decide when to refuse. When I finally measured it against the real corpus, it didn't work — "how does photosynthesis work" scored higher than a real question about product discovery, because the embedding matches the *shape* of a question, not its topic. No threshold separates those.
>
> So grounding is two stages now: a cheap filter, then a topic check on anything that isn't clearly relevant. Ten out of ten in-domain answered, ten out of ten refused. And I shipped the calibration as a command, so the next person can re-run it instead of trusting me.

### Option B — retrieve broadly, ground narrowly

> One trade-off worth calling out. I benchmarked this machine: eight retrieved passages cost twenty-two seconds before the first token appears. Four cost eleven.
>
> So I split it. The interface shows you eight citations; only four go into the prompt. You get more evidence to check, the model gets a prompt it can chew through, and the demo doesn't stall. That number came from measuring, not guessing — and it's the kind of thing you only find by running it on the hardware the customer actually has.

---

## Don't

- Read the plan aloud. Show the product.
- Apologise for the model being small. It's the constraint you designed around — say so.
- Wait in silence while anything generates. Narrate.
- Try to show everything. Six shots is already a lot for under three minutes.
- Say "as you can see" more than once.

## If you overrun

Cut shot 2 down to one sentence ("all of this runs locally on a 3B model, no API key") and fold the corpus count into shot 3. That buys ~10 seconds without losing anything an evaluator is scoring.

## After

Upload to YouTube — unlisted is fine unless the form says otherwise — and put the link at the top of `README.md`.
