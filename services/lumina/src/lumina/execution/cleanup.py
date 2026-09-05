"""Removing what a speech model writes when nobody is speaking.

Run a video with a music bed through Whisper and it does not fall silent — it writes. The
observed case on a real upload was a caption reading *"I am the Emperor of the Lord of the
Lord of the Lord of the Lord of the Lord!"* over an instrumental, followed by four lines of
song lyrics. Neither is a transcription error in the ordinary sense: the model is doing what
it was built to do, which is to emit the likeliest words, and over music the likeliest words
are a loop.

Two signals are actually available, and this uses both. Nothing here classifies music
directly — no engine we can reach exposes that — so it is worth saying plainly what this is:
a filter for the failure modes music *causes*, not a music detector.

**Repetition.** A hallucinated stretch collapses into a repeating phrase, because the model
falls into a cycle it cannot escape. Real speech does repeat, but not for most of a line, and
not identically. That is measurable from the text alone, so it works whichever engine ran.

**The model's own doubt.** Whisper reports `no_speech_prob` per segment and it is honest:
over music it is high even while the model is writing confident-looking words. Passed through
where an engine gives it, ignored where it does not.
"""

from __future__ import annotations

import re

import structlog

log = structlog.get_logger(__name__)

#: Above this, the engine itself thinks nothing was said. Whisper's own decoder uses 0.6 with
#: a log-probability check beside it; this is deliberately less eager, because deleting real
#: speech is a worse failure than keeping a line of lyrics.
NO_SPEECH = 0.75

#: How many times a phrase must repeat before it counts as a loop rather than as speech.
#:
#: It depends on how long the phrase is, because the two cases are not alike. People repeat a
#: single word for emphasis constantly — "no, no, no" — so one word needs four passes before
#: it is suspicious. Nobody says "of the Lord of the Lord of the Lord"; repeating a whole
#: phrase three times is already the model cycling, and the observed caption did exactly that.
LOOP_AT = 4
LOOP_AT_PHRASE = 3

#: Longest phrase to look for repeats of. Beyond this a "repeat" is more likely a chorus,
#: which is real content in the rare case the creator wants lyrics captioned.
LOOP_SPAN = 5

_WORD = re.compile(r"\w+", re.UNICODE)


def collapse_loops(text: str) -> str:
    """Fold a repeated phrase back to one occurrence.

    Collapsing rather than dropping: the line usually opens with something real before the
    model loses its footing — "I am the Emperor of the Lord" is plausible, the four further
    "of the Lord"s are not — and keeping the salvageable half is the conservative choice when
    the alternative is deleting a caption the creator may have needed.
    """
    parts = text.split()
    if len(parts) < LOOP_AT:
        return text

    # Matched on the bare word, kept as written. "Lord" and "Lord!" are the same repetition —
    # the model punctuates only the last pass — and comparing them literally leaves one copy
    # behind, which is the visible half of the bug rather than the whole of it.
    def bare(word: str) -> str:
        return "".join(ch for ch in word.lower() if ch.isalnum())

    keys = [bare(w) for w in parts]

    # Scanned from every position rather than in phrase-sized jumps. The loop rarely starts on
    # a boundary the jump would land on — "I am the Emperor" precedes it — and a grid-aligned
    # scan walks straight past it.
    out: list[str] = []
    i = 0
    while i < len(parts):
        best = 0
        found = 0
        for span in range(1, min(LOOP_SPAN, (len(parts) - i) // LOOP_AT_PHRASE) + 1):
            runs = 1
            while keys[i + runs * span : i + (runs + 1) * span] == keys[i : i + span]:
                runs += 1
            enough = LOOP_AT if span == 1 else LOOP_AT_PHRASE
            if runs >= enough and runs * span > best:
                best, found = runs * span, span
        if found:
            #: The last pass, not the first: it carries the sentence's closing punctuation.
            out.extend(parts[i + best - found : i + best])
            i += best
        else:
            out.append(parts[i])
            i += 1
    return " ".join(out)


def looks_hallucinated(text: str) -> bool:
    """Whether a line is mostly one phrase said over and over.

    Checked after collapsing: if folding the repeats away leaves almost nothing, the line was
    the loop rather than a line containing one.
    """
    words = _WORD.findall(text)
    if len(words) < LOOP_AT_PHRASE:
        return False
    kept = _WORD.findall(collapse_loops(text))
    return len(kept) * 3 <= len(words)
