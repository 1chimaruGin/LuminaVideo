# Tech Stack

Engineering decisions. Read alongside `DEVELOPMENT.md` (architecture, invariants) and
`design-brief.md` (UX). Where this document contradicts the "Suggested stack" section of
`DEVELOPMENT.md`, this document wins — every deviation is justified below.

## Decisions taken

| Question | Decision |
|---|---|
| Language pack #1 | English, with Burmese as the immediate second pack |
| Market / payments | Global, Stripe-first; local rails behind a `PaymentProvider` protocol |
| Infra posture | Hybrid — managed where cheap, owned where expensive |
| Frontend | Vite React SPA + PWA, static-hosted |

---

## The stack, by plane

Mirrors the five planes in `DEVELOPMENT.md`.

### Experience

| Concern | Choice | Why |
|---|---|---|
| Framework | React 19 + TypeScript, Vite | Every app screen is behind auth; SSR buys nothing. No Node server to run or pay for. |
| Routing | TanStack Router | Type-safe params. The storyboard route carries a lot of state. |
| Server state | TanStack Query | Cache, retry, optimistic per-scene mutations. |
| Local state | Zustand | Drag ordering, selection, upgrade-cart. Small surface. |
| Styling | Tailwind v4, `@theme` tokens | Tokens lifted from the prototype. See "Design tokens" below. |
| Reordering | dnd-kit | Touch-first, pointer events, accessible. The brief asks for reordering to "feel physical". |
| i18n | Lingui (ICU MessageFormat) | Real plural/gender rules, not string maps. Compiles catalogs at build time. |
| PWA | vite-plugin-pwa + Web Push | Jobs run minutes; the tab will be closed. |
| Hosting | Cloudflare Pages | Same edge as R2. Static, so it is effectively free. |

Marketing pages are a separate static site. They do not share a build with the app.

**Non-negotiable:** all spacing and alignment use CSS **logical properties**
(`padding-inline`, `margin-inline-start`, `text-align: start`) via Tailwind's `ps-/pe-/ms-/me-`
utilities. Never `left`/`right`. This is what makes RTL a language pack rather than a rewrite.

### Intelligence

| Concern | Choice |
|---|---|
| Planner | Claude `claude-opus-5` via the `anthropic` Python SDK, structured outputs |
| Prompt shaping | Same call — the planner emits per-scene image prompts inline |
| Safety pre-flight | Claude classification pass + per-provider policy rules table |
| ASR (ingest only) | faster-whisper (`large-v3`) |
| Alignment (captions) | CTC forced alignment (torchaudio `MMS_FA`), not ASR |
| Segmentation | ICU for spaced scripts; per-pack override for Burmese/Thai/Lao/Khmer |

See "LLM layer" below for model config, caching, and cost.

### Execution

| Concern | Choice | Why |
|---|---|---|
| API | FastAPI, Pydantic v2, Python 3.12 | Async-native. Provider polling is IO-bound; thousands of in-flight calls fit on one small box. |
| ORM / migrations | SQLAlchemy 2.0 async + Alembic | |
| Tooling | `uv`, ruff, mypy strict | |
| Queue | **Postgres, `FOR UPDATE SKIP LOCKED` + `LISTEN/NOTIFY`** | See Deviation 1. |
| Provider HTTP | httpx (async), webhooks preferred over polling | |
| Composition | ffmpeg 7, static build, libx264 | CPU encode is fine for 60s vertical; NVENC is not worth a GPU pool. |
| Caption raster | Headless Chromium (Playwright) | See Deviation 2. |
| Progress transport | SSE, fanned out from Redis pub/sub | Unidirectional is all we need. Survives proxies. Reconnect refetches project state. |

Worker pools per `DEVELOPMENT.md`: `light` (async, always on), `cpu-render` (ffmpeg +
Chromium, burst). **No `gpu` pool in v1** — nothing is self-hosted, so it does not exist yet.

### State

| Concern | Choice |
|---|---|
| Database | Postgres 16 — Neon (branching per PR is worth real money in review time) |
| Plan storage | JSONB, versioned, validated by Pydantic on read and write |
| Cache / pubsub / buckets | Redis — pub/sub, token buckets, circuit breaker counters, prompt-hash cache |
| Object storage | Cloudflare R2 + CDN — zero egress, as `DEVELOPMENT.md` requires |
| Ingest | tusd (resumable) for uploads, `yt-dlp` for links |

Assets are content-addressed by sha256 and immutable. R2 keys are the hash. This gives
deduplication and the idempotency guarantee in invariant 3 for free.

### Platform

| Concern | Choice |
|---|---|
| Auth | Own — FastAPI + Authlib (Google/Apple OAuth) + email OTP, argon2id, refresh cookie |
| Payments | Stripe Checkout + webhooks, behind a `PaymentProvider` protocol |
| Errors | Sentry (both app and API) |
| Logs / traces | structlog → OpenTelemetry → Grafana Cloud |
| COGS analytics | `job_costs` table, Metabase on a read replica |
| API hosting | Fly.io (API + `light` pool) |
| Render hosting | Hetzner dedicated, Kamal-deployed (`cpu-render` pool) |

Auth is owned rather than rented because the user row is the anchor of the credit ledger and
the Stripe customer. Two days of work removes a vendor from the path of every request.

**Cost telemetry is not optional.** Every `Job` records `estimated_cents`, `actual_cents`, and
`provider`. The monthly reconciliation in `DEVELOPMENT.md` is a query against this table, and
the estimator's drift is a chart, not a guess.

---

## Deviations from DEVELOPMENT.md

### 1. Postgres-backed queue, not Redis

`DEVELOPMENT.md` suggests "state machine in Postgres plus a Redis queue". That is a dual write
across two systems with no shared transaction. Reserving credits, transitioning scene state,
and enqueuing work must commit or fail as one unit — otherwise the failure modes are
*charged but never ran* and *ran twice and charged twice*. Both are invariant 4 violations
and both cost real money.

`SELECT ... FOR UPDATE SKIP LOCKED` with `LISTEN/NOTIFY` for wakeups is roughly 200 lines,
gives transactional enqueue, and puts `idempotency_key` under a unique constraint in the same
database as the ledger. Redis keeps pub/sub, rate limiting, and caching — it is just no longer
load-bearing for correctness.

Revisit only if queue throughput exceeds what one Postgres can serve, which is far past
product-market fit.

### 2. Caption rasterization in headless Chromium, not Pango

`DEVELOPMENT.md` correctly rules out `drawtext` and prefers Pango/HarfBuzz PNG overlays.
Chromium is the better version of the same idea:

- **Same CSS and webfonts as the storyboard preview**, so the render matches what the user
  approved. WYSIWYG between preview and output is a product requirement the doc does not name.
- Correct shaping for every script — Myanmar stacked marks, Thai, Devanagari — because it is
  the same engine that renders the app.
- Highlight animation states are CSS, not a layout engine API.

Cost is ~100ms per state and ~400MB in the render image. Rasterize once per caption **state**,
overlaid with `enable='between(t,...)'`, exactly as specced. libass/ASS remains the fallback
if per-state cost becomes the bottleneck.

### 3. Client-side preview playback; server compose only on Deliver

The preview tier is stills plus TTS audio. The browser can play that directly — image
sequence, CSS transforms for Ken Burns, Web Audio for the voiceover track. Running ffmpeg on
every iteration adds 30+ seconds to every edit and burns render minutes on output nobody keeps.

Server-side composition runs once, at Deliver, for the three aspect ratios. This roughly
doubles the effect of the preview-first cost architecture and makes iteration feel instant,
which is the actual product promise.

The client player and the server composer must agree on timing. Both read the same
`Plan` timing model; a golden-frame test asserts they stay in sync.

### 4. Forced alignment, not ASR, for captions

The voiceover is TTS — the exact text is already known. Transcribing it back is wasted
compute that introduces errors. Use CTC forced alignment against the known script.

ASR is needed only on the *Clip my long video* recipe, where the input is real speech.

This matters disproportionately for pack #2: Whisper's Burmese word error rate is poor, and
Burmese has no inter-word spaces, so `tokenize()` and `linebreak()` need a Myanmar syllable
segmenter rather than ICU word-break. Alignment sidesteps the ASR half of that problem
entirely. The segmentation half is real work — budget for it, do not discover it.

### 5. Aggregator as provider #1 and #2

`DEVELOPMENT.md` leaves "route everything to vendors initially" open. Resolve it: for v1,
wire **fal.ai** and **Replicate** behind the `Provider` protocol. One auth model each,
webhook-based async on both, dozens of models reachable without new integrations.

This gives the mandatory fallback chain on day one rather than in month two. Direct vendor
adapters come later, per model, where volume justifies eating the aggregator margin — and the
`Provider` protocol makes that swap invisible to the router.

### 6. Generated API client

FastAPI emits OpenAPI; `@hey-api/openapi-ts` turns it into a typed TS client at build time.
No hand-written fetch layer, no drift between Pydantic models and frontend types. A CI check
fails the build if the committed client is stale.

---

## LLM layer

Anthropic Python SDK. Pricing per 1M tokens, as of this writing:

| Model | Context | Input | Output |
|---|---|---|---|
| `claude-opus-5` | 1M | $5.00 | $25.00 |
| `claude-sonnet-5` | 1M | $2.00 | $10.00 |
| `claude-haiku-4-5` | 200K | $1.00 | $5.00 |

**Planner: `claude-opus-5`.** One call takes the brief plus the recipe and language profile and
returns the entire typed plan — all scenes, each with its script line, duration, and image
prompt. Not twelve calls. One structured output is cheaper, and more importantly the scenes
are coherent with each other because they were written together.

Request shape:

- `output_config: {"format": {...}}` for structured output — **not** the deprecated
  `output_format` parameter. `client.messages.parse()` validates against the Pydantic model.
- `thinking: {"type": "adaptive"}`. `budget_tokens` is rejected with a 400 on Opus 5.
- `output_config: {"effort": ...}` to tune depth. Start at the default and measure.
- Prompt caching on the stable prefix.

**Cache layout matters here.** Render order is `tools` → `system` → `messages`. Put the frozen
system prompt, the recipe definitions, and the language pack's `llm_profile` first with a
`cache_control` breakpoint; put the user's brief after it. The stable prefix is large and
identical across every user, so cached reads run at 0.1× input cost. Verify with
`usage.cache_read_input_tokens` — if it is zero across repeated calls, something in the prefix
is varying (a timestamp, an unsorted dict) and the saving is silently gone.

**Single-scene regeneration** is a separate, much smaller call. This is the one high-frequency
LLM path, and at 12 scenes per video its model choice is a real COGS line: roughly $0.09 per
full pass on Opus 5, $0.036 on Sonnet 5, $0.018 on Haiku 4.5 — against a preview-tier target of
$0.30–0.60 for the whole video. Default to `claude-opus-5` and measure quality on real briefs
before trading down; that is a decision to make with data, not in advance.

**Safety pre-flight** classification is high-volume and latency-sensitive, and runs before
every paid provider call. Batch it into the planner call where possible rather than paying for
a second round trip.

---

## Design tokens

Lifted from the prototypes. These belong in one Tailwind `@theme` block; nothing hardcodes a
hex value.

```
background      #05060f
foreground      #eef1fb
muted           #7d88ad
subtle          #6f7aa0
accent          linear-gradient(135deg, #ffc36b, #ff8f3d)
on-accent       #1a1103
link            #ffc36b   hover #ffdca6
hairline        rgba(150,170,230,.13)
surface         rgba(18,22,42,.85)
```

Type: **Space Grotesk** display, **IBM Plex Sans** text.

Font loading is per language pack and lazy — never bundled. `DEVELOPMENT.md` warns that some
faces are 5–20MB; the prototype already carries 364 woff2 subsets across Myanmar, Thai, JP, KR,
and SC. Subset per script, load on pack activation.

Because many writing systems have no uppercase, `text-transform: uppercase` is banned in shared
components. Emphasis comes from weight and color.

---

## Repo layout

```
apps/web/                  Vite React SPA + PWA
apps/marketing/            static site
packages/client/           generated TS API client (CI-checked)
services/lumina/           the Python service — one package, several entrypoints
  src/lumina/
    domain/                pure: no I/O, no SQLAlchemy, no vendor names
    db/                    SQLAlchemy models, session, alembic migrations
    queue/                 Postgres queue (SKIP LOCKED + LISTEN/NOTIFY)
    state/                 repositories over db
    intelligence/          planner, estimator, safety, languages/
    execution/             orchestrator, router, providers/, compose/
    platform/              auth, payments, notifications, telemetry
    api/                   FastAPI app + routers
    workers/               light + cpu-render entrypoints
infra/docker/              compose, api.Dockerfile, render.Dockerfile
infra/kamal/               render pool deployment
```

The API and the workers are **one Python package with several entrypoints**, not separate
projects — they share the domain model, and a shared model that has to be published between
two projects stops being shared. Splitting them into separate deployables happens at the
Docker layer (`api.Dockerfile` vs `render.Dockerfile`), which is also where the render image
picks up ffmpeg and Chromium and the API image does not.

`domain/` is the load-bearing rule: it may not import SQLAlchemy, httpx, or boto3. Everything
in it is unit-testable without a database, which is why the ledger and state-machine tests
run in milliseconds and need no fixtures.

---

## Local development

`docker compose up` brings Postgres, Redis, tusd, and MinIO (R2-compatible). The API and web
run on the host for fast reload. ffmpeg and Chromium live in the worker image only.

The `FakeProvider` from build step 1 is the default in development — deterministic, instant,
free. Real providers are opt-in per environment variable. Nobody should burn credits running
tests.

---

## Testing

| Layer | Approach |
|---|---|
| Domain | pytest, `FakeProvider`, in-memory ledger assertions |
| Ledger | property tests — reserve/commit/refund must sum to zero, always |
| Idempotency | replay every stage twice; assert one artifact, one charge |
| Composition | golden-frame checksums at fixed timestamps |
| Language packs | one conformance suite every pack must pass |
| Screens | Playwright, run against long strings and non-Latin script, never lorem ipsum |

The **language pack conformance suite** is what keeps invariant 1 honest. Write it while
building English, with Burmese fixtures already in it and marked xfail. If English is the only
thing that passes, the interface has not been tested — it has been asserted.

---

## Build order additions

`DEVELOPMENT.md` step 1 says: prove a job runs end to end and produces a file. Concretely, the
first vertical slice is:

1. Postgres queue + one stage + `FakeProvider` → an artifact in MinIO
2. Auth and projects around it
3. Storyboard screen reading real scene state over SSE
4. Chromium caption rasterizer, English
5. ffmpeg compose → one downloadable file
6. Language pack interface extracted from the English implementation, conformance suite written

Only then does step 2 (one full recipe) start. Step 6 is the one people skip; skipping it is
how invariant 1 gets violated in week three.

---

## Still open

- Neon vs Supabase for Postgres. Neon assumed above. Supabase would let auth be rented instead
  of owned, trading two days of work for a vendor dependency in the request path.
- Whether the client-side preview player is canvas or DOM. DOM is easier to make match the
  Chromium caption raster; canvas is easier to make match ffmpeg output. Prototype both against
  the golden-frame test before committing.
- TTS vendor for the voice profile. This is the strongest retention hook in the brief and
  deserves its own evaluation — voice cloning quality varies enormously, and Burmese support is
  not a given.
