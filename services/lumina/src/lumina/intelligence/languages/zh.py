"""Chinese language pack (Simplified).

The densest of the six. One hanzi routinely carries a whole morpheme, so a caption of twelve
characters can be a full sentence — which is why the reading speed here is the lowest in the
set even though the *lines* are the shortest.

The unit is the character. Chinese words are one to three hanzi and telling which needs a
dictionary; unlike Japanese there is no script alternation to lean on, because it is all one
script. Per-character highlighting is therefore the honest granularity: it is what the text
actually offers without a segmenter, and for a caption it tracks speech acceptably because
characters and syllables correspond one to one — which is not true in any other language here.

Latin runs and numbers stay whole, because splitting "2026" into four units would be wrong in
any script.

Simplified rather than "Chinese": Traditional needs its own pack — a different face
(Noto Sans TC), different characters, and a different endonym. `zh` here means `zh-Hans`, and
the day Traditional is added this code should narrow to match.
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

#: One hanzi, or a run of Latin/digits, or a single punctuation mark.
_UNIT = re.compile(r"[A-Za-z0-9]+(?:[.,][A-Za-z0-9]+)*|\s+|[^\s]")

_WS = re.compile(r"[ \t　]+")


class ChinesePack:
    code = "zh"
    endonym = "中文"
    english_name = "Chinese (Simplified)"

    typography = Typography(
        font_stack=("Noto Sans SC", "PingFang SC", "Microsoft YaHei", "sans-serif"),
        webfonts=("noto-sans-sc-400.woff2", "noto-sans-sc-700.woff2"),
        # The lowest in the set, and correctly so: each character is a morpheme, and a reader
        # spends longer per character than in any alphabetic script.
        sentence_enders=("。", "！", "？", "…"),
        cps=7.5,
        line_height=1.6,
        letter_spacing_em=0.0,
        has_case=False,
        space_between_units=False,
        bundled=True,
        script=((0x4E00, 0x9FFF), (0x3400, 0x4DBF), (0xF900, 0xFAFF)),
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
        decode={"language": "zh", "google": "cmn-Hans-CN"},
        engines=("whisper", "gemini", "scribe", "google", "vertex", "seamless"),
    )

    tts = TTSConfig(
        models={"gemini": "gemini-3.1-flash-tts-preview"},
        alternates=("gemini-2.5-pro-preview-tts",),
        sample="这个声音朗读你视频里的一句话，听起来是这样的。",
        speakable=True,
    )

    llm_profile = PromptProfile(
        system_suffix=(
            "Write in natural spoken Simplified Chinese (简体中文). Keep sentences short — a "
            "caption line is on screen for about two seconds. Do not insert spaces between "
            "words. Use 。，！？ rather than ASCII punctuation."
        ),
        hook_patterns=(
            "Open with the consequence, then explain how it happened.",
            "Ask the question the viewer already has.",
            "Name the number that makes it matter.",
        ),
    )

    def normalize(self, text: str) -> str:
        return _WS.sub(" ", text).strip()

    def tokenize(self, text: str) -> list[Unit]:
        return [
            Unit(text=m.group(), start=m.start(), end=m.end())
            for m in _UNIT.finditer(text)
            if m.group().strip()
        ]

    def linebreak(self, text: str, width: int) -> list[str]:
        """Break between characters, never leaving a 。 or a closing bracket to open a line."""
        return cjk.wrap([u.text for u in self.tokenize(self.normalize(text))], width)

    def align(self, audio_path: str, text: str) -> list[WordTiming]:
        raise NotImplementedError("forced alignment lands with the cpu-render pool")

    def caption_duration_ms(self, text: str) -> int:
        chars = len(self.normalize(text))
        return max(900, round(chars / self.typography.cps * 1000))
