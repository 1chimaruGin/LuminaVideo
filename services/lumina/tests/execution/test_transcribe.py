"""The transcriber seam.

Nothing above `Transcriber` may know which engine is running — the same rule as the provider
adapter, and for the same reason: this is the piece that will be swapped, tuned and made
per-language before anything else in the product, because two of the five recipes are useless
without it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lumina.execution import transcribe as tr


@pytest.mark.anyio
async def test_even_split_weights_by_sentence_length() -> None:
    """An even split visibly lags on a long sentence, and a caption drifting out of sync with
    the voice is the first thing anyone notices."""
    text = "Hi. This one is considerably longer than the first sentence was."
    out = await tr.EvenSplit(text).transcribe(Path(), language="en", duration_ms=10_000)

    assert len(out) == 2
    assert out[1].duration_ms > out[0].duration_ms * 2
    assert out[0].start_ms == 0
    assert out[1].start_ms == out[0].end_ms, "segments must be contiguous"


@pytest.mark.anyio
async def test_nothing_heard_is_nothing_returned() -> None:
    """Not a segment reading "No speech detected".

    That string is not empty, so every check for "did we get anything" passed and it became the
    script: one upload produced a 300-second scene narrated with the words "No speech
    detected", padded out with filler beats. An empty result is a state the caller must handle;
    a plausible sentence is a fabrication that reaches the rendered video.
    """
    out = await tr.EvenSplit("").transcribe(Path(), language="en", duration_ms=5_000)
    assert out == []


@pytest.mark.anyio
async def test_every_segment_has_a_positive_duration() -> None:
    """Zero-length segments become caption states with an empty `enable` window, which ffmpeg
    accepts and silently never shows."""
    out = await tr.EvenSplit("A. B. C. D. E.").transcribe(Path(), language="en", duration_ms=900)
    assert all(s.duration_ms > 0 for s in out)
    assert all(s.end_ms > s.start_ms for s in out)


def test_the_default_engine_needs_no_model_and_no_network() -> None:
    """The same reason the fake provider is the default: the pipeline has to run end to end
    with nothing installed, or the dev loop depends on a model download."""
    assert isinstance(tr.transcriber(), tr.EvenSplit)


def test_whisper_is_selected_by_configuration_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Swapping the engine must be a config change, not a code change — that is the whole
    point of the seam."""
    from lumina.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "asr_engine", "whisper", raising=False)
    assert isinstance(tr.transcriber(), tr.LocalWhisper)


def test_both_engines_satisfy_the_protocol() -> None:
    """Structural, so a new engine cannot be half-wired: `transcriber()` returns whatever is
    configured, and a missing method would only surface inside a worker."""
    for engine in (tr.EvenSplit(""), tr.LocalWhisper()):
        assert isinstance(engine, tr.Transcriber)
        assert engine.name


@pytest.mark.anyio
async def test_the_stage_reads_no_audio_for_the_split_engine(monkeypatch) -> None:
    """The asset here is the creator's whole upload. Fetching a gigabyte from storage only to
    ignore it would dominate the runtime of every development run — and it is what made the
    stage fail on an asset that had not been stored at all.
    """
    import uuid

    from lumina.execution import stages

    class Exploding:
        async def read(self, asset_id: uuid.UUID) -> bytes:
            raise AssertionError("the split engine must not read the asset")

    ctx = stages.Ctx(session=None, router=None, assets=Exploding(), ledger=None)  # type: ignore[arg-type]
    out = await stages.transcribe(
        ctx,
        {"asset_id": str(uuid.uuid4()), "duration_ms": 6000, "text": "One. Two. Three."},
    )
    assert len(out["lines"]) == 3
    assert out["engine"] == "even-split"


# --------------------------------------------------------------------------- probing


@pytest.mark.anyio
async def test_a_container_with_no_duration_header_is_still_measured(tmp_path) -> None:
    """Every file a browser records is one of these.

    MediaRecorder writes WebM as a stream, so the duration is only known once the file ends
    and is never written back into the header — ffprobe answers "N/A". Reporting zero would
    silently mistime everything downstream that sizes itself from it, which for an uploaded
    video is the entire caption track.
    """
    from lumina.execution.compose import ffmpeg

    # `-f webm` with no duration written back is exactly the shape MediaRecorder produces.
    streamed = tmp_path / "streamed.webm"
    await ffmpeg.run(
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=3",
        "-c:a",
        "libopus",
        "-f",
        "webm",
        str(streamed),
    )

    probe = await ffmpeg.probe(streamed)
    assert 2800 <= probe.duration_ms <= 3200, (
        f"a 3-second recording measured as {probe.duration_ms}ms"
    )


@pytest.mark.anyio
async def test_a_normal_container_is_read_from_its_header(tmp_path) -> None:
    """The decode fallback is expensive, so it must only run when there is no other answer."""
    from lumina.execution.compose import ffmpeg

    plain = tmp_path / "plain.mp4"
    await ffmpeg.run(
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=320x240:rate=30:duration=2",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        str(plain),
    )
    probe = await ffmpeg.probe(plain)
    assert 1900 <= probe.duration_ms <= 2100
    assert (probe.width, probe.height) == (320, 240)
