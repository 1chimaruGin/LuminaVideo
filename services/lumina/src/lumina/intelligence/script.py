"""Script Studio.

The script decides everything downstream — scene count, durations, voice timing, caption
density, cost. Getting it right *before* generation is where the money is saved, which is why
`FEATURES.md` ranks this the highest-leverage thing in the product.

Three operations, each cheap and each targeted:

  - **beats** — the script as a structure, not a blob. Every downstream stage needs the parts.
  - **hooks** — five openings for the same script. One call, aimed at the highest-variance
    three seconds in the video.
  - **rewrite** — one beat, one small operation. Never "regenerate everything".

And one that costs nothing at all: `timing`, which answers "is this too long?" from the
language pack's reading speed, locally, while the user is still typing. That is the single
most valuable use of the language-pack abstraction outside captions, and it is the reason a
creator finds out their script is twenty seconds over *before* twelve scenes are generated at
the wrong pace rather than after.
"""

from __future__ import annotations

from enum import StrEnum

import structlog
from pydantic import BaseModel, Field

from lumina.config import get_settings
from lumina.intelligence.languages import get_pack

log = structlog.get_logger(__name__)


class Beat(StrEnum):
    """The five parts of a short. Ordered; the Hook is not merely the first line."""

    HOOK = "hook"
    SETUP = "setup"
    TURN = "turn"
    PAYOFF = "payoff"
    CTA = "cta"


#: Target share of the runtime for each beat, and how many of each a script normally has.
#: Turns are the body, so they take the remainder and there can be several.
BEAT_SHAPE: dict[Beat, tuple[float, str]] = {
    Beat.HOOK: (0.05, "The whole retention decision happens here"),
    Beat.SETUP: (0.10, "Context, one sentence"),
    Beat.TURN: (0.62, "The actual content, one point per beat"),
    Beat.PAYOFF: (0.13, "The takeaway"),
    Beat.CTA: (0.10, "One action"),
}


class HookPattern(StrEnum):
    """Why a hook works. Shown next to each option so the choice is informed rather than
    aesthetic — a creator who knows they picked "surprising number" can write the next one."""

    CONTRARIAN = "contrarian"
    NUMBER = "number"
    QUESTION = "question"
    VISUAL = "visual"
    STAKES = "stakes"


class Hook(BaseModel):
    text: str = Field(description="The opening line, spoken")
    pattern: HookPattern
    why: str = Field(description="One clause on what this hook is doing")


class Hooks(BaseModel):
    hooks: list[Hook] = Field(min_length=3, max_length=6)


class DraftedBeat(BaseModel):
    beat: Beat
    text: str


class DraftedScript(BaseModel):
    beats: list[DraftedBeat] = Field(min_length=3, max_length=16)


class Timing(BaseModel):
    """Read-aloud length, computed locally.

    No model call, so this can run on every keystroke. `cps` comes from the language pack
    because reading speed varies about threefold across languages, and a hardcoded rate makes
    every non-English script silently mistimed.
    """

    chars: int
    words: int
    spoken_ms: int
    target_ms: int
    #: Positive means over length. Signed rather than absolute so the UI can say which way.
    over_ms: int
    cps: float

    @property
    def ok(self) -> bool:
        """Within ten percent of target. Short-form tolerates a little drift; the pacing is
        adjusted by scene duration, not by cutting words at render time."""
        return abs(self.over_ms) <= self.target_ms * 0.10


def timing(text: str, *, language: str = "en", target_ms: int = 60_000) -> Timing:
    """How long this will take to say. Free, local, and correct per language."""
    pack = get_pack(language)
    cps = pack.typography.cps
    chars = len(text.strip())
    spoken = round(chars / cps * 1000) if cps > 0 else 0
    return Timing(
        chars=chars,
        words=len([w for w in text.split() if w]),
        spoken_ms=spoken,
        target_ms=target_ms,
        over_ms=spoken - target_ms,
        cps=cps,
    )


def split_beats(
    text: str, *, language: str = "en", target_ms: int = 60_000
) -> list[dict[str, object]]:
    """Structure a script without asking a model.

    The fallback for `beats()` and, on its own, a perfectly reasonable answer: the first
    sentence is the hook, the last is the call to action, the one before it is the payoff, and
    what remains is setup and turns. That is the actual shape of nearly every short, and it
    costs nothing.
    """
    sentences = [s.strip() for s in text.replace("\n", " ").split(".") if s.strip()]
    if not sentences:
        return []

    labels: list[Beat] = []
    n = len(sentences)
    for i in range(n):
        if i == 0:
            labels.append(Beat.HOOK)
        elif i == 1 and n >= 4:
            labels.append(Beat.SETUP)
        elif i == n - 1 and n >= 3:
            labels.append(Beat.CTA)
        elif i == n - 2 and n >= 5:
            labels.append(Beat.PAYOFF)
        else:
            labels.append(Beat.TURN)

    pack = get_pack(language)
    return [
        {
            "beat": label.value,
            "text": f"{sentence}.",
            "duration_ms": max(800, round(len(sentence) / pack.typography.cps * 1000)),
        }
        for label, sentence in zip(labels, sentences, strict=True)
    ]


_HOOK_SYSTEM = """You write opening lines for short-form video.

The first three seconds decide whether the video is watched at all — around 71% of viewers
leave inside them. Everything about a hook follows from that.

- Never open with setup, a greeting, or what the video is "about".
- Open on the consequence, the contradiction, or the number that does not sound right.
- One sentence. Say it aloud in your head; if it needs a comma to breathe, it is too long.
- Each hook must use a different pattern, and the pattern must be labelled honestly.
"""

_REWRITE_SYSTEM = """You revise one line of a short-form video script.

Return only the revised line. Not a preamble, not options, not an explanation.

Rules:
- Keep the writer's voice. You are editing their sentence, not replacing it with yours.
- Keep the meaning. If the instruction cannot be followed without changing what is said,
  follow it as far as it can go and leave the meaning intact.
- It is spoken aloud. No parentheses, no lists, no symbols that have no sound.
"""

#: The rewrite operations offered. Small and named, so a creator can apply one to one beat
#: and leave the rest untouched — the same principle as per-scene regeneration.
REWRITES: dict[str, str] = {
    "tighter": "Cut every word that is not doing work. Same meaning, fewer syllables.",
    "simpler": "Replace jargon and abstraction with plain, concrete language.",
    "punchier": "Make it land harder. Stronger verbs, shorter sentence, put the surprise last.",
    "concrete": "Replace the general with the specific: a number, a name, a thing you can see.",
    "shorter": "Cut it to roughly two thirds of its current length.",
    "longer": "Expand it by about half, adding detail that earns its time.",
}


async def hooks(script: str, *, language: str = "en", count: int = 5) -> Hooks:
    """Five openings for the same script, each labelled by the pattern it uses."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        return _offline_hooks(script, count)

    from anthropic import AsyncAnthropic

    pack = get_pack(language)
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    response = await client.messages.parse(
        model=settings.planner_model,
        max_tokens=4_000,
        thinking={"type": "adaptive"},
        system=[
            {
                "type": "text",
                "text": (
                    f"{_HOOK_SYSTEM}\n\n{pack.llm_profile.system_suffix}\n\n"
                    "Patterns that work in this language:\n"
                    + "\n".join(f"- {h}" for h in pack.llm_profile.hook_patterns)
                ),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": f"Write {count} hooks for this script:\n\n{script}"}],
        output_format=Hooks,
    )
    if response.parsed_output is None:
        return _offline_hooks(script, count)
    log.info("script.hooks", n=len(response.parsed_output.hooks))
    return response.parsed_output


async def rewrite(line: str, operation: str, *, language: str = "en") -> str:
    """Apply one small operation to one line."""
    instruction = REWRITES.get(operation)
    if instruction is None:
        raise ValueError(f"unknown rewrite {operation!r}; expected one of {sorted(REWRITES)}")

    settings = get_settings()
    if not settings.anthropic_api_key:
        return _offline_rewrite(line, operation)

    from anthropic import AsyncAnthropic

    pack = get_pack(language)
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    response = await client.messages.create(
        model=settings.planner_model,
        max_tokens=1_000,
        system=[
            {
                "type": "text",
                "text": f"{_REWRITE_SYSTEM}\n\n{pack.llm_profile.system_suffix}",
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": f"{instruction}\n\nLine:\n{line}"}],
    )
    parts = [b.text for b in response.content if b.type == "text"]
    out = "".join(parts).strip()
    return out or line


def _offline_hooks(script: str, count: int) -> Hooks:
    """Deterministic hooks, for when no key is configured.

    These are shaped like real hooks — each one uses a different pattern, and the pattern is
    labelled — so the Hook Lab screen can be built and judged without a network or a bill.

    They deliberately do not splice the script's own words into a template. A short's first
    sentence is a clause, not a noun phrase, and dropping it into "everyone is wrong about
    ___" produces "everyone is wrong about black holes are not vacuum cleaners". A generic
    hook that is grammatical is a better stand-in than a specific one that is broken, because
    the thing being judged here is the screen, not the copy.
    """
    topic = next((s.strip() for s in script.split(".") if s.strip()), "")
    built = [
        Hook(
            text="Everyone gets this backwards.",
            pattern=HookPattern.CONTRARIAN,
            why="Opens a disagreement the viewer has to resolve",
        ),
        Hook(
            text="Ninety percent of people believe the opposite of this.",
            pattern=HookPattern.NUMBER,
            why="A number that does not sound right demands a check",
        ),
        Hook(
            text=f"{topic[:70]}?" if topic else "So what actually happens here?",
            pattern=HookPattern.QUESTION,
            why="Names the gap in the viewer's knowledge directly",
        ),
        Hook(
            text="Watch what happens in the next three seconds.",
            pattern=HookPattern.VISUAL,
            why="Promises something to see, not something to hear",
        ),
        Hook(
            text="Getting this wrong costs more than you think.",
            pattern=HookPattern.STAKES,
            why="Attaches a consequence to not watching",
        ),
    ]
    log.info("script.hooks.offline", n=min(count, len(built)))
    return Hooks(hooks=built[: max(3, min(count, len(built)))])


def _offline_rewrite(line: str, operation: str) -> str:
    """A real, if blunt, edit. Length operations are honest without a model; the rest are
    not, and say so by returning the line unchanged rather than mangling it."""
    words = line.split()
    if operation == "shorter" and len(words) > 4:
        return " ".join(words[: max(3, round(len(words) * 0.66))]).rstrip(",;:") + "."
    if operation == "tighter" and len(words) > 5:
        filler = {"just", "really", "very", "actually", "basically", "simply", "quite"}
        kept = [w for w in words if w.lower().strip(",.") not in filler]
        return " ".join(kept)
    return line


#: Caption limits, and what actually works on each platform. The limit is the hard fact; the
#: note is what the model is told to aim for, which is always far shorter than the ceiling.
PLATFORMS: dict[str, tuple[int, str]] = {
    "tiktok": (2200, "One or two lines. The caption is read after the hook lands, not before."),
    "reels": (2200, "Two or three lines. A question at the end earns replies."),
    "shorts": (
        100,
        "Under 100 characters — YouTube truncates the rest into the title. Front-load it.",
    ),
    "x": (280, "One sentence. No hashtag stuffing; one or two at most."),
}

_COPY_SYSTEM = """You write the post text that goes with a short-form video.

This is not a summary of the video. The video says what it says; the caption's job is to make
someone stop and watch it, and then to give the algorithm something to file it under.

- Never open with "In this video" or "Here's why". Say the thing.
- The caption is read on a phone, in a feed, at speed. Short lines.
- Hashtags: five or six, specific enough to describe a niche rather than a category.
  "#jwst" places the video; "#science" does not.
- Match the platform's convention and length. They are genuinely different.
"""


class PostCopySet(BaseModel):
    posts: list[PostCopyDraft] = Field(min_length=1, max_length=4)


class PostCopyDraft(BaseModel):
    platform: str
    text: str
    hashtags: list[str] = Field(min_length=1, max_length=10)


async def post_copy(
    title: str, script: str, *, language: str = "en", platforms: list[str] | None = None
) -> list[PostCopyDraft]:
    """Caption and tags per platform, for the Deliver screen."""
    wanted = [p for p in (platforms or ["tiktok", "reels", "shorts"]) if p in PLATFORMS]
    if not wanted:
        wanted = ["tiktok"]

    settings = get_settings()
    if not settings.anthropic_api_key:
        return _offline_copy(title, script, wanted)

    from anthropic import AsyncAnthropic

    pack = get_pack(language)
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    response = await client.messages.parse(
        model=settings.planner_model,
        max_tokens=3_000,
        system=[
            {
                "type": "text",
                "text": f"{_COPY_SYSTEM}\n\n{pack.llm_profile.system_suffix}",
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    f"Video: {title}\n\nScript:\n{script}\n\n"
                    + "Write the post for each of these:\n"
                    + "\n".join(f"- {p}: {PLATFORMS[p][1]}" for p in wanted)
                ),
            }
        ],
        output_format=PostCopySet,
    )
    if response.parsed_output is None:
        return _offline_copy(title, script, wanted)
    log.info("script.post_copy", platforms=len(response.parsed_output.posts))
    return response.parsed_output.posts


def _offline_copy(title: str, script: str, platforms: list[str]) -> list[PostCopyDraft]:
    """Deterministic post copy, for when no key is configured.

    Built from the script's own sentences rather than a template, so the Deliver screen shows
    something about *this* video — a fixed placeholder there is indistinguishable from a bug,
    because the whole point of the panel is that the text is specific.

    Sentences, not a word-count tail: taking "the last fourteen words" of a three-sentence
    script overlaps the opening and the caption reads as a stutter.
    """
    sentences = [x.strip() for x in script.split(".") if x.strip()]
    opening = sentences[0] if sentences else title
    rest = ". ".join(sentences[1:3])

    #: Words long enough to describe a niche rather than a category. "#jwst" places a video;
    #: "#science" does not — so the short words are exactly the ones to drop.
    stop = {"about", "these", "those", "their", "there", "which", "would", "could"}
    words = [w.lower().strip(".,!?:;") for w in title.split()]
    tags = [w for w in words if len(w) > 4 and w not in stop][:4] or ["short"]

    made: list[PostCopyDraft] = []
    for name in platforms:
        limit = PLATFORMS[name][0]
        # A short limit means the caption is the hook and nothing else.
        body = f"{opening}." if limit < 200 or not rest else f"{opening}. {rest}."
        made.append(
            PostCopyDraft(platform=name, text=body[:limit], hashtags=[f"#{t}" for t in tags])
        )
    log.info("script.post_copy.offline", platforms=len(made))
    return made
