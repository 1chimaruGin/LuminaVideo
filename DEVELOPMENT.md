# Development Notes

Engineering handoff. Read alongside `design-brief.md`, which covers UX and screens.

## What we're building

A web app that turns a creator's brief into a finished short-form video. The user describes
an outcome; the system plans it, routes generation across third-party AI models, and
assembles the result.

We are an **orchestration layer over third-party generative models**, not a video editor.
Almost all generation is somebody else's API. Our value is planning, routing, cost control,
consistency across episodes, and assembly.

---

## Non-negotiable invariants

These are the rules that keep the architecture from rotting. Violating any of them should
fail review.

1. **No language branching outside the language pack.** If you find `if lang == "..."` in a
   worker, planner, or renderer, the abstraction has leaked. All language behaviour goes
   behind `LanguagePack`.

2. **No provider branching outside the adapter.** Same rule. The router knows capabilities and
   cost, never vendor-specific request shapes.

3. **Every stage is idempotent and content-addressed.** Re-running a stage with the same
   inputs must produce the same artifact key and must not double-charge.

4. **Credits are reserved before work starts, never charged after.** See ledger semantics below.

5. **The plan is a user-editable artifact, not hidden reasoning.** Users regenerate scene 4
   without touching scenes 1–3. The data model must make that trivial.

6. **Nothing assumes the user is still connected.** Jobs run minutes. Every long operation is
   resumable and notifies on completion.

---

## Architecture — five planes

**Experience** — input, recipes, storyboard, delivery. Thin. Talks only to the API.

**Intelligence** — planner (brief → typed plan), language pack, safety, cost estimator.
The estimator is separate from the router on purpose: it answers "what will this cost"
without committing to a provider, so the UI can price a plan before running it.

**Execution** — orchestrator (stage state machine), model router, provider adapters, composer
(ffmpeg).

**State** — assets, projects, identity kit, credit ledger. This plane is what makes the
product more than a pipeline.

**Platform** — auth, payments, notifications, telemetry.

---

## Core data model

```
User
Channel        identity kit: voice_profile, logo, palette, fonts, caption_style, intro/outro
Project        belongs to Channel; holds Plan versions and Renders
Plan           typed, versioned, user-editable
Scene          one shot: prompt, duration, refs[], script_line, vo_asset, caption, state
Asset          content-addressed (sha256), immutable, typed (image|clip|audio|video)
Job            one execution of one stage; idempotency_key, status, provider_ref
LedgerEntry    double-entry; reserve | commit | refund
```

Scene state is the important one:

```
draft → previewing → preview_ready → upgrading → final → failed
```

Preview and final are different assets on the same scene. Never overwrite a preview with a
final; keep both so the user can revert.

---

## Job and stage model

Stages are pure functions producing immutable artifacts:

```
ingest → analyze → plan → [per-scene: generate] → voice → captions → compose
```

Each stage writes an artifact and records it. Changing caption style re-runs `compose` only.
Changing one scene re-runs one `generate`. This is most of our COGS control.

For v1 use an explicit state machine in Postgres plus a Redis queue. Do not reach for a
workflow engine yet; the stage boundaries are clean enough to migrate later if needed.

Workers split by resource profile — mixing these is the classic mistake that triples cost:

| Pool | Work | Hardware |
|---|---|---|
| `light` | planning, LLM calls, provider polling, ingest | small, always on |
| `cpu-render` | ffmpeg compose | cheap CPU, burst |
| `gpu` | any self-hosted inference | scale to zero, spot |

---

## Provider adapter interface

Every generative vendor sits behind this. Nothing above it knows vendor names.

```python
class Provider(Protocol):
    name: str
    capabilities: Capabilities   # modalities, durations, aspects, max_refs, languages
    def estimate(self, job: GenJob) -> Cents: ...
    def submit(self, job: GenJob) -> Handle: ...
    def poll(self, h: Handle) -> Status | Asset: ...
    def cancel(self, h: Handle) -> None: ...
```

Things that differ between vendors and must be absorbed here: auth, async semantics
(poll vs webhook), input schema, supported aspect ratios and durations, content policy,
regional availability, failure modes, latency (20s to 10min).

**Routing policy**, in order: capability filter → tier filter (what the plan allows) →
cost sort → health check → submit, with a fallback chain and cost re-estimation on fallback.
Providers go down regularly; automatic fallback is not optional.

**Capacity.** Each provider has concurrency caps and rate limits. Token bucket per provider,
plus circuit breakers fed by telemetry so a degraded vendor drops out of routing automatically.

**Caching.** Same prompt + same refs + same seed = same output. Deduplicate at the asset layer.
Users regenerate constantly; this is a meaningful fraction of COGS.

---

## Ledger semantics

Double-entry, not a balance integer. Get this wrong and it costs real money.

```
plan approved   → reserve(estimate)
asset returned  → commit(actual)
failure/reject  → refund(reserved)
```

Reserve at plan approval, not at submit — otherwise a user can queue a hundred scenes against
a balance that covers ten. Reconcile against actual provider invoices monthly; the estimate
will drift and you need to see the drift.

Refund on: provider error, timeout, policy rejection, user cancel before submit.
No refund on: successful generation the user simply dislikes.

---

## Cost model — the thing that decides viability

We are reselling. COGS is somebody else's price list, and the spread across tiers is roughly
60×. Per 60-second video, order of magnitude (verify current rates, they move monthly):

| Tier | Per 60s |
|---|---|
| Premium video gen with audio | $15–25 |
| Mid tier | $3–6 |
| Budget gen | $1–2 |
| **Image gen + programmatic motion** | **$0.30–0.60** |
| Existing footage + TTS + captions | $0.10–0.20 |

**Default routing tier is image-plus-motion.** Twelve generated stills plus Ken Burns,
parallax, and transitions in ffmpeg costs ~1% of premium video generation and is good enough
for most short-form content. Premium models are a metered upsell the user explicitly chooses
per scene.

This is why preview-first exists. It is a cost architecture, not just a UX nicety.

---

## Language pack interface

Implement one language fully. The interface exists so adding the next is a pack, not a rewrite.

```python
class LanguagePack(Protocol):
    code: str                       # BCP-47
    def normalize(self, text: str) -> str: ...        # encoding/variant folding
    asr: ASRConfig                                     # model id, decode params
    def align(self, audio, text) -> WordTimings: ...
    def tokenize(self, text: str) -> list[Unit]: ...   # units for caption highlighting
    def linebreak(self, text: str, width: int) -> list[str]: ...
    typography: Typography          # font stack, shaping engine, chars-per-second
    llm_profile: PromptProfile      # planner prompts, hook patterns, examples
```

Notes that will bite later if ignored:
- `tokenize` returns **units**, not words — granularity differs enormously by script.
- `typography.cps` must be per-language. Reading speed varies by ~3×. Never hardcode
  caption duration.
- Line breaking needs an override hook; ICU alone won't give good caption aesthetics for
  every script.
- Some fonts are 5–20MB. Load lazily, per pack, never bundled.

---

## Caption rendering — do not use ffmpeg drawtext

`drawtext` uses FreeType without HarfBuzz shaping, which breaks any script with stacked marks
or contextual forms. Two acceptable paths:

1. `libass` via ASS subtitles (HarfBuzz-shaped, correct by default), or
2. rasterize caption states server-side as transparent PNGs (Pango/HarfBuzz) and overlay.

Prefer (2). It's correct for every script and gives the word-by-word highlight animation users
actually want. Rasterize once per caption **state**, not per frame — a line with five highlight
steps is five PNGs gated by `enable='between(t,...)'`.

---

## Safety plane

Sits **before** the router, not after. Three jobs:

- **Pre-flight policy check** against the target provider's rules, so we fail fast and locally
  instead of burning a paid call on a rejection.
- **Likeness gate** on uploaded faces — consent confirmation, no public figures. We accept face
  uploads and generate video from them; this is real deepfake exposure.
- **Provenance stamping** on output. Platforms increasingly require AI-content disclosure, and
  some providers embed watermarks that must be preserved through composition.

---

## Suggested stack

Change freely if there's a reason; this is a default, not a mandate.

- API: FastAPI, Postgres, Redis
- Workers: Python, ffmpeg, containerized
- Object storage: **Cloudflare R2** — zero egress. Video egress on S3 will destroy margin.
- Ingest: `yt-dlp` for links, resumable (tus) uploads with client-side downscale as fallback
- Queue: Redis + arq or Celery

---

## Build order

1. **Skeleton** — auth, projects, asset store, job state machine, one fake provider.
   Prove a job runs end to end and produces a file.
2. **One recipe end to end** — Explainer, preview tier only (image + TTS + captions + compose).
   No premium generation at all. This is the whole product in cheap mode.
3. **Ledger and estimator** — before any paid provider is wired up. Never ship provider calls
   without reservation in place.
4. **Real providers** — two, behind the adapter, with fallback between them.
5. **Upgrade path** — per-scene promotion from preview to generated video.
6. **Identity kit** — voice profile, then series inheritance.

Milestone 2 is shippable to real users. Everything after is monetization.

---

## Non-goals

No editing timeline. No canvas or node graph. No user-facing model picker. No plugin
marketplace. No screen recording. No teams or multi-tenancy. No public API.

---

## Open decisions

- Launch with one recipe or all four
- Preview mode default for everyone, or free tier only
- Self-host any inference in v1, or route everything to vendors initially
