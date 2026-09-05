"""What a speech model writes when nobody is speaking.

Every line here came off a real upload: a video with Japanese speech, a music bed and an
English song, transcribed by Whisper. The model does not fall silent over music — it writes,
confidently, in a loop.
"""

from __future__ import annotations

from lumina.execution.cleanup import collapse_loops, looks_hallucinated


def test_the_observed_hallucination_collapses() -> None:
    """Verbatim from the caption list, over an instrumental passage."""
    said = "I am the Emperor of the Lord of the Lord of the Lord of the Lord of the Lord!"
    assert collapse_loops(said) == "I am the Emperor of the Lord!"


def test_a_phrase_repeated_three_times_is_already_a_loop() -> None:
    """The second observed line. Nobody says this; the model was cycling."""
    said = "You are the Emperor of the Lord of the Lord of the Lord!"
    assert collapse_loops(said) == "You are the Emperor of the Lord!"


def test_a_word_repeated_three_times_is_speech() -> None:
    """People do this for emphasis, and deleting it would be the worse mistake."""
    assert collapse_loops("no no no") == "no no no"
    assert collapse_loops("Very very good") == "Very very good"


def test_punctuation_does_not_hide_a_repeat() -> None:
    """The model punctuates only the last pass, so a literal comparison leaves one copy."""
    assert collapse_loops("bye bye bye bye.") == "bye."


def test_ordinary_lines_are_untouched() -> None:
    for line in (
        "I tried so hard and got so far",
        "But in the end it doesn't even matter",
        "All I know",
        "Light is fast, but it is not instant.",
    ):
        assert collapse_loops(line) == line
        assert not looks_hallucinated(line)


def test_a_line_that_is_only_a_loop_is_flagged() -> None:
    """Collapsing leaves almost nothing, so the line *was* the loop rather than containing
    one — which is a caption worth dropping rather than shortening."""
    assert looks_hallucinated("Thank you. Thank you. Thank you.")


def test_a_short_line_is_never_a_loop() -> None:
    assert not looks_hallucinated("Hello")
    assert collapse_loops("Hello") == "Hello"
