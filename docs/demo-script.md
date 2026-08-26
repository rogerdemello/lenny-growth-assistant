# Demo video — shot list

**Target: 2–3 minutes, camera on.** The brief asks you to explain the problem, show the product, demonstrate local Ollama, and cover one technical trade-off.

Record in **one take**. An unedited take that runs 2:50 beats a polished one that took an hour you did not have.

---

## Before you hit record

- [ ] **Close everything memory-hungry.** `llama3.2` needs ~3 GB resident; free RAM was 0.4 GB during development. Chrome with 40 tabs will make this demo look worse than it is.
- [ ] **Warm Ollama** — send one throwaway question so the model is loaded. First-token latency drops from ~11 s to ~2 s once it is resident. `OLLAMA_KEEP_ALIVE=30m` keeps it there.
- [ ] **Wake Supabase** — free projects auto-pause. Open the dashboard and confirm `GET /health` is green.
- [ ] **Confirm `ESSAY_PROVIDER=azure`** in `.env`. Essays return in ~37 s and score 9/9 on the validator; on local `llama3.2` the same essay takes 8–12 minutes and scores 7/9. Everything else in the demo still runs on Ollama. Say this out loud in shot 5 — it is a deliberate decision, not a shortcut.
- [ ] **Two browser tabs:** the app, and `http://127.0.0.1:8000/health`.
- [ ] Close the sidebar drawer, clear old test sessions so the UI looks intentional.

---

## The shots

### 1 · The problem (0:00–0:25)

Camera on you, no screen yet.

> "Product managers use Lenny's Podcast as a professional reference. But when you're about to make a pricing decision and you half-remember someone making a great argument about it eighteen months ago, you have two bad options: scrub through a ninety-minute video, or ask ChatGPT and get a confident answer you can't source. If you have to defend the decision to your team, an uncited answer is worthless. So the thing I built treats the citation as the product, not a footnote."

### 2 · It's running locally (0:25–0:45)

Show the sidebar, then the health tab.

> "Everything you're about to see runs on this laptop. No API key. That's Ollama serving llama3.2, a three-billion-parameter model, on CPU — no GPU in this machine. Embeddings are nomic-embed-text, also local. The only thing that isn't local is Postgres, which is a free Supabase project with pgvector."

Point at the badge: `ollama · llama3.2`. Point at the corpus count.

### 3 · A grounded answer (0:45–1:20)

Ask: **"How should I think about pricing for a B2B SaaS product?"**

Narrate while it streams — do not wait in silence:

> "It's telling me what it's doing — searching transcripts, then writing. And here's the part that matters."

When citations appear, **click one and expand it**:

> "Four passages. Each one is a real chunk of transcript with the speaker and the timestamp. And this link—"

**Click the YouTube link.** Let it land on the timestamp.

> "—goes to the exact second. Two clicks to verify any claim. That's the whole product."

### 4 · Refusal (1:20–1:40)

New chat. Ask: **"What's the best Kubernetes ingress controller?"**

> "Now the important one. llama3.2 absolutely knows something about Kubernetes. But the transcripts don't cover it—"

Point at the amber pill.

> "—so it says so instead of answering. That's not a prompt asking it nicely. Retrieval has a similarity floor, and below it the model is handed *no sources at all*. It can't answer from its own knowledge because there's nothing there to answer from. That's enforced in code, and it's tested."

### 5 · Skill and artifact (1:40–2:20)

Ask: **"Write a Ship 30 essay about growth loops"**

> "It's planning the structure first, then writing section by section — you can see the counter."

> "The Ship 30 style isn't a prompt I improvised. It's a skill file — the 1/3/1 rhythm, the six hook types, the headline formula, all encoded. And because it's encoded, I can *check* it."

**Expand the validation scorecard.**

> "Nine automated checks against the spec, all green. And this is the one place I route out to the cloud — I measured it: the local 3B model takes eight to twelve minutes and scores seven out of nine, mostly missing the word count. Everything else you've seen — the retrieval, the citations, the refusal, the artifacts — is still running on Ollama on this laptop. The provider is shown per message, and it's one line in a config file."

Then: **"Make me an HTML one-pager of that"**

> "Rendered beside the chat, in an iframe with an empty sandbox attribute and a content security policy that blocks every outbound request. Generated HTML is untrusted input — it's written by a model that just read text I don't control."

### 6 · The trade-off (2:20–2:50)

Camera on you.

Pick **one**. The retrieval split is the strongest because it is measured:

> "One trade-off worth calling out. I benchmarked this machine: eight retrieved passages cost twenty-two seconds before the first token appears. Four cost eleven. So I split it — the interface shows you eight citations, but only four go into the prompt. You get more evidence to check, the model gets a prompt it can actually chew through, and the demo doesn't stall. That number came from measuring, not guessing, and it's the kind of thing you only find by running it on the hardware the customer actually has."

Alternative, if you would rather discuss compliance:

> "The brief specified the Claude Agent SDK. I built that runtime — same tool registry, same skill file — but I didn't have an Anthropic key, and its bundled agent sends a fifteen-thousand-token system prompt that a 3B model on CPU simply can't absorb. So the demo runs on a local runtime that shares all the same components, and I documented exactly what's unrun and why. I'd rather hand over something honest than something that looks complete."

---

## Do not

- Read the plan aloud. Show the product.
- Apologise for the model being small. Frame it as the constraint you designed around.
- Wait in silence during generation. Narrate what is happening.
- Try to show everything. Six shots is already a lot for three minutes.

## After

Upload unlisted or public to YouTube, then add the link to the top of `README.md`.
