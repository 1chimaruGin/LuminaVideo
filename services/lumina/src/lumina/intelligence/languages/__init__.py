"""Language pack registry.

The registry is the ONLY place that maps a BCP-47 code to an implementation. Callers ask for
a pack and get a `LanguagePack`; they never name one.

**Adding a language is one file and one line here.** Everything else that used to need a
matching entry somewhere — what it calls itself, its English name, which font must be
fetched — now lives on the pack, because four parallel dictionaries is four chances to add a
language that renders as boxes or shows up in the picker with its code for a name.

Popularity is the exception, and it is deliberately not on the pack: which languages creators
actually shoot in, and which they translate into, is a fact about this product's audience
rather than about the language. It lives below, and it only orders the picker — every pack is
selectable on both sides.
"""

from __future__ import annotations

from collections.abc import Iterable

from lumina.intelligence.languages.base import LanguagePack, split_sentences
from lumina.intelligence.languages.en import EnglishPack
from lumina.intelligence.languages.ja import JapanesePack
from lumina.intelligence.languages.ko import KoreanPack
from lumina.intelligence.languages.my import BurmesePack
from lumina.intelligence.languages.th import ThaiPack
from lumina.intelligence.languages.zh import ChinesePack

_PACKS: dict[str, LanguagePack] = {
    "en": EnglishPack(),
    "ja": JapanesePack(),
    "zh": ChinesePack(),
    "ko": KoreanPack(),
    "my": BurmesePack(),
    "th": ThaiPack(),
}

DEFAULT_LANGUAGE = "en"

#: What creators shoot in, most common first. Orders the "spoken in" picker so the answer is
#: usually the first or second row rather than somewhere down an alphabetical list.
COMMON_SOURCES = ("en", "ja", "zh", "ko")

#: What they translate into. Overlaps with the above on purpose — Japanese is both a language
#: people film in and one they subtitle into, and English is both for almost everyone.
COMMON_TARGETS = ("en", "my", "ja", "th")


class UnsupportedLanguageError(KeyError):
    def __init__(self, code: str) -> None:
        super().__init__(f"no language pack for {code!r}; have {sorted(_PACKS)}")
        self.code = code


def get_pack(code: str) -> LanguagePack:
    try:
        return _PACKS[code]
    except KeyError:
        raise UnsupportedLanguageError(code) from None


def supported() -> list[str]:
    return sorted(_PACKS)


def detect(text: str) -> str | None:
    """Which of the supported languages this line is written in, by script.

    Gemini transcribes a code-switched file correctly — Japanese speech comes back in
    Japanese and an English song in English — but it does not label which line is which, and
    a translator has to be told what it is reading. The packs already declare the codepoint
    ranges each language is written in, so the label is recovered from the text itself.

    Two rules, both there because the naive count gets real lines wrong:

    *Exclusive evidence decides.* Han is written in Chinese and in Japanese, so counting every
    character it matches makes a Chinese sentence score equally as both and the tie falls to
    whichever pack was registered first. Kana is Japanese and nothing else; Hangul is Korean
    and nothing else. Those characters are the ones that actually identify a line, so they are
    counted first and shared scripts only break a tie between packs that have none.

    *Latin loses to anything else present.* `Computer ကိုဖွင့်ပါ` is a Burmese line — the
    glossary exists precisely to leave English technical terms sitting inside other scripts —
    and by raw character count the English half can win. A non-Latin script appearing inside
    an English line is rare; the reverse is constant.

    `None` means no script won, which is what a line of digits or punctuation should give.
    """
    hits: dict[str, int] = {}
    alone: dict[str, int] = {}
    for ch in text:
        if not ch.isalpha():
            continue
        claimed = [
            pack.code
            for pack in packs()
            if any(lo <= ord(ch) <= hi for lo, hi in pack.typography.script)
        ]
        for code in claimed:
            hits[code] = hits.get(code, 0) + 1
        # A character only one language is written with is what actually identifies a line:
        # kana is Japanese and nothing else, Hangul is Korean and nothing else. Han is claimed
        # by Chinese and Japanese alike, so it identifies neither and is only a tie-break.
        if len(claimed) == 1:
            alone[claimed[0]] = alone.get(claimed[0], 0) + 1
    if not hits:
        return None

    ranked = alone or hits
    if len(ranked) > 1:
        #: Latin is the fallback script, never the winner while another is present.
        ranked = {c: n for c, n in ranked.items() if c != "en"} or ranked
    if len(ranked) > 1 and not alone:
        #: Only reachable on shared script with no exclusive evidence at all — Han without
        #: kana, which is Chinese. Japanese borrowed the script, and a Japanese sentence of
        #: any length shows kana.
        ranked = {c: n for c, n in ranked.items() if c != "ja"} or ranked
    return max(ranked.items(), key=lambda kv: kv[1])[0]


def detect_all(lines: Iterable[str]) -> list[str | None]:
    """Which language each line is in, resolved against the rest of the file.

    `detect` sees one line and nothing else, which is not enough for a script two languages
    share. `煉獄杏寿郎。` is a Japanese name written in kanji with no kana beside it, so on its
    own the honest answer is Chinese — Han with no exclusive evidence — and in a file whose
    other lines are plainly Japanese that answer is simply wrong. It is not a cosmetic error:
    the line was then translated *from Chinese*.

    So: lines that identify themselves decide what languages the file contains, and lines that
    cannot are read as the language already present that could have written them. A file that
    really is Chinese still reads as Chinese, because then nothing Japanese identifies itself.
    """
    said = list(lines)
    first = [detect(line) for line in said]

    # What the file demonstrably contains — from lines carrying script only one language uses.
    sure = {
        code
        for line, code in zip(said, first, strict=True)
        if code is not None and _has_exclusive(line, code)
    }
    if not sure:
        return first

    out: list[str | None] = []
    for line, code in zip(said, first, strict=True):
        if code is None or code in sure or _has_exclusive(line, code):
            out.append(code)
            continue
        # Ambiguous. Prefer a language the file is already known to be in and whose script
        # could have produced this line; otherwise keep the line's own answer.
        shared = [c for c in sure if _writes(c, line)]
        out.append(shared[0] if len(shared) == 1 else code)
    return out


def _writes(code: str, text: str) -> bool:
    """Whether every letter in `text` falls inside this language's script."""
    ranges = get_pack(code).typography.script
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and all(
        any(lo <= ord(c) <= hi for lo, hi in ranges) or c.isascii() for c in letters
    )


def _has_exclusive(text: str, code: str) -> bool:
    """Whether the line carries a character only `code` is written with."""
    for ch in text:
        if not ch.isalpha():
            continue
        claimed = [
            pack.code
            for pack in packs()
            if any(lo <= ord(ch) <= hi for lo, hi in pack.typography.script)
        ]
        if claimed == [code]:
            return True
    return False


def packs() -> list[LanguagePack]:
    """Every pack, ordered for a picker: the common answers first, the rest alphabetically.

    Both lists are unioned rather than kept separate, because one ordering serving two pickers
    is one thing to keep right. A picker that wants only sources sorts by `rank`.
    """
    order = [*COMMON_SOURCES, *(c for c in COMMON_TARGETS if c not in COMMON_SOURCES)]
    ranked = [_PACKS[c] for c in order if c in _PACKS]
    rest = [_PACKS[c] for c in sorted(_PACKS) if c not in order]
    return [*ranked, *rest]


__all__ = [
    "COMMON_SOURCES",
    "COMMON_TARGETS",
    "DEFAULT_LANGUAGE",
    "LanguagePack",
    "UnsupportedLanguageError",
    "detect",
    "get_pack",
    "packs",
    "split_sentences",
    "supported",
]
