"""Reading pace: whether a caption can be read in the time it is given.

The bug these exist for is invisible in review. The words are right, the cues are in sync with
the speech, and the video looks finished — but a translation into a slower-reading script needs
more time than the line it replaced, so the caption is gone before it can be read. Only someone
who reads the target language notices, and by then it is published.
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from lumina.execution import timing


def _cue(id: str, text: str, start: int, dur: int) -> timing.Cue:
    return timing.Cue(id=id, text=text, start_ms=start, duration_ms=dur)


def test_the_same_line_needs_more_time_in_a_slower_script() -> None:
    """The whole reason this module exists, stated as a test.

    Reading speed is a property of the script, and it varies about threefold. A threshold
    picked for English silently declares every Burmese and Chinese caption acceptable.
    """
    english = timing.needed_ms("a" * 100, "en")
    burmese = timing.needed_ms("က" * 100, "my")
    chinese = timing.needed_ms("字" * 100, "zh")

    assert burmese > english, "Burmese reads slower than English and needs longer"
    assert chinese > burmese, "Chinese is the slowest of the three"


def test_a_cue_grows_into_the_silence_after_it_before_the_silence_before_it() -> None:
    """A caption that starts before its line is spoken is out of sync in the way viewers
    actually notice — the words appear under the wrong shot. Starting late is survivable, so
    the pause after a cue is spent first."""
    cues = [_cue("a", "က" * 60, 10_000, 1_000), _cue("b", "x", 30_000, 1_000)]

    out = timing.fit(cues, "my")

    assert out[0].start_ms == 10_000, "it reached backwards while there was room in front"
    assert out[0].duration_ms > 1_000, "it did not grow at all"


def test_fitting_never_makes_two_captions_overlap() -> None:
    """Burnt-in captions cannot overlap: the second is drawn on top of the first.

    This is the failure the first version of `fit` had. Each cue was bounded by its *original*
    neighbours, so one that had already grown forwards was then reached back into by the cue
    after it — on a real 36-line video that produced 8 overlapping pairs.
    """
    #: Long lines in tight windows with almost no silence: every cue wants more room than
    #: exists, which is what forces them into each other.
    cues = [_cue(str(i), "က" * 80, i * 2_000, 1_800) for i in range(12)]

    out = timing.fit(cues, "my")

    for earlier, later in pairwise(out):
        assert earlier.end_ms <= later.start_ms, f"{earlier.id} runs into {later.id}"


def test_fitting_is_stable_once_there_is_nothing_left_to_give() -> None:
    """Running it twice must not keep moving things. A creator who presses the button again
    should get the same video, not a slowly drifting one."""
    cues = [_cue(str(i), "က" * 40, i * 3_000, 1_500) for i in range(6)]

    once = timing.fit(cues, "my")
    twice = timing.fit(once, "my")

    assert [(c.start_ms, c.duration_ms) for c in once] == [
        (c.start_ms, c.duration_ms) for c in twice
    ]


def test_a_comfortable_cue_is_left_exactly_alone() -> None:
    """Widening a caption that was already readable moves it away from the speech for no gain."""
    cues = [_cue("a", "hi", 5_000, 4_000), _cue("b", "there", 20_000, 4_000)]

    out = timing.fit(cues, "en")

    assert [(c.start_ms, c.duration_ms) for c in out] == [(5_000, 4_000), (20_000, 4_000)]


@pytest.mark.parametrize("language", ["en", "my", "ja", "zh", "ko", "th"])
def test_every_language_has_a_reading_speed_that_produces_a_sane_window(language: str) -> None:
    """A pack with a missing or nonsensical `cps` would make every cue on that language look
    either fine or impossible, and nothing in between."""
    short = timing.needed_ms("x", language)
    long = timing.needed_ms("x" * 200, language)

    assert short == timing.FLOOR_MS, "a two-character line still needs a beat to be noticed"
    assert 5_000 < long < 60_000, f"{language}: 200 characters needs {long}ms, which is absurd"


def test_a_gap_too_short_to_read_as_a_gap_is_closed() -> None:
    """A caption that disappears for a fifth of a second between two sentences reads as a
    glitch in the render, not as a pause — and the time is free, because it is already
    silence."""
    cues = [_cue("a", "hi", 0, 2_000), _cue("b", "there", 2_200, 2_000)]

    out = timing.fit(cues, "en")

    assert out[0].end_ms == 2_200 - timing.GAP_MS, "the blink was left in"
    assert out[1].start_ms == 2_200, "the later caption moved off its speech"


def test_a_real_pause_between_captions_is_left_alone() -> None:
    """Silence the speaker actually took is part of the video. Filling it would leave the
    last line hanging on screen over a shot it has nothing to do with."""
    cues = [_cue("a", "hi", 0, 2_000), _cue("b", "there", 12_000, 2_000)]

    out = timing.fit(cues, "en")

    assert out[0].duration_ms == 2_000, "a five-second pause was swallowed"


def test_closing_gaps_never_creates_an_overlap() -> None:
    """The stitching pass runs after fitting and could undo its one hard guarantee."""
    cues = [_cue(str(i), "က" * 30, i * 1_500, 1_200) for i in range(15)]

    out = timing.fit(cues, "my")

    for earlier, later in pairwise(out):
        assert earlier.end_ms <= later.start_ms, f"{earlier.id} runs into {later.id}"
        assert later.start_ms - earlier.end_ms >= timing.GAP_MS or (
            later.start_ms == earlier.end_ms
        ), "captions left touching"
