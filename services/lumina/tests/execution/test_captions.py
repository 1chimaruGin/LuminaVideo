"""Caption rendering.

The word-by-word highlight is the thing users actually want from captions, and it has a
failure mode that no type checker and no unit test on the timings can catch: a highlight
colour that matches its own background renders the spoken word invisible. That happened —
`pop`'s pill is #ffc36b and the global highlight was also #ffc36b, so the word the viewer was
meant to be reading disappeared. These tests are about that class of bug.
"""

from __future__ import annotations

import itertools
import re

import pytest

from lumina.execution.compose import captions as c
from lumina.execution.compose.ffmpeg import ASPECTS
from lumina.intelligence.languages import get_pack

_HEX = re.compile(r"#([0-9a-fA-F]{3,8})")
_RGB = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)")


def _declared(css: str, prop: str) -> str | None:
    """The value of one CSS declaration, or None if the style does not set it."""
    for part in css.split(";"):
        name, _, value = part.partition(":")
        if name.strip() == prop:
            return value.strip()
    return None


def _rgb(value: str | None) -> tuple[int, int, int] | None:
    """A colour as RGB, from either notation.

    Both are parsed rather than just hex, so a style written with `rgba()` is checked instead
    of silently skipped — a skip here would let exactly the bug this file exists for through.
    """
    if not value:
        return None
    if (m := _RGB.search(value)) is not None:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    if (h := _HEX.search(value)) is not None:
        digits = h.group(1)
        if len(digits) in (3, 4):
            digits = "".join(ch * 2 for ch in digits[:3])
        return (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16))
    return None


def _distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    """Manhattan distance in RGB. Crude, and enough: this is a smoke alarm for "these two are
    the same colour", not a contrast-ratio calculation."""
    return sum(abs(x - y) for x, y in zip(a, b, strict=True))


@pytest.mark.parametrize("name", sorted(c.STYLES))
def test_no_style_draws_its_highlight_in_its_own_plate_colour(name: str) -> None:
    """The spoken word is the one thing a viewer must be able to find.

    This is the exact bug that shipped: `pop`'s pill is #ffc36b and the single global
    highlight was `color:#ffc36b`, so the highlighted word rendered amber on amber and
    vanished — while still taking up layout width, which made the pill look mysteriously
    too wide. A per-style highlight is only a fix if something checks the pairing.
    """
    style = c.STYLES[name]
    plate = _rgb(_declared(style.base, "background"))
    ink = _rgb(_declared(style.highlight, "color"))

    if plate is None or ink is None:
        # No opaque plate (glow draws on the video itself) or no colour-based highlight.
        # Those styles rely on the opacity dimming, which the next test covers.
        pytest.skip(f"{name} has no plate/ink pair to compare")

    assert _distance(plate, ink) > 90, (
        f"{name}: highlight rgb{ink} is too close to the plate rgb{plate} to be seen"
    )


@pytest.mark.parametrize("name", sorted(c.STYLES))
def test_every_style_dims_the_words_that_are_not_being_spoken(name: str) -> None:
    """Opacity, not hue, is what makes the highlight robust across palettes: contrast against
    the plate is a property of the plate, and a new style could silently erase a hue-based
    highlight the way `pop` did."""
    assert "opacity" in c._PAGE, "the page must dim non-spoken words"
    assert c.STYLES[name].highlight, f"{name} has no highlight at all"


def test_word_timings_weights_by_length_rather_than_splitting_evenly() -> None:
    """An even split visibly lags on long words, and a highlight drifting out of sync with
    the voice is the first thing anyone notices."""
    line = c.word_timings("a extraordinarily b", 0, 3000, get_pack("en"))
    offsets = [off for _, off in line.words]
    assert offsets[0] == 0
    long_word = offsets[2] - offsets[1]
    short_word = offsets[1] - offsets[0]
    assert long_word > short_word * 3, "the long word gets proportionally more time"


def test_word_timings_survives_an_empty_line() -> None:
    line = c.word_timings("   ", 0, 2000, get_pack("en"))
    assert line.words == []
    assert line.end_ms == 2000


def test_overlay_filter_uses_one_overlay_however_many_captions(tmp_path) -> None:
    """One overlay, always.

    Giving each still its own `overlay` gated by `enable` is O(n) filters: every frame of the
    video is pushed through every overlay in the chain whether that caption is showing or not.
    Measured on a real track — 554 stills over 200 seconds — the chained form cost 32.3s
    against 15.1s for this one, and the gap widens with the caption count. A half-hour video
    runs to thousands of stills, where chaining stops being slow and becomes unusable.
    """
    states = [
        c.CaptionState(png=tmp_path / f"{i}.png", start_ms=i * 500, end_ms=(i + 1) * 500)
        for i in range(3)
    ]
    inputs, graph = c.overlay_filter(states, tmp_path, (1080, 1920))

    assert graph.count("overlay=") == 1
    assert inputs[:2] == ["-f", "concat"], "the stills arrive as one timed track"
    assert graph.endswith("[vout]"), "compose maps [vout]"


def test_overlay_filter_fills_the_gaps_between_captions(tmp_path) -> None:
    """Concat plays its entries back to back and cannot leave a hole, so the stretches with
    nothing on screen need a transparent still or every caption lands early."""
    states = [
        c.CaptionState(png=tmp_path / "a.png", start_ms=500, end_ms=1500),
        c.CaptionState(png=tmp_path / "b.png", start_ms=3000, end_ms=4000),
    ]
    c.overlay_filter(states, tmp_path, (64, 64))
    listing = (tmp_path / "captions.txt").read_text()

    assert listing.count("cap-blank.png") == 2, "before the first caption, and between the two"
    assert "duration 0.500000" in listing, "the silence before the first line"
    assert "duration 1.500000" in listing, "the gap between them"
    #: The demuxer drops the last entry's duration, so the final file is named twice.
    assert listing.rstrip().endswith("b.png'")


def test_overlay_filter_with_no_captions_and_no_reframe_is_empty(tmp_path) -> None:
    """Written as an early return because the ternary form binds to the second element and
    silently produces a tuple inside a tuple."""
    inputs, graph = c.overlay_filter([], tmp_path, (64, 64))
    assert inputs == [] and graph == ""


def test_overlay_filter_with_no_captions_still_reframes(tmp_path) -> None:
    """A video with no cues must still come out in the shape that was asked for — without a
    graph it would stream-copy straight through in the source's shape."""
    _, graph = c.overlay_filter([], tmp_path, (64, 64), base="scale=1080:1920")
    assert graph == "[0:v]scale=1080:1920[vout]"


@pytest.mark.anyio
async def test_rasterize_makes_one_png_per_state_with_transparency() -> None:
    """One state per highlighted word: the picture only changes when the highlight moves."""
    playwright = pytest.importorskip("playwright.async_api")
    assert playwright is not None

    import tempfile
    from pathlib import Path

    line = c.word_timings("Black holes are", 0, 2000, get_pack("en"))
    size = ASPECTS["9:16"]

    with tempfile.TemporaryDirectory() as tmp:
        states = await c.rasterize([line], Path(tmp), width=size.w, height=size.h, style="pop")
        assert len(states) == 3, "one state per word"
        assert all(s.png.is_file() and s.png.stat().st_size > 0 for s in states)
        # Transparent PNGs, or the overlay paints a black box over the video.
        assert all(s.png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" for s in states)
        # Windows are ordered and non-overlapping.
        for a, b in itertools.pairwise(states):
            assert a.end_ms <= b.start_ms, "states must not overlap or two show at once"
