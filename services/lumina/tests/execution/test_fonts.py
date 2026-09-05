"""What `make fonts` fetches, and what happens when it has not been run.

The failure this guards against is specific and nasty: a missing face does not raise, it draws
identical empty rectangles which are then burnt into the finished video. It looks like a bug in
the app, and it survives review by everyone who does not read the script.
"""

from __future__ import annotations

import pytest

from lumina.execution.compose import fonts
from lumina.intelligence.languages import packs


def test_every_pack_that_needs_a_face_says_where_to_get_it() -> None:
    """The check that makes adding a language safe.

    A pack declaring `bundled=True` for a family the catalogue does not know could never
    render — and would only say so on some creator's first project. `required()` raises at
    import-adjacent time instead, and this runs it for every registered pack.
    """
    needed = fonts.required()
    for pack in packs():
        if pack.typography.bundled:
            assert pack.code in needed, f"{pack.code} declares bundled but resolves to nothing"
            assert needed[pack.code].family == pack.typography.font_stack[0]
        else:
            assert pack.code not in needed


def test_a_pack_asking_for_an_unknown_face_fails_loudly() -> None:
    class Nowhere:
        code = "xx"
        endonym = "XX"
        english_name = "Nowhere"
        typography = type("T", (), {"bundled": True, "font_stack": ("Totally Made Up Sans",)})()

    with pytest.raises(fonts.UncataloguedFaceError, match="CATALOGUE"):
        fonts._required_for([Nowhere()])  # type: ignore[list-item]


def test_the_fetch_list_covers_regular_and_bold() -> None:
    """Faux bold on a dense script smears the strokes together, and every caption style here
    is 650 or heavier — so both weights are fetched rather than synthesized."""
    for face in fonts.required().values():
        names = [name for name, _ in face.files()]
        assert any("Regular" in n for n in names), face.family
        assert any("Bold" in n for n in names), face.family
        assert all(url.startswith("https://") for _, url in face.files())


def test_installed_is_false_until_every_weight_is_present(tmp_path, monkeypatch) -> None:
    """Half a download is a directory that exists and a caption that renders wrong."""
    monkeypatch.setattr(fonts, "DIR", tmp_path)
    assert fonts.installed("my") is False

    face = fonts.required()["my"]
    names = [name for name, _ in face.files()]
    (tmp_path / names[0]).write_bytes(b"x")
    assert fonts.installed("my") is False, "one weight of two is not installed"

    for name in names[1:]:
        (tmp_path / name).write_bytes(b"x")
    assert fonts.installed("my") is True


def test_a_language_with_a_usable_fallback_needs_nothing_fetched() -> None:
    """English renders acceptably in whatever the box has. Requiring a face for it would make
    `make fonts` a hard dependency of the default path for no benefit."""
    assert fonts.installed("en") is True
    assert "en" not in fonts.required()


def test_the_browser_is_pointed_at_our_directory_without_touching_the_home_dir() -> None:
    """fontconfig scans `$XDG_DATA_HOME/fonts`. Installing into the user's home to achieve the
    same thing would mutate a machine outside the repository."""
    env = fonts.env()
    assert env["XDG_DATA_HOME"] == str(fonts.ROOT)
    assert fonts.DIR == fonts.ROOT / "fonts"
    assert "PATH" in env, "the rest of the environment is preserved"
