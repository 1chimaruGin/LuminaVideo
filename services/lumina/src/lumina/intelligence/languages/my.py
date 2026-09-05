"""Burmese language pack.

Pack #2, and the reason the boundary exists. Every NOT-UNIVERSAL note in `en.py` is a real
problem here:

  - **No spaces between words.** Burmese is written continuously; the space, where it appears
    at all, is a phrase break closer to a comma. So the highlightable unit is the *syllable*,
    found by rule rather than by splitting on whitespace, and units are re-joined with nothing
    between them. Joining them with spaces would render text no Burmese reader would accept.
  - **No case.** Nothing may be put through `text-transform`, and no layout may lean on an
    uppercase eyebrow.
  - **Stacked marks.** Medials, the subscript consonant (္), tone marks and the asat (်) stack
    above and below the base. The line box has to be taller than a Latin one or the marks are
    clipped, and tracking must stay at zero or the stack visibly detaches.
  - **Two encodings in the wild.** Zawgyi is not Unicode but occupies the same code points, so
    a Zawgyi string renders as plausible-looking nonsense in a Unicode font. `normalize` is
    where that has to be caught.

Syllable segmentation follows the well-established `sylbreak` rule (Ye Kyaw Thu): a break goes
before any consonant that is not itself subscripted and is not carrying a stacking mark. It is
a syllable breaker, not a word segmenter — Burmese word segmentation needs a dictionary and a
model. For captions that is the right granularity anyway: the highlight should move a syllable
at a time, which is roughly how the language is spoken.
"""

from __future__ import annotations

import re
from itertools import pairwise

from lumina.intelligence.languages.base import (
    ASRConfig,
    PromptProfile,
    TTSConfig,
    Typography,
    Unit,
    WordTiming,
)

#: The Burmese block's base consonants, က (U+1000) through အ (U+1021).
_CONSONANT = "က-အ"
#: Subscript marker (္). The consonant after it is stacked beneath, not a new syllable.
_STACK = "္"
#: Asat (်). Kills the inherent vowel; the consonant it follows is not a new syllable either.
_ASAT = "်"
#: Independent vowels, digits, and the section marks — each stands alone.
_STANDALONE = "ဣ-ဧဩဪဿ၀-၉၊-၏"

#: A break goes *before* each of these: a base consonant not preceded by the stack marker and
#: not followed by one, or any standalone character, or a run of non-Burmese text.
_BREAK = re.compile(
    rf"(?<!{_STACK})[{_CONSONANT}](?![{_STACK}{_ASAT}])|[{_STANDALONE}]|[A-Za-z0-9]+"
)

_WS = re.compile(r"\s+")

#: Code points Zawgyi uses that Unicode Burmese never does. Zawgyi stores the medials and the
#: stacked forms as separate glyphs in the range reserved for extensions, so their presence is
#: near-conclusive. Detection only: converting requires a full transliteration table, and
#: guessing wrong corrupts text that was fine.
_ZAWGYI_ONLY = frozenset("ၠၡၢၣၤၥၦၧၨၩၪ")


class ZawgyiTextError(ValueError):
    """The text is Zawgyi, not Unicode.

    Refused rather than rendered: Zawgyi and Unicode share code points, so this text will draw
    as fluent-looking nonsense in any Unicode font — the kind of failure that survives review
    by anyone who does not read Burmese, which is the worst kind to ship.
    """


class BurmesePack:
    code = "my"
    endonym = "မြန်မာ"
    english_name = "Burmese"

    typography = Typography(
        # Padauk and Myanmar Text are the two faces most likely to be already installed;
        # Noto is what the render pool actually ships.
        font_stack=("Noto Sans Myanmar", "Padauk", "Myanmar Text", "sans-serif"),
        webfonts=("noto-sans-myanmar-400.woff2", "noto-sans-myanmar-600.woff2"),
        # Slower than English per *character*: a Burmese character carries more of a syllable,
        # and the stacked forms take longer to resolve visually. A caption timed at English
        # speed is gone before it has been read.
        sentence_enders=("။", "!", "?"),
        cps=9.0,
        # Marks stack both above and below the base. At English line-height they collide with
        # the line above.
        line_height=1.75,
        # Any tracking at all visibly detaches the stacked marks from their base.
        letter_spacing_em=0.0,
        has_case=False,
        space_between_units=False,
        bundled=True,
        script=((0x1000, 0x109F), (0xA9E0, 0xA9FF), (0xAA60, 0xAA7F)),
    )

    #: Two candidates, and no Whisper.
    #:
    #: Whisper is excluded outright: its Burmese error rate is bad enough that it does not
    #: fail, it returns confident, fluent, wrong Burmese — the worst outcome for a subtitle
    #: nobody in the room can check.
    #:
    #: Which of the other two is better is an open question and deliberately not settled here
    #: by reading vendor pages. Published figures are not comparable: Burmese has no word
    #: boundaries, so Whisper and FLEURS both report *character* error rate for it while the
    #: academic Burmese literature reports word error rate after a segmentation step of its
    #: own — numbers that look like they belong on the same axis and do not. Google's Chirp 2
    #: is the only major cloud model that lists Burmese and descends from USM, which was built
    #: for the long tail; Scribe claims the better number on FLEURS. `scripts/asr_bench.py`
    #: settles it on real audio, which is the only thing that can.
    asr = ASRConfig(
        models={
            "gemini": "gemini-3.5-transcribe",
            "scribe": "scribe_v1",
            "seamless": "seamless-m4t-v2-large",
            "google": "chirp_2",
            "vertex": "gemini-2.5-flash",
        },
        decode={"language": "my", "google": "my-MM"},
        engines=("scribe", "gemini", "google", "vertex", "seamless"),
    )

    tts = TTSConfig(
        models={"gemini": "gemini-3.1-flash-tts-preview"},
        alternates=("gemini-2.5-pro-preview-tts",),
        sample="ဒီအသံက သင့်ဗီဒီယိုထဲက စာကြောင်းတစ်ကြောင်းကို ဒီလိုဖတ်ပါတယ်။",
        speakable=True,
    )

    llm_profile = PromptProfile(
        system_suffix=(
            "Write in natural spoken Burmese (မြန်မာ). Use Unicode, never Zawgyi. Keep "
            "sentences short — a caption line is on screen for about two seconds. Do not "
            "transliterate English words that have ordinary Burmese equivalents."
        ),
        hook_patterns=(
            "Open with the consequence, then explain how it happened.",
            "Ask the question the viewer already has.",
            "Name the number that makes it matter.",
        ),
    )

    def normalize(self, text: str) -> str:
        """Fold whitespace, and refuse Zawgyi rather than mangling it."""
        if _ZAWGYI_ONLY & set(text):
            raise ZawgyiTextError(
                "that text is encoded in Zawgyi rather than Unicode. Converting it "
                "automatically risks corrupting it — please re-save it as Unicode Burmese."
            )
        return _WS.sub(" ", text).strip()

    def tokenize(self, text: str) -> list[Unit]:
        """Split into syllables — the unit the caption highlight moves by."""
        units: list[Unit] = []
        starts = [m.start() for m in _BREAK.finditer(text)]
        if not starts:
            stripped = text.strip()
            return [Unit(text=stripped, start=0, end=len(text))] if stripped else []

        # Anything before the first break — a leading mark, or stray punctuation — belongs to
        # the first syllable rather than being dropped.
        bounds = [0, *(s for s in starts if s > 0), len(text)]
        for a, b in pairwise(bounds):
            piece = text[a:b]
            if piece.strip():
                units.append(Unit(text=piece, start=a, end=b))
        return units

    def linebreak(self, text: str, width: int) -> list[str]:
        """Break at syllable boundaries.

        Proper Burmese line breaking is at word boundaries and needs a dictionary. Breaking at
        syllables is what every Burmese-capable browser does without one, and for a two-line
        caption it is not visibly wrong.
        """
        units = self.tokenize(text)
        if not units:
            return []
        lines: list[str] = []
        current = ""
        for u in units:
            if current and len(current) + len(u.text) > width:
                lines.append(current.strip())
                current = u.text
            else:
                current += u.text
        if current.strip():
            lines.append(current.strip())
        return lines

    def align(self, audio_path: str, text: str) -> list[WordTiming]:
        raise NotImplementedError("forced alignment lands with the cpu-render pool")

    def caption_duration_ms(self, text: str) -> int:
        chars = len(self.normalize(text))
        return max(900, round(chars / self.typography.cps * 1000))
