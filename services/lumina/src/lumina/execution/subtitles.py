"""Timed text the creator already has.

Transcribing is the expensive, fallible way to find out what was said. When the creator
already has an SRT, a WebVTT track, or even just the script in a text file, reading it is
free, exact, and works on a server with no speech engine at all — which is every server this
product runs on today.

So the order is: use the file if there is one, listen only if there is not. `stages.transcribe`
enforces that; this module is only the reader.

Three shapes arrive in practice, and they are distinguished by content rather than by
extension — a `.txt` holding SRT is common, and a `.srt` holding plain lines is not rare:

  - **SRT** — an index, `00:00:01,000 --> 00:00:04,000`, then the text.
  - **WebVTT** — a `WEBVTT` header, optional cue ids, `.` for the decimal separator, and
    NOTE/STYLE/REGION blocks that carry no captions.
  - **Plain text** — no timings at all. The lines are real, the timing is not, so it is
    derived from the language pack's reading speed and then fitted to the video's true
    duration. Fabricating precise-looking timings from nothing is the one thing worth
    refusing to do quietly, so `timed` on the result says which of these happened.

Both cue formats carry authoring markup — `<i>`, `<c.yellow>`, `{\\an8}` positioning — that
belongs to someone else's renderer. It is stripped: ours draws its own captions, so a literal
`<i>` in a burnt-in caption is a bug the creator cannot fix.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import structlog

from lumina.execution.transcribe import Segment
from lumina.intelligence.languages import get_pack

log = structlog.get_logger(__name__)

#: What the file turned out to be. Reported so the creator is told which of their file's
#: properties was actually used, rather than being left to assume the timings were theirs.
type Format = str

SRT: Format = "srt"
VTT: Format = "vtt"
PLAIN: Format = "plain"


@dataclass(frozen=True, slots=True)
class Read:
    segments: list[Segment]
    format: Format

    @property
    def timed(self) -> bool:
        """Whether the timings came from the file or were derived from reading speed."""
        return self.format in (SRT, VTT)


class UnreadableSubtitlesError(ValueError):
    """The file parsed to nothing usable.

    Its own type because the answer differs from an ASR failure: nothing is misconfigured on
    the server, the file the creator chose has no captions in it.
    """


#: `00:01:02,500`, `01:02.500`, `1:02:03.4` — hours optional, either decimal separator, and
#: a fractional part of one to three digits. Both formats in one pattern on purpose: files
#: mixing the two separators are common enough that being strict rejects working captions.
_STAMP = r"(?:(\d{1,3}):)?(\d{1,2}):(\d{1,2})[.,](\d{1,3})"
_CUE = re.compile(rf"^\s*{_STAMP}\s*-->\s*{_STAMP}", re.MULTILINE)

#: Angle-bracket tags (`<i>`, `<c.yellow>`, `</v Roger>`) and SSA overrides (`{\\an8}`).
_MARKUP = re.compile(r"<[^>]*>|\{\\[^}]*\}")

#: WebVTT blocks that are not captions. A `NOTE` block's prose would otherwise be burnt into
#: the video as though someone had said it.
_NOT_A_CUE = re.compile(r"^(NOTE|STYLE|REGION)\b")


def parse(text: str, *, duration_ms: int, language: str = "en") -> Read:
    """Read whatever the creator gave us into segments.

    `duration_ms` is the video's real, probed length. It is used two ways: to fit untimed text
    across the video, and to drop cues that lie beyond the end — a subtitle file for the
    director's cut applied to the theatrical release would otherwise place captions after the
    picture has stopped.
    """
    body = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    cues = _cues(body, duration_ms)
    if cues:
        kind: Format = VTT if body.lstrip().upper().startswith("WEBVTT") else SRT
        log.info("subtitles.parse", format=kind, cues=len(cues))
        return Read(segments=cues, format=kind)

    lines = _plain(body, duration_ms=duration_ms, language=language)
    if not lines:
        raise UnreadableSubtitlesError(
            "that file has no captions and no text in it — check it is the subtitle track "
            "and not, say, an empty file or a spreadsheet"
        )
    log.info("subtitles.parse", format=PLAIN, cues=len(lines))
    return Read(segments=lines, format=PLAIN)


def _cues(body: str, duration_ms: int) -> list[Segment]:
    """Every timed cue, in order, non-overlapping and inside the video."""
    marks = list(_CUE.finditer(body))
    if not marks:
        return []

    out: list[Segment] = []
    for i, m in enumerate(marks):
        start = _ms(m.group(1), m.group(2), m.group(3), m.group(4))
        end = _ms(m.group(5), m.group(6), m.group(7), m.group(8))
        # Text runs from the end of the timing line to the start of the next cue. Taking it
        # positionally rather than by blank-line splitting keeps multi-paragraph cues whole.
        stop = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        chunk = body[m.end() : stop]
        said = _clean(chunk)
        if not said:
            continue
        # A cue that starts after the picture ends is for a different cut of the video.
        if duration_ms and start >= duration_ms:
            continue
        if duration_ms:
            end = min(end, duration_ms)
        if end <= start:
            # Zero-length and inverted cues both happen in hand-edited files. Give it enough
            # time to be read rather than dropping a line the creator wrote.
            end = start + 1_200
        out.append(Segment(text=said, start_ms=start, duration_ms=end - start))

    out.sort(key=lambda s: s.start_ms)
    return _untangle(out)


def _untangle(segments: list[Segment]) -> list[Segment]:
    """Trim overlaps so no two captions are on screen at once.

    Burnt-in captions cannot overlap the way a soft subtitle track can — the second would be
    drawn on top of the first. The earlier cue yields.
    """
    fixed: list[Segment] = []
    for i, s in enumerate(segments):
        end = s.end_ms
        if i + 1 < len(segments):
            end = min(end, segments[i + 1].start_ms)
        if end <= s.start_ms:
            continue
        fixed.append(Segment(text=s.text, start_ms=s.start_ms, duration_ms=end - s.start_ms))
    return fixed


def _plain(body: str, *, duration_ms: int, language: str) -> list[Segment]:
    """Untimed text, spread across the video at the language's reading speed.

    The proportions come from the language pack rather than from character count: a line of
    Burmese and a line of English of equal length take very different times to read, which is
    exactly what `typography.cps` exists to encode. They are then scaled to fill the real
    duration, so the last caption lands with the last frame instead of halfway through.
    """
    pack = get_pack(language)
    said = [_clean(line) for line in body.split("\n")]
    lines = [s for s in said if s]
    if not lines:
        return []

    wants = [max(pack.caption_duration_ms(line), 400) for line in lines]
    total = sum(wants)
    # With no duration to fit (an audio-only source, or a probe that found none) the reading
    # speed stands on its own.
    scale = (duration_ms / total) if duration_ms and total else 1.0

    out: list[Segment] = []
    at = 0
    for line, want in zip(lines, wants, strict=True):
        span = max(int(want * scale), 300)
        out.append(Segment(text=line, start_ms=at, duration_ms=span))
        at += span
    return out


def _clean(chunk: str) -> str:
    """One cue's text: markup gone, cue ids gone, wrapped onto a single line."""
    kept: list[str] = []
    for raw in chunk.split("\n"):
        line = _MARKUP.sub("", raw).strip()
        if not line or _NOT_A_CUE.match(line):
            continue
        # A bare number on its own line is an SRT index, not something anybody said.
        if line.isdigit():
            continue
        kept.append(line)
    return " ".join(kept).strip()


def _ms(hours: str | None, minutes: str, seconds: str, frac: str) -> int:
    """A timestamp in milliseconds. `frac` is left-aligned: `.5` is 500ms, not 5ms."""
    ms = int(frac.ljust(3, "0")[:3])
    return ((int(hours or 0) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + ms
