"""Projects, plans and scenes.

Thin: validate, delegate, return. Everything that decides anything lives in a stage or a
service — a route handler that contains business logic is a handler that cannot be run by a
worker, and every one of these operations is also reachable from the queue.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from lumina.api.deps import CurrentChannel, CurrentUser, Session
from lumina.api.schemas import (
    LanguageOut,
    Moment,
    NewProject,
    PlanOut,
    ProjectOut,
    RecipeOut,
    SceneOut,
    VoiceOut,
)
from lumina.db.models import Asset, Channel, Job, LedgerEntry, Plan, Project, Scene
from lumina.db.models import Plan as PlanRowModel
from lumina.domain.assets import AssetKind
from lumina.domain.jobs import Stage, stages_for
from lumina.domain.money import Cents
from lumina.domain.plan import (
    KEEPS_WHOLE_SOURCE,
    NEEDS_SOURCE,
    Recipe,
    SceneState,
)
from lumina.execution import dubtrack, speak, stages, subtitles, timing
from lumina.execution import transcribe as transcribe_mod
from lumina.execution.compose import fonts
from lumina.execution.providers.fake import FakeProvider
from lumina.execution.router import Router
from lumina.execution.transcribe import AUTO, Segment
from lumina.intelligence import translate
from lumina.intelligence.languages import (
    COMMON_SOURCES,
    COMMON_TARGETS,
    UnsupportedLanguageError,
    detect,
    detect_all,
    get_pack,
    packs,
    split_sentences,
    supported,
)
from lumina.intelligence.pricing import price_plan
from lumina.queue import Queue
from lumina.state.assets import AssetStore
from lumina.state.ledger import Ledger

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectOut])
async def list_projects(session: Session, channel: CurrentChannel) -> list[ProjectOut]:
    """Every project on the channel, newest first, each with a frame to recognise it by.

    The poster comes from the opening scene of the current plan — final if it has been
    upgraded, else the preview. Joined here rather than fetched per project by the browser:
    the sequel picker shows four of these at once, and four round trips to draw four
    thumbnails is four more than it needs.
    """
    opening = (
        select(
            Scene.plan_id.label("plan_id"),
            func.coalesce(Scene.final_asset_id, Scene.preview_asset_id).label("poster"),
        )
        .where(Scene.index == 0)
        .subquery()
    )
    # The source's length comes along too, so the "start from a video you already uploaded"
    # picker can label each one without a request per row.
    rows = await session.execute(
        select(Project, opening.c.poster, Asset.duration_ms)
        .outerjoin(opening, opening.c.plan_id == Project.current_plan_id)
        .outerjoin(Asset, Asset.id == Project.source_asset_id)
        .where(Project.channel_id == channel.id)
        .order_by(Project.created_at.desc())
    )
    return [
        ProjectOut.model_validate(project).model_copy(
            update={"poster_asset_id": poster, "source_duration_ms": seconds}
        )
        for project, poster, seconds in rows.all()
    ]


@router.get("/recipes", response_model=list[RecipeOut])
async def recipes() -> list[RecipeOut]:
    """The lanes, and which stages each one runs.

    Served rather than duplicated in the client. The storyboard draws its pipeline from
    `stages`, so a recipe that skips generation shows four steps and not a greyed-out fifth —
    and adding a sixth lane does not require a frontend release to become visible.
    """
    return [
        RecipeOut(
            id=r.value,
            stages=[s.value for s in stages_for(r.value)],
            needs_source=r in NEEDS_SOURCE,
        )
        for r in Recipe
    ]


@router.get("/voices", response_model=list[VoiceOut])
async def voices(language: str, channel: CurrentChannel) -> list[VoiceOut]:
    """The voices a dub can be spoken in, for one target language.

    The creator's own recorded voice is listed alongside the engine's, and listed even when
    it cannot be used — with the reason attached. Hiding it would be the wrong kindness:
    someone who has recorded sixty seconds of themselves for this product will go looking for
    it, and "not offered" reads as a bug where "needs a voice-cloning engine" reads as a
    fact.
    """
    out = [VoiceOut(id=v.id, character=v.character) for v in speak.voices_for(language)]

    if channel.voice_profile_asset_id is not None:
        out.append(
            VoiceOut(
                id="mine",
                character="Your voice",
                mine=True,
                #: Recorded, stored, and not yet usable: speaking *as* someone needs a
                #: cloning engine, which is a different capability from text-to-speech and
                #: is not configured here. Said plainly rather than greyed out silently.
                unavailable="Needs a voice-cloning engine — not connected yet",
            )
        )
    return out


@router.get("/voices/{voice_id}/sample")
async def voice_sample(voice_id: str, language: str) -> Response:
    """A few seconds of this voice reading this language, as playable audio.

    Cached in memory and keyed by both, because a creator auditioning six voices clicks
    through them repeatedly and comparing two means playing each more than once. The
    generated bytes are identical every time for a given pair, so re-synthesizing is money
    spent to produce a file we already had.

    The sentence comes from the language pack, not from here — a preview that reads English
    to someone choosing a Burmese voice previews nothing they are about to hear.

    Unauthenticated, like `/assets`, so it can be a plain `<audio src>` — a fetch with an
    Authorization header cannot be one, and playing six voices through blob URLs to protect a
    fixed sentence read by a stock voice protects nothing. It is not an open tap on a paid
    API either: the only inputs are a voice from the pack's list and a language that has a
    pack, so the whole space is a few dozen files and every one of them is cached after the
    first request.
    """
    if not any(v.id == voice_id for v in speak.voices_for(language)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no voice {voice_id!r} for {language}")

    key = (voice_id, language)
    cached = _SAMPLES.get(key)
    if cached is None:
        try:
            pcm = await speak.say(speak.sample_text(language), language=language, voice=voice_id)
        except speak.NoVoiceError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
        cached = _SAMPLES[key] = speak.wav(pcm)

    #: Long and immutable: a voice reading a fixed sentence does not change, so the browser
    #: should never ask twice.
    return Response(
        cached,
        media_type="audio/wav",
        headers={"Cache-Control": "public, max-age=604800, immutable"},
    )


#: Rendered previews, keyed by (voice, language). Bounded by the pack list times the voice
#: list — a few dozen short WAVs — so it needs no eviction.
_SAMPLES: dict[tuple[str, str], bytes] = {}


@router.get("/languages", response_model=list[LanguageOut])
async def languages() -> list[LanguageOut]:
    """What Lumina can hear, and what it can write.

    Two different answers, and conflating them is what made Subtitle look broken: the server
    can *render* English captions perfectly while being unable to *listen* to anything at all,
    because no speech engine is configured. A picker that shows one list cannot say that, so
    both flags are sent and the screen tells the creator to attach their subtitle file.

    Ordered with the common answers first — what creators shoot in, what they subtitle into —
    rather than alphabetically, so the usual choice is near the top of a list that is going to
    keep growing. Every language is offered on both sides regardless: the ordering is a guess
    about the audience, not a restriction on it.
    """
    return [
        LanguageOut(
            code=pack.code,
            name=pack.endonym,
            english=pack.english_name,
            # Asked per language, because it differs per language on the same server: a box
            # with a Groq key reads English and cannot read Burmese.
            can_listen=transcribe_mod.can_hear(pack.code),
            can_render=fonts.installed(pack.code),
            line_height=pack.typography.line_height,
            cps=pack.typography.cps,
            common_source=pack.code in COMMON_SOURCES,
            common_target=pack.code in COMMON_TARGETS,
            can_speak=speak.can_speak(pack.code),
        )
        for pack in packs()
    ]


@router.post("", response_model=PlanOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: NewProject, session: Session, user: CurrentUser, channel: CurrentChannel
) -> PlanOut:
    """Start a project and plan it in one step.

    Planning runs inline rather than through the queue because the user is waiting on this
    specific answer — it is the Recipe screen's whole content. Generation, which takes
    minutes, is what goes on the queue.

    The lanes that start from a file plan from its transcript rather than from the brief.
    Without that, "subtitle this video" would be planned as though the brief were the script,
    and the captions would be a summary of the video instead of what was said in it.
    """
    try:
        recipe = Recipe(body.recipe)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unknown recipe {body.recipe}") from None

    #: Narrate takes a *list* of pictures, so either field satisfies it. Checking only the
    #: singular one refused a project that had supplied thirty images and no `source_asset_id`.
    if recipe in NEEDS_SOURCE and body.source_asset_id is None and not body.source_asset_ids:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{recipe.value} works on a file you upload; POST it to /uploads first",
        )

    # Both languages must have a pack before anything is stored: the target picks the
    # typography and the font, and a project planned into a language nothing can render is a
    # project that fails at the last stage, after the creator has paid for it.
    #: "Work it out from the audio" is a legal answer for what was *spoken* and for nothing
    #: else: there is no pack for it by design, because a file may be in two languages and the
    #: answer comes back in the transcript rather than being chosen here. What comes *out* is
    #: always a decision, so the target is still required to name a real language.
    for code in (body.language, None if body.source_language == AUTO else body.source_language):
        if code is None:
            continue
        try:
            get_pack(code)
        except UnsupportedLanguageError:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"no language pack for {code!r} yet — Lumina speaks {', '.join(supported())}",
            ) from None

    project = Project(
        channel_id=channel.id,
        title=(body.title or body.brief)[:80],
        recipe=recipe.value,
        language=body.language,
        # Stored only when it says something: "the same as the target" and "not answered" are
        # the same fact, and keeping one representation of it means nothing downstream has to
        # compare two strings to find out whether this is a translation.
        source_language=(body.source_language if body.source_language != body.language else None),
        source_asset_id=body.source_asset_id,
        transcript_asset_id=body.transcript_asset_id,
        #: Only meaningful on a lane that speaks. Kept as given rather than validated against
        #: the voice list here: the list is per language, the language is on this same row,
        #: and a voice that has gone away is caught where it is used rather than at creation.
        voice_id=body.voice_id,
    )
    session.add(project)
    await session.flush()

    ctx = stages.Ctx(
        session=session,
        router=Router(providers=[FakeProvider()]),
        assets=AssetStore(session),
        ledger=Ledger(session),
    )

    #: Narrate's brief *is* the script, so it is split here rather than written by a model.
    #:
    #: Deliberately not sent to the planner. Asked to break a script into beats, a model also
    #: improves it — reorders a clause, tightens a phrase, corrects what it takes for a
    #: mistake — and on this lane the words belong to the creator. Splitting is arithmetic;
    #: rewriting is a decision nobody asked for.
    written: list[str] = []
    grown: list[int] = []
    if recipe is Recipe.NARRATE:
        #: The language is read off the script rather than asked for. Burmese is Burmese by
        #: codepoint, and the creator answered the question by typing.
        project.language = detect(body.brief) or body.language

        pack = get_pack(project.language)
        written = split_sentences(body.brief, pack)
        if not written:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "there is no script to narrate")

        #: Fill the script out if it will not reach the length that was asked for.
        #:
        #: Length targets are a real constraint — they decide whether a video is a Short or
        #: carries a mid-roll — and a script a minute short of one is a video that misses it.
        #: The model is asked for another example or the next turn in the story, never for the
        #: same thing at greater length, and it reports which lines it added so they can be
        #: cut. Below eight seconds short, leave it alone: nobody notices, and a model asked
        #: for one more sentence writes filler.
        spoken_ms = sum(pack.caption_duration_ms(line) for line in written)
        short_by = body.target_ms - spoken_ms
        if short_by > 8_000:
            written, added = await translate.expand(
                written, language=project.language, needed_seconds=round(short_by / 1000)
            )
            grown = sorted(added)

    source = Source(brief=body.brief, target_ms=body.target_ms, lines=written)
    picture: uuid.UUID | None = None

    transcript: Asset | None = None
    if body.transcript_asset_id is not None:
        transcript = await session.get(Asset, body.transcript_asset_id)
        if transcript is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such subtitle file")
        if transcript.kind != AssetKind.TEXT.value:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "that is not a subtitle file. Attach an .srt, a .vtt, or a plain text script.",
            )
        if recipe not in NEEDS_SOURCE:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"{recipe.value} writes its own script, so there is nothing to read a "
                "subtitle file against. Subtitle and Clip take one.",
            )

    #: The picture pool for a narrated video, in the order it was given.
    #:
    #: `source_asset_id` stays the single-file field every other lane uses; this is the list
    #: that makes a *sequence* possible. A lane that changes visual every few seconds cannot
    #: express itself through one foreign key.
    pool: list[Asset] = []
    if recipe is Recipe.NARRATE and body.source_asset_ids:
        for asset_id in body.source_asset_ids:
            found = await session.get(Asset, asset_id)
            if found is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, f"no such upload {asset_id}")
            if found.kind not in (AssetKind.IMAGE.value, AssetKind.VIDEO.value):
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "Narrate shows pictures and clips. That file is neither.",
                )
            pool.append(found)
        project.source_asset_id = pool[0].id

    if body.source_asset_id is not None:
        asset = await session.get(Asset, body.source_asset_id)
        if asset is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such upload")

        if recipe is Recipe.NARRATE:
            #: A bed, not a subject. Narrate needs a file and must never listen to it.
            #:
            #: Putting it in NEEDS_SOURCE alone sent it down the transcribing path with every
            #: other source lane, so uploading footage to narrate over *replaced the creator's
            #: script with the footage's own transcript* — 58 lines of someone else's video,
            #: under a heading saying "the script so far". There is nothing to hear here: the
            #: speech is the output, and the words are already in the box.
            source = _as_bed(body, project, asset, source)
        elif recipe in NEEDS_SOURCE:
            source = await _from_source(ctx, body, project, asset, transcript)
        elif asset.kind == AssetKind.IMAGE.value:
            # A picture to build the video out of. The script still comes from what was typed
            # — the image is the *subject*, not the words — and composition already knows how
            # to animate a still, so this needs nothing new downstream.
            picture = asset.id
        else:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"{recipe.value} builds a video from words and pictures, not from footage. "
                "Try Clip or Subtitle for a video you already have.",
            )

    out = await stages.plan(
        ctx,
        {
            "project_id": str(project.id),
            "brief": source.brief,
            "recipe": recipe.value,
            "language": body.language,
            #: A name the creator typed is theirs. The plan writes one for projects that have
            #: none, and it must not overrule one that does.
            "keep_title": bool(body.title),
            "target_ms": source.target_ms,
            #: Subtitling's lines come from the file or the speech engine and are
            #: authoritative — see planner.plan. Every other lane writes its own.
            "lines": source.lines or None,
            #: Which of those lines the model wrote, so the editor can mark them. An
            #: expansion the creator cannot tell apart from their own writing is a tool
            #: quietly changing what they meant to say.
            "written_by_us": grown,
        },
    )
    #: Pictures onto beats, now that both exist. After planning because the beats are what
    #: they attach to; before returning because the Recipe screen shows the storyboard.
    if pool:
        await _lay_pictures(session, uuid.UUID(out["plan_id"]), pool)

    # The target track, once the scenes exist to hang it on. After planning rather than before
    # so the transcript survives in `script_line`, and written into `translations` — the same
    # place `POST /projects/{id}/tracks/{lang}` writes, so a track added later and one made
    # here are indistinguishable to everything downstream.
    if project.source_language and project.source_language != project.language:
        planned = list(
            await session.scalars(
                select(Scene)
                .where(Scene.plan_id == uuid.UUID(out["plan_id"]))
                .order_by(Scene.index)
            )
        )
        # What each line is actually in. Asked per line rather than once for the file because
        # `auto` exists precisely for the file that changes language part way through — a
        # Japanese video with an English song over it is two languages, and translating the
        # song as though it were Japanese is how it comes out as nonsense.
        told = project.source_language
        spoken_in = (
            [code or told for code in detect_all(s.script_line for s in planned)]
            if told == AUTO
            else [told] * len(planned)
        )
        if planned and told == AUTO:
            # Record what was heard, so everything downstream has a real language to work
            # with. The commonest wins: it drives the font and the "Original" label, both of
            # which need one answer even when the audio had several.
            heard = [code for code in spoken_in if code]
            project.source_language = max(set(heard), key=heard.count) if heard else None

        # One call per source language, so a mixed file is several small translations rather
        # than one that has been lied to about what it is reading.
        for code in sorted({c for c in spoken_in if c and c != project.language}):
            batch = [s for s, c in zip(planned, spoken_in, strict=True) if c == code]
            try:
                done = await translate.translate_segments(
                    [
                        Segment(text=s.script_line, start_ms=0, duration_ms=s.duration_ms)
                        for s in batch
                    ],
                    source=code,
                    target=project.language,
                    keep=_glossary_of(channel),
                )
            except translate.TranslationUnavailableError as exc:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
            for scene, line in zip(batch, done, strict=True):
                # Reassignment, not mutation: SQLAlchemy does not track in-place edits to a
                # JSONB dict, so updating the mapping in place would write nothing.
                scene.translations = {**scene.translations, project.language: line.text}

    if source.moments:
        row = await session.get(PlanRowModel, uuid.UUID(out["plan_id"]))
        if row is not None:
            # Reassignment, not mutation: SQLAlchemy does not track in-place edits to a JSONB
            # dict, so appending to it would update the object and write nothing.
            row.content = {**row.content, "moments": source.moments}

    if picture is not None:
        # Every scene shows it. One picture with motion, narration and captions over it is
        # what "make a video from this image" means in every tool that offers it — and it
        # means the draft has nothing to generate, so it costs nothing.
        planned = list(
            await session.scalars(
                select(Scene)
                .where(Scene.plan_id == uuid.UUID(out["plan_id"]))
                .order_by(Scene.index)
            )
        )
        for scene in planned:
            scene.preview_asset_id = picture
            scene.state = SceneState.PREVIEW_READY.value

    if source.windows:
        # Point each scene at its place in the source, and take its length from there too.
        # Zipped rather than indexed by position: the planner may return fewer scenes than
        # there were lines, and a mismatch would cut the wrong part of the video rather than
        # fail loudly.
        planned = list(
            await session.scalars(
                select(Scene)
                .where(Scene.plan_id == uuid.UUID(out["plan_id"]))
                .order_by(Scene.index)
            )
        )
        # `strict` on purpose. These pair a line with the moment it was said, and a length
        # mismatch means they have come apart — which is exactly what happened when the
        # planner re-derived the cue boundaries: eleven scenes for twelve cues, and every
        # caption after the merge drawn at the wrong moment. Truncating silently is the one
        # behaviour that cannot be allowed here.
        if len(planned) != len(source.windows):
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                f"planned {len(planned)} scenes for {len(source.windows)} transcript lines; "
                "refusing to guess which line belongs to which moment",
            )
        for scene, (at, length) in zip(planned, source.windows, strict=True):
            scene.source_start_ms = at
            scene.duration_ms = max(1, length)

    await session.commit()
    return await _plan(session, uuid.UUID(out["plan_id"]))


@dataclass(frozen=True, slots=True)
class Source:
    """What an uploaded file turned out to be worth planning from."""

    brief: str
    target_ms: int
    #: Only the Clip lane finds these; every other recipe writes its scenes rather than
    #: ranking them, so there is nothing to carry.
    moments: list[dict[str, Any]] = field(default_factory=list)
    #: The lines exactly as they were transcribed or read, for the lanes where the line
    #: boundaries are given rather than authored. Empty when the planner is free to write.
    lines: list[str] = field(default_factory=list)
    #: Where each planned scene sits inside the source video: (start, duration), in order.
    #: Empty for the lanes that generate their frames — those have no source to point into.
    #:
    #: The duration travels with it because for a transcribed lane it is measured, not chosen.
    #: Letting the planner set it from reading speed produced a two-second subtitle track for
    #: an eight-second video: correct arithmetic, wrong question.
    windows: list[tuple[int, int]] = field(default_factory=list)


async def _with_languages(session: Session, scenes: Sequence[Scene]) -> list[SceneOut]:
    """The scenes, each told which language its own line is in and what kind of picture it has.

    Language is resolved across the whole plan rather than line by line: a line of kanji with
    no kana is Chinese on its own and Japanese in a Japanese file, and only the file can
    settle it.

    The picture's *kind* is sent for a duller reason — the asset URL carries no extension, so
    the browser cannot tell a still from a clip, and it must, because one is drawn and the
    other is played.
    """
    out = [SceneOut.model_validate(s) for s in scenes]
    for row, code in zip(out, detect_all(s.script_line for s in scenes), strict=True):
        row.spoken_language = code

    wanted = {s.final_asset_id or s.preview_asset_id for s in scenes} - {None}
    if wanted:
        clips = {
            row.id
            for row in await session.scalars(
                select(Asset).where(Asset.id.in_(wanted), Asset.kind == AssetKind.VIDEO.value)
            )
        }
        for row, scene in zip(out, scenes, strict=True):
            row.picture_is_clip = (scene.final_asset_id or scene.preview_asset_id) in clips
    return out


def _as_bed(body: NewProject, project: Project, asset: Asset, source: Source) -> Source:
    """Accept footage to run underneath a narration, without listening to a second of it.

    The only questions worth asking of a bed are whether it has a picture at all — an audio
    file loops into a video with no image — and how long it is, which decides nothing here
    because the script sets the length.
    """
    if asset.kind != AssetKind.VIDEO.value or not (asset.width and asset.height):
        what = "an image" if asset.kind == AssetKind.IMAGE.value else "a sound file"
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"That is {what}. Narrate plays footage under your script, so it needs a video.",
        )
    project.title = (body.title or source.lines[0] if source.lines else "Untitled")[:80]
    return source


async def _lay_pictures(session: Session, plan_id: uuid.UUID, assets: list[Asset]) -> int:
    """Give every beat a picture, cycling through what the creator supplied.

    In order, not matched. Choosing *which* picture suits which sentence is semantic work —
    an embedding or a model call per beat — and it is the part a person fixes in seconds on
    the storyboard. Assembling in order and letting them swap the two that landed wrong is
    how the real workflows run, and it costs nothing.

    Cycling rather than running out: twelve images across forty beats repeat, which reads as
    a motif. Leaving twenty-eight beats blank fails the render.
    """
    if not assets:
        return 0

    scenes = list(
        await session.scalars(select(Scene).where(Scene.plan_id == plan_id).order_by(Scene.index))
    )
    for i, scene in enumerate(scenes):
        scene.preview_asset_id = assets[i % len(assets)].id
    await session.flush()
    return len(scenes)


async def _from_source(
    ctx: stages.Ctx,
    body: NewProject,
    project: Project,
    asset: Asset,
    transcript: Asset | None = None,
) -> Source:
    """Turn an uploaded file into the brief its recipe should be planned from.

    Runs inline for the same reason planning does: the Recipe screen cannot say "18 subtitle
    lines from your 47-second clip" until the file has actually been listened to. It is one
    transcription, not a render, so the wait is seconds rather than minutes.

    Returns the text to plan from and the target length, which for these lanes is measured
    rather than chosen — a subtitle track is exactly as long as the video is.
    """
    # Both of these lanes cut and caption *frames*, so the source has to have some.
    #
    # Checked here rather than left to fail later, because it does not fail later: cutting an
    # audio-only file succeeds and produces a 0x0 MP4 — a video with no picture, delivered to
    # someone who cannot post it. A still is refused for the same reason from the other
    # direction: nothing to transcribe, no moments to rank.
    if asset.kind != AssetKind.VIDEO.value or not (asset.width and asset.height):
        what = "an image" if asset.kind == AssetKind.IMAGE.value else "a sound file"
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"That is {what}. Subtitles and Clip need a video — something with a picture in it.",
        )

    project.title = (body.title or body.brief)[:80]
    duration = asset.duration_ms or 0
    #: What was said, in the language it was said in. Distinct from `project.language`, which
    #: is what the captions come out as — the same for most projects, different for a
    #: translation, and they drive different halves of the pipeline.
    spoken = project.source_language or project.language

    try:
        heard = await stages.transcribe(
            ctx,
            {
                "asset_id": str(asset.id),
                "duration_ms": duration,
                "language": spoken,
                "transcript_asset_id": str(transcript.id) if transcript else None,
            },
        )
    except subtitles.UnreadableSubtitlesError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    lines = heard["lines"]
    if not any(str(line["text"]).strip() for line in lines):
        # Refused, not worked around. Both of these lanes are *about* what was said, so a plan
        # made without it is not a worse plan — it is a different, meaningless one. The three
        # reasons need different answers, so they get different messages.
        if transcript:
            detail = "that subtitle file has no lines in it."
        elif heard.get("engine") != "even-split":
            detail = "I could not hear any speech in that video, so there is nothing to caption."
        else:
            detail = (
                "Reading speech from a video needs a speech-to-text engine, and none is set "
                "up on this server. Attach the video's subtitle file (.srt or .vtt) or its "
                "script instead, and I will use that — or ask whoever runs the server to set "
                "ASR_ENGINE."
            )
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail)

    # Deliberately *not* translated here. A scene's own `script_line` is the source language
    # — `stages.line_for` says so and every renderer relies on it — so translating before
    # planning stored Burmese where English belonged and threw the English away.
    #
    # Keeping it has a second, larger benefit: the creator sees what was actually heard before
    # anything is translated from it. A mishearing caught at that point costs one edit; the
    # same mishearing translated first is a fluent Burmese sentence about the wrong thing,
    # which nobody reviewing the Burmese can detect.

    if Recipe(project.recipe) is not Recipe.CLIP_LONG_VIDEO:
        # Subtitles: every line, in order, for exactly as long as the video runs.
        return Source(
            brief=" ".join(str(line["text"]) for line in lines),
            target_ms=duration or body.target_ms,
            lines=[str(line["text"]) for line in lines],
            windows=[(int(x["start_ms"]), int(x["duration_ms"])) for x in lines],
        )

    # Clip: the whole point is that most of the video is not worth posting. Ranking happens
    # before planning so the plan covers the moments that survived, not the transcript.
    found = await stages.analyze(ctx, {"lines": lines, "window_ms": body.target_ms})
    moments = found["moments"]
    if not moments:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "nothing in that video looked worth cutting out",
        )
    # Back into transcript order: they came back ranked by score, and a short whose lines
    # are out of sequence is incoherent however good each one is on its own.
    best = sorted(moments[:6], key=lambda m: int(m["start_ms"]))
    # Kept on the project so the plan can carry them: the Moments screen shows the ranking,
    # and recomputing it later would give a different answer than the one the plan was cut on.
    return Source(
        brief=" ".join(str(m["line"]) for m in best),
        target_ms=min(body.target_ms, duration or body.target_ms),
        moments=best,
        windows=[(int(m["start_ms"]), int(m["end_ms"]) - int(m["start_ms"])) for m in best],
    )


@router.get("/{project_id}/plan", response_model=PlanOut)
async def get_plan(project_id: uuid.UUID, session: Session) -> PlanOut:
    project = await session.get(Project, project_id)
    if project is None or project.current_plan_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no plan yet")
    return await _plan(session, project.current_plan_id)


@router.post("/{project_id}/draft", status_code=status.HTTP_202_ACCEPTED)
async def draft(project_id: uuid.UUID, session: Session, user: CurrentUser) -> dict[str, object]:
    """Queue the whole preview tier: a picture per scene, then narration.

    **Credits are reserved here, not in the worker.** That is invariant 4, and the reason it
    says "at plan approval, not at submit" is this exact endpoint: reserving inside `generate`
    let a user queue a hundred scenes against a balance covering ten, and discover it only
    when the jobs failed one by one with nothing on screen to explain why.

    Reserved per scene rather than as one lump, so a scene that fails refunds its own hold and
    leaves the others alone — the same principle as per-scene regeneration.

    The reservations, the scene transitions and the enqueues all commit together. That
    co-commit is the entire reason the queue lives in Postgres: a reserve without its job is
    money held against work that will never run.
    """
    project = await session.get(Project, project_id)
    if project is None or project.current_plan_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no plan yet")

    scenes = list(
        await session.scalars(
            select(Scene).where(Scene.plan_id == project.current_plan_id).order_by(Scene.index)
        )
    )
    if not scenes:
        raise HTTPException(status.HTTP_409_CONFLICT, "the plan has no scenes")

    # Only what this recipe actually runs. Subtitles never draws a frame and never narrates —
    # queueing those anyway would charge for work whose output is thrown away, and would
    # leave the storyboard waiting on jobs that have nothing to do.
    runs = stages_for(project.recipe)
    queue = Queue(session)
    ledger = Ledger(session)
    ids: list[uuid.UUID] = []

    if Stage.GENERATE in runs:
        # Only the scenes that still need a picture.
        #
        # Enqueuing is idempotent, so re-drafting returns the jobs that already ran — but the
        # reservation is not, and holding credits against a finished job leaks them: nothing
        # will ever discharge that hold, because the work is not going to run again. Drafting
        # twice used to cost the user the whole quote a second time, permanently.
        todo = [s for s in scenes if not (s.preview_asset_id or s.final_asset_id)]
        if not todo:
            await session.commit()
            return {
                "queued": 0,
                "job_ids": [],
                "stages": [s.value for s in runs],
                "note": "every scene already has a picture",
            }

        quote = await price_plan(
            session,
            [FakeProvider()],
            project.current_plan_id,
            project.language,
            project.recipe,
        )
        # Priced for the whole plan, charged for what is actually being made.
        wanted = Cents(sum(int(c) for sid, c in quote.lines if sid in {s.id for s in todo}))
        available, _, _ = await ledger.balance(user.id)
        if wanted > available:
            # Refused before anything is queued, and the client is told the shortfall so it
            # can say "you need 12 more" rather than "something went wrong".
            raise HTTPException(
                status.HTTP_402_PAYMENT_REQUIRED,
                {
                    "message": "not enough credits to draft this",
                    "needed": int(wanted),
                    "available": int(available),
                    "short_by": int(wanted) - int(available),
                },
            )

        priced = dict(quote.lines)
        for scene in todo:
            job_id = await queue.enqueue(
                Stage.GENERATE,
                {"scene_id": str(scene.id), "user_id": str(user.id)},
                project=project.id,
            )
            # Tied to the job, so `generate` finds this hold instead of taking a second one.
            await ledger.reserve(user.id, priced[scene.id], job_id=job_id)
            ids.append(job_id)

    if Stage.VOICE in runs:
        ids.append(
            await queue.enqueue(
                Stage.VOICE,
                {"plan_id": str(project.current_plan_id), "language": project.language},
                project=project.id,
            )
        )

    await session.commit()
    return {
        "queued": len(ids),
        "job_ids": [str(i) for i in ids],
        "stages": [s.value for s in runs],
    }


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: uuid.UUID, session: Session, channel: CurrentChannel) -> None:
    """Throw a project away.

    The plans, scenes, renders and jobs go with it — they are cascades, and none of them mean
    anything without the project. The **ledger does not**: it is the record of what was
    charged, and a spend does not stop having happened because the thing it paid for was
    deleted. Those rows keep their entries and lose only their `job_id`.

    Which leaves one thing that has to be done by hand. A reservation is a hold that is
    released by a later `commit` or `refund` against the job that made it — and once the job
    is cascaded away nothing can ever discharge it, so the credits would be held against the
    creator for good, on a project that no longer exists. Every outstanding hold is refunded
    first, and recorded as such.

    Assets are deliberately left alone: they are content-addressed and shared, so the upload
    behind a deleted project may well be behind two others.
    """
    project = await session.get(Project, project_id)
    if project is None or project.channel_id != channel.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such project")

    discharged = select(LedgerEntry.reserve_entry_id).where(
        LedgerEntry.kind.in_(("commit", "refund")),
        LedgerEntry.reserve_entry_id.is_not(None),
    )
    mine = select(Job.id).where(Job.project_id == project_id)
    held = list(
        await session.scalars(
            select(LedgerEntry).where(
                LedgerEntry.job_id.in_(mine),
                LedgerEntry.kind == "reserve",
                LedgerEntry.id.not_in(discharged),
            )
        )
    )
    for entry in held:
        await Ledger(session).refund(entry, "project_deleted")

    await session.delete(project)
    await session.commit()
    log.info("project.delete", project=str(project_id), refunded=len(held))


@router.put("/{project_id}/voice/{voice_id}", response_model=ProjectOut)
async def choose_voice(
    project_id: uuid.UUID,
    voice_id: str,
    session: Session,
    channel: CurrentChannel,
) -> ProjectOut:
    """Which voice this dub is spoken in.

    Its own endpoint rather than a general project PATCH because it is the only field a
    creator changes after the project exists, and because changing it means something: the
    dub they are auditioning is now a different dub. A `PATCH {…}` that could also rename the
    project would make that indistinguishable from a typo fix in the request log.

    `-` clears it, which is how the picker deselects.
    """
    project = await session.get(Project, project_id)
    if project is None or project.channel_id != channel.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such project")

    if voice_id != "-" and not any(v.id == voice_id for v in speak.voices_for(project.language)):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"no voice {voice_id!r} for {project.language}",
        )
    project.voice_id = None if voice_id == "-" else voice_id
    #: Committed, not merely flushed. `get_session` hands out a session the endpoint owns and
    #: does not commit for it, so a flush alone made this endpoint *return* the new voice and
    #: then roll it back: the picker showed the choice, the next read showed the old one, and
    #: the render would have used the old one too.
    await session.commit()
    return ProjectOut.model_validate(project)


@router.get("/{project_id}/dub-preview")
async def dub_preview(
    project_id: uuid.UUID,
    session: Session,
    channel: CurrentChannel,
    voice: str | None = None,
) -> Response:
    """The opening of this dub, spoken, so it can be heard before it is paid for.

    The whole video, not an excerpt. It was capped at forty-five seconds to keep the cost of
    auditioning a voice small, and that was the wrong trade: the thing a creator needs to
    know is whether the dub holds up over the *whole* video — whether it drifts, whether a
    long line runs into the next one — and none of that is visible in the opening seconds.

    Which does mean a long video takes a while the first time each voice is heard. It is
    cached per voice and per line, so it is paid once, and switching back to a voice already
    heard is instant.

    Aligned to zero, so the browser can play it against the source video with no offset
    arithmetic: line four sits at line four's timestamp, and playing both from 0:00 keeps
    them together.
    """
    project = await session.get(Project, project_id)
    if project is None or project.channel_id != channel.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such project")
    if project.current_plan_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "nothing transcribed yet")

    picked = voice or project.voice_id
    if not picked:
        raise HTTPException(status.HTTP_409_CONFLICT, "no voice chosen")
    if not any(v.id == picked for v in speak.voices_for(project.language)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"no voice {picked!r}")

    scenes = list(
        await session.scalars(
            select(Scene).where(Scene.plan_id == project.current_plan_id).order_by(Scene.index)
        )
    )
    heard = project.source_language or project.language

    #: Where each line goes, and the two kinds of lane disagree.
    #:
    #: A lane that keeps the creator's footage has a timestamp per scene — the moment it was
    #: said — and the speech belongs there. A lane that writes its own video has none; its
    #: scenes are laid end to end, so a line starts where the one before it ended.
    #:
    #: `source_start_ms or 0` was right for the first and silently wrong for the second: every
    #: line was written at offset zero, on top of the one before it, so a narration preview
    #: came back as a dozen sentences stacked into the first few seconds.
    cues = []
    at = 0
    for scene in scenes:
        start = scene.source_start_ms if scene.source_start_ms is not None else at
        cues.append(
            speak.Cue(
                text=stages.line_for(scene, project.language, heard),
                start_ms=start,
                duration_ms=scene.duration_ms,
            )
        )
        at = start + scene.duration_ms
    if not cues:
        raise HTTPException(status.HTTP_409_CONFLICT, "nothing to say")

    #: As long as the video, not as long as the last line — the same reason the render's track
    #: is: audio shorter than the picture is a preview that stops before the video does.
    total = max(c.start_ms + c.duration_ms for c in cues)
    if project.source_asset_id is not None:
        source_asset = await session.get(Asset, project.source_asset_id)
        if source_asset is not None and source_asset.duration_ms:
            total = max(total, source_asset.duration_ms)

    #: Stored as an asset rather than held in memory, so the render can reuse the very track
    #: the creator auditioned instead of paying a second full allowance to synthesize the same
    #: words in the same voice. See `execution/dubtrack.py`.
    store = AssetStore(session)
    try:
        track, _made = await dubtrack.build(
            session, store, cues, voice=picked, language=project.language, total_ms=total
        )
    except speak.NoVoiceError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except Exception as exc:
        #: Said in terms of what the creator can act on. A dub is one request per line against
        #: a per-minute allowance, so the usual failure here is not a bad voice — it is more
        #: lines than the key is permitted in a minute, and "try another voice" is advice that
        #: cannot possibly work.
        if speak.is_quota(exc):
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                f"This key's speech quota ran out part way through {len(cues)} lines. "
                f"It refills every minute — try again shortly, or raise the limit on the key.",
            ) from exc
        raise

    await session.commit()
    audio = await store.read(track.id)

    return Response(audio, media_type="audio/wav", headers={"Cache-Control": "no-store"})


@router.post("/{project_id}/timing", response_model=PlanOut)
async def fix_timing(
    project_id: uuid.UUID,
    session: Session,
    channel: CurrentChannel,
    condense: bool = True,
) -> PlanOut:
    """Give every caption enough time to actually be read.

    Two levers, in this order, because they cost different things.

    First the cues are widened into whatever silence sits around them. That is free, exact and
    reversible — it changes no words — but it can only spend time that exists, and a densely
    narrated video has almost none. Measured on a real 3:16 video: 35 of 36 Burmese lines were
    too fast, and widening recovered 3.

    So the second lever rewrites the lines that are still short, to a per-line character budget
    derived from the window they have and the reading speed of their script. That is the part
    a model does better than an algorithm — truncating to a budget is trivial and produces
    nonsense; choosing which words carry the meaning is the whole task. `condense=false` skips
    it for a creator who would rather keep their wording and accept the pace.
    """
    project = await session.get(Project, project_id)
    if project is None or project.channel_id != channel.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such project")
    if project.current_plan_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "nothing transcribed yet")

    scenes = list(
        await session.scalars(
            select(Scene).where(Scene.plan_id == project.current_plan_id).order_by(Scene.index)
        )
    )
    if not scenes:
        raise HTTPException(status.HTTP_409_CONFLICT, "nothing to time")

    tracks = list(project.caption_languages) or [project.language]
    #: Timed against the language that is actually burnt in first. A bilingual subtitle is
    #: read top line first, and it is the one that has to fit.
    shown = tracks[0]
    heard = project.source_language or project.language
    by_id = {str(scene.id): scene for scene in scenes}

    cues = [
        timing.Cue(
            id=str(scene.id),
            text=stages.line_for(scene, shown, heard),
            start_ms=scene.source_start_ms or 0,
            duration_ms=scene.duration_ms,
        )
        for scene in scenes
    ]
    before = sum(1 for v in timing.check(cues, shown) if v.tight)

    fitted = timing.fit(cues, shown)
    for cue in fitted:
        scene = by_id[cue.id]
        #: Only the lanes that keep the source have a source timestamp to move; for the others
        #: the scenes are laid end to end and the duration is the whole of the position.
        if scene.source_start_ms is not None:
            scene.source_start_ms = cue.start_ms
        scene.duration_ms = cue.duration_ms

    rewritten = 0
    if condense:
        still = [v for v in timing.check(fitted, shown) if v.tight]
        if still:
            budgets = [
                max(1, int(v.cue.duration_ms / 1000 * get_pack(shown).typography.cps))
                for v in still
            ]
            shorter = await translate.condense([v.cue.text for v in still], budgets, language=shown)
            for verdict, new in zip(still, shorter, strict=True):
                if new == verdict.cue.text:
                    continue
                scene = by_id[verdict.cue.id]
                #: Written where the line was read from, so the change survives a re-render
                #: and shows up in the editor rather than only in this response.
                if shown == heard:
                    scene.caption = new
                else:
                    scene.translations = {**scene.translations, shown: new}
                rewritten += 1

    await session.commit()

    after = sum(
        1
        for v in timing.check(
            [
                timing.Cue(
                    id=str(scene.id),
                    text=stages.line_for(scene, shown, heard),
                    start_ms=scene.source_start_ms or 0,
                    duration_ms=scene.duration_ms,
                )
                for scene in scenes
            ],
            shown,
        )
        if v.tight
    )
    log.info(
        "timing.fix",
        project=str(project_id),
        language=shown,
        tight_before=before,
        tight_after=after,
        rewritten=rewritten,
    )
    return await _plan(session, project.current_plan_id)


@router.post("/{project_id}/tracks/{language}", response_model=PlanOut)
async def add_track(
    project_id: uuid.UUID,
    language: str,
    session: Session,
    channel: CurrentChannel,
    burn: bool = True,
) -> PlanOut:
    """Caption this video in one more language.

    A bilingual subtitle is one video, not two: the cue appears at the same moment and lasts
    the same time, only the words differ. So a track is N translations hung off the scenes
    that already exist, and the timings are shared by construction — there is no second track
    that can drift out of sync with the first, because there is no second set of timings.

    Idempotent per language. Asking twice for a track that is already there returns the plan
    unchanged rather than paying to translate it again.

    `burn=false` translates without putting the language on screen. That is for reading rather
    than publishing: a creator subtitling Chinese into Burmese cannot check the Burmese
    against a source they do not read, so the editor offers English beside it — a language
    they can check *against*, which is not a language they want burnt into the video.
    """
    try:
        get_pack(language)
    except UnsupportedLanguageError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"no language pack for {language!r} yet — Lumina speaks {', '.join(supported())}",
        ) from None

    project = await session.get(Project, project_id)
    if project is None or project.channel_id != channel.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such project")
    if project.current_plan_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "nothing planned yet")

    scenes = list(
        await session.scalars(
            select(Scene).where(Scene.plan_id == project.current_plan_id).order_by(Scene.index)
        )
    )
    if not scenes:
        raise HTTPException(status.HTTP_409_CONFLICT, "nothing to translate")

    tracks = list(project.caption_languages) or [project.language]
    spoken = project.source_language or project.language

    # Already translated, or it is the language the lines are already in.
    # Which language each line is actually in, not which language the project mostly is.
    #
    # A file can change language part way through — Japanese speech under an English song —
    # and asking "is the project's source language the same as the target" then answers for
    # the whole track at once. On that file, requesting an English track did nothing at all:
    # the project's source *was* English, so every line looked like it needed no translation,
    # including the six Japanese ones the creator asked for English precisely to read.
    said = detect_all(s.script_line for s in scenes)

    #: The lines that still need this language, grouped by what each one is written in, so
    #: nothing is translated from the wrong source.
    todo: dict[str, list[Scene]] = {}
    for scene, code in zip(scenes, said, strict=True):
        from_code = code or spoken
        if from_code == language or scene.translations.get(language):
            continue
        todo.setdefault(from_code, []).append(scene)

    for from_code, batch in sorted(todo.items()):
        try:
            done = await translate.translate_segments(
                [
                    Segment(text=scene.script_line, start_ms=0, duration_ms=scene.duration_ms)
                    for scene in batch
                ],
                source=from_code,
                target=language,
                # The channel's do-not-translate list. On the identity kit because a creator's
                # technical vocabulary is stable across every video they make — re-typing
                # "Computer" for each one is not a thing anyone will do.
                keep=_glossary_of(channel),
            )
        except translate.TranslationUnavailableError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        for scene, line in zip(batch, done, strict=True):
            # Reassigned rather than mutated: SQLAlchemy does not see an in-place change to a
            # JSONB dict, so the write would be silently dropped on commit.
            scene.translations = {**scene.translations, language: line.text}

    if burn and language not in tracks:
        tracks.append(language)
    project.caption_languages = tracks
    await session.commit()
    return await _plan(session, project.current_plan_id)


@router.delete("/{project_id}/tracks/{language}", response_model=PlanOut)
async def drop_track(
    project_id: uuid.UUID, language: str, session: Session, channel: CurrentChannel
) -> PlanOut:
    """Stop burning in one language. The translations are kept — turning a track back on
    should not cost a second translation of the same lines."""
    project = await session.get(Project, project_id)
    if project is None or project.channel_id != channel.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such project")
    if project.current_plan_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "nothing planned yet")

    tracks = [c for c in (project.caption_languages or [project.language]) if c != language]
    if not tracks:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "a video needs at least one caption track; add another before removing this one",
        )
    project.caption_languages = tracks
    await session.commit()
    return await _plan(session, project.current_plan_id)


def _glossary_of(channel: Channel) -> list[str]:
    """Terms this channel keeps untranslated, cleaned of anything unusable.

    Deduplicated case-insensitively and capped: the list goes into every translation prompt,
    and a thousand terms would cost more than the translation and dilute the instruction.
    """
    raw = channel.identity.get("glossary") if isinstance(channel.identity, dict) else None
    if not isinstance(raw, list):
        return []
    seen: dict[str, str] = {}
    for item in raw:
        term = str(item).strip()
        if term and term.lower() not in seen:
            seen[term.lower()] = term
    return list(seen.values())[:200]


async def _plan(session: Session, plan_id: uuid.UUID) -> PlanOut:
    """Assemble the wire shape.

    Title and summary live inside the plan's JSONB content rather than as columns, because
    they are authored parts of the plan document, not database facts. The wire type is built
    explicitly here instead of by attribute mapping, so that stays true on both sides.
    """
    plan = await session.scalar(
        select(Plan).where(Plan.id == plan_id).options(selectinload(Plan.scenes))
    )
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such plan")

    raw = plan.content.get("moments") or []
    project = await session.get(Project, plan.project_id)

    # How long the result will actually be. Summing the scenes is right for every lane that
    # lays them end to end, and wrong for the one that does not: subtitling returns the source
    # whole, so its length is the source's, while its scenes only cover the captioned moments.
    whole = project is not None and Recipe(project.recipe) in KEEPS_WHOLE_SOURCE
    duration = sum(scene.duration_ms for scene in plan.scenes)
    if whole and project is not None and project.source_asset_id is not None:
        source = await session.get(Asset, project.source_asset_id)
        duration = (source.duration_ms if source else None) or duration

    return PlanOut(
        id=plan.id,
        project_id=plan.project_id,
        source_asset_id=project.source_asset_id if project else None,
        duration_ms=duration,
        keeps_whole_source=whole,
        recipe=project.recipe if project else "explainer",
        written_by_us=[int(i) for i in plan.content.get("written_by_us", [])],
        caption_languages=(
            list(project.caption_languages) or [project.language] if project else []
        ),
        language=project.language if project else "en",
        source_language=project.source_language if project else None,
        version=plan.version,
        title=str(plan.content.get("title", "")),
        summary=str(plan.content.get("summary", "")),
        scenes=await _with_languages(session, plan.scenes),
        moments=[Moment.model_validate(m) for m in raw],
    )
