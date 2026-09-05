"""Reading timed text the creator already has.

This is what makes the Subtitle lane work on a server with no speech engine, which is every
server this product runs on today. The parser is therefore load-bearing rather than a
convenience, and the cases below are the ones real files actually contain.
"""

from __future__ import annotations

import pytest

from lumina.execution import subtitles


def test_srt_keeps_the_words_and_drops_the_scaffolding() -> None:
    read = subtitles.parse(
        "1\n"
        "00:00:01,000 --> 00:00:04,000\n"
        "<i>Hello there.</i>\n"
        "\n"
        "2\n"
        "00:00:04,500 --> 00:00:08,000\n"
        "{\\an8}This is a test.\n"
        "Second line of the same cue.\n",
        duration_ms=10_000,
    )
    assert read.format == subtitles.SRT
    assert read.timed, "the file's own timings, not ours"
    assert [s.text for s in read.segments] == [
        "Hello there.",
        "This is a test. Second line of the same cue.",
    ]
    # The index lines and the markup are gone; a literal `<i>` burnt into a video cannot be
    # removed by the creator afterwards.
    assert read.segments[0].start_ms == 1_000
    assert read.segments[0].duration_ms == 3_000


def test_vtt_ignores_the_blocks_that_are_not_captions() -> None:
    read = subtitles.parse(
        "WEBVTT\n\nNOTE this comment is not something anybody said\n\n"
        "intro\n00:01.000 --> 00:03.500\n<c.yellow>Kia ora.</c>\n",
        duration_ms=10_000,
    )
    assert read.format == subtitles.VTT
    assert [s.text for s in read.segments] == ["Kia ora."]


def test_a_two_digit_timestamp_is_minutes_not_hours() -> None:
    """`01:02.500` is 62.5 seconds. Reading it as an hour puts every caption past the end of
    the video, where they are then all dropped and the file looks empty."""
    read = subtitles.parse("WEBVTT\n\n01:02.500 --> 01:04.000\nHi\n", duration_ms=120_000)
    assert read.segments[0].start_ms == 62_500


def test_a_short_fraction_is_left_aligned() -> None:
    """`.5` is half a second, not five milliseconds."""
    read = subtitles.parse("1\n00:00:01,5 --> 00:00:02,0\nHi\n", duration_ms=10_000)
    assert read.segments[0].start_ms == 1_500


def test_overlapping_cues_are_trimmed_because_burnt_in_captions_cannot_stack() -> None:
    """A soft subtitle track can show two lines at once; a caption drawn into the picture
    would draw the second on top of the first."""
    read = subtitles.parse(
        "1\n00:00:00,000 --> 00:00:06,000\nFirst\n\n2\n00:00:02,000 --> 00:00:05,000\nSecond\n",
        duration_ms=10_000,
    )
    assert [(s.start_ms, s.end_ms) for s in read.segments] == [(0, 2_000), (2_000, 5_000)]


def test_cues_past_the_end_of_the_picture_are_dropped() -> None:
    """A subtitle file for the director's cut, applied to the theatrical release."""
    read = subtitles.parse(
        "1\n00:00:01,000 --> 00:00:02,000\nIn the film\n\n"
        "2\n00:00:30,000 --> 00:00:33,000\nOnly in the longer cut\n",
        duration_ms=10_000,
    )
    assert [s.text for s in read.segments] == ["In the film"]


def test_a_cue_running_past_the_end_is_cut_at_the_end() -> None:
    read = subtitles.parse("1\n00:00:08,000 --> 00:00:20,000\nLast words\n", duration_ms=10_000)
    assert read.segments[0].end_ms == 10_000


def test_plain_text_is_spread_across_the_video_at_reading_speed() -> None:
    """No timings in the file, so they are derived — and said to be derived, because
    fabricating precise-looking timings and presenting them as the creator's is the one thing
    worth refusing to do quietly."""
    read = subtitles.parse(
        "Light is fast.\nBut it is not instant.\nThat changes everything.",
        duration_ms=9_000,
    )
    assert read.format == subtitles.PLAIN
    assert not read.timed
    assert len(read.segments) == 3
    assert read.segments[0].start_ms == 0
    # Fills the video rather than stopping halfway through it.
    assert abs(read.segments[-1].end_ms - 9_000) < 50


def test_reading_speed_is_the_language_pack_s_not_a_constant() -> None:
    """The same sentence takes different time to read in different scripts. That number lives
    in the pack, and a subtitle timed at English speed is gone before a Burmese reader has
    finished it."""
    english = subtitles.parse("aaaaaaaaaaaaaaaaaaaa", duration_ms=0, language="en")
    burmese = subtitles.parse("မြန်မာစာဖတ်ရှုခြင်းသည်ကောင်း", duration_ms=0, language="my")
    per_char_en = english.segments[0].duration_ms / 20
    per_char_my = burmese.segments[0].duration_ms / 28
    assert per_char_my > per_char_en


def test_an_empty_file_is_refused_rather_than_planned_as_nothing() -> None:
    with pytest.raises(subtitles.UnreadableSubtitlesError):
        subtitles.parse("   \n\n  \n", duration_ms=10_000)


def test_a_byte_order_mark_and_windows_line_endings_do_not_break_it() -> None:
    """Both are what a subtitle file saved on Windows actually looks like."""
    read = subtitles.parse("﻿1\r\n00:00:01,000 --> 00:00:02,000\r\nHi\r\n", duration_ms=10_000)
    assert read.format == subtitles.SRT
    assert [s.text for s in read.segments] == ["Hi"]
