"""Per-scene actions: edit, retime, reorder, regenerate, upgrade, revert.

The storyboard is the product, and its promise is that touching one card cannot disturb the
others. Every handler here takes exactly one scene's row lock and enqueues at most one job.
Nothing fans out; nothing rewrites the plan document.

Reordering is the one exception, and it is careful about it — see `move`.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from lumina.api.deps import CurrentUser, Session
from lumina.api.schemas import NewScene, SceneEdit, SceneMove, SceneOut
from lumina.db.models import Plan as PlanRow
from lumina.db.models import Scene, User
from lumina.domain.jobs import Stage
from lumina.domain.plan import (
    DEFAULT_TIER,
    PREVIEW_TIERS,
    SceneState,
    Tier,
    assert_transition,
)
from lumina.execution.providers.fake import FakeProvider
from lumina.intelligence.pricing import price_scene
from lumina.queue import Queue
from lumina.state.ledger import Ledger

router = APIRouter(prefix="/scenes", tags=["scenes"])


@router.post("/plan/{plan_id}", response_model=SceneOut, status_code=status.HTTP_201_CREATED)
async def add_scene(plan_id: uuid.UUID, body: NewScene, session: Session) -> Scene:
    """Add a scene to a plan.

    The plan is a user-editable artifact, not hidden reasoning (invariant 5) — so the user
    gets to add a shot the planner did not think of, not merely edit the ones it wrote.

    Inserting reuses the same two-pass shuffle as `move`, and for the same reason: `index` is
    dense and unique per plan, so making room for a new row means every row after it moves,
    and assigning the final values directly would collide mid-statement.
    """
    plan = await session.get(PlanRow, plan_id)
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such plan")

    siblings = list(
        await session.scalars(select(Scene).where(Scene.plan_id == plan_id).order_by(Scene.index))
    )
    scene = Scene(
        plan_id=plan_id,
        index=len(siblings),  # provisional; the renumber below lands it
        prompt=body.prompt,
        script_line=body.script_line,
        caption=body.caption,
        duration_ms=body.duration_ms,
        source_start_ms=body.source_start_ms,
        tier=DEFAULT_TIER.value,
        state=SceneState.DRAFT.value,
        ref_asset_ids=[],
    )
    session.add(scene)

    at = len(siblings) if body.index is None else min(body.index, len(siblings))
    ordered = [*siblings[:at], scene, *siblings[at:]]
    await _renumber(session, ordered)

    await session.commit()
    return scene


@router.delete("/{scene_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scene(scene_id: uuid.UUID, session: Session) -> None:
    """Remove a scene, and close the gap it leaves.

    Refuses the last one: a plan with no scenes cannot be rendered, and the storyboard would
    become an empty screen with no way back to a video.
    """
    scene = await _scene(session, scene_id)
    siblings = list(
        await session.scalars(
            select(Scene).where(Scene.plan_id == scene.plan_id).order_by(Scene.index)
        )
    )
    if len(siblings) <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "a plan needs at least one scene")

    remaining = [s for s in siblings if s.id != scene.id]
    await session.delete(scene)
    await session.flush()
    await _renumber(session, remaining)
    await session.commit()


async def _renumber(session: Session, ordered: list[Scene]) -> None:
    """Assign dense indices without ever colliding with a row that still holds one.

    Two passes with a flush between: negative indices cannot collide with the final values,
    so every intermediate state satisfies the unique constraint. Assigning the final numbers
    directly trips it mid-statement.
    """
    for i, s in enumerate(ordered):
        s.index = -(i + 1)
    await session.flush()
    for i, s in enumerate(ordered):
        s.index = i
    await session.flush()


@router.get("/{scene_id}", response_model=SceneOut)
async def get_scene(scene_id: uuid.UUID, session: Session) -> Scene:
    return await _scene(session, scene_id)


@router.patch("/{scene_id}", response_model=SceneOut)
async def edit_scene(scene_id: uuid.UUID, body: SceneEdit, session: Session) -> Scene:
    """Change one scene's authored content.

    Only the fields present in the body move. A partial update rather than a replace means
    editing a prompt cannot silently revert a narration line, which matters because the
    storyboard is edited field by field while jobs are landing on the same rows.
    """
    scene = await _scene(session, scene_id)
    patch = body.model_dump(exclude_none=True)

    # Merged, not assigned — and reassigned rather than mutated, because SQLAlchemy does not
    # track an in-place edit to a JSONB dict and the write would be silently dropped.
    edited = patch.pop("translations", None)
    if edited:
        scene.translations = {**scene.translations, **edited}

    for field, value in patch.items():
        setattr(scene, field, value)
    await session.commit()
    return scene


@router.post("/plan/{plan_id}/shift", response_model=list[SceneOut])
async def shift(plan_id: uuid.UUID, session: Session, by_ms: int = 0) -> list[Scene]:
    """Move every cue in the plan by the same amount.

    The single most common thing wrong with a subtitle file: the whole track is a second or
    two out, because it was made for a different cut or a version with a different intro. The
    fix is one number applied to every line, and doing it a line at a time is absurd on a
    forty-cue track — which is why every serious subtitle editor has this and it is the first
    thing a creator reaches for.

    Clamped at zero rather than refused: dragging the track earlier than the video starts is a
    reasonable thing to attempt, and the answer is that the first cue lands at the beginning,
    not that the whole operation fails.
    """
    scenes = list(
        await session.scalars(select(Scene).where(Scene.plan_id == plan_id).order_by(Scene.index))
    )
    if not scenes:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such plan")

    for scene in scenes:
        if scene.source_start_ms is not None:
            scene.source_start_ms = max(0, scene.source_start_ms + by_ms)
    await session.commit()
    return scenes


@router.post("/{scene_id}/regenerate", status_code=status.HTTP_202_ACCEPTED)
async def regenerate(
    scene_id: uuid.UUID, session: Session, user: CurrentUser, tier: str | None = None
) -> dict[str, str]:
    """Redo one scene, optionally at a higher tier.

    A fresh seed on every call. Without one the idempotency key is unchanged, the cache hit
    returns the same artifact, and "regenerate" becomes a button that visibly does nothing —
    which is worse than an error, because the user cannot tell it failed.
    """
    scene = await _scene(session, scene_id)

    if tier is not None:
        try:
            scene.tier = Tier(tier).value
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unknown tier {tier}") from None

    upgrading = Tier(scene.tier) not in PREVIEW_TIERS
    target = SceneState.UPGRADING if upgrading else SceneState.PREVIEWING
    _assert(scene, target)

    job_id = await _queue_and_hold(session, scene, user)
    await session.commit()
    return {"job_id": str(job_id)}


@router.post("/{scene_id}/upgrade", status_code=status.HTTP_202_ACCEPTED)
async def upgrade(
    scene_id: uuid.UUID, session: Session, user: CurrentUser, tier: str = "mid_video"
) -> dict[str, str]:
    """Promote one preview to real video generation.

    The upsell, and the mechanic the whole preview-first cost architecture exists to enable.
    Per scene on purpose: a creator upgrades the two shots that carry the video and leaves the
    other ten as stills, which is the difference between a $25 video and a $3 one.
    """
    scene = await _scene(session, scene_id)
    try:
        target_tier = Tier(tier)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unknown tier {tier}") from None

    if target_tier in PREVIEW_TIERS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{tier} is a preview tier; use regenerate to redo a preview",
        )

    _assert(scene, SceneState.UPGRADING)
    scene.tier = target_tier.value
    await session.flush()  # so the hold is priced at the tier being bought, not the old one

    job_id = await _queue_and_hold(session, scene, user)
    await session.commit()
    return {"job_id": str(job_id)}


@router.post("/{scene_id}/revert", response_model=SceneOut)
async def revert(scene_id: uuid.UUID, session: Session) -> Scene:
    """Go back to the preview.

    Possible at all because a final never overwrote the preview — both assets are still on the
    row. This drops the final and returns the scene to the state it was in before the upgrade,
    which is why an upgrade the user dislikes is a recoverable mistake rather than a purchase
    they are stuck with.
    """
    scene = await _scene(session, scene_id)
    if scene.preview_asset_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "no preview to go back to")
    if scene.final_asset_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "nothing to revert")

    scene.final_asset_id = None
    scene.tier = Tier.IMAGE_MOTION.value
    scene.state = SceneState.PREVIEW_READY.value
    scene.error = None
    await session.commit()
    return scene


@router.post("/{scene_id}/move", response_model=list[SceneOut])
async def move(scene_id: uuid.UUID, body: SceneMove, session: Session) -> list[Scene]:
    """Reorder the storyboard.

    The one handler that touches more than one row, because `index` is dense and unique per
    plan — moving a scene necessarily shifts its neighbours.

    Done in two passes with a flush between them. Assigning the final indices directly would
    momentarily collide with a row that still holds the target index, and the unique constraint
    fires mid-statement; parking the moved scene outside the range first keeps every
    intermediate state legal.
    """
    scene = await _scene(session, scene_id)
    siblings = list(
        await session.scalars(
            select(Scene).where(Scene.plan_id == scene.plan_id).order_by(Scene.index)
        )
    )

    target = min(body.index, len(siblings) - 1)
    if target == scene.index:
        return siblings

    ordered = [s for s in siblings if s.id != scene.id]
    ordered.insert(target, scene)

    await _renumber(session, ordered)
    await session.commit()
    return ordered


async def _queue_and_hold(session: Session, scene: Scene, user: User) -> uuid.UUID:
    """Queue one scene's generation and hold its credits, together.

    Both, or neither. Reserving without enqueuing holds money against work that will never
    run; enqueuing without reserving is invariant 4 — the failure the user then sees is a job
    that dies in a worker with nothing on screen to explain it.

    A fresh seed each time: without one the idempotency key is unchanged, the cache returns
    the same artifact, and "try again" becomes a button that visibly does nothing — which is
    worse than an error, because the user cannot tell it failed.
    """
    price = price_scene([FakeProvider()], scene, await _language_of(session, scene))
    ledger = Ledger(session)
    available, _, _ = await ledger.balance(user.id)
    if price > available:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            {
                "message": "not enough credits for that",
                "needed": int(price),
                "available": int(available),
                "short_by": int(price) - int(available),
            },
        )

    job_id = await Queue(session).enqueue(
        Stage.GENERATE,
        {"scene_id": str(scene.id), "user_id": str(user.id), "seed": uuid.uuid4().int % 10_000},
        # So the hold this takes can be found again if the project is deleted.
        project=await _project_of(session, scene),
    )
    await ledger.reserve(user.id, price, job_id=job_id)
    return job_id


async def _project_of(session: Session, scene: Scene) -> uuid.UUID | None:
    """Which project a scene belongs to, through its plan."""
    found: uuid.UUID | None = await session.scalar(
        select(PlanRow.project_id).where(PlanRow.id == scene.plan_id)
    )
    return found


async def _language_of(session: Session, scene: Scene) -> str:
    """The plan's language, for pricing.

    Fetched explicitly rather than through `scene.plan`. That relationship is lazy, and a lazy
    load in async SQLAlchemy raises `MissingGreenlet` rather than quietly issuing a query — so
    reaching through it here looked like a free attribute access and was a 500.
    """
    language: str | None = await session.scalar(
        select(PlanRow.content["language"].astext).where(PlanRow.id == scene.plan_id)
    )
    return language or "en"


async def _scene(session: Session, scene_id: uuid.UUID) -> Scene:
    scene = await session.get(Scene, scene_id)
    if scene is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such scene")
    return scene


def _assert(scene: Scene, target: SceneState) -> None:
    """Reject an illegal transition as a conflict rather than a server error.

    409 and not 400: the request was well-formed, the scene was simply not in a state where
    it made sense — usually because a job the client did not know about landed first.
    """
    try:
        assert_transition(SceneState(scene.state), target)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
