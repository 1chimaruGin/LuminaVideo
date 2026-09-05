"""Synthesizing a dub once and reusing it.

Speech is quota'd per *request* and a dub is one request per line, so the same track must
never be built twice. It was: the preview synthesized the whole dub so the creator could hear
it, then the render synthesized it again from the same lines in the same voice. On a key
allowing a hundred requests a day, that is the difference between dubbing two of a
thirty-six line video and dubbing one.
"""

from __future__ import annotations

import pytest

from lumina.execution import dubtrack, speak


def _cues() -> list[speak.Cue]:
    return [speak.Cue("မင်္ဂလာပါ။", 0, 1500), speak.Cue("ကျေးဇူးတင်ပါတယ်။", 2000, 1500)]


def test_the_same_lines_in_the_same_voice_are_the_same_dub() -> None:
    assert dubtrack.fingerprint(_cues(), voice="Kore", language="my") == dubtrack.fingerprint(
        _cues(), voice="Kore", language="my"
    )


@pytest.mark.parametrize(
    "change",
    [
        pytest.param(
            lambda c: [speak.Cue("တခြားစာ", c[0].start_ms, c[0].duration_ms), c[1]], id="text"
        ),
        pytest.param(lambda c: [speak.Cue(c[0].text, 500, c[0].duration_ms), c[1]], id="start"),
        pytest.param(lambda c: [speak.Cue(c[0].text, c[0].start_ms, 9_000), c[1]], id="duration"),
        pytest.param(lambda c: c[:1], id="a line removed"),
    ],
)
def test_anything_that_changes_the_audio_changes_the_fingerprint(change) -> None:
    """Serving a stale track is worse than re-synthesizing one: the video ships saying
    something its captions do not."""
    before = dubtrack.fingerprint(_cues(), voice="Kore", language="my")
    after = dubtrack.fingerprint(change(_cues()), voice="Kore", language="my")

    assert before != after


def test_the_voice_and_the_language_are_part_of_it() -> None:
    base = dubtrack.fingerprint(_cues(), voice="Kore", language="my")

    assert dubtrack.fingerprint(_cues(), voice="Puck", language="my") != base
    assert dubtrack.fingerprint(_cues(), voice="Kore", language="th") != base


def test_two_scripts_cannot_collide_by_moving_a_boundary() -> None:
    """Joining the fields with a separator would let a line containing that separator hash the
    same as a different pair of lines. Subtitles are user text — assuming a character will
    never appear in them is exactly how that assumption gets violated."""
    one = [speak.Cue("a:b", 0, 1000)]
    two = [speak.Cue("a", 0, 1000), speak.Cue("b", 0, 1000)]

    assert dubtrack.fingerprint(one, voice="Kore", language="my") != dubtrack.fingerprint(
        two, voice="Kore", language="my"
    )
