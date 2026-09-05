"""How many scenes a plan may hold, and why the answer differs by lane.

A 3:21 video produced 39 subtitle cues and was rejected with "at most 24 items, not 39" —
after it had been transcribed, translated and paid for. The 24 was a generation budget that
the transcribing lanes had quietly inherited.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lumina.domain.plan import Recipe
from lumina.intelligence.planner import (
    MAX_DRAFTED_SCENES,
    DraftedVideo,
    PlannedScene,
    _verbatim,
)


def _scenes(n: int) -> list[PlannedScene]:
    return [
        PlannedScene(script_line=f"Line {i}", image_prompt="", caption=f"Line {i}", duration_ms=900)
        for i in range(n)
    ]


def test_a_transcript_longer_than_the_generation_budget_still_plans() -> None:
    """The failure exactly as reported: 39 cues from a 3:21 video."""
    plan = _verbatim([f"Line number {i}." for i in range(39)], Recipe.SUBTITLE_ONLY, "en")
    assert len(plan.scenes) == 39


def test_an_hour_of_speech_plans() -> None:
    """Roughly a thousand cues — an ordinary long video, not an edge case."""
    plan = _verbatim([f"Line {i}." for i in range(1000)], Recipe.SUBTITLE_ONLY, "en")
    assert len(plan.scenes) == 1000


def test_the_generation_budget_still_binds_what_a_model_may_draft() -> None:
    """The cap is real where it belongs: every drafted scene is a frame someone pays for."""
    with pytest.raises(ValidationError):
        DraftedVideo(title="t", summary="", scenes=_scenes(MAX_DRAFTED_SCENES + 1))


def test_a_draft_at_the_budget_is_accepted() -> None:
    assert len(DraftedVideo(title="t", summary="", scenes=_scenes(MAX_DRAFTED_SCENES)).scenes) == 24
