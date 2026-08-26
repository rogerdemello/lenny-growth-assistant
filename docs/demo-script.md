# Demo video script

**Camera on. One take. Lands at ~2:51.**

Everything in **bold brackets** is a stage direction — don't read it. The rest is meant to be said close to verbatim.

Measured: **389 spoken words**, which is 2:31 at 155 words a minute, plus roughly 20 seconds of waiting you can't narrate over. That fits the brief's 2–3 minutes with very little slack, so don't ad-lib extra sentences — if you want to say something that isn't here, cut something that is.

| Shot | Words | Time |
|---|---|---|
| 1 · Problem | 70 | ~27s |
| 2 · Runs locally | 28 | ~11s |
| 3 · Grounded answer | 46 | ~18s + streaming |
| 4 · Refusal | 51 | ~20s |
| 5 · Skill + artifact | 105 | ~41s, over a 37s generation |
| 6 · Trade-off | 89 | ~34s |

Read it out loud once before recording and change any phrase that feels wrong in your mouth.

---

## Before you press record

- [ ] **Close everything.** `llama3.2` needs ~3 GB resident and this machine has 15 GB. Chrome with forty tabs will make the demo look worse than it is.
- [ ] **Warm the model** — ask one throwaway question and discard it. First-token latency drops from ~11s to ~2s once it's resident.
- [ ] **Wake Supabase** — free projects auto-pause. Confirm `/health` shows `status: ok`.
- [ ] **Confirm `ESSAY_PROVIDER=azure`** in `.env`. Essays return in ~37s and score 9/9; on local `llama3.2` the same essay takes 8–12 minutes and scores 7/9. You mention this on camera — it's a decision, not a shortcut.
- [ ] **Delete old test sessions** so the sidebar looks deliberate.
- [ ] Run shot 3's question once beforehand so you know it works before you're live.

---

## 1 · The problem — 0:00–0:20

**[Camera on you. No screen yet.]**

> Product managers treat Lenny's Podcast as a professional reference. But when you half-remember a great argument about pricing from eighteen months ago, you have two bad options: scrub a ninety-minute video, or ask ChatGPT and get a confident answer you can't source.
>
> If you have to defend that decision to your team, an uncited answer is worthless. So this treats the citation as the product, not a footnote.

---

## 2 · It runs locally — 0:20–0:30

**[Share screen. Point at the sidebar.]**

> All of this runs on this laptop with no API key — Llama 3.2, three billion parameters, on CPU. Embeddings local too. Forty episodes, fifteen hundred passages indexed.

---

## 3 · A grounded answer — 0:30–1:05

**[Click the seeded question: "How should I think about pricing a B2B SaaS product?"]**

**[Narrate while it streams — don't wait in silence.]**

> It tells you what it's doing — searching, then writing. Here's the part that matters.

**[Citations appear. Expand one.]**

> Every claim ties to a real passage: guest, episode, timestamp. This isn't the model recalling a podcast, it's the transcript.

**[Click "Watch at…". Let YouTube land on the timestamp.]**

> And it lands on the exact second. Two clicks to verify anything.

---

## 4 · What it refuses — 1:05–1:25

**[New chat: "What's the best Kubernetes ingress controller?"]**

> Now the one I actually care about.

**[Amber refusal appears.]**

> Llama 3.2 definitely knows something about Kubernetes. The transcripts don't, so it says so.
>
> That's not a prompt asking nicely. When nothing clears the bar, retrieval hands the model no sources at all — there's nothing to answer from even if it wanted to.

---

## 5 · The skill and the artifact — 1:25–2:10

**[Type: "Write a Ship 30 essay about growth loops"]**

> It plans the structure, then writes section by section.

**[Essay lands ~37s. Expand the validation scorecard.]**

> The Ship 30 style isn't a prompt I improvised — it's a skill file, with the rhythm and the hook types encoded. And because it's encoded, I can check it. Nine automated checks, all green.
>
> This is the one place I go to the cloud, and I measured before deciding: locally it takes eight to twelve minutes and scores seven out of nine. Everything else is still on Ollama.

**[Type: "Make me an HTML one-pager of that"]**

> Generated HTML renders beside the chat, in a sandbox that blocks every outbound request. It's untrusted input — written by a model that just read text I don't control.

---

## 6 · The trade-off — 2:10–2:51

**[Camera on you. Pick ONE. The first is stronger.]**

### Option A — grounding needed two stages *(recommended)*

> One trade-off worth calling out. My original design used a similarity threshold to decide when to refuse. Measured against the real corpus, it didn't work — "how does photosynthesis work" scored *higher* than a genuine question about product discovery, because the embedding matches the shape of a question, not its topic.
>
> So grounding is two stages now: ten out of ten in-domain answered, ten out of ten refused. And I shipped the calibration as a command, so the next engineer can re-run it instead of trusting me.

### Option B — retrieve broadly, ground narrowly

> One trade-off worth calling out. I benchmarked this machine: eight retrieved passages cost twenty-two seconds before the first token. Four cost eleven.
>
> So I split it. The interface shows you eight citations; only four reach the prompt. You get more evidence to check, the model gets a prompt it can chew through. That number came from measuring on the hardware the customer actually has.

---

## Don't

- Read the plan aloud. Show the product.
- Apologise for the model being small. It's the constraint you designed around.
- Wait in silence while anything generates. Narrate.
- Say "as you can see" more than once.

## If you overrun

Drop the second half of shot 3 (the "two clicks to verify" line) and the first sentence of shot 5. That's ~15 seconds and costs nothing an evaluator is scoring.

## After

Upload to YouTube, then put the link at the top of `README.md`.
