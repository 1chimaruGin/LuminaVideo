"""Japanese language pack.

Written continuously, like Burmese and Thai, but hard in its own way: it mixes three scripts
in one sentence, and the boundaries between them are most of the information about where words
begin. 「東京に行きました」 is kanji, kana particle, kanji, kana inflection — and a reader parses
it largely by watching the script change.

So the unit is found by watching the script change. That is a well-known heuristic and it is
not word segmentation — proper Japanese segmentation needs a dictionary and a model (MeCab,
Sudachi), and no rule approximates it. For a caption highlight it lands close enough to
phrase-shaped that the highlight moves at roughly the pace of speech, which is the job.
Per-character highlighting, the obvious alternative, moves five times too fast to follow.

One refinement that earns its place: **a kanji or katakana run keeps the hiragana that
follows it**. Those kana are the word's inflectional tail and the particles attached to it —
行きました, 東京に — and the result is close to a 文節 (bunsetsu), the phrase unit Japanese
readers actually parse by. Splitting them off puts the highlight on a bare stem, which reads
as a typo; keeping them gives a highlight that moves phrase by phrase, at about the pace of
speech.
"""

from __future__ import annotations

import re

from lumina.intelligence.languages import cjk
from lumina.intelligence.languages.base import (
    ASRConfig,
    PromptProfile,
    TTSConfig,
    Typography,
    Unit,
    WordTiming,
)

_HIRAGANA = r"ぁ-ゟ"
_KATAKANA = r"゠-ヿㇰ-ㇿ"
_KANJI = r"一-鿿㐀-䶿々〆ヵヶ"

#: One phrase-ish unit, or one punctuation mark. Ordered so the longest sensible run wins:
#: a kanji or katakana run takes the hiragana after it — its okurigana and particles — which
#: approximates a 文節.
_RUN = re.compile(
    rf"[{_KANJI}]+[{_HIRAGANA}]*"
    rf"|[{_KATAKANA}ー]+[{_HIRAGANA}]*"
    rf"|[{_HIRAGANA}]+"
    rf"|[A-Za-z0-9]+(?:[.,][A-Za-z0-9]+)*"
    rf"|\s+"
    rf"|[^\s]"
)

_WS = re.compile(r"[ \t　]+")


class JapanesePack:
    code = "ja"
    endonym = "日本語"
    english_name = "Japanese"

    typography = Typography(
        font_stack=("Noto Sans JP", "Hiragino Sans", "Yu Gothic", "Meiryo", "sans-serif"),
        webfonts=("noto-sans-jp-400.woff2", "noto-sans-jp-700.woff2"),
        # Dense per character: one kanji can carry a whole word. Well below English, and
        # measured studies of Japanese subtitle reading put comfortable speed near here.
        sentence_enders=("。", "！", "？", "…"),
        cps=8.5,
        # Furigana is not drawn, but the glyphs themselves are full-height with no descender
        # room to borrow, so lines need separating.
        line_height=1.6,
        # Tracking on CJK reads as broken spacing rather than as style.
        letter_spacing_em=0.0,
        has_case=False,
        space_between_units=False,
        bundled=True,
        script=((0x3040, 0x30FF), (0x4E00, 0x9FFF), (0x31F0, 0x31FF)),
    )

    #: Whisper is at or near the state of the art here and is by far the cheapest way to run
    #: it; Scribe is kept as a fallback for when the audio is hard rather than the language.
    asr = ASRConfig(
        models={
            "gemini": "gemini-3.5-transcribe",
            "whisper": "whisper-large-v3-turbo",
            "scribe": "scribe_v1",
            "seamless": "seamless-m4t-v2-large",
            "google": "chirp_3",
            "vertex": "gemini-2.5-flash",
        },
        decode={"language": "ja", "google": "ja-JP"},
        engines=("whisper", "gemini", "scribe", "google", "vertex", "seamless"),
    )

    tts = TTSConfig(
        models={"gemini": "gemini-3.1-flash-tts-preview"},
        alternates=("gemini-2.5-pro-preview-tts",),
        sample="この声で、あなたの動画のセリフはこう聞こえます。",
        speakable=True,
    )

    llm_profile = PromptProfile(
        system_suffix=(
            "Write in natural spoken Japanese (日本語). Keep sentences short — a caption line "
            "is on screen for about two seconds. Use です・ます form unless the source is "
            "clearly casual. Do not insert spaces between words."
        ),
        hook_patterns=(
            "Open with the consequence, then explain how it happened.",
            "Ask the question the viewer already has.",
            "Name the number that makes it matter.",
        ),
    )

    def normalize(self, text: str) -> str:
        # Ideographic space (U+3000) folded along with the ASCII ones: it is a real character
        # in Japanese input and would otherwise survive into a caption as a visible gap.
        return _WS.sub(" ", text).strip()

    def tokenize(self, text: str) -> list[Unit]:
        return [
            Unit(text=m.group(), start=m.start(), end=m.end())
            for m in _RUN.finditer(text)
            if m.group().strip()
        ]

    def linebreak(self, text: str, width: int) -> list[str]:
        """Break between script runs, never leaving a 。 or a closing bracket to open a line."""
        return cjk.wrap([u.text for u in self.tokenize(self.normalize(text))], width)

    def align(self, audio_path: str, text: str) -> list[WordTiming]:
        raise NotImplementedError("forced alignment lands with the cpu-render pool")

    def caption_duration_ms(self, text: str) -> int:
        chars = len(self.normalize(text))
        return max(900, round(chars / self.typography.cps * 1000))
