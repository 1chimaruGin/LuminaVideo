from __future__ import annotations

import uuid

import pytest

from lumina.domain.ids import AssetId, SceneId
from lumina.domain.plan import (
    SCENE_TRANSITIONS,
    IllegalTransitionError,
    Scene,
    SceneState,
    Tier,
    assert_transition,
)


def scene(**kw: object) -> Scene:
    base = dict(
        id=SceneId(uuid.uuid4()),
        index=0,
        prompt="a lighthouse at dusk",
        script_line="Here is why it matters.",
        duration_ms=5_000,
    )
    return Scene(**{**base, **kw})  # type: ignore[arg-type]


def test_a_failed_upgrade_returns_to_preview_ready_not_failed() -> None:
    """The preview asset still exists, so the user can still watch their video while we
    retry. Dropping them to FAILED would hide work they already paid for."""
    assert SceneState.PREVIEW_READY in SCENE_TRANSITIONS[SceneState.UPGRADING]
    assert SceneState.FAILED not in SCENE_TRANSITIONS[SceneState.UPGRADING]


def test_illegal_transition_is_rejected() -> None:
    with pytest.raises(IllegalTransitionError):
        assert_transition(SceneState.DRAFT, SceneState.FINAL)


def test_every_state_is_reachable_and_escapable() -> None:
    reachable = {t for targets in SCENE_TRANSITIONS.values() for t in targets}
    for state in SceneState:
        if state is not SceneState.DRAFT:
            assert state in reachable, f"{state} is unreachable"
        assert SCENE_TRANSITIONS[state], f"{state} is a dead end"


def test_final_never_overwrites_preview() -> None:
    preview, final = AssetId(uuid.uuid4()), AssetId(uuid.uuid4())
    s = scene(preview_asset_id=preview, final_asset_id=final, state=SceneState.FINAL)
    assert s.preview_asset_id == preview, "the preview must survive so revert is possible"
    assert s.visible_asset_id == final, "the player prefers the final"


def test_preview_only_scene_shows_its_preview() -> None:
    preview = AssetId(uuid.uuid4())
    s = scene(preview_asset_id=preview, state=SceneState.PREVIEW_READY)
    assert s.visible_asset_id == preview
    assert s.is_upgradable


def test_premium_scene_is_not_upgradable() -> None:
    s = scene(tier=Tier.PREMIUM_VIDEO, state=SceneState.PREVIEW_READY)
    assert not s.is_upgradable
