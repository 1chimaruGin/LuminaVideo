"""Korean language pack.

The odd one out among the East Asian packs, and the reason `space_between_units` is a property
rather than an assumption about CJK: **Korean puts spaces between words.** 어절 (eojeol) are
space-delimited, so whitespace segmentation — useless for Japanese and Chinese — is right here,
and units are re-joined with a space like English.

What it does *not* share with English:

  - **No case.** Nothing may go through `text-transform`, and no layout may lean on an
    uppercase eyebrow.
  - **Syllable blocks.** Each 한글 syllable is one composed character occupying a full square,
    so a line of Korean is far wider per character than a line of Latin at the same point size,
    and a caption width tuned for English overflows.
  - **Line breaking is at spaces, not anywhere.** Unlike Chinese and Japanese, Korean must not
    break mid-word — so the naive per-character break a CJK renderer might apply is wrong.
"""

from __future__ import annotations

import re

from lumina.intelligence.languages.base import (
    ASRConfig,
    PromptProfile,
    TTSConfig,
    Typography,
    Unit,
    WordTiming,
)

_WORD = re.compile(r"\S+")
_WS = re.compile(r"[ \t　]+")


class KoreanPack:
    code = "ko"
    endonym = "한국어"
    english_name = "Korean"

    typography = Typography(
        font_stack=("Noto Sans KR", "Apple SD Gothic Neo", "Malgun Gothic", "sans-serif"),
        webfonts=("noto-sans-kr-400.woff2", "noto-sans-kr-700.woff2"),
        # Between Latin and the continuous scripts: a syllable block carries more than a Latin
        # character and less than a hanzi.
        sentence_enders=(".", "!", "?", "…"),
        cps=11.0,
        line_height=1.55,
        letter_spacing_em=0.0,
        has_case=False,
        # The one East Asian script in this set that is written with spaces.
        space_between_units=True,
        bundled=True,
        script=((0xAC00, 0xD7AF), (0x1100, 0x11FF), (0x3130, 0x318F)),
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
        decode={"language": "ko", "google": "ko-KR"},
        engines=("whisper", "gemini", "scribe", "google", "vertex", "seamless"),
    )

    tts = TTSConfig(
        models={"gemini": "gemini-3.1-flash-tts-preview"},
        alternates=("gemini-2.5-pro-preview-tts",),
        sample="이 목소리로 당신의 영상 대사는 이렇게 들립니다.",
        speakable=True,
    )

    llm_profile = PromptProfile(
        system_suffix=(
            "Write in natural spoken Korean (한국어). Keep sentences short — a caption line is "
            "on screen for about two seconds. Use 해요체 unless the source is clearly formal. "
            "Keep the spacing rules (띄어쓰기) correct."
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
        """Split on whitespace — right here, and wrong for every other pack in this package."""
        return [Unit(text=m.group(), start=m.start(), end=m.end()) for m in _WORD.finditer(text)]

    def linebreak(self, text: str, width: int) -> list[str]:
        """Break at spaces only. Korean must not break mid-word."""
        units = self.tokenize(self.normalize(text))
        if not units:
            return []
        lines: list[str] = []
        current: list[str] = []
        length = 0
        for u in units:
            add = len(u.text) + (1 if current else 0)
            if current and length + add > width:
                lines.append(" ".join(current))
                current, length = [u.text], len(u.text)
            else:
                current.append(u.text)
                length += add
        if current:
            lines.append(" ".join(current))
        return lines

    def align(self, audio_path: str, text: str) -> list[WordTiming]:
        raise NotImplementedError("forced alignment lands with the cpu-render pool")

    def caption_duration_ms(self, text: str) -> int:
        chars = len(self.normalize(text))
        return max(900, round(chars / self.typography.cps * 1000))
