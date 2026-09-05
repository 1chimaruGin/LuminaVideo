"""The API, against a real database.

Exercises the path the frontend will take: create a project, read its plan, price it, edit a
scene, queue a draft. Uses httpx against the ASGI app rather than a live server, so it is
fast, but nothing below the transport is faked.
"""

from __future__ import annotations

import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lumina.api.main import app
from lumina.db.base import get_session
from lumina.db.models import Channel, User
from lumina.domain.money import Cents
from lumina.state.ledger import Ledger

DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB, reason="TEST_DATABASE_URL not set")


@pytest.fixture
async def engine():
    """One engine per test, disposed after.

    The app's own engine is built from cached settings at import time, so it would connect
    to whatever DATABASE_URL happened to be set in the shell rather than to the test
    database. Overriding the dependency below makes the test say which database it means
    instead of depending on ambient environment.
    """
    eng = create_async_engine(DB or "")
    yield eng
    await eng.dispose()


@pytest.fixture
async def user_id(engine):
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        u = User(email=f"{uuid.uuid4()}@example.com")
        s.add(u)
        await s.flush()
        s.add(Channel(user_id=u.id, name="Test", language="en", identity={}))
        await Ledger(s).grant(u.id, Cents(500))
        await s.commit()
        uid = u.id
    return uid


@pytest.fixture
async def api(engine):
    """The app, wired to the test database."""
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def client(api, user_id):
    api.headers["X-Lumina-User"] = str(user_id)
    return api


async def test_health_needs_no_auth(api):
    assert (await api.get("/health")).json() == {"status": "ok"}


async def test_unauthenticated_requests_are_rejected(api):
    assert (await api.get("/projects")).status_code == 401


async def test_create_project_returns_a_planned_storyboard(client):
    r = await client.post(
        "/projects",
        json={
            "brief": (
                "Light is fast but not instant. The Sun you see is eight minutes old. "
                "Look far enough and you are looking backwards in time."
            ),
            "recipe": "explainer",
            "language": "en",
        },
    )
    assert r.status_code == 201, r.text
    plan = r.json()

    assert plan["version"] == 1
    assert len(plan["scenes"]) >= 3
    # Scenes come back ordered and dense, which the storyboard depends on.
    assert [s["index"] for s in plan["scenes"]] == list(range(len(plan["scenes"])))
    for scene in plan["scenes"]:
        assert scene["state"] == "draft"
        assert scene["prompt"] and scene["script_line"]
        assert scene["duration_ms"] > 0


async def test_quote_prices_the_plan_without_side_effects(client):
    created = (await client.post("/projects", json={"brief": "One. Two. Three. Four."})).json()
    projects = (await client.get("/projects")).json()
    project_id = projects[0]["id"]

    before = (await client.get("/credits")).json()
    quote = (await client.get(f"/credits/quote/{project_id}")).json()
    after = (await client.get("/credits")).json()

    assert quote["credits"] > 0
    assert len(quote["per_scene"]) == len(created["scenes"])
    assert before == after, "pricing a plan moved credits"


async def test_editing_one_scene_leaves_the_others_alone(client):
    plan = (await client.post("/projects", json={"brief": "One. Two. Three."})).json()
    target, other = plan["scenes"][0], plan["scenes"][1]

    r = await client.patch(f"/scenes/{target['id']}", json={"prompt": "a new picture"})
    assert r.status_code == 200
    assert r.json()["prompt"] == "a new picture"
    # Only the named field moved.
    assert r.json()["script_line"] == target["script_line"]

    project_id = (await client.get("/projects")).json()[0]["id"]
    after = (await client.get(f"/projects/{project_id}/plan")).json()
    untouched = next(s for s in after["scenes"] if s["id"] == other["id"])
    assert untouched == other, "editing one scene changed another"


async def test_draft_reserves_up_front_but_charges_nothing(client):
    """Invariant 4, and the distinction the whole ledger exists for.

    Approving a plan HOLDS what it will cost — that is what stops a user queueing a hundred
    scenes against a balance covering ten. It does not CHARGE, because nothing has run yet and
    the estimate will not be what it actually cost.

    An earlier version of this test asserted the balance was untouched, which read as
    "queueing must not charge" and was satisfied by reserving nothing at all.
    """
    await client.post("/projects", json={"brief": "One. Two. Three."})
    project_id = (await client.get("/projects")).json()[0]["id"]

    quote = (await client.get(f"/credits/quote/{project_id}")).json()
    before = (await client.get("/credits")).json()

    r = await client.post(f"/projects/{project_id}/draft")
    assert r.status_code == 202
    assert r.json()["queued"] >= 4, "one per scene, plus voice"

    after = (await client.get("/credits")).json()
    assert after["committed"] == before["committed"] == 0, "nothing has run, so nothing is spent"
    assert after["reserved"] == before["reserved"] + quote["credits"], (
        "the whole quote is held the moment the plan is approved"
    )
    assert after["available"] == before["available"] - quote["credits"]


async def test_a_draft_nobody_can_afford_is_refused_before_it_queues(client, engine):
    """The failure a user can act on.

    Reserving inside the worker meant an overdraft surfaced as jobs dying one by one with
    nothing on screen to explain it. Refusing here says how far short they are.
    """
    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from lumina.db.models import LedgerEntry

    await client.post("/projects", json={"brief": "One. Two. Three. Four. Five."})
    project_id = (await client.get("/projects")).json()[0]["id"]

    # Spend the balance down to nothing by removing the welcome grant.
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        await s.execute(delete(LedgerEntry))
        await s.commit()

    r = await client.post(f"/projects/{project_id}/draft")
    assert r.status_code == 402, r.text
    detail = r.json()["detail"]
    assert detail["short_by"] > 0
    assert detail["needed"] > detail["available"]

    # And nothing was queued: a refusal must not leave half the work running.
    balance = (await client.get("/credits")).json()
    assert balance["reserved"] == 0
