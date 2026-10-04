"""Deliver: one storyboard out to 9:16, 1:1 and 16:9.

This is where `captions` and `compose` finally get enqueued. Until now nothing in the API
reached them, so the pipeline stopped after narration and the storyboard was as far as a
project could get.

One render row per aspect rather than one row with three files. They finish at different
times, any one of them can fail on its own, and the user downloads them individually — a
single row would have to invent a status that means "two of three".
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from lumina.api.deps import Session
from lumina.api.schemas import NewRender, RenderOut
from lumina.db.models import Project, Render, Scene
from lumina.domain.jobs import Stage
from lumina.execution.compose import ffmpeg
from lumina.queue import Queue

router = APIRouter(prefix="/renders", tags=["renders"])


@router.get("/project/{project_id}", response_model=list[RenderOut])
async def list_renders(project_id: uuid.UUID, session: Session) -> list[Render]:
    rows = await session.scalars(
        select(Render).where(Render.project_id == project_id).order_by(Render.created_at.desc())
    )
    return list(rows)


@router.post("/project/{project_id}", response_model=list[RenderOut])
async def start_render(project_id: uuid.UUID, body: NewRender, session: Session) -> list[Render]:
    """Render the current plan to every requested aspect.

    Captions are rasterized once per aspect, not once per project: a caption laid out for a
    1080-wide vertical frame line-breaks differently at 1920 wide, and reusing the vertical
    states on the wide cut is how text ends up overhanging the frame.

    Both stages go on the render pool. Neither belongs near the light pool's event loop —
    one runs Chromium and the other runs ffmpeg, and either will stall thousands of in-flight
    provider polls if it lands there.
    """
    project = await session.get(Project, project_id)
    if project is None or project.current_plan_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no plan yet")

    unknown = [a for a in body.aspects if a not in ffmpeg.ASPECTS]
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unknown aspect(s): {', '.join(unknown)}")

    scenes = list(
        await session.scalars(select(Scene).where(Scene.plan_id == project.current_plan_id))
    )
    if not scenes:
        raise HTTPException(status.HTTP_409_CONFLICT, "the plan has no scenes")

    # A scene that points into the project's source video has its picture already — it is
    # the footage. Only the lanes that generate frames can have a hole.
    missing = [
        s.index
        for s in scenes
        if not (s.final_asset_id or s.preview_asset_id)
        and not (project.source_asset_id is not None and s.source_start_ms is not None)
    ]
    if missing:
        # Refuse rather than render a video with holes in it. The user is told which scenes
        # are not ready, because "generate the draft first" is actionable and "compose
        # failed" is not.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"scene(s) {', '.join(str(i + 1) for i in sorted(missing))} have nothing to show yet",
        )

    plan_id = project.current_plan_id
    queue = Queue(session)
    out: list[Render] = []

    for aspect in body.aspects:
        render = Render(project_id=project_id, plan_id=plan_id, aspect=aspect, status="queued")
        session.add(render)
        await session.flush()

        # Only captions is enqueued. It chains to compose once the states exist — enqueuing
        # both here would race, and compose would claim its job and find nothing to burn in.
        await queue.enqueue(
            Stage.CAPTIONS,
            {
                "plan_id": str(plan_id),
                "aspect": aspect,
                "language": project.language,
                "style": body.caption_style,
                "caption_bottom": body.caption_bottom,
                "caption_scale": body.caption_scale,
                "caption_karaoke": body.caption_karaoke,
                "render_id": str(render.id),
            },
            project=project.id,
        )
        out.append(render)

    # One commit for the rows and both enqueues. The co-commit is why the queue is in
    # Postgres: a render row without its job is a spinner that never resolves.
    await session.commit()
    return out


@router.get("/{render_id}", response_model=RenderOut)
async def get_render(render_id: uuid.UUID, session: Session) -> Render:
    render = await session.get(Render, render_id)
    if render is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such render")
    return render
