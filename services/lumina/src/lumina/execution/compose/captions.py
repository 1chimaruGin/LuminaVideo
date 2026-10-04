"""Caption rasterization.

`DEVELOPMENT.md` rules out ffmpeg's `drawtext`, and rightly: it uses FreeType without
HarfBuzz shaping, so any script with stacked marks or contextual forms — Myanmar, Thai,
Devanagari, Arabic — comes out wrong. The two acceptable paths are libass, or rasterizing
caption states server-side and overlaying them.

This takes the second, using Chromium. It costs a browser in the render image, and buys:

  - correct shaping for every writing system, because it is the same engine the app renders
    in, so what the creator approved is what gets burned in;
  - caption styles expressed as CSS, which is how they are authored in the Identity kit;
  - highlight animation for free.

Crucially this rasterizes once per caption *state*, not per frame. A line with five highlight
steps is five PNGs, each gated by `enable='between(t,...)'` in the filter graph — not 150
images.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import structlog

from lumina.execution.compose import fonts
from lumina.intelligence.languages import LanguagePack, get_pack

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CaptionState:
    """One rendered still of a caption, and when it is on screen."""

    png: Path
    start_ms: int
    end_ms: int


@dataclass(frozen=True, slots=True)
class Line:
    """A caption line, with the word timings alignment produced.

    `also` carries the same line in the other burnt-in languages, in the order they stack
    under the primary. A bilingual subtitle is one caption, not two overlapping ones: they
    share this line's window exactly, which is the point — a translation appears when the
    thing it translates is said.
    """

    text: str
    start_ms: int
    end_ms: int
    words: list[tuple[str, int]]  # (word, start offset within the line)
    #: (language, text) for each secondary track, primary first.
    also: list[tuple[str, str]] = field(default_factory=list)


#: Caption styles, keyed the way the Identity kit names them. CSS, because that is what the
#: design authors and what the preview in the browser already uses.
#:
#: Each style carries its own highlight, and the highlight works by *dimming the other words*
#: rather than colouring the current one.
#:
#: That inversion is what makes it robust. A single global highlight colour cannot work — the
#: spoken word is the one thing a viewer must be able to find, and "amber" is the right answer
#: on a dark plate and completely invisible on an amber pill, which is exactly the bug this
#: replaced. Contrast against the plate is a property of the plate; opacity is not, so leaning
#: on opacity means a new style cannot silently erase its own highlight.
@dataclass(frozen=True, slots=True)
class Style:
    base: str
    #: Applied to the word currently being spoken, on top of `base`.
    highlight: str


STYLES: dict[str, Style] = {
    "pop": Style(
        base=(
            "background:#ffc36b;color:#1d1204;padding:.28em .5em;border-radius:.22em;"
            "font-weight:800;letter-spacing:-.01em"
        ),
        # Darkening rather than colouring: the plate is already the accent, so the spoken
        # word is picked out by weight and depth instead of by hue.
        highlight="color:#0b0600",
    ),
    "line": Style(
        base=(
            "background:rgba(8,10,20,.82);color:#fff;padding:.26em .5em;border-radius:.16em;"
            "font-weight:650"
        ),
        highlight="color:#ffc36b",
    ),
    "glow": Style(
        base="color:#fff;font-weight:700;text-shadow:0 0 .5em rgba(150,200,255,.95)",
        highlight="color:#ffc36b;text-shadow:0 0 .6em rgba(255,195,107,.95)",
    ),
    #: A hard outline instead of a plate. Reads on any footage without covering it, which is
    #: the case a filled plate handles badly — a plate over a face hides the face.
    "outline": Style(
        base=(
            "color:#fff;font-weight:800;letter-spacing:-.01em;"
            "-webkit-text-stroke:.055em #05070f;paint-order:stroke fill"
        ),
        highlight="color:#ffc36b",
    ),
    #: Documentary lower-third: quiet, wide, and clearly not trying to be a meme.
    "news": Style(
        base=(
            "background:rgba(6,8,16,.9);color:#f2f5ff;padding:.32em .7em;border-radius:0;"
            "border-left:.18em solid #ffc36b;font-weight:550;letter-spacing:.005em"
        ),
        highlight="color:#ffc36b",
    ),
    #: The karaoke look this style exists for: the spoken word gets the plate, not the line.
    #: Only meaningful with word timing, and it degrades to plain white when held static.
    "spot": Style(
        base="color:#fff;font-weight:800;letter-spacing:-.01em;text-shadow:0 .06em .3em #000",
        highlight=(
            "background:#ffc36b;color:#1d1204;padding:.1em .22em;border-radius:.18em;"
            "text-shadow:none"
        ),
    ),
    #: For footage that is already bright. A light plate is the one thing none of the others
    #: offer, and white video with white captions is the commonest unreadable combination.
    "paper": Style(
        base=(
            "background:rgba(248,248,245,.94);color:#14161c;padding:.28em .55em;"
            "border-radius:.22em;font-weight:700"
        ),
        highlight="color:#a4610d",
    ),
}

#: How present the mark is. Legible on any footage, and not competing with the video: this is
#: a signature, not a badge.
MARK_OPACITY = 0.72

_PAGE = """<!doctype html><meta charset="utf-8">
<style>
  html,body{{margin:0;padding:0;background:transparent}}
  body{{width:{w}px;height:{h}px;display:flex;align-items:flex-end;justify-content:center;
       padding:0 {pad}px {bottom}px;box-sizing:border-box;
       font-family:{family};-webkit-font-smoothing:antialiased}}
  .stack{{display:flex;flex-direction:column;align-items:center;gap:{stack_gap}px;
          max-width:100%}}
  .cap{{max-width:100%;text-align:center;font-size:{size}px;line-height:{lh};
        letter-spacing:{tracking}em;text-wrap:balance;{style}}}
  /* A secondary track is smaller and quieter: it is a translation of the line above, and
     drawing both at full weight makes neither the one being read. Each carries its own
     script's font stack and line-height, because a Burmese gloss under an English caption
     needs the taller box and the Myanmar face regardless of what the primary uses. */
  .cap.sub{{font-size:{sub_size}px;opacity:.88}}
  /* Everything not being spoken recedes. Transitionless: each state is a separate still,
     so an animation here would render as a stutter rather than a fade. */
  .w{{display:inline-block;margin:0 {gap};opacity:.42}}
  .on{{opacity:1;{highlight}}}
</style>
<div class="stack">{html}</div>"""


def _family(language: str) -> str:
    """A language's font stack as a CSS value."""
    stack = get_pack(language).typography.font_stack
    return ",".join(f"'{f}'" if " " in f else f for f in stack)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def rasterize(
    lines: list[Line],
    out_dir: Path,
    *,
    width: int,
    height: int,
    style: str = "pop",
    language: str = "en",
    bottom: float = 0.16,
    scale: float = 1.0,
    karaoke: bool = True,
) -> list[CaptionState]:
    """Render every caption state to a transparent PNG.

    One state per highlighted unit: the picture only changes when the highlight moves, so
    that is the only time a new image is needed.

    `karaoke=False` draws each line once, whole and unhighlighted, and holds it for the line's
    duration. Not every video wants the word-by-word treatment — it is the convention on
    short-form, and it is a distraction on a documentary or anything a viewer is reading
    rather than skimming. It is also far cheaper: a forty-word line is one image instead of
    forty, and one entry in the overlay graph instead of forty.

    Typography comes from the language pack, not from a default argument. The face, the line
    height, the tracking and — most importantly — whether units are separated by a space are
    all properties of the writing system. The stack used to be a hardcoded default listing
    Myanmar and Thai faces it never actually selected, and units were always joined with a
    space, which in a continuously-written script inserts word breaks the language does not
    have.
    """
    from playwright.async_api import async_playwright

    # Before the browser starts, not after a hundred stills have been drawn as empty boxes.
    # Every language on screen, not just the primary: a bilingual caption whose second track
    # has no face draws that track as boxes while the first looks perfect.
    for code in {language, *(c for line in lines for c, _ in line.also)}:
        fonts.require(code)

    chosen = STYLES.get(style, STYLES["pop"])
    type_ = get_pack(language).typography
    family = _family(language)
    # Continuous scripts get no inter-unit margin either: `inline-block` with a margin draws a
    # visible gap between syllables that should touch.
    gap = ".12em" if type_.space_between_units else "0"
    joiner = " " if type_.space_between_units else ""
    # From the frame's SHORT edge, which is the dimension that does not change when the same
    # content is reframed — so a caption stays the same physical size across every export.
    #
    # It used to come from the height, and that is why a TikTok export came out with enormous
    # text: at 1080x1920 the font was 81px, or 7.5% of the width, while the same caption in
    # 16:9 was 2.3%. A 3.3x difference between two exports of one video. Sizing off the short
    # edge also lands where the conventions are — vertical captions occupy a larger share of
    # the width than landscape ones, because a phone is watched on mute and the caption is
    # carrying the content.
    size = max(12, round(min(width, height) * 0.05 * scale))
    states: list[CaptionState] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--force-color-profile=srgb"], env=fonts.env())
        page = await browser.new_page(viewport={"width": width, "height": height})

        for li, line in enumerate(lines):
            steps = line.words or [(line.text, 0)]
            #: Each state as (which unit is lit, when it appears, when it goes).
            #:
            #: Spelled out rather than derived from the loop index, because the static case has
            #: no index to derive from: a sentinel of -1 fed straight back into the "where does
            #: the next unit start" lookup, which answered *the first one* — so a still caption
            #: was on screen for the 60ms floor and gone. It flashed, and nothing failed.
            if karaoke:
                plan = [
                    (
                        wi,
                        line.start_ms + offset,
                        line.start_ms + steps[wi + 1][1] if wi + 1 < len(steps) else line.end_ms,
                    )
                    for wi, (_, offset) in enumerate(steps)
                ]
            else:
                #: One picture, nothing lit, held for the whole line.
                plan = [(-1, line.start_ms, line.end_ms)]

            for si, (wi, start, end) in enumerate(plan):
                html = joiner.join(
                    f'<span class="w{" on" if j == wi else ""}">{_escape(w)}</span>'
                    for j, (w, _) in enumerate(steps)
                )
                # Secondary tracks under the primary, each in its own script's face and
                # line-height. A Burmese gloss under an English caption needs the Myanmar
                # font and the taller box whatever the primary is set in.
                stacked = f'<div class="cap">{html}</div>' + "".join(
                    f'<div class="cap sub" style="font-family:{_family(code)};'
                    f'line-height:{get_pack(code).typography.line_height}">'
                    f"{_escape(text)}</div>"
                    for code, text in line.also
                    if text.strip()
                )
                await page.set_content(
                    _PAGE.format(
                        w=width,
                        h=height,
                        pad=round(width * 0.08),
                        bottom=round(height * bottom),
                        family=family,
                        size=size,
                        sub_size=round(size * 0.72),
                        stack_gap=round(size * 0.22),
                        lh=type_.line_height,
                        tracking=type_.letter_spacing_em,
                        gap=gap,
                        style=chosen.base,
                        highlight=chosen.highlight,
                        html=stacked,
                    )
                )
                # Fonts must be present before the shot or the first frames render in a
                # fallback face — the exact failure this whole approach exists to avoid.
                await page.evaluate("document.fonts.ready")

                png = out_dir / f"cap-{li:03d}-{si:03d}.png"
                await page.screenshot(path=str(png), omit_background=True)

                states.append(CaptionState(png=png, start_ms=start, end_ms=max(end, start + 60)))

        await browser.close()

    log.info("captions.rasterize", lines=len(lines), states=len(states))
    return states


def overlay_filter(
    states: list[CaptionState],
    work: Path,
    size: tuple[int, int],
    *,
    fps: int = 30,
    base: str = "",
    watermark: Path | None = None,
) -> tuple[list[str], str]:
    """Build the ffmpeg inputs and filter graph that burn the captions in.

    **One overlay, fed by a timed sequence** — not one overlay per caption.

    The obvious construction gives each still its own input and its own `overlay` gated with
    `enable='between(t,...)'`, and it is what this did. It is also O(n) filters: every frame of
    the video is pushed through every overlay in the chain, whether or not that caption is
    showing. Measured on a real track of 554 stills over a 200-second video, that chain costs
    32.3s against 15.1s for the version below — and the gap widens with the caption count,
    because one side grows and the other does not. A half-hour video runs to thousands of
    stills, where the chained form stops being slow and starts being unusable.

    Instead the stills are handed to the concat demuxer as a *video track* with a duration per
    frame, and composited in a single overlay. Gaps between captions are filled with a
    transparent still, because concat plays its entries back to back and cannot leave a hole.

    `base` is filtering applied to the video before the captions land — scaling and cropping to
    the export shape. Doing it here rather than in an earlier pass is what lets a subtitling
    render encode once instead of twice.
    """
    if not states:
        # No captions. Still a graph when there is reframing or a mark to apply — otherwise a
        # video with no cues would stream-copy straight through and come out in the source's
        # shape, unbranded.
        if not base and not watermark:
            return [], ""
        head = f"[0:v]{base}[plain]" if base else "[0:v]null[plain]"
        inputs, tail = _brand("[plain]", watermark, size, 1)
        return inputs, f"{head};{tail}" if tail else f"{head.replace('[plain]', '[vout]')}"

    blank = work / "cap-blank.png"
    if not blank.exists():
        blank.write_bytes(_transparent(*size))

    #: The stills laid end to end with the gaps filled, which is the only shape concat accepts.
    rows: list[tuple[Path, float]] = []
    at = 0
    for state in sorted(states, key=lambda x: x.start_ms):
        if state.start_ms > at:
            rows.append((blank, (state.start_ms - at) / 1000))
        rows.append((state.png, max(1 / fps, (state.end_ms - state.start_ms) / 1000)))
        at = max(at, state.end_ms)

    listing = work / "captions.txt"
    listing.write_text(
        "".join(f"file '{png.resolve()}'\nduration {secs:.6f}\n" for png, secs in rows)
        # The concat demuxer drops the final entry's duration, so the last file is named twice:
        # once with its length, once to be the frame that ends the track.
        + f"file '{rows[-1][0].resolve()}'\n"
    )

    head = f"[0:v]{base}[base];" if base else ""
    label = "[base]" if base else "[0:v]"
    captioned = "[vout]" if watermark is None else "[capped]"
    mark_inputs, mark = _brand("[capped]", watermark, size, 2)
    return (
        ["-f", "concat", "-safe", "0", "-i", str(listing), *mark_inputs],
        f"{head}[1:v]format=rgba,fps={fps}[cap];"
        f"{label}[cap]overlay=0:0:shortest=0{captioned}" + (f";{mark}" if mark else ""),
    )


def _brand(
    label: str, logo: Path | None, size: tuple[int, int], stream: int
) -> tuple[list[str], str]:
    """The mark in the corner, if there is one.

    Sized from the frame's *short* edge for the same reason the caption is: it is the dimension
    that does not change when one video is exported in three shapes, so the mark stays the same
    physical size on all of them instead of shrinking on the wide one.

    Bottom right, inset by a margin of its own width. Away from the caption, which sits at the
    bottom centre, and away from the corners every platform puts its own furniture in — a
    TikTok handle goes bottom-left, a YouTube timestamp bottom-right of the *player*, not of
    the frame.
    """
    if logo is None:
        return [], ""
    #: `stream` because the logo's input index depends on what came before it: the video is
    #: always 0, the caption track is 1 when there is one, and the mark follows. Hardcoding it
    #: put the mark on input 2 even when nothing occupied input 1, and ffmpeg then read the
    #: caption track's slot as the logo.
    short = min(size)
    width = max(48, round(short * 0.11))
    inset = round(short * 0.045)
    return (
        ["-i", str(logo)],
        f"[{stream}:v]scale={width}:-1,format=rgba,"
        f"colorchannelmixer=aa={MARK_OPACITY}[mark];"
        f"{label}[mark]overlay=W-w-{inset}:H-h-{inset}[vout]",
    )


def _transparent(width: int, height: int) -> bytes:
    """A fully transparent PNG, for the stretches with no caption on screen."""
    import struct
    import zlib

    raw = b"".join(b"\x00" + b"\x00\x00\x00\x00" * width for _ in range(height))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def word_timings(text: str, start_ms: int, duration_ms: int, pack: LanguagePack) -> Line:
    """Distribute a line's duration across its highlightable units.

    A stand-in for forced alignment. Weighting by length rather than splitting evenly matters
    even here: an even split visibly lags on long units, and the highlight drifting out of
    sync with the voice is the first thing anyone notices.

    The units come from the pack, not from `str.split`. Splitting on whitespace is invariant
    1's exact failure: Burmese and Thai are written continuously, so a whole caption is one
    "word" and the highlight never moves at all.
    """
    units = [u.text for u in pack.tokenize(pack.normalize(text)) if u.text.strip()]
    if not units:
        return Line(text=text, start_ms=start_ms, end_ms=start_ms + duration_ms, words=[])

    total = sum(len(u) for u in units)
    timed: list[tuple[str, int]] = []
    offset = 0
    for u in units:
        timed.append((u, offset))
        offset += round(duration_ms * (len(u) / total))
    return Line(text=text, start_ms=start_ms, end_ms=start_ms + duration_ms, words=timed)
