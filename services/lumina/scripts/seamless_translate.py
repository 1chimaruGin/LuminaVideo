"""SeamlessM4T v2 as a translator, run out of process.

The same checkpoint the transcriber uses — it carries a text encoder and decoder as well as a
speech encoder — so translating costs no extra download and no extra VRAM beyond loading a
different head.

It is here for the same reason the transcriber is: the languages this product actually
translates *into* are the ones a hosted API is least likely to have been measured on. English,
Chinese, Japanese and Korean into Burmese is the real workload, and Burmese is where the
published numbers stop being comparable to each other. A model that runs here can be read by
someone who speaks the language.

One line in, one line out, in order. That is not a nicety: every caption has a start and a
duration taken from the video, so a translator that merges two lines or splits one puts every
later caption against speech it does not belong to. Each line is translated on its own, which
guarantees the count by construction rather than by asking a model to respect it.

The cost of that guarantee is real and worth naming: translating line by line means the model
never sees the sentence before or after, so a pronoun whose referent was established two
captions ago has nothing to resolve against. A subtitle track is unusually forgiving of this —
lines are short, and the picture carries most of the context — but it is a limitation, not a
free lunch.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import torch
from transformers import AutoProcessor, SeamlessM4Tv2ForTextToText

#: BCP-47 to the three-letter codes Seamless uses. Kept beside the speech worker's copy
#: rather than shared: they are the same table today and there is no reason they must stay
#: that way — a language Seamless can read but not write would belong in one and not the other.
LANGS = {
    "en": "eng",
    "ja": "jpn",
    "zh": "cmn",
    "ko": "kor",
    "my": "mya",
    "th": "tha",
}

#: Greedy, matching the transcriber. Worth re-measuring for translation specifically — beam
#: search earns its keep more often in generation than in recognition — but not assumed.
BEAMS = 1


def main() -> int:
    job = json.loads(sys.stdin.read())
    src = LANGS.get(job["source"])
    tgt = LANGS.get(job["target"])
    if src is None or tgt is None:
        print(json.dumps({"error": f"seamless has no code for {job['source']}/{job['target']}"}))
        return 1

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32
    processor = AutoProcessor.from_pretrained(job["model_dir"])
    model = SeamlessM4Tv2ForTextToText.from_pretrained(job["model_dir"], dtype=dtype).to(device)
    model.eval()

    out: list[str] = []
    for line in job["lines"]:
        if not line.strip():
            out.append("")
            continue
        inputs = processor(text=line, src_lang=src, return_tensors="pt").to(device)
        with torch.inference_mode():
            tokens = model.generate(**inputs, tgt_lang=tgt, num_beams=BEAMS, max_new_tokens=256)
        out.append(
            processor.decode(
                tokens[0].tolist(), skip_special_tokens=True, clean_up_tokenization_spaces=False
            ).strip()
        )

    print(json.dumps({"lines": out, "device": device}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
