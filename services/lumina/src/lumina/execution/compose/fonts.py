"""Faces for the caption rasterizer.

Chromium draws the captions, so Chromium has to have the font. On a plain Linux box it has
DejaVu and nothing else — no Myanmar, no Thai, no CJK — and a missing face does not fail, it
draws tofu: a caption of identical empty rectangles, burnt into the video, looking like a
rendering glitch rather than a missing dependency. That is the worst possible failure here,
because it survives review by anyone who does not read the script.

So: faces live in a directory this module owns, the browser is pointed at it, and a pack whose
script has no face refuses to render rather than producing boxes.

Pointing the browser at them is done through `XDG_DATA_HOME` rather than by installing into
the user's home. fontconfig scans `$XDG_DATA_HOME/fonts`, Playwright passes `env` through to
the browser process, and nothing outside the repository is touched.

**What to fetch is derived, not listed.** A pack declares `typography.bundled` when its script
has no acceptable fallback, and names the family it needs as the head of its font stack. This
module maps family to a download, and `make fonts` fetches exactly the set the registered packs
require — so adding a language cannot leave a font requirement behind in a list nobody updated.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import structlog

from lumina.intelligence.languages import LanguagePack, get_pack, packs

log = structlog.get_logger(__name__)

#: `<repo>/services/lumina/var/fontroot`, holding `fonts/` for fontconfig to find.
ROOT = Path(__file__).resolve().parents[4] / "var" / "fontroot"
DIR = ROOT / "fonts"

_NOTO = "https://raw.githubusercontent.com/notofonts/notofonts.github.io/main/fonts"
_CJK = "https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/SubsetOTF"


@dataclass(frozen=True, slots=True)
class Face:
    """One downloadable family, in the weights the caption styles use.

    Regular and Bold both, rather than letting the browser synthesize: faux bold on a dense
    script smears the strokes together, and every caption style here is 650 or heavier.
    """

    #: The CSS family name a pack asks for. The key into `CATALOGUE`.
    family: str
    #: Filename stem on disk and upstream.
    stem: str
    #: Where the weights live. `{stem}` and `{weight}` are filled in.
    url: str
    ext: str = "ttf"
    weights: tuple[str, ...] = ("Regular", "Bold")

    def files(self) -> list[tuple[str, str]]:
        """(filename, url) for each weight."""
        return [
            (
                f"{self.stem}-{w}.{self.ext}",
                self.url.format(stem=self.stem, weight=w, ext=self.ext),
            )
            for w in self.weights
        ]


#: Where each bundled family comes from. CJK lives in its own repository and ships as OTF
#: subsets per language — the full TTC is tens of megabytes and carries five languages we
#: would then download five times.
CATALOGUE: dict[str, Face] = {
    "Noto Sans Myanmar": Face(
        family="Noto Sans Myanmar",
        stem="NotoSansMyanmar",
        url=f"{_NOTO}/NotoSansMyanmar/hinted/ttf/{{stem}}-{{weight}}.{{ext}}",
    ),
    "Noto Sans Thai": Face(
        family="Noto Sans Thai",
        stem="NotoSansThai",
        url=f"{_NOTO}/NotoSansThai/hinted/ttf/{{stem}}-{{weight}}.{{ext}}",
    ),
    "Noto Sans JP": Face(
        family="Noto Sans JP",
        stem="NotoSansJP",
        url=f"{_CJK}/JP/{{stem}}-{{weight}}.{{ext}}",
        ext="otf",
    ),
    "Noto Sans SC": Face(
        family="Noto Sans SC",
        stem="NotoSansSC",
        url=f"{_CJK}/SC/{{stem}}-{{weight}}.{{ext}}",
        ext="otf",
    ),
    "Noto Sans KR": Face(
        family="Noto Sans KR",
        stem="NotoSansKR",
        url=f"{_CJK}/KR/{{stem}}-{{weight}}.{{ext}}",
        ext="otf",
    ),
}


class MissingFontError(RuntimeError):
    """No face for this script.

    Raised instead of rendering, because the alternative is a finished video full of empty
    rectangles that looks like a bug in the app rather than a missing file on the server.
    """


class UncataloguedFaceError(RuntimeError):
    """A pack requires a face nothing knows how to fetch.

    Raised at startup by `required()` rather than at render time: a pack that declares
    `bundled=True` for a family with no entry here can never render, and finding that out on
    a creator's first project is finding out far too late.
    """


def required() -> dict[str, Face]:
    """The faces the registered packs actually need, keyed by language code."""
    return _required_for(packs())


def _required_for(available: list[LanguagePack]) -> dict[str, Face]:
    """Split out so the failure path can be tested with a pack that is not registered — the
    whole point being to catch it before one is."""
    out: dict[str, Face] = {}
    for pack in available:
        if not pack.typography.bundled:
            continue
        family = pack.typography.font_stack[0]
        face = CATALOGUE.get(family)
        if face is None:
            raise UncataloguedFaceError(
                f"the {pack.code} pack needs {family!r} and nothing in fonts.CATALOGUE says "
                "where to get it, so it could never render. Add an entry."
            )
        out[pack.code] = face
    return out


def env() -> dict[str, str | float | bool]:
    """Environment for the browser process, so fontconfig finds our faces.

    Typed the way Playwright declares it — `dict` is invariant, so a `dict[str, str]` is not
    accepted where `dict[str, str | float | bool]` is asked for.
    """
    out: dict[str, str | float | bool] = dict(os.environ)
    out["XDG_DATA_HOME"] = str(ROOT)
    return out


def installed(language: str) -> bool:
    """Whether this language's face is present."""
    face = required().get(language)
    if face is None:
        return True
    return all((DIR / name).is_file() for name, _ in face.files())


def require(language: str) -> None:
    """Refuse to rasterize a script we have no face for."""
    if installed(language):
        return
    family = get_pack(language).typography.font_stack[0]
    raise MissingFontError(
        f"no {family} on this server, so {language} captions would render as empty boxes. "
        f"Run `make fonts` to fetch it into {DIR}."
    )
