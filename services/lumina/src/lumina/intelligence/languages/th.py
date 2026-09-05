"""Thai language pack.

Pack #3. Shares Burmese's two hardest properties — no inter-word spaces and no case — and adds
one of its own: **the leading vowels**. เ แ โ ใ ไ are written *before* the consonant they are
pronounced after, so a naive left-to-right split puts them in the wrong cluster and the
highlight lands on a fragment no reader recognises. `tokenize` handles that explicitly.

The unit here is the Thai Character Cluster: a base character plus the marks that hang off it
— the above/below vowels, the tone marks, the phinthu — which is the smallest run that can be
highlighted without breaking a glyph apart. It is not a word: Thai word segmentation needs a
dictionary and a model, and no rule-based approximation of it is worth shipping as though it
were one. For captions the cluster is the right granularity anyway.
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

#: Vowels written to the left of their consonant: เ แ โ ใ ไ (U+0E40 to U+0E44).
_LEADING = "เ-ไ"
#: Marks that hang above or below the base and never stand alone: the upper and lower vowels,
#: phinthu, the tone marks, thanthakhat and friends.
_ABOVE_BELOW = "ัิ-ฺ็-๎"
#: Vowels written *after* the consonant on the baseline: ะ า ำ ๅ. They are full-width
#: characters rather than marks, so they need naming separately — without them `เรา` splits
#: into `เร` and `า`, which is a fragment no reader recognises.
_TRAILING = "ะาำๅ"

#: One cluster: an optional leading vowel, a base character, whatever hangs off it, and any
#: baseline vowel that follows. Spaces are matched too — Thai uses them at phrase boundaries,
#: and they are the best available line break — but `tokenize` drops them.
_CLUSTER = re.compile(
    rf"[{_LEADING}]?[^\s{_ABOVE_BELOW}{_TRAILING}][{_ABOVE_BELOW}]*[{_TRAILING}]?"
    rf"|[{_TRAILING}]|\s+"
)

_WS = re.compile(r"\s+")


class ThaiPack:
    code = "th"
    endonym = "ไทย"
    english_name = "Thai"

    typography = Typography(
        font_stack=("Noto Sans Thai", "Leelawadee UI", "Tahoma", "sans-serif"),
        webfonts=("noto-sans-thai-400.woff2", "noto-sans-thai-600.woff2"),
        # Faster per character than Burmese — Thai clusters are shorter — but still well below
        # English, whose characters carry less each.
        sentence_enders=(".", "!", "?", "…"),
        cps=13.0,
        # Two levels of mark above the base (vowel then tone) need the room.
        line_height=1.65,
        letter_spacing_em=0.0,
        has_case=False,
        space_between_units=False,
        bundled=True,
        script=((0x0E00, 0x0E7F),),
    )

    #: Thai is better served than Burmese but still well behind the majors on Whisper, so a
    #: dedicated engine leads and Whisper backs it up rather than the other way round.
    asr = ASRConfig(
        models={
            "gemini": "gemini-3.5-transcribe",
            "scribe": "scribe_v1",
            "seamless": "seamless-m4t-v2-large",
            "google": "chirp_2",
            "vertex": "gemini-2.5-flash",
            "whisper": "whisper-large-v3-turbo",
        },
        decode={"language": "th", "google": "th-TH"},
        engines=("scribe", "google", "whisper", "gemini", "vertex", "seamless"),
    )

    tts = TTSConfig(
        models={"gemini": "gemini-3.1-flash-tts-preview"},
        alternates=("gemini-2.5-pro-preview-tts",),
        sample="เสียงนี้อ่านประโยคจากวิดีโอของคุณออกมาแบบนี้",
        speakable=True,
    )

    llm_profile = PromptProfile(
        system_suffix=(
            "Write in natural spoken Thai (ภาษาไทย). Keep sentences short — a caption line is "
            "on screen for about two seconds. Use spaces only where Thai uses them, at phrase "
            "boundaries, never between every word."
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
        """Split into Thai character clusters, keeping leading vowels with their consonant."""
        units: list[Unit] = []
        for m in _CLUSTER.finditer(text):
            piece = m.group()
            if piece.strip():
                units.append(Unit(text=piece, start=m.start(), end=m.end()))
        return units

    def linebreak(self, text: str, width: int) -> list[str]:
        """Break at cluster boundaries, preferring a real space when one is near.

        Thai does use spaces — at phrase boundaries, roughly where English uses a comma — so
        breaking at one is always correct when it is available. Falling back to a cluster
        boundary is what a browser does without a dictionary: not ideal, but never mid-glyph.

        Works from the pieces rather than from `tokenize`, because `tokenize` drops the spaces
        and those are the good break points.
        """
        text = self.normalize(text)
        if not text:
            return []

        lines: list[str] = []
        current = ""
        for m in _CLUSTER.finditer(text):
            piece = m.group()
            if current and len(current) + len(piece) > width:
                cut = current.rstrip()
                # A phrase space inside the last stretch is a better break than the cluster
                # boundary we happen to have reached.
                at = cut.rfind(" ")
                if at > width // 2:
                    lines.append(cut[:at].strip())
                    current = (cut[at:] + piece).lstrip()
                else:
                    lines.append(cut)
                    current = piece.lstrip()
            else:
                current += piece
        if current.strip():
            lines.append(current.strip())
        return lines

    def align(self, audio_path: str, text: str) -> list[WordTiming]:
        raise NotImplementedError("forced alignment lands with the cpu-render pool")

    def caption_duration_ms(self, text: str) -> int:
        chars = len(self.normalize(text))
        return max(900, round(chars / self.typography.cps * 1000))
