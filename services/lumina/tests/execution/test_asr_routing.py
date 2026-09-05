"""Which engine reads which language, and what happens when none can.

The spread between engines is not a matter of taste. Whisper is at or near the state of the
art for English, Chinese, Japanese and Korean and costs about four cents an hour on Groq; its
published Burmese word error rate is above 80%. A bad ASR on a low-resource language does not
fail — it returns confident, fluent, wrong sentences, which for a subtitle nobody in the room
can read is the worst possible outcome.
"""

from __future__ import annotations

import pytest

from lumina.execution import transcribe
from lumina.intelligence.languages import get_pack, packs


@pytest.fixture
def keys(monkeypatch):
    """Turn engines on and off by their environment, as a deployment would."""

    def install(*, groq: str = "", eleven: str = "", local: str = "even-split") -> None:
        settings = transcribe.get_settings()
        monkeypatch.setattr(settings, "groq_api_key", groq)
        monkeypatch.setattr(settings, "elevenlabs_api_key", eleven)
        monkeypatch.setattr(settings, "asr_engine", local)

    return install


def test_every_language_names_an_engine_that_exists() -> None:
    """A pack asking for an engine nothing implements would fall through to the split
    silently, and the lane would refuse for a reason no error message explains."""
    known = {"whisper", "scribe", "google", "vertex", "seamless", "gemini"}
    for pack in packs():
        assert pack.asr.engines, f"{pack.code} names no engine"
        assert set(pack.asr.engines) <= known, f"{pack.code} wants {pack.asr.engines}"


def test_every_ranked_engine_has_a_model_to_run() -> None:
    """The bug the bench found: one `model_id` for a language that ranks several engines is
    one answer to several different questions, and `faster-whisper` was handed "scribe_v1"."""
    for pack in packs():
        for engine in pack.asr.engines:
            assert engine in pack.asr.models, f"{pack.code} ranks {engine} with no model"


def test_no_language_names_a_model_for_an_engine_it_does_not_rank() -> None:
    """Dead configuration drifts. Whisper has no Burmese entry precisely because it must never
    be reached for Burmese, and a stray model id there would quietly make it reachable."""
    for pack in packs():
        extra = set(pack.asr.models) - set(pack.asr.engines)
        assert not extra, f"{pack.code} carries unused models for {sorted(extra)}"


def test_burmese_never_falls_back_to_whisper(keys) -> None:
    """The whole reason the ranking is per language.

    With only Whisper configured, Burmese gets the split — which produces nothing and makes
    the lane refuse — rather than a fluent hallucination in a script the creator's audience
    reads and they cannot check.
    """
    keys(groq="gsk-test")
    assert isinstance(transcribe.transcriber(language="my"), transcribe.EvenSplit)
    assert "whisper" not in get_pack("my").asr.engines


def test_burmese_uses_scribe_when_it_is_available(keys) -> None:
    keys(eleven="sk-test")
    assert isinstance(transcribe.transcriber(language="my"), transcribe.Scribe)


@pytest.mark.parametrize("code", ["en", "ja", "zh", "ko"])
def test_the_well_served_languages_take_the_cheap_engine_first(keys, code: str) -> None:
    """Whisper is both the best and by far the cheapest for these, so it leads. Preferring
    Scribe here would multiply the bill for no accuracy."""
    keys(groq="gsk-test", eleven="sk-test")
    assert isinstance(transcribe.transcriber(language=code), transcribe.HostedWhisper)


@pytest.mark.parametrize("code", ["en", "ja", "zh", "ko"])
def test_they_fall_back_to_scribe_rather_than_refusing(keys, code: str) -> None:
    keys(eleven="sk-test")
    assert isinstance(transcribe.transcriber(language=code), transcribe.Scribe)


def test_thai_prefers_scribe_but_will_take_whisper(keys) -> None:
    keys(groq="gsk-test", eleven="sk-test")
    assert isinstance(transcribe.transcriber(language="th"), transcribe.Scribe)
    keys(groq="gsk-test")
    assert isinstance(transcribe.transcriber(language="th"), transcribe.HostedWhisper)


def test_with_nothing_configured_every_language_gets_the_split(keys) -> None:
    """The default a fresh checkout is in: no keys, no model download, no network, and the
    pipeline still runs end to end."""
    keys()
    for pack in packs():
        assert isinstance(transcribe.transcriber(language=pack.code), transcribe.EvenSplit)


def test_the_split_refuses_rather_than_inventing_words(keys) -> None:
    """It distributes text it was handed. Handed none, it returns none — the lanes then say
    so. An earlier version returned a "No speech detected" sentence, which passed the
    emptiness check and became the script."""
    keys()
    engine = transcribe.transcriber(language="en")
    assert engine.name == "even-split"


def test_a_local_model_stands_in_for_hosted_whisper(keys) -> None:
    """`ASR_ENGINE=whisper` is the self-hosted path. It satisfies the same slot in the
    ranking, so a language wanting Whisper gets it without knowing where it runs."""
    keys(local="whisper")
    assert isinstance(transcribe.transcriber(language="en"), transcribe.LocalWhisper)
    # ...and still does not get used for the language it cannot read.
    assert isinstance(transcribe.transcriber(language="my"), transcribe.EvenSplit)


# ----------------------------------------------------------------- what the adapters parse


@pytest.fixture
def wav(tmp_path):
    """A file to hand the adapter. Its bytes never reach a real server."""
    path = tmp_path / "speech.wav"
    path.write_bytes(b"RIFF....WAVE")
    return path


def _stub(monkeypatch, payload: dict, seen: dict) -> None:
    """Answer any POST with `payload`, recording what was sent."""
    import httpx

    class Reply:
        def json(self) -> dict:
            return payload

        def raise_for_status(self) -> None:
            return None

    class Client:
        def __init__(self, *a, **kw) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a) -> None:
            return None

        async def post(self, url, **kw):
            seen.update(url=url, **kw)
            return Reply()

    monkeypatch.setattr(httpx, "AsyncClient", Client)


@pytest.mark.anyio
async def test_whisper_asks_for_the_language_rather_than_letting_it_guess(monkeypatch, wav, keys):
    """Whisper's own detection is a coin toss between Chinese and Japanese on a short clip,
    and guessing wrong produces fluent output in the wrong script rather than an error."""
    keys(groq="gsk-test")
    seen: dict = {}
    _stub(monkeypatch, {"segments": [{"start": 0.5, "end": 2.0, "text": " Hello there. "}]}, seen)

    out = await transcribe.HostedWhisper().transcribe(wav, language="ja", duration_ms=3000)

    assert seen["data"]["language"] == "ja"
    assert seen["data"]["model"] == "whisper-large-v3-turbo"
    assert seen["data"]["response_format"] == "verbose_json"
    assert "groq.com" in seen["url"]
    # Trimmed, and in milliseconds.
    assert out == [transcribe.Segment(text="Hello there.", start_ms=500, duration_ms=1500)]


@pytest.mark.anyio
async def test_whisper_drops_the_empty_segments_it_emits_on_silence(monkeypatch, wav, keys):
    keys(groq="gsk-test")
    _stub(
        monkeypatch,
        {
            "segments": [
                {"start": 0.0, "end": 1.0, "text": "   "},
                {"start": 1.0, "end": 2.0, "text": "Real words."},
            ]
        },
        {},
    )
    out = await transcribe.HostedWhisper().transcribe(wav, language="en", duration_ms=2000)
    assert [s.text for s in out] == ["Real words."]


@pytest.mark.anyio
async def test_scribe_groups_words_into_lines_on_the_pauses(monkeypatch, wav, keys):
    """Scribe times every word. One caption per word would be unreadable, so they are grouped
    where the speaker actually paused."""
    keys(eleven="sk-test")
    words = [
        {"type": "word", "text": "Light", "start": 0.0, "end": 0.4},
        {"type": "word", "text": "is", "start": 0.4, "end": 0.6},
        {"type": "word", "text": "fast.", "start": 0.6, "end": 1.0},
        # A second and a half of silence: a new line.
        {"type": "word", "text": "But", "start": 2.5, "end": 2.8},
        {"type": "word", "text": "not", "start": 2.8, "end": 3.0},
        {"type": "word", "text": "instant.", "start": 3.0, "end": 3.6},
        {"type": "spacing", "text": " ", "start": 3.6, "end": 3.7},
    ]
    seen: dict = {}
    _stub(monkeypatch, {"words": words}, seen)

    out = await transcribe.Scribe().transcribe(wav, language="en", duration_ms=4000)

    assert seen["data"]["timestamps_granularity"] == "word"
    assert [s.text for s in out] == ["Light is fast.", "But not instant."]
    assert out[0].start_ms == 0 and out[0].end_ms == 1000
    assert out[1].start_ms == 2500


@pytest.mark.anyio
async def test_scribe_does_not_put_spaces_into_a_script_that_has_none(monkeypatch, wav, keys):
    """Joining Burmese words with spaces is a spelling error, not a formatting choice — the
    language is written continuously. The pack already knows; the adapter asks it."""
    keys(eleven="sk-test")
    words = [
        {"type": "word", "text": "မြန်မာ", "start": 0.0, "end": 0.5},
        {"type": "word", "text": "စာ", "start": 0.5, "end": 0.9},
    ]
    _stub(monkeypatch, {"words": words}, {})
    out = await transcribe.Scribe().transcribe(wav, language="my", duration_ms=1000)
    assert [s.text for s in out] == ["မြန်မာစာ"]


@pytest.mark.anyio
async def test_scribe_breaks_a_speaker_who_never_pauses(monkeypatch, wav, keys):
    """Otherwise one unbroken delivery becomes a single caption the length of the video."""
    keys(eleven="sk-test")
    words = [
        {"type": "word", "text": f"w{i}", "start": i * 0.4, "end": i * 0.4 + 0.4} for i in range(40)
    ]
    _stub(monkeypatch, {"words": words}, {})
    out = await transcribe.Scribe().transcribe(wav, language="en", duration_ms=16_000)
    assert len(out) > 1
    assert all(s.duration_ms <= transcribe.Scribe.MAX_MS + 500 for s in out)


def test_a_server_reports_what_it_can_hear_language_by_language(keys) -> None:
    """`/projects/languages` drives the editor's "this server cannot listen" banner, and a
    single yes/no would be wrong on every mixed deployment — which is all of them, since the
    cheap engine covers four languages and not the fifth."""
    keys(groq="gsk-test")
    assert transcribe.can_hear("en") is True
    assert transcribe.can_hear("ja") is True
    assert transcribe.can_hear("my") is False, "Groq alone cannot read Burmese"

    keys(groq="gsk-test", eleven="sk-test")
    assert all(transcribe.can_hear(p.code) for p in packs())

    keys()
    assert not any(transcribe.can_hear(p.code) for p in packs())
