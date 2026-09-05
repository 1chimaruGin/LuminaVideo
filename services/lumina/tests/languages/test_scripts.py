"""What each script needs that the conformance suite cannot ask for generically.

`test_conformance.py` checks every pack against the protocol. These check the handful of facts
that are specific to one writing system and that a generic test would have to know the answer
to in advance — the places where treating all six the same produces something a reader of that
script would reject.
"""

from __future__ import annotations

import pytest

from lumina.intelligence.languages import cjk, get_pack, packs


def test_korean_is_the_east_asian_language_written_with_spaces() -> None:
    """The reason `space_between_units` is a property and not an assumption about CJK.

    Grouping Korean with Japanese and Chinese — the obvious thing to do — joins 어절 with no
    space and produces text no Korean reader would accept.
    """
    ko = get_pack("ko")
    assert ko.typography.space_between_units is True
    assert [u.text for u in ko.tokenize("빛은 빠르지만 즉각적이지는 않습니다")] == [
        "빛은",
        "빠르지만",
        "즉각적이지는",
        "않습니다",
    ]

    for code in ("ja", "zh", "my", "th"):
        assert get_pack(code).typography.space_between_units is False, (
            f"{code} is written continuously; joining its units with spaces inserts word "
            "breaks the language does not have"
        )


def test_japanese_keeps_a_word_with_its_okurigana_and_particles() -> None:
    """Splitting 行きました into 行 and きました puts the highlight on a bare stem, which reads
    as a typo. Keeping them together lands near a 文節, which is what a reader parses by."""
    units = [u.text for u in get_pack("ja").tokenize("東京に行きました。カレーを食べる")]
    assert units == ["東京に", "行きました", "。", "カレーを", "食べる"]


def test_chinese_highlights_a_character_at_a_time_but_keeps_numbers_whole() -> None:
    """Characters and syllables correspond one to one in Chinese, which is not true anywhere
    else here — so per-character is honest granularity rather than a shortcut. Splitting
    "2026" into four units would be wrong in any script."""
    units = [u.text for u in get_pack("zh").tokenize("光速是每秒30万公里")]
    assert units == ["光", "速", "是", "每", "秒", "30", "万", "公", "里"]


def test_a_full_stop_never_starts_a_line() -> None:
    """Kinsoku shori. A width-only break puts 。 alone at the head of a line, which reads as
    broken typography to anyone who reads the script and is invisible to anyone who does not.
    """
    # Nine columns would put the 。 at position ten, i.e. first on the next line.
    lines = get_pack("ja").linebreak("東京に行きました。カレーを食べる", 9)
    assert not any(line[0] in cjk.NEVER_STARTS for line in lines), lines
    assert lines[0].endswith("。")


def test_an_opening_bracket_never_ends_a_line() -> None:
    lines = cjk.wrap(["あ", "い", "う", "「", "え", "お"], 4)
    assert not any(line[-1] in cjk.NEVER_ENDS for line in lines), lines


def test_kinsoku_never_empties_a_line_to_satisfy_itself() -> None:
    """The adjustment moves a break by one unit. Applied without a floor it can strip a line
    to nothing, which would drop a caption entirely."""
    lines = cjk.wrap(["。", "。", "。", "。"], 1)
    assert all(line for line in lines)
    assert "".join(lines) == "。。。。"


def test_dense_scripts_are_given_more_time_to_read() -> None:
    """Reading speed varies by roughly three times across these six, which is why it lives in
    the pack. A Chinese caption timed at English speed is gone before it has been read."""
    speeds = {p.code: p.typography.cps for p in packs()}
    assert speeds["zh"] < speeds["ja"] < speeds["ko"] < speeds["en"]
    assert speeds["en"] / speeds["zh"] > 2


def test_stacked_and_full_square_scripts_get_taller_lines() -> None:
    """Burmese stacks marks above and below; CJK glyphs fill the square with no descender room
    to borrow. Both collide with the line above at Latin line-height."""
    latin = get_pack("en").typography.line_height
    for code in ("my", "th", "ja", "zh", "ko"):
        assert get_pack(code).typography.line_height > latin, code


def test_no_pack_asks_for_tracking_on_a_script_that_breaks_under_it() -> None:
    """Letter-spacing detaches Burmese stacked marks from their base and reads as broken
    spacing on CJK."""
    for code in ("my", "th", "ja", "zh", "ko"):
        assert get_pack(code).typography.letter_spacing_em == 0.0, code


def test_scripts_without_case_say_so() -> None:
    """`design-brief.md` calls this out: a layout leaning on capitalised labels breaks outside
    English, and `text-transform` on these is at best a no-op and at worst corrupting."""
    for code in ("my", "th", "ja", "zh", "ko"):
        assert get_pack(code).typography.has_case is False, code


def test_burmese_refuses_zawgyi_rather_than_rendering_it_as_nonsense() -> None:
    """Zawgyi occupies the same code points as Unicode Burmese, so it draws as fluent-looking
    nonsense — the kind of failure that survives review by anyone who does not read Burmese."""
    from lumina.intelligence.languages.my import ZawgyiTextError

    with pytest.raises(ZawgyiTextError, match="Zawgyi"):
        get_pack("my").normalize("ျမန္မာစာ ၠၡၢ")


def test_every_pack_names_itself_in_its_own_script() -> None:
    """Adding a language used to need matching entries in four separate dictionaries. It now
    needs one file, and this is what would have caught the language that shipped showing its
    BCP-47 code as its name."""
    for pack in packs():
        assert pack.endonym.strip(), pack.code
        assert pack.english_name.strip(), pack.code
        assert pack.endonym != pack.code, f"{pack.code} is showing its code as its name"
