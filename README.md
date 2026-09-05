# Lumina

A creator describes what they want to post; Lumina plans it, generates it across third-party
AI models, and assembles a finished short-form video.

We are an **orchestration layer over third-party generative models**, not a video editor.
Almost all generation is somebody else's API. The value is planning, routing, cost control,
consistency across episodes, and assembly.

## Documents

| | |
|---|---|
| [design-brief.md](design-brief.md) | UX, the four screens, interaction mechanics |
| [DEVELOPMENT.md](DEVELOPMENT.md) | Architecture, invariants, ledger and cost model |
| [TECHSTACK.md](TECHSTACK.md) | Stack decisions and the reasoning behind each |

Read the invariants in `DEVELOPMENT.md` before writing code. Violating one should fail review.

## Layout

```
apps/web/          Vite React SPA + PWA — the only thing users touch
apps/marketing/    static marketing site, deliberately separate
packages/client/   TS API client, GENERATED from OpenAPI (never hand-edited)
services/lumina/   the Python service: API + both worker pools, one package
infra/             compose for local dev, Dockerfiles, deployment
```

## Getting started

```bash
make install       # uv sync + pnpm install
make up            # local postgres + migrations
make fonts         # caption faces (~35 MB, fetched not vendored)
```

`make up` runs a **private PostgreSQL cluster in `.data/pg`** — no Docker, no root, no system
service. It creates two databases: `lumina` for development and `lumina_test` for the suite.
They are separate because the tests write jobs into the same table a running worker claims
from, and sharing one means a dev worker picks up a test's job and fails it — a failure that
is harmless, misleading, and takes an afternoon to trace back. Postgres ships everything needed to do this: `initdb` creates a cluster in a
directory you own and `pg_ctl` starts it on port 55432, chosen so it can never collide with a
system Postgres on 5432. It is the same server production uses, which a SQLite stand-in would
not be.

Media goes to `.data/media` by default, so the whole pipeline runs end to end — planning,
generation, narration, ffmpeg composition — with **nothing external installed** beyond
Postgres and ffmpeg.

```bash
make db            # psql shell
make db-reset      # delete the cluster and rebuild from migrations
make down          # stop it
make up-docker     # the container stack instead (adds redis, minio, tusd)
```

Then, in separate terminals:

```bash
make api           # http://localhost:8000  (docs at /docs)
make worker        # light pool: planning, LLM, provider polling
make render        # cpu-render pool: ffmpeg + chromium
make web           # http://localhost:5173
```

`make help` lists everything.

## The design reference

`make web` also serves the design mockup at **http://localhost:5173/mock.html** — the
prototype from `Lumina-Web.html` running as React, with all nine screens live.

Two views, toggled top-right:

| View | What it shows |
|---|---|
| **App only** (default) | The product UI — sidebar and screen — filling the window, responsive from ~900px to ultra-wide |
| **Design export** (`?frame=1`) | The raw export, including the review harness and the fake browser window. Pixel-identical to `Lumina-Web.html`; use it to check fidelity, not layout |

The export is a fixed artboard (a 1300x820 window holding a 232px rail and a 680px column),
so it letterboxes on a wide screen and clips on a short one. The app-only view drops that
scaffolding and makes the column responsive. It caps at 1560px because a single-column
layout gains nothing past that; going wider needs a real multi-column design, which is a
design decision rather than a CSS one.

It is generated, not hand-written:

```bash
make mock          # re-run after the design export changes
```

`apps/web/scripts/convert-prototype.py` unpacks the prototype and emits `src/mock/`. Every
CSS string passes through byte-identical, so fidelity is a property of the converter rather
than of a thousand hand-transcriptions. **Do not hand-edit `src/mock/`** — it is overwritten.

It has its own Vite entry on purpose. Loading the app's Tailwind preflight into it shifts
textarea and button metrics by 1-2px, which is enough to make a pixel comparison against the
design meaningless. `src/mock/` is a reference copy of the design, not product code: the real
screens live in `src/app/screens/`, and every one of them reads the API. There is no mock data
path in the app itself — fake rows in the real product are how you ship a demo that does not
work, so a failed load says it failed.

## Development

```bash
make test          # pytest
make lint          # ruff + mypy strict + tsc
make fmt           # autoformat
make client        # regenerate the TS client from OpenAPI (API must be running)
```

**The default provider is `FakeProvider`** — deterministic, instant, free. Real providers are
opt-in via `PROVIDERS_ENABLED`. Nobody should burn credits running the test suite.

**Reading beats listening.** Subtitle and Clip take an `.srt`, a `.vtt` or a plain script
alongside the video, and when one is attached nothing is transcribed — reading it is exact,
free, and needs no speech engine, which is the state a fresh checkout is in. See
`execution/subtitles.py`.

**Speech-to-text is chosen per language, not per deployment.** Each language pack ranks the
engines that can actually read its script; the environment says which of those have keys; the
first that is both wanted and available wins.

| | engines, best first | why |
|---|---|---|
| English, Chinese, Japanese, Korean | Groq Whisper → Scribe → Google | Whisper is at or near state of the art and ~$0.04/hr at ~200x real time |
| Thai | Scribe → Google → Whisper | both dedicated engines beat Whisper here, which still works |
| Burmese | Scribe → Google → Seamless (**no Whisper**) | see below |

Burmese lists **no** Whisper fallback at all. A bad ASR on a low-resource language does not
fail — it returns confident fluent nonsense, which for a subtitle nobody in the room reads is
worse than no transcript. With no key it gets the split, produces nothing, and the lane
refuses.

**Which of Scribe and Google is better for Burmese is not settled here, and cannot be settled
by reading.** The published figures are not comparable: Burmese has no word boundaries, so
Whisper's own evaluation and FLEURS report *character* error rate for it, while much of the
Burmese literature reports *word* error rate after a segmentation step of its own — a 3% and
an 80% from those two sources are not on the same axis. Vendor numbers are also on FLEURS,
which is clean read speech, not an uploaded video.

**SeamlessM4T v2** (Meta, open weights) is in the ranking for exactly this reason: it runs on
your own machine, so its Burmese can be measured rather than taken on a vendor's word. It has
no timestamps — it is sequence-to-sequence — so captions are timed from where the speech
actually is, by splitting on silence (`execution/speech.py`). It runs as a subprocess in
whichever interpreter has torch, so a 9 GB dependency stays out of the API image.

So measure it, on audio you actually publish:

```bash
uv run python scripts/asr_bench.py clip.wav --language my --reference truth.txt
```

It runs every configured engine over the same file and scores them with the metric the
language needs — character error rate where the script has no word boundaries, word error rate
where it does, taken from the language pack rather than a second list. Reorder
`ASRConfig.engines` in the pack once you have an answer.

**The default is `EvenSplit`**, so a fresh checkout runs end to end with no key, no model
download and no network — and refuses rather than inventing words. Self-hosted Whisper is
`ASR_ENGINE=whisper` plus `uv sync --extra asr`. Everything sits behind one seam:
`execution/transcribe.py`.

**Captions can be translated.** Set a source language different from the target and the lines
are translated with their timings untouched — a subtitle is anchored to when the thing was
said, not to how long the translation takes to read. Needs `ANTHROPIC_API_KEY`; without one it
says so rather than handing back the original text. See `intelligence/translate.py`.

**Six languages ship**, each with a pack: English, Japanese, Chinese (Simplified), Korean,
Burmese and Thai. A pack is what makes a language renderable — its face, its reading speed, its
unit segmentation, its line-breaking rule — so `supported()` *is* the list of languages, and
`/projects/languages` serves it rather than the frontend keeping a copy.

Adding one is a single file in `intelligence/languages/` plus a line in its `__init__`. The
pack declares its own endonym, its English name and whether its face must be fetched;
`make fonts` then downloads exactly what the registered packs need, and the conformance suite
picks the new pack up automatically.

**`make fonts` is not optional for any script but English.** Chromium draws the captions, and a
box with no Myanmar or CJK face does not fail — it renders identical empty rectangles, burnt
into the video. `execution/compose/fonts.py` refuses to rasterize rather than shipping that.

**The planner falls back to a deterministic outline** when `ANTHROPIC_API_KEY` is unset, so the
whole pipeline is exercisable without a key or a bill. Script Studio's hooks and rewrites do
the same.

## Turning on Continue with Google / Apple / GitHub

They are off until you register an OAuth app with each provider — credentials belong to you
and cannot ship in a repository. A greyed button reading "NOT SET UP" is that, not a fault.

**Step-by-step: [OAUTH.md](OAUTH.md).** GitHub takes about two minutes and is the easiest to
test with.

## Auth and credits

Sign-up and sign-in are separate endpoints and fail differently on purpose. Signing up with an
address that already exists is a **409**, because the person needs to know to sign in instead.
Signing in with an unknown address and signing in with the wrong password are the **same 401
with the same message** — distinguishing them turns the endpoint into an oracle for which
addresses are registered.

Passwords are Argon2id and re-hashed on sign-in when the parameters move. The credential the
client carries is a signed token (`Authorization: Bearer`), never the user id — that is a
database key, and it turns up in logs and tickets. `X-Lumina-User` still works for curl and
the test suite and is **refused in production**. Production also refuses to start with the
development `LUMINA_SECRET_KEY`, or one shorter than 32 bytes.

Credits are reserved **when the plan is approved**, not when a worker picks the job up
(invariant 4). Approving a plan holds the whole quote per scene; finishing a scene commits
what it really cost; a failed job refunds — from the worker, which is the only session that
commits after a stage has raised. `/credits/quote` and the draft reserve through the *same*
function, so the number shown and the number taken cannot drift apart.

## Where things go

- Anything language-specific goes in `intelligence/languages/`. An `if lang == "..."` anywhere
  else is a bug (invariant 1).
- Anything vendor-specific goes in `execution/providers/`. The router knows capabilities and
  cost, never a vendor name (invariant 2).
- `domain/` imports no I/O library. If your change needs a database inside `domain/`, the
  logic belongs a layer up.
- Credits are reserved before work starts, never charged after (invariant 4). Any code path
  that calls a paid provider without a prior reservation is wrong.
