"""Whose mark goes on the finished video.

Ours by default: a channel that has said nothing gets the Lumina mark, because a video that
leaves the product unbranded is one nobody can trace back. The other two answers — the
creator's own logo, or nothing — are the same question, so they are one setting.

Who is *allowed* to change it is deliberately not decided here. There is no plan on an account
yet, only a credit ledger, so the render honours whatever the channel says and the paywall
belongs in front of the control that sets it.
"""

from __future__ import annotations

from pathlib import Path

from lumina.execution.compose.captions import CaptionState, overlay_filter


def test_the_mark_is_the_last_thing_applied(tmp_path: Path) -> None:
    """After the captions, so it is never drawn under them — and it is what produces `[vout]`,
    which is the label compose maps."""
    cap = tmp_path / "c.png"
    cap.write_bytes(b"x")
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"x")

    _, graph = overlay_filter(
        [CaptionState(png=cap, start_ms=0, end_ms=500)],
        tmp_path,
        (1080, 1920),
        watermark=logo,
    )
    assert graph.index("[cap]overlay") < graph.index("[mark]overlay")
    assert graph.endswith("[vout]")


def test_the_logo_reads_the_input_it_was_actually_given(tmp_path: Path) -> None:
    """Its stream index depends on what came before it.

    The video is always 0 and the caption track is 1 *when there is one*. Hardcoding the mark
    to input 2 put it on the caption track's slot on a render with no captions, and ffmpeg read
    whatever was there as the logo.
    """
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"x")
    cap = tmp_path / "c.png"
    cap.write_bytes(b"x")

    _, with_caps = overlay_filter(
        [CaptionState(png=cap, start_ms=0, end_ms=500)], tmp_path, (1080, 1920), watermark=logo
    )
    _, alone = overlay_filter([], tmp_path, (1080, 1920), base="scale=1:1", watermark=logo)

    assert "[2:v]scale=" in with_caps, "video 0, captions 1, mark 2"
    assert "[1:v]scale=" in alone, "video 0, mark 1 — nothing occupies input 1"


def test_no_mark_leaves_the_graph_alone(tmp_path: Path) -> None:
    cap = tmp_path / "c.png"
    cap.write_bytes(b"x")
    _, graph = overlay_filter(
        [CaptionState(png=cap, start_ms=0, end_ms=500)], tmp_path, (1080, 1920), watermark=None
    )
    assert "[mark]" not in graph
    assert graph.endswith("[vout]")


def test_a_mark_alone_still_produces_a_render(tmp_path: Path) -> None:
    """No captions and no reframing, but a mark to apply: without a graph the video would
    stream-copy straight through and come out unbranded."""
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"x")
    inputs, graph = overlay_filter([], tmp_path, (1080, 1080), watermark=logo)
    assert inputs.count("-i") == 1
    assert graph.endswith("[vout]")


def test_the_mark_is_sized_from_the_short_edge(tmp_path: Path) -> None:
    """The dimension that does not change when one video is exported in three shapes, so the
    mark stays the same physical size instead of shrinking on the wide one."""
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"x")
    _, tall = overlay_filter([], tmp_path, (1080, 1920), watermark=logo)
    _, wide = overlay_filter([], tmp_path, (1920, 1080), watermark=logo)
    assert "scale=118:-1" in tall or "scale=119:-1" in tall
    assert tall.split("scale=")[1].split(":")[0] == wide.split("scale=")[1].split(":")[0]
