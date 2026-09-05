"""Whether a caption can actually be read in the time it is on screen, and widening it if not.

Every cue in a translated video inherits its window from the *original* speech. That is
correct for the picture — the words have to appear when they are said — and frequently wrong
for the reading: the translation is a different length in a script with a different reading
speed, and the window did not change to match.

The size of the mismatch is not marginal. English sustains about 17 characters per second;
Burmese about 9, Chinese about 7.5. So the same sentence, translated faithfully, can need
twice the time it was given, and the creator sees a caption that flashes past before it can
be read. Nothing about it looks broken — the words are right, the timing is "in sync" — which
is why it survives review and reaches the audience.

No model is involved and none should be. How long a line takes to read is a property of the
script and its length, both of which are known exactly; asking an LLM would be slower, cost
money, and give a different answer each time for the same input. What the algorithm cannot
invent is *time* — a cue can only grow into silence around it, never over its neighbours — so
this widens what it can and reports honestly on what it cannot.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from lumina.intelligence.languages import get_pack

#: Never leave two cues touching. A caption that swaps at the exact frame the next begins
#: reads as a flicker rather than as two lines.
GAP_MS = 80

#: No caption is comfortable below this, however short the text. Two words still need a beat
#: to be noticed, and a 200ms flash is a subliminal frame rather than a subtitle.
FLOOR_MS = 700

#: A gap smaller than this between two captions is closed rather than left.
#:
#: Below about a third of a second the caption does not read as having ended — it reads as
#: having flickered, and the eye is pulled to the blink instead of the words. The time is also
#: the cheapest there is to give away: it is already silence, and the line before it is
#: usually the one that needed more room.
STITCH_MS = 340

#: Reading time is measured against this much of the shortfall before a line is called tight.
#: A cue 5% short is not what anyone is complaining about, and flagging it buries the ones
#: that are genuinely unreadable.
TOLERANCE = 1.08


@dataclass(frozen=True, slots=True)
class Cue:
    """One caption, and the window it currently occupies."""

    id: str
    text: str
    start_ms: int
    duration_ms: int

    @property
    def end_ms(self) -> int:
        return self.start_ms + self.duration_ms


@dataclass(frozen=True, slots=True)
class Verdict:
    """What one cue needs, against what it has."""

    cue: Cue
    needed_ms: int

    @property
    def tight(self) -> bool:
        return self.cue.duration_ms * TOLERANCE < self.needed_ms

    @property
    def short_by_ms(self) -> int:
        return max(0, self.needed_ms - self.cue.duration_ms)


def needed_ms(text: str, language: str) -> int:
    """How long this line needs to be on screen to be read.

    From the pack's `cps`, which is the only place reading speed is allowed to live — it is a
    property of the script, not of the renderer or the screen asking the question.
    """
    pack = get_pack(language)
    units = len(pack.normalize(text).strip())
    return max(FLOOR_MS, round(units / pack.typography.cps * 1000))


def check(cues: list[Cue], language: str) -> list[Verdict]:
    """Every cue, with the time it needs. Read-only — nothing is changed."""
    return [Verdict(cue=cue, needed_ms=needed_ms(cue.text, language)) for cue in cues]


def fit(cues: list[Cue], language: str) -> list[Cue]:
    """Give each cue the time it needs, as far as the silence around it allows.

    Growing forward first is deliberate. A caption may start late and still be read; a caption
    that starts *before* the line is spoken is out of sync in the way viewers actually notice,
    because the words appear under the wrong shot. So a cue takes the pause after it before it
    takes any of the pause before it, and takes neither past a neighbour.

    The two bounds are asymmetric, and that asymmetry is what makes overlaps impossible.
    Backwards, a cue may reach only as far as the previous cue's *final* end — already decided,
    because the list is walked in order. Forwards, it may reach only the next cue's *original*
    start. The next cue may then want to begin earlier than that, but it cannot cross this
    one, because its own backward bound is this cue's final end.

    Bounding backwards against the previous cue's original end instead is the obvious version
    and it is wrong: a cue that had already grown forwards was then reached back into by the
    one after it, and the pair overlapped. On a video with tight lines that produced captions
    stacked two-deep on screen.
    """
    if not cues:
        return []

    ordered = sorted(cues, key=lambda c: c.start_ms)
    wants = [needed_ms(c.text, language) for c in ordered]
    out: list[Cue] = []
    settled_end = 0

    for i, cue in enumerate(ordered):
        want = wants[i]
        floor = settled_end + GAP_MS if i > 0 else 0
        ceiling = ordered[i + 1].start_ms - GAP_MS if i + 1 < len(ordered) else None

        start = max(cue.start_ms, floor)
        end = max(start + cue.duration_ms, cue.end_ms)
        if end - start < want:
            end = end + (want - (end - start))
        if ceiling is not None:
            end = min(end, max(ceiling, start + 1))

        #: Only reach backwards for time the forward growth could not find.
        if end - start < want:
            start = max(floor, start - (want - (end - start)))

        settled_end = end
        out.append(replace(cue, start_ms=start, duration_ms=max(1, end - start)))

    return _stitched(out)


def _stitched(cues: list[Cue]) -> list[Cue]:
    """Close the gaps too small to read as gaps.

    Run after fitting rather than inside it because it is a different question. Fitting asks
    "can this line be read"; this asks "does the track look continuous", and the answer
    matters even where every line already fits: a caption that vanishes for a fifth of a
    second between two sentences reads as a glitch in the render.

    Only ever extends the *earlier* cue, so nothing moves earlier than the speech it belongs
    to, and never past the gap the next line needs to stay separate.
    """
    out = list(cues)
    for i in range(len(out) - 1):
        gap = out[i + 1].start_ms - out[i].end_ms
        if 0 < gap <= STITCH_MS:
            grown = out[i + 1].start_ms - GAP_MS
            if grown > out[i].start_ms:
                out[i] = replace(out[i], duration_ms=grown - out[i].start_ms)
    return out
