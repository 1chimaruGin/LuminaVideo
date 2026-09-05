"""Run the same lines through every configured translator and show them side by side.

The companion to `asr_bench.py`, and it exists for the same reason: the published numbers
stop being comparable exactly where this product is hardest. BURMESE-SAN measures English
and Burmese in both directions; nothing published measures Japanese, Chinese or Korean *into*
Burmese, which is most of the real workload. So the answer is measured here.

Two things are reported, and the second is the one that matters:

  - **What each engine said**, so a person who reads the target can judge it. No automatic
    metric replaces that, which is why the transcripts are printed and not just a score.
  - **Whether the output is in the target language at all.** A model translating into a
    low-resource language sometimes stops part way and copies the source through; the result
    is fluent, correctly punctuated and completely wrong. SeamlessM4T leaves 22% of Japanese
    untranslated going into Burmese. That is invisible to a reviewer who does not read
    Burmese and obvious to a codepoint range, so it is checked rather than eyeballed.

    uv run python scripts/translate_bench.py lines.txt --source ja --target my

With `--keep` it also checks the glossary held.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path

from lumina.intelligence.languages import supported
from lumina.intelligence.translate import (
    ClaudeTranslator,
    GeminiTranslator,
    Translator,
    _leaked,
)

ENGINES: dict[str, type[Translator]] = {
    "gemini": GeminiTranslator,
    "claude": ClaudeTranslator,
}


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lines", type=Path, help="one source line per line, as captions are")
    parser.add_argument("--source", required=True, choices=supported())
    parser.add_argument("--target", required=True, choices=supported())
    parser.add_argument("--engines", nargs="*", default=list(ENGINES))
    parser.add_argument(
        "--keep", nargs="*", default=[], help="terms that must survive untranslated"
    )
    args = parser.parse_args()

    lines = [ln for ln in args.lines.read_text(encoding="utf-8").splitlines() if ln.strip()]
    keep = tuple(args.keep)
    print(f"{len(lines)} line(s) · {args.source} → {args.target}\n")

    for name in args.engines:
        make = ENGINES.get(name)
        if make is None:
            print(f"── {name}: unknown engine\n")
            continue
        started = time.monotonic()
        try:
            out = await make().translate(lines, source=args.source, target=args.target, keep=keep)
        except Exception as exc:  # a bench reports failures rather than raising on them
            print(f"── {name}: unavailable — {type(exc).__name__}: {str(exc)[:96]}\n")
            continue
        took = time.monotonic() - started

        leaked = [
            i for i, ln in enumerate(out) if _leaked(ln, source=args.source, target=args.target)
        ]
        verdict = "clean" if not leaked else f"{len(leaked)}/{len(out)} lines still in the source"
        print(f"── {name}   {took:4.1f}s   {verdict}")
        for src, got in zip(lines, out, strict=True):
            print(f"   {src}")
            print(f"     → {got}")
        print()

    print("Read the output, not just the verdict: the check can tell you a line was not")
    print("translated. It cannot tell you a translated line means the wrong thing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
