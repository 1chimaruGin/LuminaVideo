"""ffmpeg composition.

The default routing tier is image-plus-motion: generated stills with Ken Burns, parallax and
transitions cost roughly one percent of premium video generation and are good enough for most
short-form. That makes this file, not a video model, the thing that renders most videos.

Two details that matter and are easy to get wrong:

  - `zoompan` computes its zoom per *output* frame but samples the input at the input's size,
    so panning a still at its native resolution produces visible stepping. Upscaling first
    and letting zoompan work inside the larger frame removes it.
  - Every clip is normalised to the same size, pixel format, frame rate and timebase before
    concatenation. The concat demuxer does not resample; mismatched inputs produce a file
    that plays for some viewers and not others.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from lumina.config import get_settings

log = structlog.get_logger(__name__)

FPS = 30

#: Stills are upscaled by this before zoompan, so the per-frame zoom step lands on a
#: sub-pixel boundary in source space and the pan does not visibly stair-step.
#:
#: 1.5 is enough for a 1.14 maximum zoom. It was 2.0, which made every frame a 2160x3840
#: resample — nearly four times the pixels for no visible gain, and it took the render from
#: seconds to minutes.
OVERSAMPLE = 1.5


@dataclass(frozen=True, slots=True)
class Size:
    w: int
    h: int

    def __str__(self) -> str:
        """`WxH`, the form scale/zoompan's `s=` option expects."""
        return f"{self.w}x{self.h}"

    @property
    def wh(self) -> str:
        """`W:H` — crop and several other filters take colon-separated arguments, and
        silently fail to parse the `WxH` form with a confusing expression error."""
        return f"{self.w}:{self.h}"


#: Our own mark, resolved at import for the same reason the Seamless worker is: `resolve`
#: touches the filesystem, and doing that inside a coroutine is the habit that matters in a
#: worker. Overridden by `WATERMARK_LOGO` where the file lives somewhere else.
_OWN_LOGO = Path(__file__).resolve().parents[6] / "logo" / "lumina-disc-1024.png"


def own_logo() -> Path | None:
    """The mark burnt in when a channel has not asked for something else."""
    configured = get_settings().watermark_logo
    path = Path(configured) if configured else _OWN_LOGO
    return path if path.is_file() else None


def video_codec() -> list[str]:
    """The encoder settings every render shares.

    Measured on a 3:21 720p source scaled to 1080x1920, which is the shape of the real work:

        veryfast crf 20   66.0s   137 MB    (what this was)
        veryfast crf 23   67.7s   102 MB
        superfast crf 20  35.8s   193 MB
        superfast crf 23  34.3s   140 MB    (what this is)

    So: the same file size in a little over half the time. The preset is what buys the speed —
    `crf` barely moves it — and raising `crf` alongside gives the size back that a faster
    preset costs. Quality per bit is slightly lower than `veryfast` at the same size; on video
    every platform re-encodes on upload anyway, that is the right side of the trade.

    Two things that looked promising and were not, both measured rather than reasoned about:
    copying the audio instead of re-encoding it saved 0.1s, and not upscaling a 720p source to
    1080p saved 14% — decode and scaling dominate, not the pixel count of the output.

    Settable, because the right point on this curve depends on hardware nobody has measured yet.
    """
    settings = get_settings()
    return [
        "-c:v",
        "libx264",
        "-preset",
        settings.ffmpeg_preset,
        "-crf",
        str(settings.ffmpeg_crf),
    ]


ASPECTS: dict[str, Size] = {
    "9:16": Size(1080, 1920),
    "1:1": Size(1080, 1080),
    "16:9": Size(1920, 1080),
}


class FfmpegError(RuntimeError):
    def __init__(self, args: list[str], stderr: str) -> None:
        tail = stderr.strip().splitlines()[-6:]
        super().__init__("ffmpeg failed: " + " ".join(args[:6]) + "\n" + "\n".join(tail))


def available() -> bool:
    return shutil.which("ffmpeg") is not None


async def run(*args: str) -> None:
    """Run ffmpeg, quietly, and surface only the tail of stderr on failure."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        *args,
        # DEVNULL, not inherited: under a test runner or a daemonised worker, stdin may be a
        # closed or captured pipe, and ffmpeg will block on it forever rather than exiting.
        # `-nostdin` covers ffmpeg's own reads; this covers everything else.
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise FfmpegError(list(args), err.decode("utf-8", "replace"))


async def still(path: Path, size: Size, c0: str, c1: str) -> Path:
    """Synthesize a placeholder frame.

    Stands in for a generated image while no paid provider is wired up, so the rest of the
    pipeline can be exercised — and watched — without spending anything.
    """
    await run(
        "-f",
        "lavfi",
        "-i",
        f"gradients=s={size}:c0={c0}:c1={c1}:x0=0:y0=0:x1={size.w}:y1={size.h}:d=1",
        "-frames:v",
        "1",
        str(path),
    )
    return path


async def silence(path: Path, ms: int) -> Path:
    """A silent track of a known length. Real narration replaces this at the voice stage."""
    await run(
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-t",
        f"{ms / 1000:.3f}",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(path),
    )
    return path


async def ken_burns(image: Path, out: Path, *, ms: int, size: Size, zoom_in: bool) -> Path:
    """One still becomes one moving clip.

    Two passes on purpose. `-loop 1` decodes the same still once per output frame, so any
    filter placed ahead of zoompan runs on every frame — a scale in that chain resampled the
    image 150 times per clip and took the render from seconds to minutes. Pre-scaling once
    and looping the result leaves only the pan as per-frame work.

    Alternating zoom direction between scenes is what stops a run of Ken Burns shots reading
    as a slideshow with one effect stuck on.
    """
    frames = max(2, round(ms / 1000 * FPS))
    big = Size(round(size.w * OVERSAMPLE), round(size.h * OVERSAMPLE))

    # Pass 1: resample once.
    scaled = out.with_name(out.stem + "-src.png")
    await run(
        "-i",
        str(image),
        "-vf",
        f"scale={big}:force_original_aspect_ratio=increase,crop={big.wh}",
        "-frames:v",
        "1",
        str(scaled),
    )

    # Pass 2: pan. The step is derived from the frame count, so the move always completes
    # exactly at the end of the clip whatever its duration — a fixed per-frame increment
    # either finishes early on a long scene or gets cut short on a brief one.
    span = 0.14
    z = (
        f"min(1.0+{span}*on/{frames},{1.0 + span:.4f})"
        if zoom_in
        else f"max({1.0 + span:.4f}-{span}*on/{frames},1.0)"
    )
    await run(
        "-loop",
        "1",
        "-i",
        str(scaled),
        "-filter_complex",
        (
            f"[0:v]zoompan=z='{z}':d={frames}:s={size}:fps={FPS}"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)',"
            f"format=yuv420p,setsar=1[v]"
        ),
        "-map",
        "[v]",
        "-frames:v",
        str(frames),
        *video_codec(),
        "-r",
        str(FPS),
        "-video_track_timescale",
        "90000",
        str(out),
    )
    scaled.unlink(missing_ok=True)
    return out


async def stitch(
    clips: list[Path],
    audio: Path | None,
    out: Path,
    workdir: Path,
    *,
    captions: tuple[list[str], str] | None = None,
) -> Path:
    """Concatenate the clips, burn in the captions, and mux the narration over them.

    Captions are overlaid in the *same* pass as the concatenation rather than in a second one.
    Burning them afterwards would decode and re-encode the whole video a second time, which
    doubles the most expensive stage in the product for no gain in quality — and generational
    loss from a second lossy encode is real, even if it is subtle.

    The tradeoff is that a captioned render cannot stream-copy the video. That is unavoidable:
    an overlay is a change to the pixels, so the frames have to be re-encoded whatever order
    the passes run in.
    """
    listing = workdir / "concat.txt"
    listing.write_text("".join(f"file '{c.resolve()}'\n" for c in clips))

    caption_inputs, graph = captions or ([], "")

    args = ["-f", "concat", "-safe", "0", "-i", str(listing)]
    args += caption_inputs
    if audio is not None:
        args += ["-i", str(audio)]

    if graph:
        # Counted, not derived from the length of the argument list.
        #
        # This was `1 + len(caption_inputs) // 2`, which held only while every caption was its
        # own `-i file` pair. The caption track is now one concat input written as six
        # arguments, so the arithmetic put the narration on input 4 when it was on input 2 —
        # and ffmpeg would have mapped whatever it found there, or refused.
        audio_index = caption_inputs.count("-i") + 1
        args += ["-filter_complex", graph, "-map", "[vout]"]
        args += [*video_codec(), "-pix_fmt", "yuv420p"]
        if audio is not None:
            args += ["-map", f"{audio_index}:a:0"]
        else:
            # The source's own sound.
            #
            # Mapping `[vout]` alone maps *only* that: with captions burnt in and no separate
            # narration — which is every subtitling render — the finished video came out
            # silent. `?` because a source without an audio track is legal and must not fail
            # the render.
            args += ["-map", "0:a?"]
    else:
        args += ["-c:v", "copy"]
        if audio is not None:
            args += ["-map", "0:v:0", "-map", "1:a:0"]

    if audio is not None:
        # `-shortest` so a narration longer than the picture does not extend the video with
        # a frozen last frame, which is the usual way this goes wrong.
        args += ["-c:a", "aac", "-b:a", "160k", "-shortest"]
    elif graph:
        # Copied, not re-encoded: the clips this concatenates were written by `cut`, so the
        # track is already AAC and a second pass would cost time to lose quality.
        args += ["-c:a", "copy"]

    args += ["-movflags", "+faststart", str(out)]

    await run(*args)
    log.info(
        "compose.stitch",
        clips=len(clips),
        audio=audio is not None,
        captions=len(caption_inputs) // 2,
        out=out.name,
    )
    return out


async def probe_duration_ms(path: Path) -> int:
    """Read a rendered file's real duration, rather than trusting what we asked for."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=nw=1:nk=1",
        str(path),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await proc.communicate()
    try:
        return round(float(out.decode().strip()) * 1000)
    except ValueError:
        return 0


@dataclass(frozen=True, slots=True)
class Probe:
    """What a source file actually is, as opposed to what the client claimed it was."""

    duration_ms: int
    width: int
    height: int
    has_audio: bool

    @property
    def aspect(self) -> str:
        """The closest delivery aspect. Used to pick a default, never to constrain output —
        one storyboard still produces all three."""
        if not self.height:
            return "9:16"
        ratio = self.width / self.height
        return min(ASPECTS, key=lambda a: abs(ratio - ASPECTS[a].w / ASPECTS[a].h))


async def probe(path: Path) -> Probe:
    """Dimensions, duration and whether there is anything to transcribe.

    Everything downstream sizes itself from these numbers, so they are measured rather than
    taken from the upload's metadata — a client can claim anything, and a mis-stated duration
    silently produces captions that drift further out of sync the longer the video runs.
    """
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=codec_type,width,height",
        "-of",
        "json",
        str(path),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise FfmpegError(["ffprobe", str(path)], err.decode("utf-8", "replace"))

    data: dict[str, Any] = json.loads(out or b"{}")
    streams: list[dict[str, Any]] = data.get("streams") or []
    video: dict[str, Any] = next((s for s in streams if s.get("codec_type") == "video"), {})

    duration = _ms(data.get("format", {}).get("duration"))
    if not duration:
        # Some containers carry no duration in their header, and every file a browser records
        # is one of them: MediaRecorder writes WebM as a stream, so the field is only known
        # once the file ends and is never written back. Falling through to a decode is the
        # only way to learn the length, and reporting zero would silently mistime everything
        # downstream that sizes itself from it.
        duration = max((_ms(s.get("duration")) for s in streams), default=0)
    if not duration:
        duration = await _decoded_duration_ms(path)

    return Probe(
        duration_ms=duration,
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
    )


def _ms(value: object) -> int:
    """Seconds as a string, in milliseconds. Zero for anything unparseable — including the
    literal "N/A" ffprobe emits when a container does not state a duration."""
    try:
        return round(float(str(value)) * 1000)
    except (TypeError, ValueError):
        return 0


async def _decoded_duration_ms(path: Path) -> int:
    """How long a file really is, by decoding it and discarding the output.

    The expensive answer, so it is only asked when the container did not carry one. `-f null`
    writes nothing; the length comes back on stderr as the last `time=` ffmpeg printed.
    """
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-i",
        str(path),
        "-f",
        "null",
        "-",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()

    found = re.findall(r"time=(\d+):(\d\d):(\d\d(?:\.\d+)?)", err.decode("utf-8", "replace"))
    if not found:
        return 0
    hours, minutes, seconds = found[-1]
    return round((int(hours) * 3600 + int(minutes) * 60 + float(seconds)) * 1000)


async def extract_audio(source: Path, out: Path) -> Path:
    """Pull the speech out of a video, mono 16 kHz — what every ASR model wants.

    Downmixing and downsampling here rather than at the transcriber is deliberate: it is the
    same conversion for every model, it shrinks what crosses the network by an order of
    magnitude, and doing it once means a re-transcribe never re-decodes the video.
    """
    await run(
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(out),
    )
    return out


async def bed(source: Path, out: Path, *, ms: int, size: Size) -> Path:
    """The creator's footage, made exactly `ms` long, reframed to `size`.

    For the lane where the words come first. The script decides how long the video is, so the
    footage has to become that length: looped if it is shorter than the narration, cut short
    if it is longer. Silent, because the narration is the audio — carrying the bed's own sound
    under a voice-over is a mix nobody asked for.

    `-stream_loop -1` before the input rather than a filter, so a bed far shorter than the
    script repeats without decoding it once per repetition.
    """
    await run(
        "-stream_loop",
        "-1",
        "-i",
        str(source),
        "-t",
        f"{ms / 1000:.3f}",
        "-an",
        "-vf",
        f"scale={size}:force_original_aspect_ratio=increase,crop={size.wh},setsar=1",
        *video_codec(),
        str(out),
    )
    return out


async def cut(source: Path, out: Path, *, start_ms: int, end_ms: int, size: Size) -> Path:
    """Take one moment out of a long video and reframe it for vertical.

    Re-encoded rather than stream-copied. A copy can only cut on a keyframe, which moves the
    real start by up to several seconds — fine for an archive, useless when the whole point is
    that the first line lands immediately.

    The reframe crops to fill rather than letterboxing: bars at the top and bottom of a Short
    read as reposted content, and every platform's algorithm treats them accordingly.
    """
    await run(
        "-ss",
        f"{start_ms / 1000:.3f}",
        "-i",
        str(source),
        "-t",
        f"{max(1, end_ms - start_ms) / 1000:.3f}",
        "-vf",
        f"scale={size}:force_original_aspect_ratio=increase,crop={size.wh},setsar=1",
        *video_codec(),
        "-r",
        str(FPS),
        "-video_track_timescale",
        "90000",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-movflags",
        "+faststart",
        str(out),
    )
    return out
