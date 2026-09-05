"""Script Studio.

Pure functions with no infrastructure, which is the point: timing and beat splitting run on
every keystroke, so neither may touch a network or a database.
"""

from __future__ import annotations

import pytest

from lumina.intelligence import script as studio
from lumina.intelligence.languages import get_pack


def test_timing_uses_the_language_pack_reading_speed() -> None:
    """`typography.cps` is per-language because reading speed varies about threefold.

    A hardcoded rate would silently mistime every non-English script, so the test pins the
    computation to the pack rather than to a constant.
    """
    text = "Black holes are not vacuum cleaners."
    t = studio.timing(text, language="en", target_ms=60_000)

    cps = get_pack("en").typography.cps
    assert t.cps == cps
    assert t.spoken_ms == round(len(text) / cps * 1000)
    assert t.chars == len(text)


def test_timing_signs_the_overage_so_the_ui_can_say_which_way() -> None:
    short = studio.timing("Three words here.", target_ms=60_000)
    assert short.over_ms < 0, "an 18-character script is not over a 60-second target"
    assert short.ok is False, "18 characters against 60 seconds is not within 10%"

    long = studio.timing("word " * 4000, target_ms=5_000)
    assert long.over_ms > 0


def test_timing_ok_is_a_ten_percent_band() -> None:
    """Short-form tolerates drift; pacing is fixed by scene duration, not by cutting words."""
    cps = get_pack("en").typography.cps
    exact = "x" * round(cps * 30)  # exactly 30 seconds of reading
    assert studio.timing(exact, target_ms=30_000).ok
    assert studio.timing(exact, target_ms=32_900).ok, "9.7% under is still inside the band"
    assert studio.timing(exact, target_ms=34_000).ok is False, "11.8% under is outside it"


def test_split_beats_labels_the_shape_of_a_short() -> None:
    """First sentence is the hook, last is the CTA. That is the actual shape of nearly every
    short, and getting it from structure costs nothing."""
    beats = studio.split_beats(
        "Everyone is wrong about black holes. "
        "They are not vacuum cleaners. "
        "Orbit one and you just orbit. "
        "The danger is only close in. "
        "Follow for more."
    )
    labels = [b["beat"] for b in beats]
    assert labels[0] == studio.Beat.HOOK.value
    assert labels[-1] == studio.Beat.CTA.value
    assert studio.Beat.PAYOFF.value in labels
    assert all(int(b["duration_ms"]) >= 800 for b in beats)  # type: ignore[call-overload]


def test_split_beats_survives_a_single_sentence() -> None:
    beats = studio.split_beats("Just one line.")
    assert [b["beat"] for b in beats] == [studio.Beat.HOOK.value]


def test_split_beats_on_empty_input_is_empty_not_an_error() -> None:
    assert studio.split_beats("   ") == []


@pytest.mark.anyio
async def test_rewrite_rejects_an_unknown_operation() -> None:
    """The client gets its operation list from the API, so an unknown one is a bug worth
    surfacing rather than silently passing through to a model."""
    with pytest.raises(ValueError, match="unknown rewrite"):
        await studio.rewrite("A line.", "make-it-blue")


@pytest.mark.anyio
async def test_offline_rewrite_actually_edits() -> None:
    """Without a key the length operations still do real work. A no-op would make the Script
    Studio screen impossible to judge."""
    line = "This is really just basically a very simple idea about gravity."
    out = await studio.rewrite(line, "tighter")
    assert out != line
    assert "basically" not in out and "really" not in out


@pytest.mark.anyio
async def test_offline_hooks_are_grammatical_and_distinctly_patterned() -> None:
    """Each hook must use a different pattern — that labelling is the feature, not decoration.

    The offline set deliberately does not splice the script's own clause into a template:
    "everyone is wrong about black holes are not vacuum cleaners" is what that produces.
    """
    hooks = await studio.hooks(
        "Black holes are not vacuum cleaners. They pull less than you think."
    )
    assert len(hooks.hooks) >= 3
    patterns = [h.pattern for h in hooks.hooks]
    assert len(set(patterns)) == len(patterns), "hooks must not repeat a pattern"
    assert all(h.text.strip() for h in hooks.hooks)
    assert all(h.why.strip() for h in hooks.hooks)
