"""The check that a translation actually reached the target language.

Every line here is real output from SeamlessM4T v2 into Burmese, kept verbatim. That matters:
the failure this guards against is not a garbled string that any check would catch, it is
fluent output with the source language woven through it, and only real samples show that.
"""

from __future__ import annotations

import pytest

from lumina.intelligence.translate import _leaked

# What the model actually returned, translating into Burmese.
CLEAN_FROM_ENGLISH = [
    "အဲဒီညက ဖြစ်ပျက်ခဲ့တာကို ဘယ်သူ့ကိုမှ မပြောခဲ့ဘူး။",
    "ကွန်ပျူတာ setting များကိုဖွင့်ပြီး Install ကိုနှိပ်ပါ။",
]
LEAKING_FROM_JAPANESE = [
    "ဘယ်သူ့ကိုမှ မပြောさなかった",
    "ရှစ်分かかる",
    "怪傷をするぞ",
]
LEAKING_FROM_KOREAN = [
    "ချက်ချင်း적이지는 않다",
    "မင်းကို 위해서 လုပ် တာ က မဟုတ်야 ။",
]


@pytest.mark.parametrize("line", CLEAN_FROM_ENGLISH)
def test_a_real_translation_passes(line: str) -> None:
    assert not _leaked(line, source="en", target="my")


@pytest.mark.parametrize("line", LEAKING_FROM_JAPANESE)
def test_untranslated_japanese_is_caught(line: str) -> None:
    assert _leaked(line, source="ja", target="my")


@pytest.mark.parametrize("line", LEAKING_FROM_KOREAN)
def test_untranslated_korean_is_caught(line: str) -> None:
    assert _leaked(line, source="ko", target="my")


def test_a_kept_english_term_is_not_leakage() -> None:
    """The glossary exists to leave these in place — the check must not undo it."""
    assert not _leaked("Computer ကိုဖွင့်ပြီး Install ကိုနှိပ်ပါ။", source="en", target="my")


def test_shared_han_is_not_leakage_but_kana_is() -> None:
    """Japanese and Chinese share Han, so only what is *uniquely* Japanese can be evidence.

    Han passing through untouched is what a correct translation between these two looks like;
    kana surviving into Chinese output is the model giving up mid-sentence.
    """
    assert not _leaked("我是学生", source="ja", target="zh")
    assert _leaked("私は学生です", source="ja", target="zh")


def test_a_single_quoted_character_is_tolerated() -> None:
    """One proper noun in a long line is not a failure to translate."""
    line = "ဂျပန်စာလုံး 山 ကို ဆိုလိုတာက တောင် ဖြစ်ပါတယ် လို့ ဆရာက ရှင်းပြခဲ့ပါတယ်။"
    assert not _leaked(line, source="ja", target="my")


def test_an_empty_line_is_not_a_failure() -> None:
    assert not _leaked("", source="ja", target="my")
    assert not _leaked("   ", source="ja", target="my")


def test_an_unknown_language_does_not_raise() -> None:
    assert not _leaked("anything", source="xx", target="my")
