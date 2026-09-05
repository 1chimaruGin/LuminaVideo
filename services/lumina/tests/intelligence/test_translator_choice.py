"""Which engine runs, and what every engine is held to.

The routing question is small but load-bearing: an operator sets keys, and nothing above
`translator()` is allowed to know which vendor answered. The guarantees are the larger half —
they are asserted against a fake engine here precisely because they must hold for an engine
that has not been written yet.
"""

from __future__ import annotations

import pytest

from lumina.intelligence.translate import (
    ClaudeTranslator,
    GeminiTranslator,
    NoTranslator,
    TranslationUnavailableError,
    _Checked,
    translator,
)


def _keys(monkeypatch, *, gemini: str, anthropic: str) -> None:
    """Set keys the way the rest of the suite does — on the cached settings instance.

    Not via the environment plus `cache_clear()`: that rebuilds the singleton everything
    shares and leaves the rebuilt one in place after the test, so a later test in another
    file sees a translator this file configured.
    """
    from lumina.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_api_key", gemini)
    monkeypatch.setattr(settings, "anthropic_api_key", anthropic)


def test_gemini_leads_when_both_keys_are_set(monkeypatch) -> None:
    """Not a preference — Gemini is the better-measured engine for Burmese."""
    _keys(monkeypatch, gemini="g", anthropic="a")
    assert isinstance(translator(), GeminiTranslator)


def test_claude_covers_a_deployment_with_only_an_anthropic_key(monkeypatch) -> None:
    _keys(monkeypatch, gemini="", anthropic="a")
    assert isinstance(translator(), ClaudeTranslator)


def test_no_key_refuses_in_words_an_operator_can_act_on(monkeypatch) -> None:
    _keys(monkeypatch, gemini="", anthropic="")
    assert isinstance(translator(), NoTranslator)


@pytest.mark.anyio
async def test_the_refusal_names_both_keys() -> None:
    with pytest.raises(TranslationUnavailableError) as caught:
        await NoTranslator().translate(["hi"], source="en", target="my")
    assert "GEMINI_API_KEY" in str(caught.value)
    assert "ANTHROPIC_API_KEY" in str(caught.value)


class _Fake(_Checked):
    """An engine that returns whatever it is told to, to test what wraps it."""

    name = "fake"

    def __init__(self, replies: list[list[str]]) -> None:
        self.replies = replies
        self.asked: list[bool] = []

    async def _ask(self, lines, *, source, target, keep, insist, whole=False):
        self.asked.append(whole)
        return self.replies.pop(0)


@pytest.mark.anyio
async def test_a_new_engine_inherits_the_leak_check() -> None:
    """The point of the base class: an engine written tomorrow is checked today."""
    engine = _Fake([["မပြောさなかった"], ["ဘယ်သူ့ကိုမှ မပြောခဲ့ဘူး။"]])
    out = await engine.translate(["誰にも言わなかった"], source="ja", target="my")
    assert out == ["ဘယ်သူ့ကိုမှ မပြောခဲ့ဘူး။"]
    # Second pass was told the first had left the source untranslated.
    assert engine.asked == [False, True]


@pytest.mark.anyio
async def test_a_clean_translation_is_not_re_asked() -> None:
    engine = _Fake([["ဘယ်သူ့ကိုမှ မပြောခဲ့ဘူး။"]])
    await engine.translate(["He never told anyone."], source="en", target="my")
    assert engine.asked == [False]  # one call, no retry


class _Counting(_Checked):
    """Records every batch it is handed, so batching and dedupe can be asserted."""

    name = "counting"

    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    async def _ask(self, lines, *, source, target, keep, insist, whole=False):
        self.batches.append(list(lines))
        return [f"MY:{line}" for line in lines]


@pytest.mark.anyio
async def test_a_long_track_is_split_into_batches() -> None:
    """A thousand-line subtitle track in one request truncates and loses the lot."""
    from lumina.intelligence.translate import BATCH

    engine = _Counting()
    lines = [f"line {i}" for i in range(1000)]
    out = await engine.translate(lines, source="en", target="my")

    assert out == [f"MY:line {i}" for i in range(1000)]
    assert len(engine.batches) == 1000 // BATCH + (1 if 1000 % BATCH else 0)
    assert all(len(b) <= BATCH for b in engine.batches)


@pytest.mark.anyio
async def test_a_repeated_line_is_translated_once() -> None:
    """Subtitle tracks repeat heavily, and a line translated without context has no reason
    to come back differently the second time."""
    engine = _Counting()
    lines = ["[Music]", "Hello.", "[Music]", "Goodbye.", "[Music]"]
    out = await engine.translate(lines, source="en", target="my")

    assert out == ["MY:[Music]", "MY:Hello.", "MY:[Music]", "MY:Goodbye.", "MY:[Music]"]
    assert engine.batches == [["[Music]", "Hello.", "Goodbye."]]  # three sent, not five


@pytest.mark.anyio
async def test_order_survives_batching() -> None:
    """Batches complete out of order; the track must not."""
    engine = _Counting()
    lines = [f"{i}" for i in range(250)]
    assert await engine.translate(lines, source="en", target="my") == [
        f"MY:{i}" for i in range(250)
    ]


@pytest.mark.anyio
async def test_an_empty_track_asks_nothing() -> None:
    engine = _Counting()
    assert await engine.translate([], source="en", target="my") == []
    assert engine.batches == []
