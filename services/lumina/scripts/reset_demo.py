"""Clear the development database down to one account and a handful of believable projects.

Development leaves a lot behind. Every browser run of the test harness signs up a throwaway
account, every experiment leaves a project, and a bug that has since been fixed leaves its
evidence in the titles — a screenful of "No speech detected" and "Untitled" from before the
transcriber learned to refuse rather than invent.

None of it is precious and all of it is in the way, so this puts the database back to
something worth looking at: the accounts named on the command line, a few projects each with a
real plan, and nothing else.

    uv run python scripts/reset_demo.py keep@example.com

It is destructive and says so. Nothing here runs against anything but the configured
`DATABASE_URL`, which for this repository is the local development cluster.
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from lumina.db.base import get_engine
from lumina.db.models import Channel, Job, Project, User
from lumina.domain.money import Cents
from lumina.domain.plan import Recipe
from lumina.execution import stages
from lumina.execution.providers.fake import FakeProvider
from lumina.execution.router import Router
from lumina.state.assets import AssetStore
from lumina.state.ledger import Ledger

#: What a freshly reset account has to look at. Real briefs, so the planner produces real
#: scenes and every screen downstream has something honest to show.
SEED: list[tuple[str, Recipe]] = [
    ("Why the sky is blue, in ninety seconds", Recipe.EXPLAINER),
    ("How rainbows actually form", Recipe.EXPLAINER),
    ("The Matrix, and why the first one still holds up", Recipe.RECAP),
]

#: The starting balance a demo account gets, in credits.
GRANT = Cents(500)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("keep", nargs="*", help="email addresses to keep (others are deleted)")
    parser.add_argument("--seed", action="store_true", help="add demo projects to kept accounts")
    parser.add_argument("--yes", action="store_true", help="do it without asking")
    args = parser.parse_args()

    keep = {e.strip().lower() for e in args.keep if e.strip()}
    engine = get_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async with sessions() as session:
        users = int(await session.scalar(select(func.count()).select_from(User)) or 0)
        projects = int(await session.scalar(select(func.count()).select_from(Project)) or 0)
        doomed = list(
            await session.scalars(select(User).where(func.lower(User.email).not_in(keep)))
        )

    print(f"database has {users} accounts and {projects} projects")
    print(f"keeping    {sorted(keep) or '(nothing)'}")
    print(f"deleting   {len(doomed)} accounts and everything belonging to them")
    if args.seed:
        print(f"seeding    {len(SEED)} projects on each kept account")

    # Off the event loop: `input` blocks, and blocking the loop from inside a coroutine is
    # the habit that matters for real in a worker even when it is harmless in a script.
    if not args.yes and await asyncio.to_thread(_confirm) is False:
        print("nothing changed")
        return 1

    async with sessions() as session:
        # Accounts first: channels, projects, plans, scenes, renders and ledger entries are
        # all cascades from here. Assets are deliberately untouched — they are
        # content-addressed and shared, so the upload behind a deleted project is very likely
        # behind a kept one too.
        if doomed:
            await session.execute(delete(User).where(User.id.in_([u.id for u in doomed])))

        # Then the leftovers on the accounts being kept. Every one of these titles is the
        # signature of an experiment or of a bug that has since been fixed.
        await session.execute(
            delete(Project).where(
                Project.title.in_(("No speech detected", "Untitled"))
                | Project.title.like("Welcome to the Example Subtitle File!%")
            )
        )
        await session.commit()

    async with sessions() as session:
        # Jobs whose project is gone.
        #
        # These do not always cascade: `jobs.project_id` existed from the start and nothing
        # wrote to it until recently, so every job enqueued before that has a null there and
        # survives the deletion of everything it belonged to. Finished ones are only clutter,
        # but a queued orphan is worse — a worker will claim it and fail on a scene that no
        # longer exists.
        stale = list(
            await session.scalars(
                select(Job.id).where(
                    Job.project_id.is_(None) | Job.project_id.not_in(select(Project.id))
                )
            )
        )
        if stale:
            await session.execute(delete(Job).where(Job.id.in_(stale)))
            print(f"  removed {len(stale)} orphaned jobs")
        await session.commit()

    if args.seed and keep:
        await _seed(sessions, keep)

    async with sessions() as session:
        users = int(await session.scalar(select(func.count()).select_from(User)) or 0)
        projects = int(await session.scalar(select(func.count()).select_from(Project)) or 0)
    print(f"\nnow: {users} accounts, {projects} projects")
    await engine.dispose()
    return 0


def _confirm() -> bool:
    return input("\ntype 'reset' to continue: ").strip() == "reset"


async def _seed(sessions: async_sessionmaker[Any], keep: set[str]) -> None:
    """Give each kept account a few planned projects.

    Planned through the real stage rather than inserted as rows, so the scenes, durations and
    prices are the ones the product actually produces — a fixture that skips the planner is a
    fixture that stops matching it.
    """
    async with sessions() as session:
        rows = list(await session.scalars(select(User).where(func.lower(User.email).in_(keep))))
        for user in rows:
            channel = await session.scalar(select(Channel).where(Channel.user_id == user.id))
            if channel is None:
                continue

            available, _, _ = await Ledger(session).balance(user.id)
            if available < GRANT:
                await Ledger(session).grant(user.id, GRANT, reason="demo_reset")

            ctx = stages.Ctx(
                session=session,
                router=Router(providers=[FakeProvider()]),
                assets=AssetStore(session),
                ledger=Ledger(session),
            )
            for brief, recipe in SEED:
                project = Project(
                    channel_id=channel.id,
                    title=brief[:80],
                    recipe=recipe.value,
                    language=channel.language,
                )
                session.add(project)
                await session.flush()
                await stages.plan(
                    ctx,
                    {
                        "project_id": str(project.id),
                        "brief": brief,
                        "recipe": recipe.value,
                        "language": channel.language,
                        "target_ms": 60_000,
                    },
                )
            print(f"  seeded {len(SEED)} projects for {user.email}")
        await session.commit()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
