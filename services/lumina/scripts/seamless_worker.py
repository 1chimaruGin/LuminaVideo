"""SeamlessM4T v2 as a transcriber, run out of process.

Meta's model, open weights, 2.3B parameters. It is here because Burmese is the language this
product cannot buy its way out of: every hosted option is either untested on it or reports a
number that is not comparable to any other number, and a model you can run is a model you can
measure.

**Two things make it awkward, and both are handled here rather than pretended away.**

*It lives in a different interpreter.* The service runs on Python 3.12 under `uv`; torch and
transformers live in a conda environment with the CUDA build that matches this machine.
Rather than force one to move, this is a worker: it reads a job on stdin, writes segments on
stdout as JSON, and the adapter in `execution/transcribe.py` runs it as a subprocess. The
model stays loaded for the life of the process, so a caller that transcribes several files
pays the twenty-second load once.

*It has no timestamps.* Seamless is sequence-to-sequence — it returns a sentence, not an
alignment, and no amount of asking will get word times out of it. Timings therefore come from
**where the speech is**, not from the model: the audio is split on silence, each stretch is
transcribed on its own, and each stretch's text takes that stretch's start and end. That is
honest — the caption appears exactly while someone is speaking — and it is the only thing
available. Handing back one caption for the whole file, or inventing offsets by counting
characters, would both be worse.

The splitting itself lives in `lumina.execution.speech`, on numpy rather than torch, so the
one piece of load-bearing logic here is tested by the ordinary suite instead of being skipped
wherever torch is absent — which is everywhere except this environment.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# This runs under a different interpreter from the service — the one that has torch — so the
# package is not on its path. It is on disk right beside this file, though, and the segmenter
# lives there rather than here so that it can be tested without torch.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import torch
import torchaudio

from lumina.execution.speech import SAMPLE_RATE, find_speech

#: BCP-47 to the three-letter codes Seamless uses.
#: Greedy decoding. Beam search at width 5 was three times slower and produced *character for
#: character identical* Burmese on every clip of the OpenSLR sample — so it is not paid for by
#: default. Raise it if harder audio turns out to benefit; the measurement is a `--engines
#: seamless` run away.
BEAMS = 1

LANGS = {
    "en": "eng",
    "ja": "jpn",
    "zh": "cmn",
    "ko": "kor",
    "my": "mya",
    "th": "tha",
}


def main() -> int:
    from transformers import AutoProcessor, SeamlessM4Tv2ForSpeechToText

    job = json.loads(sys.stdin.read())
    model_dir = job["model_dir"]
    audio_path = job["audio"]
    language = job["language"]

    tgt = LANGS.get(language)
    if tgt is None:
        print(json.dumps({"error": f"seamless has no code for {language!r}"}), flush=True)
        return 1

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32
    processor = AutoProcessor.from_pretrained(model_dir)
    model = SeamlessM4Tv2ForSpeechToText.from_pretrained(model_dir, dtype=dtype).to(device)
    model.eval()

    wave, rate = torchaudio.load(audio_path)
    wave = wave.mean(dim=0)  # mono
    if rate != SAMPLE_RATE:
        wave = torchaudio.functional.resample(wave, rate, SAMPLE_RATE)

    stretches = find_speech(wave.numpy())
    segments: list[dict[str, object]] = []

    for stretch in stretches:
        a = int(stretch.start_ms / 1000 * SAMPLE_RATE)
        b = int(stretch.end_ms / 1000 * SAMPLE_RATE)
        clip = wave[a:b]
        if clip.numel() < SAMPLE_RATE // 10:
            continue

        inputs = processor(audio=clip.numpy(), sampling_rate=SAMPLE_RATE, return_tensors="pt")
        inputs = {
            k: v.to(device=device, dtype=dtype if v.is_floating_point() else v.dtype)
            for k, v in inputs.items()
        }
        with torch.inference_mode():
            tokens = model.generate(**inputs, tgt_lang=tgt, num_beams=BEAMS, max_new_tokens=256)
        # `clean_up_tokenization_spaces` is a WordPiece post-step and is destructive for this
        # BPE tokenizer — it strips spaces before punctuation, which in a script that does not
        # use spaces the way English does is silent corruption.
        text = processor.decode(
            tokens[0].tolist(), skip_special_tokens=True, clean_up_tokenization_spaces=False
        ).strip()
        if text:
            segments.append(
                {
                    "text": text,
                    "start_ms": stretch.start_ms,
                    "duration_ms": stretch.end_ms - stretch.start_ms,
                }
            )

    print(json.dumps({"segments": segments, "device": device}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
