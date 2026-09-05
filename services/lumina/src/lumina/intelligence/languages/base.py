"""The language pack boundary.

INVARIANT 1: no language branching outside this package. If you find `if lang == "..."` in a
worker, planner, or renderer, the abstraction has leaked and review should fail.

Notes that will bite later if ignored:
  - `tokenize` returns UNITS, not words. Granularity differs enormously by script.
  - `typography.cps` is per-language. Reading speed varies by ~3x. Never hardcode a caption
    duration anywhere else in the codebase.
  - Line breaking needs an override hook; ICU alone will not give good caption aesthetics
    for every script.
  - Some fonts are 5-20MB. Load lazily, per pack, never bundled.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Unit:
    """One highlightable caption unit. A word in English, a syllable cluster in Burmese,
    a character in Chinese. The caption animator highlights units, never characters."""

    text: str
    start: int  # index into the normalized source string
    end: int


@dataclass(frozen=True, slots=True)
class WordTiming:
    unit: Unit
    start_ms: int
    end_ms: int


@dataclass(frozen=True, slots=True)
class Typography:
    #: CSS font stack, most specific first. Must include a script-appropriate Noto fallback.
    font_stack: tuple[str, ...]
    #: Web font files to lazy-load when this pack activates. Never bundled into the app.
    webfonts: tuple[str, ...]
    #: Characters per second a reader of this language comfortably sustains. Drives caption
    #: duration. Varies by ~3x across languages — this is why it lives here.
    cps: float
    #: What ends a sentence in this script.
    #:
    #: Here because it is not universal and getting it wrong is invisible in a language you do
    #: not read: Burmese ends sentences with ။ and uses ၊ as a comma, and a splitter that only
    #: knows "." treats a whole Burmese paragraph as one sentence. Used to break a pasted
    #: script into lines someone can say — see `split_sentences`.
    sentence_enders: tuple[str, ...] = (".", "!", "?", "…")
    #: Complex scripts with stacked marks need more room and no tight tracking.
    line_height: float = 1.4
    letter_spacing_em: float = 0.0
    #: Scripts with no uppercase must never be rendered through text-transform.
    has_case: bool = True
    #: The Unicode ranges this language is written in.
    #:
    #: Here so that a translation can be *checked* rather than trusted. A model translating
    #: into a low-resource language sometimes stops part way and emits the source verbatim —
    #: SeamlessM4T returns Japanese-into-Burmese output that is 22% raw kana — and the result
    #: is fluent-looking, correctly timed, and completely wrong to anyone who reads the target.
    #: Nobody on the team who does not read Burmese would catch it. A codepoint range does.
    #:
    #: Latin is excluded deliberately: a kept technical term, a product name or a number is
    #: not leakage, and every script here admits them.
    script: tuple[tuple[int, int], ...] = ((0x0041, 0x024F),)
    #: Whether the first family must actually be shipped. True for scripts with no acceptable
    #: fallback: a box with no Myanmar face does not fail, it draws identical empty rectangles.
    #: `execution.compose.fonts` reads this to decide what `make fonts` fetches and what it
    #: refuses to render without — so a new pack declares its own dependency here rather than
    #: needing a matching entry added somewhere else.
    bundled: bool = False
    #: Whether rendered units are separated by a space. False for the scripts that write
    #: continuously — Burmese, Thai, Lao, Khmer, Japanese, Chinese. The caption renderer
    #: highlights one unit at a time, and joining those units with spaces in a script that has
    #: none inserts word breaks the language does not have. This is the difference between a
    #: caption and a misspelling, so it lives here rather than in the renderer.
    space_between_units: bool = True


@dataclass(frozen=True, slots=True)
class ASRConfig:
    #: Which model each engine should use for this language, keyed by engine name.
    #:
    #: Per engine rather than one `model_id`, because a language that ranks several engines
    #: has several answers and they are not interchangeable: Burmese wants `scribe_v1` from
    #: Scribe and `chirp_2` from Google — Chirp *3* is newer and does not cover Burmese — and
    #: handing either string to the other engine is nonsense. A single field made that a
    #: latent bug: `LocalWhisper` loaded whatever the preferred engine's model happened to be
    #: and failed with "invalid model size 'scribe_v1'".
    models: dict[str, str]
    #: Passed through to the decoder. Vendor-neutral keys only, except the locale each
    #: vendor wants for this language, which is a fact about the language rather than a knob.
    decode: dict[str, str] = field(default_factory=dict)
    #: Which engine reads this language acceptably, best first.
    #:
    #: A property of the language, not of the deployment, and therefore here rather than in a
    #: lookup table beside the adapters — a `dict[language, engine]` in the execution layer is
    #: precisely the branching invariant 1 exists to stop.
    #:
    #: It matters because the spread is enormous. Whisper is at or near the state of the art
    #: for English, Chinese, Japanese and Korean, and on Burmese the published numbers put it
    #: past 80% word error — worse than useless, because it returns fluent-looking sentences
    #: rather than failing. Burmese therefore names an engine that has actually been measured
    #: on it, and falls back to nothing rather than to a plausible lie.
    engines: tuple[str, ...] = ("whisper",)


@dataclass(frozen=True, slots=True)
class Voice:
    """One voice a creator can pick, described the way a person would describe it.

    `id` is the vendor's name for it. Everything else exists because "Sulafat" tells a
    creator nothing: they are choosing how their video will sound, and the only honest way to
    present that is a word for the character of it plus a button that plays it.
    """

    id: str
    #: A word for how it sounds — "Warm", "Firm". Not a name, not a gender.
    character: str


#: The voices offered when a pack does not name its own.
#:
#: Shared rather than per-language because the engine's voices are not per-language: the same
#: voice reads Burmese, Japanese and English, taking its accent from the text it is given. A
#: pack overrides this only if some voice is genuinely wrong for its language.
DEFAULT_VOICES: tuple[Voice, ...] = (
    Voice("Kore", "Firm"),
    Voice("Puck", "Upbeat"),
    Voice("Charon", "Informative"),
    Voice("Aoede", "Breezy"),
    Voice("Sulafat", "Warm"),
    Voice("Achird", "Friendly"),
)


@dataclass(frozen=True, slots=True)
class TTSConfig:
    """How this language is spoken aloud.

    The sample sentence is the part that has to be here. A voice picker that previews every
    voice reading the same English line is not a preview of anything a Burmese creator is
    about to hear — the accent, the pacing and the stacking of the script are exactly what
    they are trying to judge. So each pack supplies a sentence in its own language, and
    picking a voice plays that.
    """

    #: Which model each engine uses for this language, keyed by engine name.
    models: dict[str, str] = field(default_factory=dict)
    #: Models to fall back to, in order, when the one above will not serve.
    #:
    #: Quota is counted per *model*, so a second model is a second allowance and not merely a
    #: retry — an exhausted primary does not mean an exhausted key. Which alternates are
    #: usable is a fact about the language, which is why the list lives here: measured on
    #: Burmese, `gemini-2.5-pro-preview-tts` speaks it correctly and
    #: `gemini-2.5-flash-preview-tts` returns a refusal with no audio at all.
    alternates: tuple[str, ...] = ()
    #: What the preview says. One short, ordinary sentence in this language.
    sample: str = ""
    voices: tuple[Voice, ...] = DEFAULT_VOICES
    #: Whether any engine here has been confirmed to speak this language. False means the
    #: picker offers nothing rather than offering voices that will read it as gibberish.
    speakable: bool = False


@dataclass(frozen=True, slots=True)
class PromptProfile:
    """Planner prompting for this language. Part of the CACHED prefix of the planner call —
    keep it byte-stable, or every plan pays full input price."""

    system_suffix: str
    hook_patterns: tuple[str, ...]
    examples: tuple[str, ...] = ()


@runtime_checkable
class LanguagePack(Protocol):
    code: str  # BCP-47
    #: What speakers call it, in their own script. A Burmese creator scanning a picker is
    #: looking for "မြန်မာ". Lives on the pack so adding a language is one file.
    endonym: str
    #: The English name, for a creator choosing a language they cannot read.
    english_name: str
    typography: Typography
    asr: ASRConfig
    tts: TTSConfig
    llm_profile: PromptProfile

    def normalize(self, text: str) -> str:
        """Encoding and variant folding. For Burmese this is Zawgyi->Unicode detection and
        conversion; for Arabic, presentation-form folding. Everything downstream assumes
        normalized text."""
        ...

    def tokenize(self, text: str) -> list[Unit]:
        """Split into highlightable units. NOT words."""
        ...

    def linebreak(self, text: str, width: int) -> list[str]:
        """Break into caption lines of at most `width` display columns."""
        ...

    def align(self, audio_path: str, text: str) -> list[WordTiming]:
        """Forced alignment against known text. NOT transcription — the voiceover is TTS,
        so the exact script is already known and re-transcribing it only adds error."""
        ...

    def caption_duration_ms(self, text: str) -> int:
        """Fallback duration when alignment is unavailable. Derived from `typography.cps`."""
        ...


def split_sentences(text: str, pack: LanguagePack) -> list[str]:
    """A written script, broken into lines someone can say.

    Deterministic on purpose. A model asked to split a script will also improve it — reorder a
    clause, tighten a phrase, fix what it thinks is a mistake — and on this lane the words are
    the creator's, not ours. Splitting is arithmetic; rewriting is a decision nobody asked for.

    The creator's own line breaks win. Someone who wrote a script in short lines has already
    said where the beats are, and a splitter that reflows their paragraph is overruling them;
    only runs that are still long get broken again at sentence ends.
    """
    out: list[str] = []
    for block in (b.strip() for b in text.splitlines()):
        if not block:
            continue
        out.extend(_by_sentence(block, pack))
    return out


def _by_sentence(block: str, pack: LanguagePack) -> list[str]:
    """One paragraph, cut after each sentence ender, keeping the ender on its sentence."""
    lines: list[str] = []
    current = ""
    for char in block:
        current += char
        if char in pack.typography.sentence_enders:
            if current.strip():
                lines.append(current.strip())
            current = ""
    if current.strip():
        lines.append(current.strip())
    return lines
