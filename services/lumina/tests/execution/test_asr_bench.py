"""Scoring an engine's output, with the metric the language actually needs.

The reason this file exists is the mistake it prevents: Burmese has no word boundaries, so a
word error rate over it measures whichever segmenter you happened to use as much as it
measures the engine. Whisper's own evaluation reports character error rate for Burmese, Thai,
Lao, Khmer, Chinese and Japanese for exactly that reason — and mixing a CER from one source
with a WER from another is how "3%" and "80%" end up in the same sentence.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "asr_bench", Path(__file__).parents[2] / "scripts" / "asr_bench.py"
)
assert _spec and _spec.loader
bench = importlib.util.module_from_spec(_spec)
sys.modules["asr_bench"] = bench
_spec.loader.exec_module(bench)


def test_a_perfect_transcript_scores_zero() -> None:
    metric, rate = bench.score("Light is fast.", "Light is fast.", spaced=True)
    assert (metric, rate) == ("WER", 0.0)


def test_case_and_punctuation_are_not_mistakes() -> None:
    """They are not what an engine is being judged on, and every engine punctuates
    differently."""
    _, rate = bench.score("light is FAST", "Light is fast.", spaced=True)
    assert rate == 0.0


def test_a_spaced_language_is_scored_by_word() -> None:
    metric, rate = bench.score("Light is slow", "Light is fast", spaced=True)
    assert metric == "WER"
    assert rate == pytest.approx(1 / 3)


def test_a_continuous_script_is_scored_by_character() -> None:
    """The whole point. Burmese is written without spaces, so there are no words to count
    without inventing them — and the invention is what gets measured."""
    metric, rate = bench.score("မြန်မာစာ", "မြန်မာစာ", spaced=False)
    assert metric == "CER"
    assert rate == 0.0

    _, wrong = bench.score("မြန်မာစ", "မြန်မာစာ", spaced=False)
    assert 0 < wrong < 0.5, "one character out of eight, not a whole-string miss"


def test_whitespace_is_ignored_where_the_script_has_none() -> None:
    """Two engines disagreeing about where to put spaces in Burmese have not disagreed about
    what was said, and scoring them apart for it would rank on formatting."""
    _, rate = bench.score("မြန်မာ စာ", "မြန်မာစာ", spaced=False)
    assert rate == 0.0


def test_unicode_normalisation_is_not_an_error() -> None:
    """The same Burmese text in NFD would otherwise score as almost entirely wrong."""
    import unicodedata

    truth = "မြန်မာစာ"
    _, rate = bench.score(unicodedata.normalize("NFD", truth), truth, spaced=False)
    assert rate == 0.0


def test_the_metric_comes_from_the_language_pack_not_a_list_here() -> None:
    """`space_between_units` already answers this for every language, and a second list would
    be a second thing to keep right."""
    from lumina.intelligence.languages import get_pack

    assert get_pack("en").typography.space_between_units is True
    assert get_pack("ko").typography.space_between_units is True
    for code in ("my", "th", "ja", "zh"):
        assert get_pack(code).typography.space_between_units is False, code


def test_every_engine_the_packs_name_is_runnable_by_the_bench() -> None:
    """A pack ranking an engine the bench cannot construct would silently drop it from the
    comparison, which is the one thing a bench must not do."""
    from lumina.intelligence.languages import packs

    for pack in packs():
        for engine in pack.asr.engines:
            assert engine in bench.ENGINES, f"{pack.code} names {engine!r}"
