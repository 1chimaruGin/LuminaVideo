"""Terms that must survive translation.

"Computer" in a Burmese sentence stays "Computer" — that is how the word is said, and the
dictionary equivalent reads as stilted or plain wrong. Same for product names, brands,
acronyms and most technical vocabulary. A dropped term is invisible to anyone who cannot read
the target language, which is exactly why it is checked rather than hoped for.
"""

from __future__ import annotations

import pytest

from lumina.intelligence.translate import ClaudeTranslator, _glossary, _holds


@pytest.mark.parametrize(
    ("text", "term", "kept"),
    [
        ("Computer ကို သုံးပါ။", "Computer", True),
        ("ကွန်ပျူတာကို သုံးပါ။", "Computer", False),
        # A word boundary, not a substring: the plural is a different word, and a translation
        # that produced it has not kept the term.
        ("computers are fast", "Computer", False),
        ("A Computer, and a phone", "computer", True),
        ("Use the API today", "API", True),
        # The failure a naive substring check makes: "API" inside "RAPIDLY".
        ("RAPIDLY changing", "API", False),
        ("", "Computer", False),
        ("anything", "", False),
    ],
)
def test_a_term_survives_only_as_a_whole_word(text: str, term: str, kept: bool) -> None:
    assert _holds(text, term) is kept


def test_a_non_latin_term_matches_as_a_substring() -> None:
    """Burmese and Thai have no word boundaries, so there is nothing to anchor against. The
    terms that matter in those languages are Latin loanwords anyway, which do."""
    assert _holds("မြန်မာနိုင်ငံ", "မြန်မာ") is True


def test_the_prompt_names_the_terms_and_what_not_to_do_with_them() -> None:
    block = _glossary(("Computer", "API"), None)
    assert '"Computer"' in block and '"API"' in block
    # Naming the failure modes matters: "do not translate" alone still lets a model decline
    # a loanword into the target's grammar, which is the common way a term half-survives.
    assert "transliterate" in block and "inflect" in block


def test_a_retry_names_only_what_actually_went_missing() -> None:
    block = _glossary(("Computer", "API", "GPU"), ["Computer"])
    assert 'dropped "Computer"' in block
    assert '"API"' in block, "the full list is still there"
    assert 'dropped "API"' not in block


@pytest.mark.anyio
async def test_a_dropped_term_is_re_asked_rather_than_shipped(monkeypatch) -> None:
    """The whole point of checking. A model that translates "Computer" into the target on the
    first pass gets one more chance, with the term named."""
    tries: list[list[str]] = []

    async def flaky(self, lines, *, source, target, keep, insist, whole=False):
        tries.append(list(lines))
        if insist is None:
            # First pass: translates the term, which is the mistake.
            return [line.replace("Computer", "ကွန်ပျူတာ") for line in lines]
        return [line.replace("Computer", "Computer") for line in lines]

    monkeypatch.setattr(ClaudeTranslator, "_ask", flaky)

    out = await ClaudeTranslator().translate(
        ["Use a Computer.", "No term here."],
        source="en",
        target="my",
        keep=("Computer",),
    )
    assert len(tries) == 2, "one retry"
    assert tries[1] == ["Use a Computer."], "only the line that lost the term is re-asked"
    assert out == ["Use a Computer.", "No term here."]


@pytest.mark.anyio
async def test_nothing_is_re_asked_when_every_term_survived(monkeypatch) -> None:
    tries: list[object] = []

    async def clean(self, lines, *, source, target, keep, insist, whole=False):
        tries.append(insist)
        return list(lines)

    monkeypatch.setattr(ClaudeTranslator, "_ask", clean)
    await ClaudeTranslator().translate(
        ["Use a Computer."], source="en", target="my", keep=("Computer",)
    )
    assert tries == [None], "one call, no retry"


@pytest.mark.anyio
async def test_a_term_the_source_never_used_is_not_demanded_of_the_output(monkeypatch) -> None:
    """Otherwise every line would be re-asked for every term in a long glossary."""
    tries: list[object] = []

    async def clean(self, lines, *, source, target, keep, insist, whole=False):
        tries.append(insist)
        return ["ဘာသာပြန်ထားသည်။"]

    monkeypatch.setattr(ClaudeTranslator, "_ask", clean)
    await ClaudeTranslator().translate(
        ["A line with no terms in it."], source="en", target="my", keep=("Computer", "GPU")
    )
    assert tries == [None]
