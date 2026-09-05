"""Which language a transcript line is written in.

Gemini transcribes a code-switched file correctly — Japanese speech comes back in Japanese
and an English song in English — but it does not say which line is which, and the translator
has to be told what it is reading. So the label is recovered from the script.

The two rules here both exist because the obvious character count gets real lines wrong.
"""

from __future__ import annotations

import pytest

from lumina.intelligence.languages import detect


@pytest.mark.parametrize(
    ("want", "line"),
    [
        ("en", "I tried so hard and got so far"),
        ("ja", "彼はあの夜に起きたことを誰にも話さなかった"),
        ("ko", "그는 그날 밤에 일어난 일을 아무에게도 말하지 않았다"),
        ("zh", "这座寺庙建于九百多年前"),
        ("my", "ရင်ထဲက အိပ်မက်တွေ တောက်လောင်နေတယ်"),
        ("th", "แสงเดินทางเร็ว"),
    ],
)
def test_each_language_is_recognised(want: str, line: str) -> None:
    assert detect(line) == want


def test_han_without_kana_is_chinese() -> None:
    """Both languages are written in Han, so a count alone ties and the tie falls to whichever
    pack happened to be registered first — which made every Chinese line Japanese."""
    assert detect("这座寺庙建于九百多年前") == "zh"
    assert detect("打开设置") == "zh"


def test_kana_makes_it_japanese_however_much_han_is_present() -> None:
    """Kana is exclusive to Japanese, so it identifies the line no matter how the counts fall."""
    assert detect("光は速いが") == "ja"
    assert detect("彼は東京駅の近所の会社員です") == "ja"


def test_an_english_term_inside_another_script_does_not_win() -> None:
    """The glossary deliberately leaves English technical terms inside Burmese lines, and by
    raw character count the English half can outnumber the Burmese one."""
    assert detect("Computer ကိုဖွင့်ပါ") == "my"
    assert detect("Install ကို နှိပ်ပါ") == "my"


def test_a_line_with_no_letters_claims_nothing() -> None:
    """Better than guessing: a line of digits belongs to no language, and saying so lets the
    caller fall back to the file's own answer rather than trust a coin flip."""
    assert detect("123 — !?") is None
    assert detect("") is None


def test_a_kanji_only_line_is_japanese_in_a_japanese_file() -> None:
    """The bug a real upload found.

    `煉獄杏寿郎。` is a Japanese name written in kanji with no kana beside it. On its own the
    honest answer is Chinese — Han, with nothing exclusive to Japanese in it — and in a file
    whose other lines are plainly Japanese that answer is wrong twice over: the column is
    mislabelled, and the line gets translated *from Chinese*.
    """
    from lumina.intelligence.languages import detect_all

    lines = ["心を燃やせ。", "限界を超えろ。", "煉獄杏寿郎。", "玖の型。"]
    assert detect("煉獄杏寿郎。") == "zh", "on its own, Han with no kana"
    assert detect_all(lines) == ["ja", "ja", "ja", "ja"]


def test_a_genuinely_chinese_file_stays_chinese() -> None:
    """The other half: nothing here identifies itself as Japanese, so nothing pulls it there."""
    from lumina.intelligence.languages import detect_all

    assert detect_all(["这座寺庙建于九百多年前", "打开设置", "我不是为你做的"]) == [
        "zh",
        "zh",
        "zh",
    ]


def test_a_mixed_file_keeps_both() -> None:
    """Japanese speech under an English song — the file this came from."""
    from lumina.intelligence.languages import detect_all

    lines = ["心を燃やせ。", "煉獄杏寿郎。", "I tried", "so hard", "and got so far."]
    assert detect_all(lines) == ["ja", "ja", "en", "en", "en"]


def test_a_line_that_identifies_itself_is_never_overridden() -> None:
    """Kana is Japanese whatever the rest of the file says."""
    from lumina.intelligence.languages import detect_all

    assert detect_all(["ရင်ထဲက အိပ်မက်", "光は速いが"]) == ["my", "ja"]


def test_chinese_cannot_prove_itself_against_japanese() -> None:
    """A limitation worth stating rather than hiding.

    Japanese identifies itself with kana; Chinese has no script that Japanese does not also
    use, so it can never do the same. In a file that contains kana, a line of bare Han is
    therefore read as Japanese — which is right for the case this was built for (a Japanese
    video whose names are written in kanji) and wrong for a file that genuinely mixes Chinese
    and Japanese prose. That file needs semantics, not codepoints, and nothing here pretends
    otherwise.

    A file with no kana in it at all still reads as Chinese, which is the common case.
    """
    from lumina.intelligence.languages import detect_all

    assert detect_all(["这座寺庙建于九百多年前", "光は速いが"]) == ["ja", "ja"]
    assert detect_all(["这座寺庙建于九百多年前", "打开设置"]) == ["zh", "zh"]
