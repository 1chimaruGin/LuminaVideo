"""Captions in a language other than the one that was spoken.

Same shape as `Transcriber` and `Provider`, for the same reason: nothing above this file knows
which engine translated. Two implementations, chosen by whether a key is configured —
`ClaudeTranslator`, and `NoTranslator`, which refuses in words a person can act on rather than
silently handing back the original text as though it had been translated.

Two properties this has to guarantee, both of which a naive "translate this blob" call breaks:

  - **One line in, one line out.** Every caption line already has a start and a duration taken
    from the video — when it was actually said. Translation must not merge or split them, or
    the timings stop pointing at the right moment. Structured output enforces the count, and
    it is checked again on the way out.
  - **Nothing but the words.** No commentary, no transliteration in brackets, no "here is the
    translation". A caption is burnt into the video; anything extra is unremovable.

**Some words must not be translated.** "Computer" in a Burmese sentence stays "Computer" —
that is how the word is actually said, and the dictionary equivalent reads as stilted or
simply wrong. The same is true of product names, brands, acronyms and most technical
vocabulary, in every language this ships. So a translation carries a **glossary**: terms the
model is told to leave exactly as they are, checked afterwards rather than hoped for, with one
retry naming whatever went missing. The result is a single line in mixed scripts, which is
what a real Burmese or Thai subtitle looks like.

Subtitle timings are deliberately *not* recomputed for the target language's reading speed.
A subtitle is anchored to speech: it appears when the thing was said. If the translation is
too long to read in that window, the fix is a shorter translation — which is why the prompt
asks for one — not a caption that outlives the sentence it belongs to.
"""

from __future__ import annotations

import asyncio
import re
from typing import Protocol, runtime_checkable

import structlog
from pydantic import BaseModel, Field

from lumina.config import get_settings
from lumina.execution.transcribe import Segment
from lumina.intelligence.languages import get_pack

log = structlog.get_logger(__name__)


def _name(code: str) -> str:
    """A language's own name, for a message a person reads. Falls back to the code for one
    with no pack, which only happens in an error path that is already going wrong."""
    try:
        return get_pack(code).endonym
    except Exception:
        return code


@runtime_checkable
class Translator(Protocol):
    """What every engine must offer. No vendor concepts above this line."""

    name: str

    async def translate(
        self, lines: list[str], *, source: str, target: str, keep: tuple[str, ...] = ()
    ) -> list[str]: ...


class TranslationUnavailableError(RuntimeError):
    """No engine is configured.

    Its own type so the API can turn it into an explanation of what an operator must set,
    rather than a 500. Returning the source text unchanged would be worse than failing: the
    creator asked for Burmese captions and would get English ones, correctly timed and
    labelled Burmese.
    """


class _Lines(BaseModel):
    """Structured output. The count is the contract."""

    lines: list[str] = Field(description="One translation per input line, in the same order.")


class NoTranslator:
    """What runs when no key is set."""

    name = "none"

    async def translate(
        self, lines: list[str], *, source: str, target: str, keep: tuple[str, ...] = ()
    ) -> list[str]:
        raise TranslationUnavailableError(
            f"translating {_name(source)} to {_name(target)} "
            "needs a translation engine, and none is set up on this server. Ask whoever "
            "runs it to set GEMINI_API_KEY (preferred) or ANTHROPIC_API_KEY."
        )


#: Lines per request.
#:
#: A subtitle track is not a script — a long video carries thousands of lines, and sending
#: them as one call fails three ways at once: the reply outgrows the model's output limit and
#: truncates, one malformed line loses the whole track, and the retry bills for all of it
#: again. Eighty lines is about three thousand source characters, which leaves the output
#: comfortably inside every engine's cap and keeps the blast radius of a bad batch small.
BATCH = 80

#: Batches in flight at once. Enough to keep a long track from crawling, low enough not to
#: trip per-minute rate limits on a free-tier key.
LANES = 4


class _Checked:
    """The guarantees, applied to whatever an engine returns.

    Deliberately not part of any one engine. Both of the ones below are strong models and
    both break these rules — a dropped glossary term and a half-translated line are failures
    of the task, not of a vendor — so the checking lives once, above the engine, and every
    engine inherits it by implementing `_ask` alone.
    """

    name = "unset"

    async def _ask(
        self,
        lines: list[str],
        *,
        source: str,
        target: str,
        keep: tuple[str, ...],
        insist: list[str] | None,
        whole: bool = False,
    ) -> list[str]:
        raise NotImplementedError

    async def translate(
        self, lines: list[str], *, source: str, target: str, keep: tuple[str, ...] = ()
    ) -> list[str]:
        """Translate a whole track: deduplicated, batched, and checked batch by batch."""
        if not lines:
            return []

        # The same line twice gets translated once. Subtitle tracks repeat heavily — speaker
        # labels, sound effects, catchphrases, the channel's own sign-off — and because each
        # line is translated without its neighbours anyway, two identical lines have no reason
        # to come back different. Consistency and a smaller bill from the same change.
        unique = list(dict.fromkeys(lines))
        batches = [unique[i : i + BATCH] for i in range(0, len(unique), BATCH)]

        gate = asyncio.Semaphore(LANES)

        async def run(batch: list[str]) -> list[str]:
            async with gate:
                return await self._batch(batch, source=source, target=target, keep=keep)

        done = await asyncio.gather(*(run(b) for b in batches))
        table = dict(zip(unique, [line for batch in done for line in batch], strict=True))
        log.info(
            "translate.track",
            engine=self.name,
            lines=len(lines),
            unique=len(unique),
            batches=len(batches),
        )
        return [table[line] for line in lines]

    async def _batch(
        self, lines: list[str], *, source: str, target: str, keep: tuple[str, ...]
    ) -> list[str]:
        out = await self._ask(lines, source=source, target=target, keep=keep, insist=None)

        # Checked, not hoped for. A dropped term is the failure the glossary exists to stop,
        # and it is invisible to anyone who cannot read the target — so the lines that lost
        # one are re-asked with the term named, rather than shipped and left to be noticed.
        missing = {
            i: dropped
            for i, (src, got) in enumerate(zip(lines, out, strict=True))
            if (dropped := [t for t in keep if _holds(src, t) and not _holds(got, t)])
        }
        # The other way a line comes back wrong: not translated at all. Same retry, because
        # the remedy is the same — ask again, naming what went wrong.
        untranslated = [
            i for i, got in enumerate(out) if _leaked(got, source=source, target=target)
        ]
        if untranslated:
            log.warning("translate.source_leaked", lines=len(untranslated), target=target)
            for i in untranslated:
                missing.setdefault(i, [])
        if missing:
            log.info("translate.retry", lines=len(missing), target=target)
            redo = await self._ask(
                [lines[i] for i in missing],
                source=source,
                target=target,
                keep=keep,
                insist=sorted({t for terms in missing.values() for t in terms}),
                whole=bool(untranslated),
            )
            for (i, _), line in zip(missing.items(), redo, strict=True):
                out[i] = line
            # A line that leaks twice is not going to stop. Loud, because the caption will
            # ship half in the wrong script and no log below this one would say why.
            if stuck := [i for i in untranslated if _leaked(out[i], source=source, target=target)]:
                log.warning("translate.still_leaked", target=target, lines=len(stuck))
            still = {
                i: t
                for i, terms in missing.items()
                for t in terms
                if _holds(lines[i], t) and not _holds(out[i], t)
            }
            if still:
                # Not fatal: the rest of the translation is good, and a human reading the
                # target can fix one line. Loud in the log so it is not invisible.
                log.warning("translate.term_lost", target=target, terms=sorted(set(still.values())))
        return out


class ClaudeTranslator(_Checked):
    """Anthropic. Strong on Burmese (87.5 on BURMESE-SAN) and the engine the planner already
    uses, so a deployment with only an Anthropic key still translates."""

    name = "claude"

    async def _ask(
        self,
        lines: list[str],
        *,
        source: str,
        target: str,
        keep: tuple[str, ...],
        insist: list[str] | None,
        whole: bool = False,
    ) -> list[str]:
        from anthropic import AsyncAnthropic

        settings = get_settings()
        pack = get_pack(target)
        client = AsyncAnthropic(api_key=settings.anthropic_api_key)

        response = await client.messages.parse(
            model=settings.planner_model,
            max_tokens=8_000,
            thinking={"type": "adaptive"},
            system=[
                {
                    "type": "text",
                    "text": _SYSTEM,
                    # Byte-identical for every request, so it reads at cache prices. The
                    # language-specific half goes below the breakpoint, where it belongs.
                    "cache_control": {"type": "ephemeral"},
                },
                {"type": "text", "text": pack.llm_profile.system_suffix},
            ],
            messages=[
                {
                    "role": "user",
                    "content": _ask_text(
                        lines,
                        source=source,
                        target=target,
                        keep=keep,
                        insist=insist,
                        whole=whole,
                    ),
                }
            ],
            output_format=_Lines,
        )
        out = response.parsed_output
        if out is None:
            raise TranslationUnavailableError(
                "the translator declined that text. If it is a transcript of something "
                "sensitive, translating it here may not be possible."
            )
        log.info("translate", engine=self.name, source=source, target=target, lines=len(lines))
        return _checked_lines(out.lines, len(lines), source=source, target=target)


class GeminiTranslator(_Checked):
    """Google, via an AI Studio key.

    The default, because it is the best-measured engine for the language this product is
    hardest on. On BURMESE-SAN — built by Burmese native speakers, scored with MetricX-24 —
    Gemini Flash reaches 89.5 on Burmese translation against 87.5 for both Claude Opus 4.1
    and GPT-5, and Gemini Pro 90.2. That ordering does not match the general-purpose
    leaderboards, which is the point: for a low-resource language you have to look at that
    language.

    It also bills per token instead of per character, which makes it about a fourteenth of
    the price of Cloud Translation on subtitle-sized text. Both facts are secondary to the
    first — at these volumes the whole spend is dollars per thousand videos — but there is no
    reason to pay more for the worse option.
    """

    name = "gemini"

    async def _ask(
        self,
        lines: list[str],
        *,
        source: str,
        target: str,
        keep: tuple[str, ...],
        insist: list[str] | None,
        whole: bool = False,
    ) -> list[str]:
        from google import genai
        from google.genai import types

        settings = get_settings()
        pack = get_pack(target)
        client = genai.Client(api_key=settings.gemini_api_key)

        response = await client.aio.models.generate_content(
            model=settings.gemini_model,
            contents=_ask_text(
                lines, source=source, target=target, keep=keep, insist=insist, whole=whole
            ),
            config=types.GenerateContentConfig(
                system_instruction=[_SYSTEM, pack.llm_profile.system_suffix],
                response_mime_type="application/json",
                response_schema=_Lines,
                # Translation is not a task that benefits from sampling: there is a right
                # answer and a set of worse ones. It also makes a bench repeatable.
                temperature=0.0,
                # No tools are in play, and leaving the automatic loop on makes the SDK warn
                # about it on every single call — which at eighty lines a batch is a lot of
                # noise in the worker logs for a feature this never uses.
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            ),
        )
        out = response.parsed
        if not isinstance(out, _Lines):
            # A blocked response parses to nothing. Say which, because "translation failed"
            # sends an operator looking at the network and a safety block is not that.
            raise TranslationUnavailableError(
                "the translator returned nothing for that text. If it is a transcript of "
                "something sensitive, translating it here may not be possible."
            )
        log.info(
            "translate",
            engine=self.name,
            # Which version actually answered — the model setting is an alias by default, so
            # without this a quality change between deploys would have no record anywhere.
            model=getattr(response, "model_version", settings.gemini_model),
            source=source,
            target=target,
            lines=len(lines),
        )
        return _checked_lines(out.lines, len(lines), source=source, target=target)


def _ask_text(
    lines: list[str],
    *,
    source: str,
    target: str,
    keep: tuple[str, ...],
    insist: list[str] | None,
    whole: bool,
) -> str:
    """The request, identical whichever engine receives it — so a bench comparing two
    engines is comparing the engines and not two differently-worded prompts."""
    return (
        f"Translate from {_name(source)} ({source}) to {_name(target)} ({target}).\n"
        f"Return exactly {len(lines)} lines, in order.\n"
        + _glossary(keep, insist)
        + (
            f"\nThe previous attempt left part of the {_name(source)} untranslated. Every "
            f"word must be in {_name(target)}, except the terms listed above.\n"
            if whole
            else ""
        )
        + "\n"
        + "\n".join(f"{i + 1}. {line}" for i, line in enumerate(lines))
    )


def _checked_lines(got: list[str], wanted: int, *, source: str, target: str) -> list[str]:
    """One line out per line in, or a clear failure.

    Padding a short answer would be worse than raising: a caption track one line short puts
    every later subtitle against the wrong moment in the video, which is far harder to spot
    than an error message.
    """
    if len(got) != wanted:
        raise TranslationUnavailableError(
            f"the translator returned {len(got)} lines for {wanted}. "
            "Try again, or shorten the source."
        )
    return [line.strip() for line in got]


_SYSTEM = """You translate subtitle lines for short-form video.

Rules, in order of importance:
1. Return exactly one translation per input line, in the same order. Never merge two lines
   into one, never split one into two. Each line's timing is fixed to the video.
2. Output only the translation. No commentary, no notes, no transliteration, no brackets,
   no quotation marks that were not in the source.
3. Keep each line short enough to read in about the time the original takes to say. Prefer a
   shorter natural phrasing over a literal one that will not fit.
4. Preserve names, numbers and units. Do not localise currencies or measurements.
5. If a line is a sound effect, a speaker label, or otherwise not speech, translate it the way
   subtitlers do rather than describing it.
6. Match the register of the source. Casual speech stays casual."""


#: Word-boundary matching for terms written in a script that has word boundaries. Burmese and
#: Thai have none, so a substring check is the only option there — and the terms that matter in
#: those languages are Latin loanwords anyway, which do have boundaries.
def _holds(text: str, term: str) -> bool:
    """Whether `term` survives in `text`, ignoring case."""
    if not term:
        return False
    lowered, needle = text.lower(), term.lower()
    if term.isascii():
        return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", lowered) is not None
    return needle in lowered


#: Never counted as untranslated, whatever the pair. See `_leaked`.
LATIN = ((0x0041, 0x024F),)


def _leaked(text: str, *, source: str, target: str) -> bool:
    """Whether `text` is still substantially in the source's script rather than the target's.

    A model translating into a low-resource language sometimes gives up part way and copies
    the source through. It is not a garbled output — it is fluent, correctly punctuated, and
    completely undetectable to anyone on the team who does not read the target. Measured on
    six parallel lines, SeamlessM4T into Burmese leaves 22% of Japanese and 12% of Korean
    untranslated; English and Chinese come through clean. So the same model is fine for one
    source language and unusable for another, and only a check per line can tell you which.

    Latin is never counted as leakage: a kept glossary term, a product name or a number is
    supposed to survive, and every script here admits them.
    """
    try:
        src, tgt = get_pack(source).typography, get_pack(target).typography
    except Exception:
        return False

    def inside(ch: str, ranges: tuple[tuple[int, int], ...]) -> bool:
        return any(lo <= ord(ch) <= hi for lo, hi in ranges)

    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    # Latin is exempt no matter what the source language is. When translating *from* English
    # this is the whole ballgame: "Computer" and "Install" surviving into a Burmese line is
    # the glossary working exactly as intended, and counting it as leakage would make the
    # check fire hardest on the output it should like best.
    foreign = sum(
        1
        for c in letters
        if not inside(c, LATIN) and inside(c, src.script) and not inside(c, tgt.script)
    )
    # One stray character is a proper noun or a quoted term; a tenth of the line is a failure
    # to translate. The threshold is deliberately forgiving — a false alarm costs one retry.
    return foreign / len(letters) > 0.10


def _glossary(keep: tuple[str, ...], insist: list[str] | None) -> str:
    """The do-not-translate list, as prompt text."""
    if not keep:
        return ""
    terms = ", ".join(f'"{t}"' for t in keep)
    block = (
        "\nLeave these exactly as written, in their original script, wherever they appear. "
        "Do not translate, transliterate, inflect or decline them; build the sentence around "
        f"them:\n{terms}\n"
    )
    if insist:
        named = ", ".join(f'"{t}"' for t in insist)
        block += (
            f"\nThe previous attempt dropped {named}. Those must appear verbatim in the "
            "output this time.\n"
        )
    return block


_CONDENSE = """You tighten subtitle lines that are too long to read in the time they are on screen.

You are given lines already in the target language, each with a character budget. Rewrite each
one to fit its budget.

Rules, in order of importance:
1. Return exactly one rewrite per input line, in the same order. Never merge or split lines.
2. Stay in the same language as the input. This is not a translation — do not switch scripts.
3. Meaning first, brevity second. Drop hedges, fillers, repeated subjects and anything the
   picture already shows. Never drop a fact, a name, a number or a negation.
4. It must still read as something a person would say. A telegram is not an improvement.
5. If a line already fits, return it unchanged rather than rewriting it for its own sake.
6. Budgets are a target, not a hard cap. Going slightly over beats mangling the sentence."""


async def condense(lines: list[str], budgets: list[int], *, language: str) -> list[str]:
    """Shorten lines that cannot be read in the time they have.

    The counterpart to `execution.timing.fit`, and needed because that function runs out of
    room. Widening a cue can only use silence that exists, and a densely narrated video has
    almost none: on a real 3:16 video, 35 of 36 Burmese lines were too fast and widening
    recovered 3. The remaining problem is not the timing, it is that the text is twice as long
    as the window — which only rewriting can fix.

    This is the part of the job a model is genuinely better at than an algorithm. Truncating
    to the budget is trivial and produces nonsense; deciding *which words carry the meaning*
    is the whole task. Hence a rewrite with an explicit per-line budget, in the language the
    line is already in.

    Returns the input unchanged when no engine is configured, so a caller can offer this
    without checking first.
    """
    if not lines:
        return []

    engine = translator()
    if isinstance(engine, NoTranslator):
        return list(lines)

    settings = get_settings()
    if not settings.gemini_api_key:
        #: Only wired for the engine this deployment runs on. Claude would take another
        #: `_ask`-shaped method on the engine; there is no point adding one unused.
        return list(lines)

    from google import genai
    from google.genai import types

    pack = get_pack(language)
    client = genai.Client(api_key=settings.gemini_api_key)
    asked = "\n".join(
        f"{i + 1}. [{budget} chars] {line}"
        for i, (line, budget) in enumerate(zip(lines, budgets, strict=True))
    )

    response = await client.aio.models.generate_content(
        model=settings.gemini_model,
        contents=f"Tighten these {len(lines)} lines to their budgets.\n\n{asked}",
        config=types.GenerateContentConfig(
            system_instruction=[_CONDENSE, pack.llm_profile.system_suffix],
            response_mime_type="application/json",
            response_schema=_Lines,
            temperature=0.0,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        ),
    )
    out = response.parsed
    if not isinstance(out, _Lines) or len(out.lines) != len(lines):
        #: The original is a worse subtitle than a shorter one, and a better one than a
        #: mangled or misaligned rewrite. A failed tighten changes nothing.
        log.warning("condense.unusable", language=language, asked=len(lines))
        return list(lines)

    #: A rewrite that left the target language is not a shortening, it is a different bug —
    #: and it would be burnt into the video. Each line is kept only if it is still in the
    #: language it started in and actually shorter.
    kept = [
        new if (len(new) < len(old) and _still_written_in(new, old, language)) else old
        for old, new in zip(lines, out.lines, strict=True)
    ]
    log.info(
        "condense",
        language=language,
        lines=len(lines),
        shortened=sum(1 for a, b in zip(lines, kept, strict=True) if a != b),
    )
    return kept


_EXPAND = """You extend a short narration script so it fills the time the creator asked for.

You are given the script and how many more seconds of speech are needed.

Rules, in order of importance:
1. Add substance, never length for its own sake. A new example, a consequence, a detail, the
   next turn in the story. Never restate what is already there in more words.
2. Keep the creator's voice, register and vocabulary. This is their script; you are writing
   the parts they did not get to, not improving the parts they did.
3. Never change a line that is already there. Return the original lines untouched, with your
   additions placed where they belong in the flow.
4. Stay in the same language as the input.
5. Each line must be one spoken sentence. No headings, no stage directions, no speaker names.
6. Getting close to the target beats hitting it exactly. Padding to reach a number is the
   failure this whole task exists to avoid."""


async def expand(
    lines: list[str], *, language: str, needed_seconds: int
) -> tuple[list[str], set[int]]:
    """Fill a script out toward a target length, and say which lines are new.

    Returns the whole script and the indices that were added, because the creator has to be
    able to see and cut them. An expansion the creator cannot distinguish from their own
    writing is how a tool quietly changes what someone meant to say.

    The instinct this serves is real — length targets drive monetisation — but "make it
    longer" is also how faceless channels produce filler. Hence a prompt that asks for another
    example rather than more words, and a return value built for review rather than trust.
    """
    if not lines or needed_seconds <= 0:
        return list(lines), set()

    settings = get_settings()
    if not settings.gemini_api_key:
        return list(lines), set()

    from google import genai
    from google.genai import types

    pack = get_pack(language)
    joined = "\n".join(f"{i + 1}. {line}" for i, line in enumerate(lines))
    response = await genai.Client(api_key=settings.gemini_api_key).aio.models.generate_content(
        model=settings.gemini_model,
        contents=(f"This script runs about {needed_seconds} seconds short. Extend it.\n\n{joined}"),
        config=types.GenerateContentConfig(
            system_instruction=[_EXPAND, pack.llm_profile.system_suffix],
            response_mime_type="application/json",
            response_schema=_Lines,
            temperature=0.4,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        ),
    )

    out = response.parsed
    if not isinstance(out, _Lines) or len(out.lines) < len(lines):
        #: A shorter answer than the input means it rewrote rather than extended, which is the
        #: one thing rule 3 forbids. The creator's script is returned untouched.
        log.warning("expand.unusable", language=language, asked=len(lines))
        return list(lines), set()

    original = list(lines)
    added: set[int] = set()
    for i, line in enumerate(out.lines):
        if original and line.strip() == original[0].strip():
            original.pop(0)
            continue
        added.add(i)

    #: Every original line has to still be in there, in order. If any went missing the model
    #: edited the creator's words, and the whole result is discarded rather than part-trusted.
    if original:
        log.warning("expand.dropped_lines", language=language, missing=len(original))
        return list(lines), set()

    log.info("expand", language=language, was=len(lines), now=len(out.lines), added=len(added))
    return list(out.lines), added


def _still_written_in(new: str, old: str, language: str) -> bool:
    """Whether a tightened line is still in the language it started in.

    The failure this catches is a model that "shortens" a Burmese line by answering in
    English. That is not caught by `_leaked`, which exempts Latin on purpose — a kept product
    name or a number is not leakage — so the test here is the opposite one: the line had
    characters in this script before, and it must still have them after.

    Proportional rather than absolute, because a line that was half Latin to begin with (a
    product name, a URL) should not be held to the same count as one that was not.
    """
    ranges = get_pack(language).typography.script

    def native(text: str) -> int:
        return sum(any(lo <= ord(ch) <= hi for lo, hi in ranges) for ch in text)

    had = native(old)
    if not had:
        return True
    return native(new) >= had * 0.4


def translator() -> Translator:
    """The engine for this deployment. One place decides; nothing else names an engine.

    Gemini first on measured quality for Burmese, not on preference — see `GeminiTranslator`.
    Claude is a capable second and needs no extra key where the planner is already running.
    """
    settings = get_settings()
    if settings.gemini_api_key:
        return GeminiTranslator()
    if settings.anthropic_api_key:
        return ClaudeTranslator()
    return NoTranslator()


async def translate_segments(
    segments: list[Segment], *, source: str, target: str, keep: list[str] | None = None
) -> list[Segment]:
    """Translate timed text, keeping every timing exactly as it was.

    A no-op when the languages match, which is the common case — most projects are not
    translations, and the caller should not have to check.
    """
    if source == target or not segments:
        return segments

    said = [s.text for s in segments]
    done = await translator().translate(said, source=source, target=target, keep=tuple(keep or ()))
    return [
        Segment(text=text, start_ms=s.start_ms, duration_ms=s.duration_ms)
        for s, text in zip(segments, done, strict=True)
    ]
