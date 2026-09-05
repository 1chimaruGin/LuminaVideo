"""English language pack.

Pack #1. Written to be unremarkable — the point of shipping it first is that the *interface*
gets exercised, not that English is interesting. Every simplification taken here that would
not hold for another script is marked NOT-UNIVERSAL, so pack #2 knows where to look.
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
_WS = re.compile(r"\s+")


class EnglishPack:
    code = "en"
    endonym = "English"
    english_name = "English"

    typography = Typography(
        font_stack=("IBM Plex Sans", "system-ui", "sans-serif"),
        webfonts=("ibm-plex-sans-latin-400.woff2", "ibm-plex-sans-latin-600.woff2"),
        cps=17.0,
        line_height=1.4,
        letter_spacing_em=0.0,
        has_case=True,
        script=((0x0041, 0x024F),),
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
        decode={"language": "en", "google": "en-US"},
        engines=("whisper", "gemini", "scribe", "google", "vertex", "seamless"),
    )

    tts = TTSConfig(
        models={"gemini": "gemini-3.1-flash-tts-preview"},
        alternates=("gemini-2.5-pro-preview-tts",),
        sample="Here is how this voice sounds reading a line from your video.",
        speakable=True,
    )

    llm_profile = PromptProfile(
        system_suffix=(
            "Write in plain, spoken English. Short sentences. No jargon unless the source "
            "uses it. Each scene's script line must be speakable in its allotted duration."
        ),
        hook_patterns=(
            "Open with the surprising consequence, not the setup.",
            "Ask the question the viewer already has.",
            "State the number that makes the topic matter.",
        ),
    )

    def normalize(self, text: str) -> str:
        # NOT-UNIVERSAL: English needs only whitespace folding. Burmese needs Zawgyi
        # detection and conversion here; Arabic needs presentation-form folding.
        return _WS.sub(" ", text).strip()

    def tokenize(self, text: str) -> list[Unit]:
        # NOT-UNIVERSAL: whitespace-delimited words. Burmese, Thai, Lao, Khmer, Japanese
        # and Chinese have no inter-word spaces and need a real segmenter.
        return [Unit(text=m.group(), start=m.start(), end=m.end()) for m in _WORD.finditer(text)]

    def linebreak(self, text: str, width: int) -> list[str]:
        units = self.tokenize(text)
        if not units:
            return []
        lines: list[str] = []
        current: list[str] = []
        length = 0
        for u in units:
            # NOT-UNIVERSAL: len() as display width. Wrong for CJK (double-width) and for
            # any script with combining marks, where codepoints != columns.
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
        # Real implementation: CTC forced alignment (torchaudio MMS_FA) against the known
        # script. Until the render pool exists, fall back to proportional timing.
        raise NotImplementedError("forced alignment lands with the cpu-render pool")

    def caption_duration_ms(self, text: str) -> int:
        chars = len(self.normalize(text))
        return max(800, round(chars / self.typography.cps * 1000))
