"""Brief -> typed plan.

One call produces the entire plan, not one call per scene. Twelve separate calls cost more
and, more importantly, produce twelve scenes that do not know about each other — the pacing
drifts and the same idea gets made twice. Written together, they hold a shape.

Caching matters here. The system prompt, the recipe definition and the language profile are
byte-identical across every user and every request; only the brief is volatile. Putting the
stable part first and marking it cacheable makes those tokens read at roughly a tenth of the
input price, which is a real line on a product whose whole preview tier targets well under a
dollar.

`plan()` falls back to a deterministic outline when no API key is configured, so the pipeline
runs end to end in development and in tests without a network or a bill.
"""

from __future__ import annotations

import uuid

import structlog
from pydantic import BaseModel, Field

from lumina.config import get_settings
from lumina.domain.ids import PlanId, SceneId
from lumina.domain.jobs import Stage, stages_for
from lumina.domain.plan import Aspect, Plan, Recipe, Scene
from lumina.intelligence.languages import get_pack

log = structlog.get_logger(__name__)

#: The stable prefix. Every byte here is shared across all users, so it must not carry
#: anything request-specific — a timestamp or a user name in this string silently forfeits
#: the cache discount for everyone.
SYSTEM = """You plan short-form vertical videos for independent creators.

Return a plan as structured output. Rules that matter:

- The first scene is the hook. Around 71% of viewers decide inside three seconds, so lead
  with the surprising consequence, never the setup.
- One idea per scene. If a scene needs "and", it is two scenes.
- script_line is spoken aloud. Write it to be said, not read: short sentences, no
  parentheses, no lists, no numerals that are ambiguous when spoken.
- image_prompt describes one still frame. Concrete subject, lighting and mood. No text in
  the image, no camera directions, no shot numbers.
- caption is two to four words. It is the on-screen label for the scene, not a summary.
- duration_ms must be achievable at the language's reading speed for that script_line.
"""


#: What each recipe asks for, on top of the universal rules above.
#:
#: These are stable per recipe and shared across every user, so each one gets its own cache
#: breakpoint: a request extends the universal prefix (one hot entry for the whole product)
#: with the recipe's block (one entry per recipe). Both layers stay cheap.
#:
#: The recipe is the only thing that varies the planner. There is no `if recipe ==` anywhere
#: downstream — a sixth lane is an entry in this dict and a row in RECIPE_STAGES.
RECIPE_BRIEFS: dict[Recipe, str] = {
    Recipe.EXPLAINER: """This is an EXPLAINER.

The viewer does not yet care about this topic. Earn it.

- Open on the consequence, not the definition. "Your passwords are already public" beats
  "Today we are talking about data breaches."
- Every scene must move the argument forward. Cut anything that is context for context's sake.
- Land one takeaway the viewer could repeat to someone else.""",
    Recipe.DUB: """This is a DUB: a finished video that has to be spoken in another language.

The words are already there — heard on the video or written by the creator. Your job is to
break them into lines someone can say over the footage, not to rewrite them.

- Preserve their meaning and their register. Keep their phrasing, their jokes and their voice.
- Split only where a natural breath falls, and never mid-thought.
- A dubbed line has to fit the time the original took to say it. Prefer the shorter phrasing
  when two are equally faithful — a line that runs long desyncs everything after it.
- Nothing is drawn for this recipe. The creator's frames are what the viewer sees.""",
    Recipe.CLIP_LONG_VIDEO: """This is a CLIP cut from the creator's own long video.

The footage exists. You are choosing and framing, not writing.

- Each scene corresponds to a moment already in the transcript. Use their words verbatim.
- The first scene must stand alone. A viewer arriving mid-scroll has no context for the
  original video, so the opening line cannot depend on anything that came before it.
- caption carries the moment. On mute, the caption is the whole video.""",
    Recipe.SUBTITLE_ONLY: """This is SUBTITLES for a video that is already finished.

Change nothing. Do not improve the wording, fix the grammar, or tidy the phrasing — this is a
transcript of what was actually said, and altering it puts words in the speaker's mouth.

- script_line is the speech, exactly as spoken.
- caption is the same text broken for the screen, short enough to read in the time it takes
  to say it.
- image_prompt is unused here; the creator's own footage is on screen. Leave it empty.""",
}


class PlannerRefusedError(RuntimeError):
    """The model declined, or its output failed validation.

    Worth its own type: the caller must refund rather than retry, and must not quietly
    substitute a lower-quality plan for one the user is being charged for.
    """

    def __init__(self, stop_reason: object = None) -> None:
        super().__init__(f"planner returned no usable plan (stop_reason={stop_reason})")


class PlannedScene(BaseModel):
    script_line: str = Field(description="Spoken narration for this scene")
    image_prompt: str = Field(description="One still frame: subject, light, mood")
    caption: str = Field(description="Two to four words shown on screen")
    duration_ms: int = Field(ge=800, le=15_000)


#: How many scenes a *generated* video may have.
#:
#: A budget, not a limit on the data: every scene here is a frame somebody pays to generate,
#: so a model that answers a one-line brief with sixty of them has misunderstood the job and
#: the bill. It belongs to the lanes that draw pictures, and to nothing else.
MAX_DRAFTED_SCENES = 24

#: How many scenes a *transcribed* video may have.
#:
#: A sanity bound rather than a budget. Subtitle cues are counted by how much was said, not
#: chosen — a three-minute video runs to about forty and an hour runs to a thousand, none of
#: which costs anything to draw. Sharing the generation budget here is what made a 3:21 clip
#: fail with "at most 24 items, not 39" after it had already been transcribed and translated.
MAX_CAPTION_SCENES = 5_000


class PlannedVideo(BaseModel):
    title: str
    summary: str = Field(description="The outcome in one plain sentence, shown before running")
    #: One is legal. Three was an arbitrary floor that made sense for an explainer and was
    #: wrong for subtitles: a six-second clip with one sentence in it is one line, and padding
    #: it to three puts words on screen that were never said.
    scenes: list[PlannedScene] = Field(min_length=1, max_length=MAX_CAPTION_SCENES)


class DraftedVideo(PlannedVideo):
    """What a model is asked for, as opposed to what a transcript produces.

    Same shape, tighter ceiling. This is the schema handed to the planner as its output
    format, so the budget is enforced where the spending decision is actually made.
    """

    scenes: list[PlannedScene] = Field(min_length=1, max_length=MAX_DRAFTED_SCENES)


async def plan(
    brief: str,
    *,
    recipe: Recipe = Recipe.EXPLAINER,
    language: str = "en",
    target_ms: int = 60_000,
    lines: list[str] | None = None,
) -> Plan:
    """Turn a brief into a typed, versioned plan.

    `lines` is the escape hatch for the lanes where the lines are *given* rather than written:
    a subtitle track's boundaries come from the file or the speech engine, and they are
    authoritative. Passing the transcript as a brief and letting the planner re-derive them
    silently rewrote them — it splits on full stops, so a cue ending in "!" merged with the
    one after it and a cue containing a "." split in two. The count then no longer matched the
    timings, and every caption after the first mismatch was drawn at the wrong moment.

    Nothing is invented, merged, split or padded when this is set. That is the whole point.
    """
    if lines is not None:
        return _to_domain(_verbatim(lines, recipe, language), recipe=recipe, language=language)

    settings = get_settings()
    drafted = (
        await _ask_claude(brief, recipe, language, target_ms)
        if settings.anthropic_api_key
        else _offline(brief, recipe, language, target_ms)
    )
    return _to_domain(drafted, recipe=recipe, language=language)


def _verbatim(lines: list[str], recipe: Recipe, language: str) -> PlannedVideo:
    """One scene per given line, in order, unchanged.

    Durations here are placeholders: the caller overwrites every one of them with the window
    the line actually occupies in the source. They are set from reading speed rather than left
    at zero only so the plan is valid on its own.
    """
    pack = get_pack(language)
    kept = [line.strip() for line in lines if line.strip()]
    return PlannedVideo(
        # The first line, which is a poor name and better than the alternative: the brief on
        # these lanes is the uploaded filename, and projects listed as
        # `ch11_20260825_1600-1605.mp4` were worse. A name the creator actually typed beats
        # both and is never overwritten — see `keep_title` in the plan stage.
        title=kept[0][:80] if kept else "Untitled",
        summary="",
        scenes=[
            PlannedScene(
                script_line=line,
                caption=line,
                # A lane that keeps the creator's footage never draws a frame, so an image
                # prompt would be dead weight carried through every stage downstream.
                image_prompt="",
                duration_ms=min(15_000, max(800, pack.caption_duration_ms(line))),
            )
            for line in kept
        ],
    )


async def _ask_claude(brief: str, recipe: Recipe, language: str, target_ms: int) -> DraftedVideo:
    from anthropic import AsyncAnthropic

    settings = get_settings()
    pack = get_pack(language)

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    response = await client.messages.parse(
        model=settings.planner_model,
        max_tokens=16_000,
        thinking={"type": "adaptive"},
        # Manual cache breakpoint. `parse()` takes no top-level `cache_control`, so the
        # system prompt is sent as blocks and the breakpoint goes on the last one.
        #
        # Everything above the breakpoint is byte-identical for every user and every
        # request, so it reads at roughly a tenth of the input price. The brief is volatile
        # and therefore lives in `messages`, after it — putting anything request-specific
        # above the breakpoint silently forfeits the discount for everyone.
        system=[
            # Block 1: universal. Identical for every user, every recipe, every language —
            # one hot cache entry for the entire product.
            {
                "type": "text",
                "text": (
                    f"{SYSTEM}\n\n{pack.llm_profile.system_suffix}\n\n"
                    + "\n".join(f"- {h}" for h in pack.llm_profile.hook_patterns)
                ),
                "cache_control": {"type": "ephemeral"},
            },
            # Block 2: the recipe. Extends the prefix above rather than replacing it, so a
            # recap request still hits block 1's cache and adds one entry of its own.
            {
                "type": "text",
                "text": RECIPE_BRIEFS.get(recipe, RECIPE_BRIEFS[Recipe.EXPLAINER]),
                "cache_control": {"type": "ephemeral"},
            },
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    f"Recipe: {recipe.value}\n"
                    f"Language: {language}\n"
                    f"Target length: {target_ms // 1000} seconds\n\n"
                    f"Brief:\n{brief}"
                ),
            }
        ],
        output_format=DraftedVideo,
    )
    out = response.parsed_output
    if out is None:
        # `parsed_output` is None when the model declines or the output does not validate.
        # Failing loudly is right: a silent fall-through to the offline outline would hand
        # the user a generic plan and charge them for a real one.
        raise PlannerRefusedError(getattr(response, "stop_reason", None))

    log.info("planner.claude", scenes=len(out.scenes), model=settings.planner_model)
    return out


#: How each recipe describes itself once planned. Shown on the Recipe screen as the one plain
#: sentence the user confirms, so it names the outcome rather than the machinery.
_SUMMARY: dict[Recipe, str] = {
    Recipe.EXPLAINER: "A {secs}-second explainer in {n} scenes.",
    Recipe.DUB: "Your video dubbed across {n} lines, {secs} seconds.",
    Recipe.CLIP_LONG_VIDEO: "{n} {moments} cut from your video, {secs} seconds.",
    Recipe.SUBTITLE_ONLY: "Subtitles for {secs} seconds of video, in {n} {lines}.",
}


def _offline(brief: str, recipe: Recipe, language: str, target_ms: int) -> DraftedVideo:
    """A deterministic outline, for when no key is configured.

    Not a stub that returns nothing: it produces a real, correctly shaped plan whose scene
    durations obey the language's reading speed, so every stage downstream is exercised
    exactly as it would be in production.

    It honours the two recipe rules that are correctness rather than quality — subtitles are
    the speaker's words untouched, and nothing here invents an image prompt for a lane that
    is not going to generate a frame. The rest of what a recipe brief asks for is a judgement
    the model makes; offline, there is no model, and pretending otherwise would hide that.
    """
    pack = get_pack(language)
    sentences = [s.strip() for s in brief.replace("\n", " ").split(".") if s.strip()]

    # Lanes that keep footage the user already has never draw a new frame, so an image prompt
    # here would be dead weight carried through every stage downstream.
    stages = stages_for(recipe.value)
    draws = Stage.GENERATE in stages

    if draws and len(sentences) < 3:
        # Padding is only ever acceptable where the lines are being *written*. On a lane that
        # transcribes, an invented line is a subtitle for something nobody said — and it would
        # be cut against a stretch of video chosen at random.
        filler = ["It is stranger than it sounds", "Here is why", "More soon"]
        sentences = [*sentences, *filler][:3]
    if not sentences:
        sentences = [brief.strip()[:120] or "Untitled"]

    per = max(1, target_ms // max(1, len(sentences)))
    scenes = [
        PlannedScene(
            script_line=f"{s}.",
            image_prompt=f"{s.lower()}, cinematic, vertical, dramatic light" if draws else "",
            caption=" ".join(s.split()[:3]),
            duration_ms=min(15_000, max(800, min(per, pack.caption_duration_ms(s) * 2))),
        )
        for s in sentences[:12]
    ]
    log.info("planner.offline", scenes=len(scenes), recipe=recipe.value)
    template = _SUMMARY.get(recipe, _SUMMARY[Recipe.EXPLAINER])
    # A summary reading "in 1 lines" looks like a bug whether or not it is one, and this
    # sentence is the whole content of the Recipe screen.
    n = len(scenes)
    return DraftedVideo(
        title=sentences[0][:80],
        summary=template.format(
            secs=target_ms // 1000,
            n=n,
            lines="line" if n == 1 else "lines",
            moments="moment" if n == 1 else "moments",
        ),
        scenes=scenes,
    )


def _to_domain(draft: PlannedVideo, *, recipe: Recipe, language: str) -> Plan:
    return Plan(
        id=PlanId(uuid.uuid4()),
        version=1,
        recipe=recipe,
        language=language,
        title=draft.title,
        summary=draft.summary,
        aspects=[Aspect.VERTICAL],
        scenes=[
            Scene(
                id=SceneId(uuid.uuid4()),
                index=i,
                prompt=s.image_prompt,
                script_line=s.script_line,
                caption=s.caption,
                duration_ms=s.duration_ms,
            )
            for i, s in enumerate(draft.scenes)
        ],
    )
