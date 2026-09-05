"""Where the speech is in a waveform.

Some engines return an alignment; some return only a sentence. SeamlessM4T is the second kind
— sequence-to-sequence, no word times, no way to ask for them — so a caption's timing has to
come from somewhere else, and the only honest source is where someone is actually speaking.

The audio is split on silence and each stretch of speech keeps the times it occupies. The
alternative would be handing back one caption for the whole file, or inventing offsets by
counting characters, and both are worse than measuring.

On numpy rather than torch on purpose: this is the load-bearing part of that engine, and
keeping it out of the model's environment means it is tested by the ordinary suite instead of
skipped everywhere torch is not installed.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SAMPLE_RATE = 16_000
#: Silence longer than this ends a stretch. Shorter gaps are pauses inside a sentence —
#: breaking on those gives captions that flicker a word at a time.
SILENCE_MS = 420
#: A ceiling regardless, because a speaker who never pauses would otherwise be handed to the
#: model as one enormous sequence: quality falls off, and the caption would be unreadable.
MAX_MS = 12_000
#: Below this a stretch is a cough or a door, not speech worth a caption.
MIN_MS = 240
#: A 20 ms analysis window, which is the usual granularity for this and divides evenly.
WINDOW_MS = 20
#: About -46 dBFS. Below room tone on any usable recording, and under even a quiet capture.
FLOOR = 0.005


@dataclass(frozen=True, slots=True)
class Stretch:
    start_ms: int
    end_ms: int


def find_speech(wave: np.ndarray) -> list[Stretch]:
    """Stretches of speech in a mono 16 kHz signal, by short-window energy.

    Deliberately not a neural VAD: this runs beside a 2.3B model, so the marginal accuracy of
    one would be lost in the noise while its weights and dependency would not be.

    The threshold is relative to the file's own loudness, so a quiet recording works without
    a hand-set level — but relative *alone* is not enough. On a file with no speech in it both
    quantiles are room tone a hair apart, every frame clears a threshold set between them, and
    the whole file comes back as one stretch of "speech". So there is an absolute floor as
    well.

    The floor is loudness, not contrast. Contrast cannot separate the two cases: a file that
    is *entirely* speech also has its quantiles close together, and rejecting on a narrow
    spread threw away exactly the continuous delivery that most needs captioning.
    """
    window = int(SAMPLE_RATE * WINDOW_MS / 1000)
    usable = (len(wave) // window) * window
    if usable == 0:
        return []

    frames = wave[:usable].reshape(-1, window)
    energy = np.sqrt(np.mean(np.square(frames.astype(np.float64)), axis=1))

    quiet = float(np.quantile(energy, 0.10))
    loud = float(np.quantile(energy, 0.95))
    if loud < FLOOR:
        return []

    threshold = quiet + (loud - quiet) * 0.12
    speaking = energy > threshold
    gap = SILENCE_MS // WINDOW_MS

    out: list[Stretch] = []
    start: int | None = None
    silent = 0

    def keep(a: int, b: int) -> None:
        if (b - a) * WINDOW_MS >= MIN_MS:
            out.append(Stretch(a * WINDOW_MS, b * WINDOW_MS))

    for i, live in enumerate(speaking):
        if live:
            if start is None:
                start = i
            silent = 0
        elif start is not None:
            silent += 1
            if silent >= gap or (i - start) * WINDOW_MS >= MAX_MS:
                keep(start, i - silent + 1)
                start, silent = None, 0

    if start is not None:
        keep(start, len(speaking))
    return out


def split_points(wave: np.ndarray, *, max_ms: int) -> list[int]:
    """Sample offsets at which to cut a long recording into pieces no longer than `max_ms`.

    Hosted engines take a file, not a stream, and they cap its size. Groq's is 25 MB, which
    at the mono 16 kHz PCM every ASR model wants is about thirteen minutes — and it is not
    refused politely: a larger file comes back as a 502, which reaches the creator as "that
    did not work" and tells nobody anything.

    So a long file is cut. Where it is cut matters: slicing at a round number lands mid-word
    about as often as not, and the two halves of that word are then two wrong transcriptions
    rather than one right one. Each boundary is therefore nudged to the quietest 20 ms in the
    half-minute before it, which on real speech is a pause between sentences.
    """
    if wave.size == 0:
        return []
    window = SAMPLE_RATE * WINDOW_MS // 1000
    step = int(max_ms / 1000 * SAMPLE_RATE)
    #: How far back to look for a pause. Long enough to reach one in ordinary speech, short
    #: enough that the chunk stays comfortably under the limit.
    search = 30 * SAMPLE_RATE

    cuts: list[int] = []
    at = step
    while at < wave.size:
        lo = max(cuts[-1] + window if cuts else 0, at - search)
        if lo >= at:
            cuts.append(at)
        else:
            frames = wave[lo:at]
            usable = (frames.size // window) * window
            if usable == 0:
                cuts.append(at)
            else:
                loudness = np.abs(frames[:usable].reshape(-1, window)).mean(axis=1)
                cuts.append(lo + int(np.argmin(loudness)) * window)
        at = cuts[-1] + step
    return cuts
