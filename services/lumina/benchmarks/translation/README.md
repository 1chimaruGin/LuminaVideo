# Parallel lines for translation benchmarking

Six sentences, the same six in English, Chinese, Japanese and Korean, for measuring
translation *into* Burmese — the direction this product runs and the one no published
benchmark covers. BURMESE-SAN scores English and Burmese in both directions; nothing scores
Japanese, Chinese or Korean into Burmese, so this is measured rather than assumed.

They are short on purpose, and each one is chosen to break something different:

1. A plain past-tense sentence with an indefinite object — the control.
2. Two clauses joined by a contrast, with a number and a unit.
3. An instruction containing English technical terms that must survive untranslated.
4. A conditional with an implied threat — register, not vocabulary.
5. A number that is large, approximate, and attached to a date.
6. Two short sentences with a pronoun in the second whose referent is in the first, which
   is the case line-by-line translation is least able to resolve.

Run them with:

    uv run python scripts/translate_bench.py benchmarks/translation/ja.txt \
        --source ja --target my --keep Computer Install

SeamlessM4T v2, measured here in September 2026, was clean on English but left 22% of the
Japanese and 12% of the Korean untranslated in its Burmese output. That is the failure the
bench's script check exists to catch.
