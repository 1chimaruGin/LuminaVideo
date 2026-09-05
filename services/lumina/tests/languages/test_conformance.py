"""Language pack conformance suite.

Every registered pack runs every test here. This is what keeps invariant 1 honest: if the
interface only works for English, that is a bug in the interface, and this suite is where it
surfaces — not three weeks later inside a renderer.

Adding pack #2 (Burmese) means adding "my" to the registry and making these pass. Nothing
else in the codebase should need to change. If it does, the abstraction leaked.
"""

from __future__ import annotations

import pytest

from lumina.intelligence.languages import get_pack, supported
from lumina.intelligence.languages.base import LanguagePack

#: Per-language fixtures. Never Latin placeholder text for a non-Latin pack — the whole
#: point is to exercise the script's real behaviour.
SAMPLES: dict[str, str] = {
    "en": "The James Webb telescope sees light that left its source before Earth existed.",
    "my": "ဂျိမ်းစ်ဝဘ်တယ်လီစကုပ်သည် ကမ္ဘာမြေမတည်ရှိမီကထွက်ခဲ့သောအလင်းကိုမြင်နိုင်သည်။",
    "th": "กล้องโทรทรรศน์เจมส์เวบบ์มองเห็นแสงที่ออกจากต้นทางก่อนโลกจะถือกำเนิด",
    "ja": "ジェイムズウェッブ望遠鏡は地球が生まれる前に出発した光を見ています。",
    "zh": "詹姆斯韦布望远镜看到的光在地球诞生之前就已出发。",
    "ko": "제임스 웹 망원경은 지구가 생기기 전에 출발한 빛을 봅니다",
}

PACKS = [pytest.param(get_pack(code), id=code) for code in supported()]


@pytest.fixture(params=PACKS)
def pack(request: pytest.FixtureRequest) -> LanguagePack:
    return request.param


def sample_for(pack: LanguagePack) -> str:
    if pack.code not in SAMPLES:
        pytest.fail(f"pack {pack.code!r} has no sample text in SAMPLES — add one")
    return SAMPLES[pack.code]


def test_pack_declares_a_bcp47_code(pack: LanguagePack) -> None:
    assert pack.code
    assert pack.code == pack.code.strip()


def test_normalize_is_idempotent(pack: LanguagePack) -> None:
    once = pack.normalize(sample_for(pack))
    assert pack.normalize(once) == once, "normalize must be a fixed point after one pass"


def test_tokenize_covers_the_string_without_overlap(pack: LanguagePack) -> None:
    """Units must be ordered, non-overlapping, and within bounds. The caption animator
    highlights unit ranges; overlapping units make it flicker."""
    text = pack.normalize(sample_for(pack))
    units = pack.tokenize(text)
    assert units, "tokenize returned nothing"

    prev_end = 0
    for u in units:
        assert 0 <= u.start < u.end <= len(text), f"unit out of bounds: {u}"
        assert u.start >= prev_end, f"units overlap or are unordered at {u}"
        assert text[u.start : u.end] == u.text, "unit offsets must index back to unit text"
        prev_end = u.end


def test_linebreak_respects_width_where_it_can(pack: LanguagePack) -> None:
    text = pack.normalize(sample_for(pack))
    lines = pack.linebreak(text, width=24)
    assert lines
    for line in lines:
        # A single unit longer than the width is allowed to overflow; nothing else is.
        assert len(line) <= 24 or len(pack.tokenize(line)) == 1, f"line too long: {line!r}"


def test_linebreak_preserves_content(pack: LanguagePack) -> None:
    text = pack.normalize(sample_for(pack))
    rejoined = "".join(pack.linebreak(text, width=24))
    assert len(rejoined.replace(" ", "")) == len(text.replace(" ", "")), "line breaking lost text"


def test_linebreak_of_empty_text_is_empty(pack: LanguagePack) -> None:
    assert pack.linebreak("", width=24) == []


def test_typography_is_declared_per_language(pack: LanguagePack) -> None:
    t = pack.typography
    assert t.cps > 0, "reading speed varies ~3x by language and must never be hardcoded"
    assert t.font_stack, "a pack must name its font stack"
    assert t.line_height >= 1.0


def test_caption_duration_scales_with_length(pack: LanguagePack) -> None:
    short = pack.caption_duration_ms(sample_for(pack)[:10])
    long = pack.caption_duration_ms(sample_for(pack))
    assert 0 < short <= long


def test_planner_profile_is_present(pack: LanguagePack) -> None:
    """The profile is part of the planner's CACHED prompt prefix, so it must be non-empty
    and stable — a pack that computes it per call silently forfeits the cache discount."""
    assert pack.llm_profile.system_suffix
    assert pack.llm_profile is get_pack(pack.code).llm_profile, "profile must be a constant"
