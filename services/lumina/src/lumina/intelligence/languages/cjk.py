"""What Japanese and Chinese share, and only what they share.

Not a base class — the two packs differ in the ways that matter (segmentation especially) and
inheriting would hide that. This is the one rule they genuinely have in common, plus the
character classes it needs, so a Traditional Chinese or Cantonese pack added later gets it for
free instead of copying it wrong.

**Kinsoku shori** — line-breaking prohibition. Some characters may never begin a line: closing
brackets, the sentence-ending 。 and 、, the long vowel mark ー, the small kana. Others may
never end one: opening brackets. A renderer that breaks purely on width puts a full stop alone
at the start of a line, which reads as broken typography to anyone who reads the script and is
invisible to anyone who does not. Browsers implement this; our caption lines are pre-broken
here, so we have to as well.
"""

from __future__ import annotations

#: Never allowed at the start of a line. Sentence-final marks, closing brackets, the small
#: kana that belong to the syllable before them, and the sound-extension mark.
NEVER_STARTS = frozenset(
    "、。，．・：；？！ヽヾゝゞ々ー"
    "」』）〕］｝〉》〙〗"
    "ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ"
    "%‰℃°"
)

#: Never allowed at the end of a line. Opening brackets, which would be orphaned from what
#: they open.
NEVER_ENDS = frozenset("「『（〔［｛〈《〘〖¥＄£")


def wrap(units: list[str], width: int) -> list[str]:
    """Break a run of units into lines of at most `width`, honouring kinsoku.

    Two adjustments after the naive width break, both of which move the break by one unit:

      - a line that would *begin* with a prohibited character takes one more unit from the
        line before it, so the mark stays attached to what it belongs to;
      - a line that would *end* with an opening bracket gives that bracket to the next line.

    Deliberately not a full implementation — real kinsoku also compresses inter-character
    spacing to avoid ragged edges, which is a justification problem a centred caption does not
    have.
    """
    if not units:
        return []

    lines: list[list[str]] = []
    current: list[str] = []
    for u in units:
        if current and sum(len(x) for x in current) + len(u) > width:
            lines.append(current)
            current = [u]
        else:
            current.append(u)
    if current:
        lines.append(current)

    for i in range(1, len(lines)):
        prev, line = lines[i - 1], lines[i]
        # A prohibited opener pulls one more unit back from the previous line — but never the
        # last one, which would leave that line empty.
        while line and line[0][0] in NEVER_STARTS and len(prev) > 1:
            prev.append(line.pop(0))
        # An orphaned opening bracket goes forward instead.
        while prev and prev[-1][-1] in NEVER_ENDS and len(prev) > 1:
            line.insert(0, prev.pop())

    return ["".join(line) for line in lines if line]
