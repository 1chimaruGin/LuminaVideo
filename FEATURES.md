# Feature Plan

What to build, in what order, and why. Read alongside `design-brief.md` (UX),
`DEVELOPMENT.md` (architecture) and `TECHSTACK.md` (stack).

Researched against the 2026 tool landscape — sources at the bottom.

---

## The gap, stated plainly

Subtitles and voiceover are **already specified** in `design-brief.md`: four recipes at
launch — Explainer, Talking-head from script, Clip my long video, Subtitle only. They are not
missing from the plan. They are missing from the **build**: the app currently exposes one
path (Explainer) and treats voice and captions as invisible pipeline stages rather than as
things a creator opens, judges and adjusts.

That is the first thing to fix, and it costs no new architecture — the stages already exist:

```
ingest → analyze → plan → [per-scene: generate] → voice → captions → compose
```

---

## What the market actually looks like

Every serious tool in 2026 owns **one link** of the same chain:

| Tool | Owns |
|---|---|
| Opus Clip | finding the good moments inside a long video, ranked |
| Submagic | animated captions and polish — but cannot ingest long-form |
| Descript | editing video by editing the transcript |
| Captions (Mirage) | avatars, one-tap restyle |
| ElevenLabs | voice — the clear leader on cloning and multilingual |

And the pipeline underneath all of them is identical: **transcribe → ask a model what matters
→ reframe → caption → render.**

The creator's actual job today is being the integration layer: exporting from one tool,
importing to the next, six times per video. `design-brief.md` names this exactly — "juggling
CapCut plus three separate AI tools and a lot of file-shuffling."

**So the product is not a better clipper or a better captioner. It is the one app that runs
the whole fixed stack in order.** That is a defensible position precisely because none of the
incumbents can take it without rebuilding as a different product.

---

## Recipes

The recipe *is* the feature surface. Each one is a different entry into the same stages, so
adding one is configuration plus prompts, not a new subsystem.

| Recipe | Input | Stages used | Competes with |
|---|---|---|---|
| **Explainer** | link, article, idea | all | Pictory |
| **Talking-head** | script + voice/avatar | plan, voice, captions, compose | HeyGen, Captions |
| **Clip my long video** | a long upload | ingest, analyze, captions, compose | Opus Clip |
| **Subtitle only** | a finished video | ingest, captions, compose | Submagic |
| **Recap** *(new)* | a title + your take | all | the faceless-channel stack |

### Why Recap earns a slot

It is one of the largest faceless-video categories, and it has a specific, well-documented
failure mode that a product can design away:

- Creators use **actual film footage**, which is one Content ID strike from channel deletion.
  The safe path is fully generated visuals — every frame original.
- The defensible posture is **transformative commentary**: critique, motivation, plot
  analysis. Not retelling.

Both are product decisions we can enforce rather than leave to the user: the Recap recipe
generates original visuals only, and its planner prompt is built around commentary structure.
That is a real reason to pick us over a stack of general tools, and it lands squarely in the
Safety plane that `DEVELOPMENT.md` already requires.

---

## Script Studio — the missing core

This is the answer to "some users will find scripting hard", and the highest-leverage thing
on this list. The script determines everything downstream: scene count, durations, voice
timing, caption density, cost. Getting it right before generation is where the money is saved.

### The script is beats, not a blob

Store and edit it as a structure, because every downstream stage needs the parts:

```
Hook      0–3s     the whole retention decision happens here
Setup     3–8s     context, one sentence
Turns     8–45s    the actual content, one point per beat
Payoff    ~5s      the takeaway
CTA       3–5s     one action
```

Research is unambiguous that **71% of viewers decide within 3 seconds**, so the Hook beat gets
its own treatment rather than being the first line of a paragraph.

### Hook lab

Generate five hooks for the same script, each labelled by the pattern it uses — contrarian
statement, surprising number, direct question, visual shock. The creator picks. This is a
single cheap LLM call and it targets the highest-variance part of the video.

### Read-aloud timing, before anything renders

The `LanguagePack` already carries `typography.cps` (characters per second), per language,
because reading speed varies ~3×. Use it in reverse: given a target duration, show whether the
script is over or under length **while it is being written** — not after twelve scenes have
been generated at the wrong pace.

This is the single most valuable use of the language-pack abstraction outside captions.

### Rewrite as small operations

Not "regenerate everything". Tighter, simpler, punchier, more concrete, shorter by 5 seconds —
applied to one beat, leaving the rest untouched. Same principle as per-scene regeneration on
the storyboard: **local edits must not disturb the parts the user already approved.**

### Three ways in, by effort

The brief's outcome-first principle applied to writing:

1. **An idea or a link** → we write the whole thing
2. **My script** → paste it, we structure it into beats and time it
3. **Guided** → answer three questions (who is it for, what is the one point, what should they
   do) → we draft from that

Most tools offer only (1) or only (2). The ladder is what makes it usable for someone who
finds writing hard *and* someone who already writes well.

---

## Voiceover as a surface, not a stage

Currently `voice` is an invisible pipeline step. It needs a screen, because voice is the
thing creators are most particular about:

- **Voice profile** — record ~60s once, reuse forever (already in the brief; strongest
  retention hook in the product)
- **Per-line re-record** — regenerate one scene's narration without touching the others
- **Direction** — pace, pauses, emphasis marks on specific words. A thin layer over the
  script, not a separate editor
- **Preview before spend** — hear the whole narration before any visual is generated

Route ElevenLabs behind the existing `Provider` protocol. It is the current leader on cloning
and multilingual, but naming it anywhere above the adapter violates invariant 2.

---

## Captions as a surface

The rendering approach is already decided (`DEVELOPMENT.md`: rasterize caption states, never
`drawtext`). What is missing is the creator-facing half:

- Word-by-word highlight timing, editable — alignment gets words wrong, and the fix must not
  require re-rendering the video
- Style presets in the Identity kit (already specified)
- **Platform post copy** — the caption that goes in the upload box, which is a different
  artefact from the on-screen captions and is currently nowhere in the product. TikTok rewards
  long keyword-rich copy (up to 4,000 chars); Reels wants 100–300. Same video, different copy,
  generated per destination.

---

## Optimisation

### Preview the script before the pictures

`design-brief.md` establishes preview-first at the *visual* tier: stills plus TTS instead of
generated video. Push it one step earlier — a **script preview**: read the whole thing aloud
with TTS, no images at all. Costs about a cent, takes seconds, and catches pacing and tone
problems that would otherwise be discovered after twelve scenes have been generated.

The cost ladder then reads:

| Stage | Order of magnitude |
|---|---|
| Script + read-aloud | ~$0.01 |
| Full preview (stills + TTS + captions) | $0.30–0.60 |
| Selected scenes upgraded to real video | $1–25 per scene, user's explicit choice |

Competitor stacks land under $3 per finished video. Our preview tier is an order of magnitude
below that, which is what makes iteration free enough to be the product's main loop.

### Reuse

- **Series memory** — episode 2 inherits voice, palette, caption style, character (specified)
- **Asset dedup** — same prompt + refs + seed returns the cached asset (specified)
- **Script templates per channel** — a creator's fifth explainer should start from the shape
  of their first four, not from zero

---

## Build order

Slots into the existing order in `DEVELOPMENT.md` after Milestone 2:

1. **Script Studio** — beats, timing from `cps`, hook lab, the three entry ramps.
   No new providers, no new stages. Highest value per unit of work on this list.
2. **Voice surface** — profile, per-line regeneration, direction, preview-before-spend
3. **Caption surface** — editable word timing, platform post copy per destination
4. **Subtitle-only and Clip recipes** — the two lanes that need no visual generation at all,
   so they are cheap to ship and immediately competitive with Submagic and Opus Clip
5. **Recap recipe** — original-visuals-only, commentary-structured planner, provenance
   stamping. Needs the Safety plane in place first.

Recipes 4 ship before 5 deliberately: they exercise `ingest` and `analyze` end-to-end on real
user media, which is the riskiest untested part of the pipeline.

---

## Sources

- [Opus Clip / Descript / Submagic / Captions / DaVinci — tool engineering, 2026](https://www.forasoft.com/learn/ai-for-video-engineering/articles-ai/opus-clip-descript-submagic-captions-ai-video-editor-tools-2026)
- [Opus Clip vs Submagic, 2026](https://www.ngram.com/blog/opus-clip-vs-submagic)
- [OpusClip alternatives, tested](https://pictory.ai/blog/opusclip-alternatives)
- [Faceless YouTube automation stack, 2026](https://autoadify.com/blog/faceless-youtube-ai-automation-channel-2026)
- [How to make a movie recap video with AI](https://www.pixara.ai/blogs/how-to-make-movie-recap-video-with-ai)
- [Best AI tools for faceless YouTube channels](https://recapo.ai/blog/best-ai-tools-for-faceless-youtube-channels/)
- [Viral hook templates, 2026](https://virvid.ai/blog/ai-shorts-script-hook-ultimate-guide-2026)
- [Short video script frameworks](https://virvid.ai/blog/short-video-script-frameworks-with-trending-examples)
- [Scripting YouTube videos for retention, 2026](https://rivereditor.com/guides/how-to-script-youtube-videos-2026)
