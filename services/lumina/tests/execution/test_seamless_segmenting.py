"""Where SeamlessM4T's timings come from.

Seamless is sequence-to-sequence: it returns a sentence, not an alignment, and there is no
way to ask it for word times. So the timings come from *where the speech is* — the audio is
split on silence and each stretch takes the times it actually occupies.

That makes this segmenter load-bearing rather than a convenience: get it wrong and every
caption from this engine is at the wrong moment. It is tested without the model, and without
torch, so it runs in the ordinary suite.
"""

from __future__ import annotations

import numpy as np

from lumina.execution import speech


def build(
    spans: list[tuple[int, int]], total_ms: int, *, amp: float = 0.3, noise: float = 0.002
) -> np.ndarray:
    """A waveform with tone where speech should be and room tone everywhere else."""
    rng = np.random.default_rng(7)
    n = int(total_ms / 1000 * speech.SAMPLE_RATE)
    wave = rng.standard_normal(n) * noise
    t = np.arange(n) / speech.SAMPLE_RATE
    for a, b in spans:
        i, j = int(a / 1000 * speech.SAMPLE_RATE), int(b / 1000 * speech.SAMPLE_RATE)
        wave[i:j] += amp * np.sin(2 * np.pi * 220 * t[i:j])
    return wave


def spans_of(wave: np.ndarray) -> list[tuple[int, int]]:
    return [(s.start_ms, s.end_ms) for s in speech.find_speech(wave)]


def test_it_finds_the_speech_and_leaves_the_silence() -> None:
    assert spans_of(build([(500, 2000), (3000, 4500)], 5000)) == [(500, 2000), (3000, 4500)]


def test_a_pause_inside_a_sentence_does_not_split_it() -> None:
    """Breaking on every 200ms gap gives captions that flicker a word at a time."""
    assert spans_of(build([(500, 2000), (2200, 3500)], 4000)) == [(500, 3500)]


def test_silence_is_not_speech() -> None:
    """The bug this caught: the threshold was purely relative, so on a file with no speech in
    it both quantiles were noise a hair apart, every frame cleared the threshold, and the
    whole file came back as one stretch — to be handed to a 2.3B model in full."""
    assert spans_of(build([], 3000)) == []


def test_a_noisy_room_is_still_not_speech() -> None:
    assert spans_of(build([], 3000, noise=0.004)) == []


def test_speech_that_never_pauses_is_still_broken_into_captions() -> None:
    """And the fix for the silence bug must not swallow this case: a file that is *entirely*
    speech also has its quantiles close together, so rejecting on a narrow spread threw away
    exactly the continuous delivery that most needs captioning."""
    spans = spans_of(build([(0, 15_000)], 15_000))
    assert len(spans) > 1
    assert all(b - a <= speech.MAX_MS + 200 for a, b in spans)
    assert spans[0][0] == 0 and spans[-1][1] == 15_000


def test_a_quiet_recording_is_still_transcribed() -> None:
    """The loudness floor has to sit below a quiet capture, not just below a loud one."""
    assert spans_of(build([(500, 2500)], 3000, amp=0.03, noise=0.001)) == [(500, 2500)]


def test_every_language_the_packs_ship_has_a_seamless_code() -> None:
    """A pack ranking `seamless` for a language the worker cannot name would fail inside the
    subprocess, after the model had loaded."""
    from lumina.intelligence.languages import packs

    for pack in packs():
        if "seamless" in pack.asr.engines:
            import importlib.util
            import sys
            from pathlib import Path

            spec = importlib.util.spec_from_file_location(
                "seamless_worker", Path(__file__).parents[2] / "scripts" / "seamless_worker.py"
            )
            assert spec and spec.loader
            # Read the table without importing torch, which the worker pulls in at module
            # level and which does not live in this environment.
            source = (Path(__file__).parents[2] / "scripts" / "seamless_worker.py").read_text()
            assert f'"{pack.code}":' in source.split("LANGS = {")[1].split("}")[0], (
                f"{pack.code} has no Seamless code"
            )
            del sys, importlib
