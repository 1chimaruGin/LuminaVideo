"""What is running, and what happened.

`design-brief.md`: jobs take minutes and the app must survive a closed tab. That makes this
less a debug view than a real screen — it is how someone who came back an hour later finds out
whether their video is ready, and why it is not.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import or_, select

from lumina.api.deps import CurrentUser, Session
from lumina.api.schemas import JobOut
from lumina.db.models import Job, Plan, Scene
from lumina.domain.jobs import JobStatus
from lumina.queue import Queue

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=list[JobOut])
async def list_jobs(
    session: Session,
    user: CurrentUser,
    project_id: uuid.UUID | None = None,
    limit: int = 50,
) -> list[Job]:
    """Recent jobs, newest first.

    Scoped by project when one is given. Unscoped it is every job in the system, which is
    honest about what this is right now — the queue has no owner column, and inventing one
    from the payload would be a filter that looks like authorisation without being it.
    """
    stmt = select(Job).order_by(Job.created_at.desc()).limit(min(limit, 200))
    if project_id is not None:
        # A job belongs to a project either directly or through the scene it operates on.
        scene_ids = (
            select(Scene.id)
            .join(Plan, Plan.id == Scene.plan_id)
            .where(Plan.project_id == project_id)
        )
        stmt = stmt.where(or_(Job.project_id == project_id, Job.scene_id.in_(scene_ids)))
    rows = await session.scalars(stmt)
    return list(rows)


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: uuid.UUID, session: Session) -> Job:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such job")
    return job


@router.post("/{job_id}/retry", response_model=JobOut)
async def retry(job_id: uuid.UUID, session: Session, user: CurrentUser) -> Job:
    """Put a failed job back on the queue.

    Only from `failed`. A running job is either alive or about to be reaped by its expired
    lease, and requeuing it by hand would run the same work twice — which for a paid stage
    means paying for it twice.
    """
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such job")
    if JobStatus(job.status) is not JobStatus.FAILED:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"only a failed job can be retried; this one is {job.status}"
        )

    job.status = JobStatus.QUEUED.value
    job.error = None
    job.claimed_until = None
    await session.commit()

    # Wake a worker. Without the notify the job still runs, but not until the idle poll
    # comes round, which makes the retry button feel broken for several seconds.
    await Queue(session).notify(job.pool)
    await session.commit()
    return job
