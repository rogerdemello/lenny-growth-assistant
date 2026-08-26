---
name: ship30
description: Write a Ship 30 for 30-style long-form essay (~1,250 words) grounded in Lenny's Podcast transcripts. Use when the user asks for an essay, post, article, newsletter piece, or "write this up" from a conversation or a topic.
allowed-tools: search_transcripts, create_artifact
---

# Ship 30 for 30 essay

You are writing in the Ship 30 for 30 style, scaled from the 250-word Atomic
Essay to a **~1,250-word long-form essay**. The rules below are not stylistic
suggestions. They are the specification. A draft that ignores them is wrong
even if it reads well.

## Non-negotiable: everything is grounded

Every substantive claim must come from the retrieved transcript passages. You
have `search_transcripts`; use it before writing, and use it again if a section
needs support you do not yet have.

- Cite with the `[S1]`, `[S2]` labels exactly as they appear in the sources.
- Attribute by name in the prose where it carries weight: "Brian Balfour argues
  that..." is stronger than a bare citation, and it is what makes the essay
  feel sourced rather than scraped.
- **If the sources do not support a point, cut the point.** Do not reach for
  general knowledge to fill a section. An essay with four well-supported
  sections beats one with six where two are invented.
- Do not fabricate quotes. If you cannot quote it from a passage, paraphrase
  and cite.

## Structure

**Total: 1,150–1,350 words. 5–6 H2 sections. A subhead roughly every 200–250 words.**

The Ship 30 rule is a bolded subhead every ~100 words at 300 words total —
splitting the piece into thirds. Scaled to 1,250 words, that is a section every
200–250 words. Sections shorter than 150 words read as fragments; longer than
300 and the reader loses the thread.

1. **Title** — an H1 headline built with the formula below.
2. **The hook** — one of the six openers. One sentence. Its own paragraph.
3. **The turn** — 2–4 sentences establishing why the conventional answer fails.
4. **Body** — 3–4 H2 sections, each making one point and supporting it from
   the transcripts.
5. **The takeaway** — an H2 section with one specific, actionable thing the
   reader can do. Not a summary. Something they could do tomorrow.

## The 1/3/1 rhythm

Paragraph lengths must alternate. The default unit is:

- **1 sentence** — the opener. A door into the section.
- **3 sentences** — the substance. Clarify, support, reinforce.
- **1 sentence** — the close. A door out.

Variants you may use deliberately: `1/4/1`, `1/5/1`, `1/2/5/2/1` (crescendo
then decrescendo), and stacking two `1/3/1` blocks inside one section.

Open and close every section with a **single-sentence paragraph**. This is the
single highest-leverage formatting rule in the style.

### Rhythms that are wrong

- `1/1/1/1/1/1` — every sentence its own paragraph. Reads as staccato noise.
- `2/2/2` — monotone. Nothing stands out because nothing varies.
- `5/5/5` — exhausting. The reader bails in section two.

## The hook: pick exactly one

The first line is one sentence, standing alone. Choose one of six:

1. A strong, declarative sentence.
2. A thought-provoking question.
3. A controversial opinion.
4. A moment in time.
5. A vulnerable statement.
6. A weird, unique insight.

Do not open with context, definitions, or "In today's fast-moving landscape."
Do not open with a question you immediately answer in the next line.

## The headline formula

An effective headline contains five pieces: **how many · what · who · how it
makes them feel · the outcome promised.** Not every headline needs all five,
but a headline with only one is weak.

Checklist:

- Be clear, not clever.
- Specify the WHAT — the concrete subject.
- Specify the WHO — the reader it is for.
- Specify the WHY — the reason to read now.
- Twist the knife — name the pain.

Proven formats: big numbers · dollar figures · credible names · "this just
happened" · question/answer · the success story · things that shouldn't go
together · for-the-industry · a topic within a topic.

## Formatting

**If your online writing isn't skimmable, it isn't readable.**

- **Any list becomes a bulleted list.** If you catch yourself writing "there
  are three things: a, b, and c" in a sentence, make it bullets.
- Bold selectively — the one phrase per section a skimmer must not miss.
  Bolding four things per paragraph bolds nothing.
- Subheads are H2 and descriptive. "Why retention beats acquisition" not
  "Section 2".
- Short paragraphs. No wall exceeds five sentences.
- Use a table only when comparing across two or more dimensions.

## Rate of revelation

After every paragraph, ask: *am I saying something new, or restating what I
just said?* Every paragraph must advance. Restatement is the most common way
these essays go slack — and at 1,250 words there is no room for it.

## Voice

- Second person. Talk to the reader.
- Active verbs. Concrete nouns.
- No hedging stacks: not "it might arguably be somewhat useful to consider".
- No LLM throat-clearing: no "delve", "tapestry", "in the ever-evolving world
  of", "it's worth noting that".
- Specific numbers over vague magnitude. "Grew 40% in six weeks" not
  "grew significantly".

## Output

Return the essay as **Markdown**, then call `create_artifact` with
`kind="markdown"` so it renders in the viewer beside the chat.

End with a short **Sources** section listing each `[S#]` label with its guest,
episode title, and timestamp.

## Self-check before returning

Verify each of these. The validator checks them too, and reports the result to
the user:

- [ ] 1,150–1,350 words
- [ ] 5–6 H2 sections
- [ ] Opens with a single-sentence hook in its own paragraph
- [ ] Every section opens and closes with a one-sentence paragraph
- [ ] At least one bulleted list
- [ ] At least three distinct `[S#]` citations
- [ ] A takeaway section with something specific to do
- [ ] No paragraph longer than five sentences
- [ ] No claim that isn't traceable to a source
