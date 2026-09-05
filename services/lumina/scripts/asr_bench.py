"""Run one audio file through every configured engine and score them against a reference.

This exists because the published numbers cannot answer "which engine is best for Burmese",
and reading harder does not fix that:

  - Burmese, Thai, Lao, Khmer, Chinese and Japanese have no word boundaries, so Whisper's own
    evaluation reports **character** error rate for them. Much of the academic Burmese
    literature reports **word** error rate after a segmentation step of its own choosing.
    A 3% and an 80% from those two sources are not on the same axis.
  - Vendor figures are on FLEURS, which is clean read speech by professional voice talent.
    A creator's uploaded video is not that.
  - Burmese in particular has so little published head-to-head data that any ranking taken
    from a blog post is a guess wearing a number.

So: bring your own audio and your own transcript, and the answer is measured rather than
argued. The metric is chosen the same way everything else in this codebase chooses one — from
the language pack, which already knows whether the script has word boundaries.

    uv run python scripts/asr_bench.py clip.wav --language my --reference truth.txt

With no reference it still runs every engine and prints what each heard, side by side, which
is enough to judge a language you can read.
"""

from __future__ import annotations

import argparse
import asyncio
import time
import unicodedata
from pathlib import Path

from lumina.execution.transcribe import (
    Gemini,
    GoogleChirp,
    HostedWhisper,
    LocalWhisper,
    Scribe,
    Seamless,
    Segment,
    Transcriber,
    VertexGemini,
)
from lumina.intelligence.languages import get_pack, supported

ENGINES: dict[str, type[Transcriber]] = {
    "whisper": HostedWhisper,
    "scribe": Scribe,
    "google": GoogleChirp,
    "gemini": Gemini,
    "vertex": VertexGemini,
    "seamless": Seamless,
    "local-whisper": LocalWhisper,
}


def normalise(text: str, *, spaced: bool) -> str:
    """Fold the differences that are not mistakes.

    Case, punctuation and Unicode form are not what is being measured, and a Burmese
    transcript in a different normalisation form would otherwise score as entirely wrong. For
    a script written continuously, whitespace is not meaningful either.
    """
    folded = unicodedata.normalize("NFC", text).strip().lower()
    kept = "".join(ch for ch in folded if not unicodedata.category(ch).startswith("P"))
    return " ".join(kept.split()) if spaced else "".join(kept.split())


def edits(a: list[str] | str, b: list[str] | str) -> int:
    """Levenshtein distance, over words or characters depending on what is passed."""
    if len(a) < len(b):
        a, b = b, a
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        nxt = [i]
        for j, y in enumerate(b, 1):
            nxt.append(min(row[j] + 1, nxt[j - 1] + 1, row[j - 1] + (x != y)))
        row = nxt
    return row[-1]


def score(heard: str, truth: str, *, spaced: bool) -> tuple[str, float]:
    """Error rate, and which metric it is.

    Word error rate where the script has word boundaries; character error rate where it does
    not. Using WER on Burmese would measure our segmenter as much as the engine — which is
    exactly the confusion that makes the published figures unusable.
    """
    h = normalise(heard, spaced=spaced)
    t = normalise(truth, spaced=spaced)
    if not t:
        return ("—", 0.0)
    if spaced:
        return ("WER", edits(h.split(), t.split()) / max(1, len(t.split())))
    return ("CER", edits(h, t) / max(1, len(t)))


async def run(engine: Transcriber, audio: Path, language: str) -> tuple[list[Segment], float]:
    started = time.monotonic()
    out = await engine.transcribe(audio, language=language, duration_ms=0)
    return out, time.monotonic() - started


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "audio", type=Path, nargs="+", help="mono 16 kHz wavs, as the pipeline produces"
    )
    parser.add_argument("--language", required=True, choices=supported())
    parser.add_argument(
        "--reference",
        type=Path,
        nargs="*",
        help="what was actually said. One per clip, or omit and a sibling .txt is used.",
    )
    parser.add_argument(
        "--engines", nargs="*", default=None, help="default: this language's, then the rest"
    )
    args = parser.parse_args()

    pack = get_pack(args.language)
    spaced = pack.typography.space_between_units

    # One reference per clip: given on the command line, or the clip's own `.txt` beside it,
    # which is how a corpus is usually laid out.
    if args.reference:
        truths = [p.read_text(encoding="utf-8").strip() for p in args.reference]
    else:
        truths = [
            (
                a.with_suffix(".txt").read_text(encoding="utf-8").strip()
                if a.with_suffix(".txt").exists()
                else ""
            )
            for a in args.audio
        ]

    order = args.engines or [*pack.asr.engines, *(e for e in ENGINES if e not in pack.asr.engines)]

    print(
        f"{len(args.audio)} clip(s) · {pack.english_name} · scoring by "
        f"{'word' if spaced else 'character'} error rate\n"
    )

    # Engine -> its rate on each clip, so the summary is over the set rather than one file.
    tally: dict[str, list[float]] = {}

    for clip, truth in zip(args.audio, truths, strict=True):
        print(f"── {clip.name}")
        if truth:
            print(f"   truth   {truth}")
        for name in order:
            make = ENGINES.get(name)
            if make is None:
                print(f"   {name:<14} unknown engine")
                continue
            try:
                out, took = await run(make(), clip, args.language)
            except Exception as exc:  # a bench reports failures rather than raising on them
                print(f"   {name:<14} unavailable: {type(exc).__name__}: {str(exc)[:64]}")
                continue

            heard = (" " if spaced else "").join(s.text for s in out)
            if truth:
                metric, rate = score(heard, truth, spaced=spaced)
                tally.setdefault(name, []).append(rate)
                head = f"{metric} {rate:5.1%}"
            else:
                head = "         "
            print(f"   {name:<14} {head}  {took:5.1f}s  {heard}")
        print()

    if tally:
        print("average over the set:")
        for name, rates in sorted(tally.items(), key=lambda kv: sum(kv[1]) / len(kv[1])):
            mean = sum(rates) / len(rates)
            print(f"   {name:<14} {mean:6.1%}   ({len(rates)} clip(s))")
        print("\nRead the transcripts, not just the number: an error rate cannot tell you")
        print("whether a mistake is a misheard particle or a sentence that means something else.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
