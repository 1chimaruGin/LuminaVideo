"""The Plan is a user-editable artifact, not hidden reasoning (invariant 5).

The data model exists to make "regenerate scene 4 without touching 1-3" trivial. Every scene
carries its own state and its own assets; nothing about a scene is stored on the plan.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from lumina.domain.ids import AssetId, PlanId, SceneId


class Tier(StrEnum):
    """Routing tier. Drives cost by roughly 60x — see the cost model in DEVELOPMENT.md."""

    STOCK = "stock"  # existing footage + TTS + captions
    IMAGE_MOTION = "image_motion"  # DEFAULT: stills + programmatic motion
    BUDGET_VIDEO = "budget_video"
    MID_VIDEO = "mid_video"
    PREMIUM_VIDEO = "premium_video"  # metered upsell, chosen per scene


#: The tier every scene starts at. Premium generation is never implicit.
DEFAULT_TIER = Tier.IMAGE_MOTION

#: Tiers that count as a "preview". Anything above is a paid upgrade.
PREVIEW_TIERS = frozenset({Tier.STOCK, Tier.IMAGE_MOTION})


class SceneState(StrEnum):
    DRAFT = "draft"
    PREVIEWING = "previewing"
    PREVIEW_READY = "preview_ready"
    UPGRADING = "upgrading"
    FINAL = "final"
    FAILED = "failed"


#: Legal transitions. A failed upgrade returns to PREVIEW_READY rather than FAILED: the
#: preview asset still exists, so the user can still watch their video while we retry.
SCENE_TRANSITIONS: dict[SceneState, frozenset[SceneState]] = {
    SceneState.DRAFT: frozenset({SceneState.PREVIEWING}),
    SceneState.PREVIEWING: frozenset({SceneState.PREVIEW_READY, SceneState.FAILED}),
    SceneState.PREVIEW_READY: frozenset({SceneState.PREVIEWING, SceneState.UPGRADING}),
    SceneState.UPGRADING: frozenset({SceneState.FINAL, SceneState.PREVIEW_READY}),
    SceneState.FINAL: frozenset({SceneState.UPGRADING, SceneState.PREVIEWING}),
    SceneState.FAILED: frozenset({SceneState.PREVIEWING, SceneState.UPGRADING}),
}


class IllegalTransitionError(ValueError):
    def __init__(self, frm: SceneState, to: SceneState) -> None:
        super().__init__(f"scene cannot move {frm.value} -> {to.value}")
        self.frm = frm
        self.to = to


def assert_transition(frm: SceneState, to: SceneState) -> None:
    if to not in SCENE_TRANSITIONS[frm]:
        raise IllegalTransitionError(frm, to)


class Scene(BaseModel):
    """One shot.

    `preview_asset_id` and `final_asset_id` are separate fields on purpose. A final never
    overwrites a preview — keeping both is what makes "revert" possible.
    """

    id: SceneId
    index: int = Field(ge=0)

    prompt: str
    script_line: str
    duration_ms: int = Field(gt=0, le=60_000)
    ref_asset_ids: list[AssetId] = Field(default_factory=list)

    tier: Tier = DEFAULT_TIER
    state: SceneState = SceneState.DRAFT

    preview_asset_id: AssetId | None = None
    final_asset_id: AssetId | None = None
    vo_asset_id: AssetId | None = None
    caption: str | None = None

    #: Set when generation fails, cleared on the next successful attempt.
    error: str | None = None

    @property
    def visible_asset_id(self) -> AssetId | None:
        """What the player shows: the final if we have one, else the preview."""
        return self.final_asset_id or self.preview_asset_id

    @property
    def is_upgradable(self) -> bool:
        return self.state is SceneState.PREVIEW_READY and self.tier in PREVIEW_TIERS


class Recipe(StrEnum):
    """A recipe is an entry point into the same stages, not a subsystem of its own.

    Adding one is configuration plus a prompt profile. What differs between them is which
    stages run (`RECIPE_STAGES`) and how the planner is briefed — never the pipeline.
    """

    EXPLAINER = "explainer"
    DUB = "dub"
    NARRATE = "narrate"
    CLIP_LONG_VIDEO = "clip_long_video"
    SUBTITLE_ONLY = "subtitle_only"


#: Which recipes begin from a file the user already has, rather than from an idea. These are
#: the ones that cannot be planned until something has been uploaded and probed.
NEEDS_SOURCE = frozenset({Recipe.CLIP_LONG_VIDEO, Recipe.SUBTITLE_ONLY, Recipe.DUB, Recipe.NARRATE})

#: Recipes that hand back the creator's video whole, with something drawn on it.
#:
#: The distinction that matters is against Clip, which starts from the same kind of file and
#: does the opposite: Clip's entire value is that most of a long video is not worth posting, so
#: it keeps the moments and drops the rest. Subtitling drops nothing.
#:
#: Without this the two lanes shared one code path and Subtitle inherited Clip's behaviour: a
#: sixty-second video with two subtitle cues rendered as a five-second video containing only
#: the two captioned moments, under a screen that said "your video is untouched".
KEEPS_WHOLE_SOURCE = frozenset({Recipe.SUBTITLE_ONLY, Recipe.DUB})

#: Recipes whose clock comes from the narration rather than from the footage.
#:
#: The inversion that defines Narrate. Every other lane inherits its timing from something
#: that already exists — the speech in the source, the moments found in it — so the words have
#: to land where the video says. Here the words come first: a line is as long as it takes to
#: say, the cues follow from that, and the footage is a bed stretched or trimmed to whatever
#: length the script turned out to be. Nothing is cut to a moment and nothing is synced to
#: anything; the picture adapts to the voice.
TIMED_BY_NARRATION = frozenset({Recipe.NARRATE})


class Aspect(StrEnum):
    VERTICAL = "9:16"
    SQUARE = "1:1"
    WIDE = "16:9"


class Plan(BaseModel):
    """Typed and versioned. Stored as JSONB, validated by this model on read and write."""

    id: PlanId
    version: int = Field(ge=1)
    recipe: Recipe
    language: str = Field(description="BCP-47 tag; selects the LanguagePack")
    title: str
    summary: str = Field(description="The one plain sentence shown on the Recipe screen")
    aspects: list[Aspect] = Field(default_factory=lambda: [Aspect.VERTICAL])
    scenes: list[Scene]

    @model_validator(mode="after")
    def _scene_indices_are_dense(self) -> Plan:
        expected = list(range(len(self.scenes)))
        actual = [s.index for s in self.scenes]
        if actual != expected:
            raise ValueError(f"scene indices must be dense and ordered, got {actual}")
        return self

    @property
    def duration_ms(self) -> int:
        return sum(s.duration_ms for s in self.scenes)

    def scene(self, scene_id: SceneId) -> Scene:
        for s in self.scenes:
            if s.id == scene_id:
                return s
        raise KeyError(scene_id)
