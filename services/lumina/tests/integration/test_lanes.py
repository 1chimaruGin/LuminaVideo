"""The four API surfaces that did not exist: uploads, assets, per-scene actions, renders.

Against a real database and real ffmpeg. The upload tests in particular are pointless with a
mocked probe — the whole claim being tested is that we measure the file rather than trust what
the client said it was.
"""

from __future__ import annotations

import os
import re
import subprocess
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lumina.api.main import app
from lumina.db.base import get_session
from lumina.db.models import Asset, Channel, Scene, User
from lumina.domain.money import Cents
from lumina.domain.plan import Recipe
from lumina.state.ledger import Ledger

DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB, reason="TEST_DATABASE_URL not set")


@pytest.fixture
async def engine():
    eng = create_async_engine(DB or "")
    yield eng
    await eng.dispose()


@pytest.fixture
async def maker(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def user_id(maker):
    async with maker() as s:
        u = User(email=f"{uuid.uuid4()}@example.com")
        s.add(u)
        await s.flush()
        s.add(Channel(user_id=u.id, name="Test", language="en", identity={}))
        await Ledger(s).grant(u.id, Cents(2000))
        await s.commit()
        return u.id


@pytest.fixture
async def api(maker):
    async def override():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def clip(tmp_path: Path) -> Path:
    """A real 3-second 640x360 video with a tone on it."""
    out = tmp_path / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=640x360:rate=30:duration=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            "-shortest",
            str(out),
        ],
        check=True,
    )
    return out


def auth(user_id: uuid.UUID) -> dict[str, str]:
    return {"X-Lumina-User": str(user_id)}


# --------------------------------------------------------------------------- uploads


@pytest.mark.anyio
async def test_upload_measures_the_file_rather_than_trusting_the_client(api, user_id, clip):
    """Duration and dimensions are probed, not read from the request.

    Everything downstream sizes itself from these numbers, and a mis-stated duration produces
    captions that drift further out of sync the longer the video runs.
    """
    with clip.open("rb") as fh:
        r = await api.post(
            "/uploads", headers=auth(user_id), files={"file": ("lie.mp4", fh, "video/mp4")}
        )
    assert r.status_code == 201, r.text
    body = r.json()

    assert body["kind"] == "video"
    assert 2900 <= body["duration_ms"] <= 3100
    assert (body["width"], body["height"]) == (640, 360)
    assert body["has_audio"] is True
    assert body["aspect"] == "16:9"


@pytest.mark.anyio
async def test_upload_refuses_a_type_no_stage_can_consume(api, user_id):
    """A spreadsheet is not something any stage knows how to open."""
    r = await api.post(
        "/uploads",
        headers=auth(user_id),
        files={"file": ("book.xlsx", b"PK\x03\x04", "application/vnd.ms-excel")},
    )
    assert r.status_code == 415
    assert "subtitle" in r.json()["detail"], "the refusal lists what would have worked"


@pytest.mark.anyio
async def test_upload_takes_timed_text_because_reading_beats_listening(api, user_id):
    """Subtitle and Clip are about what was said. When the creator already has that written
    down, reading it is exact, free, and works on a server with no speech engine — which is
    every server this runs on today. So text is a first-class upload, not a refusal."""
    r = await api.post(
        "/uploads",
        headers=auth(user_id),
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert r.status_code == 201
    assert r.json()["kind"] == "text"


@pytest.mark.anyio
async def test_upload_takes_an_srt_the_browser_could_not_name(api, user_id):
    """`.srt` arrives as `application/x-subrip`, `application/octet-stream`, or nothing at
    all depending on the platform. Refusing on the type would refuse a working subtitle file
    on most of them."""
    r = await api.post(
        "/uploads",
        headers=auth(user_id),
        files={"file": ("ep1.srt", b"1\n00:00:01,000 --> 00:00:02,000\nHi\n", "")},
    )
    assert r.status_code == 201
    assert r.json()["kind"] == "text"


@pytest.mark.anyio
async def test_upload_refuses_something_that_is_not_media(api, user_id):
    """A file ffprobe cannot open is not media whatever it claimed. Rejecting here rather
    than storing it means the failure lands on the request at fault, not four stages later
    inside a worker."""
    r = await api.post(
        "/uploads",
        headers=auth(user_id),
        files={"file": ("fake.mp4", b"not actually an mp4", "video/mp4")},
    )
    assert r.status_code == 422


@pytest.mark.anyio
async def test_upload_refuses_an_empty_file(api, user_id):
    r = await api.post(
        "/uploads", headers=auth(user_id), files={"file": ("empty.mp4", b"", "video/mp4")}
    )
    assert r.status_code == 400


@pytest.mark.anyio
async def test_upload_enforces_its_limit_by_counting_bytes(api, user_id, monkeypatch, clip):
    """The limit is enforced against what actually arrived.

    Content-Length is a claim by the sender, and a chunked request need not send one at all —
    checking it would reject honest large files and admit dishonest ones.
    """
    from lumina.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "max_upload_bytes", 1024, raising=False)

    with clip.open("rb") as fh:
        r = await api.post(
            "/uploads", headers=auth(user_id), files={"file": ("big.mp4", fh, "video/mp4")}
        )
    assert r.status_code == 413


@pytest.mark.anyio
async def test_identical_uploads_are_one_asset(api, user_id, clip):
    """Content-addressed: the same bytes are the same asset. This is the dedup that makes
    regeneration a cache hit rather than a second charge."""
    ids = []
    for _ in range(2):
        with clip.open("rb") as fh:
            r = await api.post(
                "/uploads", headers=auth(user_id), files={"file": ("a.mp4", fh, "video/mp4")}
            )
        ids.append(r.json()["asset_id"])
    assert ids[0] == ids[1]


# --------------------------------------------------------------------------- assets


@pytest.mark.anyio
async def test_asset_serves_a_range_so_a_player_can_seek(api, user_id, clip):
    """A `<video>` element seeks by asking for byte windows. An endpoint that only ever
    returns 200 with the whole body gives you a player that cannot scrub."""
    with clip.open("rb") as fh:
        asset_id = (
            await api.post(
                "/uploads", headers=auth(user_id), files={"file": ("a.mp4", fh, "video/mp4")}
            )
        ).json()["asset_id"]

    whole = await api.get(f"/assets/{asset_id}")
    assert whole.status_code == 200
    assert whole.headers["accept-ranges"] == "bytes"
    total = len(whole.content)

    part = await api.get(f"/assets/{asset_id}", headers={"Range": "bytes=0-999"})
    assert part.status_code == 206
    assert len(part.content) == 1000
    assert part.headers["content-range"] == f"bytes 0-999/{total}"
    assert part.content == whole.content[:1000]

    # A suffix range: the last N bytes, which is how a player finds a trailing moov atom.
    tail = await api.get(f"/assets/{asset_id}", headers={"Range": "bytes=-500"})
    assert tail.status_code == 206
    assert tail.content == whole.content[-500:]


@pytest.mark.anyio
async def test_asset_ignores_an_unsatisfiable_range_rather_than_erroring(api, user_id, clip):
    """A player handed a 416 will not retry. Sending the bytes it asked about is better."""
    with clip.open("rb") as fh:
        asset_id = (
            await api.post(
                "/uploads", headers=auth(user_id), files={"file": ("a.mp4", fh, "video/mp4")}
            )
        ).json()["asset_id"]

    r = await api.get(f"/assets/{asset_id}", headers={"Range": "bytes=99999999-"})
    assert r.status_code == 200


@pytest.mark.anyio
async def test_unknown_asset_is_a_404(api):
    assert (await api.get(f"/assets/{uuid.uuid4()}")).status_code == 404


# --------------------------------------------------------------------------- scenes


async def _plan(api, user_id, **over):
    body = {
        "brief": "Black holes are not vacuum cleaners. "
        "Orbit one and you just orbit. "
        "The danger is only close in.",
        "recipe": "explainer",
        "target_ms": 12_000,
        **over,
    }
    r = await api.post("/projects", headers=auth(user_id), json=body)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.anyio
async def test_move_reorders_without_breaking_the_dense_index(api, user_id, maker):
    """`index` is dense and unique per plan, so a move necessarily shifts its neighbours.

    Assigning the final indices directly would momentarily collide with a row still holding
    the target index and trip the unique constraint mid-statement, so the handler parks the
    moved scene outside the range first. This test is what would catch that regressing.
    """
    plan = await _plan(api, user_id)
    scenes = plan["scenes"]
    assert len(scenes) >= 3
    last = scenes[-1]

    r = await api.post(f"/scenes/{last['id']}/move", headers=auth(user_id), json={"index": 0})
    assert r.status_code == 200, r.text
    after = r.json()

    assert [s["index"] for s in after] == list(range(len(after))), "indices stayed dense"
    assert after[0]["id"] == last["id"], "the moved scene landed first"
    assert {s["id"] for s in after} == {s["id"] for s in scenes}, "nothing was lost"


@pytest.mark.anyio
async def test_move_past_the_end_clamps(api, user_id):
    plan = await _plan(api, user_id)
    first = plan["scenes"][0]
    r = await api.post(f"/scenes/{first['id']}/move", headers=auth(user_id), json={"index": 99})
    assert r.status_code == 200
    assert r.json()[-1]["id"] == first["id"]


@pytest.mark.anyio
async def test_upgrade_refuses_a_preview_tier(api, user_id):
    """Upgrading to a preview tier is a category error — that is what regenerate is for."""
    plan = await _plan(api, user_id)
    scene = plan["scenes"][0]
    r = await api.post(
        f"/scenes/{scene['id']}/upgrade", headers=auth(user_id), params={"tier": "image_motion"}
    )
    assert r.status_code == 400


@pytest.mark.anyio
async def test_upgrade_from_draft_is_a_conflict_not_a_crash(api, user_id):
    """A scene with no preview yet cannot be upgraded. The request was well-formed, so it is
    a 409 — usually the client simply did not know a job had not landed."""
    plan = await _plan(api, user_id)
    scene = plan["scenes"][0]
    r = await api.post(f"/scenes/{scene['id']}/upgrade", headers=auth(user_id))
    assert r.status_code == 409


@pytest.mark.anyio
async def test_revert_returns_to_the_preview_that_was_never_overwritten(api, user_id, maker):
    """A final never overwrote the preview — both assets are on the row. That is what makes
    an upgrade the user dislikes recoverable rather than a purchase they are stuck with."""
    from lumina.domain.assets import AssetKind
    from lumina.domain.plan import SceneState, Tier
    from lumina.state.assets import AssetStore

    plan = await _plan(api, user_id)
    scene_id = uuid.UUID(plan["scenes"][0]["id"])

    async with maker() as s:
        store = AssetStore(s)
        preview = await store.put_bytes(b"preview-bytes", kind=AssetKind.IMAGE, mime="image/png")
        final = await store.put_bytes(b"final-bytes", kind=AssetKind.CLIP, mime="video/mp4")
        scene = await s.get(Scene, scene_id)
        assert scene is not None
        scene.preview_asset_id, scene.final_asset_id = preview.id, final.id
        scene.tier, scene.state = Tier.MID_VIDEO.value, SceneState.FINAL.value
        await s.commit()
        preview_id = preview.id

    r = await api.post(f"/scenes/{scene_id}/revert", headers=auth(user_id))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["final_asset_id"] is None
    assert body["preview_asset_id"] == str(preview_id), "the preview survived the upgrade"
    assert body["state"] == "preview_ready"


@pytest.mark.anyio
async def test_revert_with_nothing_to_revert_is_a_conflict(api, user_id):
    plan = await _plan(api, user_id)
    r = await api.post(f"/scenes/{plan['scenes'][0]['id']}/revert", headers=auth(user_id))
    assert r.status_code == 409


# --------------------------------------------------------------------------- recipes


@pytest.mark.anyio
async def test_every_recipe_is_advertised_with_the_stages_it_runs(api):
    """The storyboard draws its pipeline from this, so a lane that skips generation shows
    four steps rather than a greyed-out fifth."""
    r = await api.get("/projects/recipes")
    assert r.status_code == 200
    lanes = {x["id"]: x for x in r.json()}

    assert set(lanes) == {"explainer", "dub", "narrate", "clip_long_video", "subtitle_only"}
    assert lanes["subtitle_only"]["stages"] == ["ingest", "transcribe", "captions", "compose"]
    assert "generate" not in lanes["subtitle_only"]["stages"], "subtitles never draws a frame"
    assert lanes["subtitle_only"]["needs_source"] is True
    assert lanes["explainer"]["needs_source"] is False

    # Dub is a source lane, not a scripted one. It shipped as "Voice over" — PLAN, GENERATE,
    # VOICE — which drew new footage for a video the creator had already made, and asked for
    # a script when what it needs is the video.
    assert lanes["dub"]["stages"] == ["ingest", "transcribe", "captions", "voice", "compose"]
    assert "generate" not in lanes["dub"]["stages"], "a dub keeps their frames"
    assert lanes["dub"]["needs_source"] is True
    assert lanes["dub"]["stages"].index("captions") < lanes["dub"]["stages"].index("voice"), (
        "the words have to be translated before anyone can say them"
    )

    # Narrate hears nothing and draws nothing: the creator writes the words, and the footage
    # they picked runs underneath. Its clock comes from the narration, so VOICE runs before
    # CAPTIONS — the opposite of Dub, and the reason the two cannot share a stage list.
    assert lanes["narrate"]["stages"] == ["plan", "voice", "captions", "compose"]
    assert lanes["narrate"]["needs_source"] is True, "there is nothing to narrate over"
    assert lanes["narrate"]["stages"].index("voice") < lanes["narrate"]["stages"].index(
        "captions"
    ), "the narration is what decides how long each caption is on screen"


@pytest.mark.anyio
async def test_a_source_lane_without_a_source_is_refused_with_a_next_step(api, user_id):
    r = await api.post(
        "/projects",
        headers=auth(user_id),
        json={"brief": "subtitle it", "recipe": "subtitle_only"},
    )
    assert r.status_code == 400
    assert "/uploads" in r.json()["detail"], "the error says what to do next"


@pytest.mark.anyio
async def test_subtitle_lane_plans_from_the_transcript_not_the_brief(api, user_id, clip, hears):
    """Otherwise "subtitle this video" is planned as though the brief were the script, and
    the captions summarise the video instead of transcribing it."""
    hears("Light is fast.", "But it is not instant.", "That changes everything.")
    with clip.open("rb") as fh:
        up = (
            await api.post(
                "/uploads", headers=auth(user_id), files={"file": ("a.mp4", fh, "video/mp4")}
            )
        ).json()

    r = await api.post(
        "/projects",
        headers=auth(user_id),
        json={
            "brief": "My holiday video",
            "recipe": "subtitle_only",
            "source_asset_id": up["asset_id"],
        },
    )
    assert r.status_code == 201, r.text
    plan = r.json()
    assert plan["scenes"], "the transcript produced lines"
    assert all(s["prompt"] == "" for s in plan["scenes"]), (
        "a lane that keeps the user's footage must not carry image prompts"
    )


@pytest.mark.anyio
async def test_draft_queues_only_the_stages_the_recipe_runs(api, user_id, clip, hears):
    """Queueing generate and voice for a subtitle job would charge for work whose output is
    thrown away, and leave the storyboard waiting on jobs with nothing to do."""
    hears("Light is fast.", "But it is not instant.", "That changes everything.")
    with clip.open("rb") as fh:
        up = (
            await api.post(
                "/uploads", headers=auth(user_id), files={"file": ("a.mp4", fh, "video/mp4")}
            )
        ).json()
    await api.post(
        "/projects",
        headers=auth(user_id),
        json={
            "brief": "Subtitles",
            "recipe": "subtitle_only",
            "source_asset_id": up["asset_id"],
        },
    )
    project = (await api.get("/projects", headers=auth(user_id))).json()[0]

    r = await api.post(f"/projects/{project['id']}/draft", headers=auth(user_id))
    assert r.status_code == 202, r.text
    body = r.json()
    assert "generate" not in body["stages"] and "voice" not in body["stages"]
    assert body["queued"] == 0, "subtitles has nothing to queue before the render"


# --------------------------------------------------------------------------- renders


@pytest.mark.anyio
async def test_render_refuses_a_storyboard_with_holes_and_says_which(api, user_id):
    """ "Generate the draft first" is actionable; "compose failed" is not."""
    plan = await _plan(api, user_id)
    project = (await api.get("/projects", headers=auth(user_id))).json()[0]

    r = await api.post(
        f"/renders/project/{project['id']}", headers=auth(user_id), json={"aspects": ["9:16"]}
    )
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "1" in detail, "the message names the scenes that are not ready"
    assert len(plan["scenes"]) >= 1


@pytest.mark.anyio
async def test_render_rejects_an_unknown_aspect(api, user_id):
    plan = await _plan(api, user_id)
    project = (await api.get("/projects", headers=auth(user_id))).json()[0]
    r = await api.post(
        f"/renders/project/{project['id']}", headers=auth(user_id), json={"aspects": ["3:7"]}
    )
    assert r.status_code == 400
    assert len(plan["scenes"]) >= 1


@pytest.mark.anyio
async def test_render_makes_one_row_per_aspect_and_queues_captions_first(api, user_id, maker):
    """One row per aspect: they finish at different times and any one can fail alone.

    Only captions is enqueued — it chains to compose once the states exist. Enqueuing both
    would race, and compose would claim its job and find nothing to burn in.
    """
    from sqlalchemy import select

    from lumina.db.models import Job

    plan = await _plan(api, user_id)
    project = (await api.get("/projects", headers=auth(user_id))).json()[0]

    # Give every scene something to show, so the render is not refused for holes.
    async with maker() as s:
        from lumina.domain.assets import AssetKind
        from lumina.state.assets import AssetStore

        asset = await AssetStore(s).put_bytes(b"a-frame", kind=AssetKind.IMAGE, mime="image/png")
        for scene in plan["scenes"]:
            row = await s.get(Scene, uuid.UUID(scene["id"]))
            assert row is not None
            row.preview_asset_id = asset.id
        await s.commit()

    r = await api.post(
        f"/renders/project/{project['id']}",
        headers=auth(user_id),
        json={"aspects": ["9:16", "1:1"], "caption_style": "pop"},
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    assert [x["aspect"] for x in rows] == ["9:16", "1:1"]
    assert all(x["status"] == "queued" and x["asset_id"] is None for x in rows)

    async with maker() as s:
        queued = list(
            await s.scalars(
                select(Job).where(
                    Job.payload["plan_id"].astext == plan["id"], Job.status == "queued"
                )
            )
        )
    stages = {j.stage for j in queued}
    assert stages == {"captions"}, f"compose must not be queued yet, got {stages}"
    assert len(queued) == 2, "one captions job per aspect"


# --------------------------------------------------------------------------- auth


def _creds(name: str = "") -> dict[str, str]:
    return {"email": f"{name or uuid.uuid4()}@example.com", "password": "a-real-password"}


@pytest.mark.anyio
async def test_signing_up_creates_the_account_its_channel_and_its_balance(api):
    body = _creds()
    r = await api.post("/auth/signup", json={**body, "display_name": "Ada"})
    assert r.status_code == 201, r.text

    out = r.json()
    assert out["token"], "a session token, not the user id"
    assert out["user_id"] not in out["token"], "the token must not simply carry the id in clear"
    assert out["channel_id"], "every user has a channel or has nothing to inherit"
    assert out["credits"] >= 100, "the free tier must be genuinely usable, not a taste"


@pytest.mark.anyio
async def test_signing_up_twice_is_refused_and_says_why(api):
    """The person needs to know they already have an account so they can sign in instead."""
    body = _creds()
    assert (await api.post("/auth/signup", json=body)).status_code == 201

    again = await api.post("/auth/signup", json=body)
    assert again.status_code == 409
    assert "already" in again.json()["detail"].lower()


@pytest.mark.anyio
async def test_the_welcome_grant_happens_once(api):
    """Getting this wrong turns a sign-in button into a money printer."""
    body = _creds()
    first = (await api.post("/auth/signup", json=body)).json()
    for _ in range(3):
        again = (await api.post("/auth/session", json=body)).json()
    assert again["user_id"] == first["user_id"]
    assert again["credits"] == first["credits"]


@pytest.mark.anyio
async def test_signing_in_needs_the_right_password(api):
    """The whole point. Before this, typing any address signed you in as its owner."""
    body = _creds()
    await api.post("/auth/signup", json=body)

    good = await api.post("/auth/session", json=body)
    assert good.status_code == 200, good.text
    assert good.json()["token"]

    bad = await api.post("/auth/session", json={**body, "password": "not-the-password"})
    assert bad.status_code == 401


@pytest.mark.anyio
async def test_a_wrong_password_and_an_unknown_account_are_indistinguishable(api):
    """Different messages would turn this endpoint into an oracle for which addresses are
    registered — which is exactly what an attacker enumerating accounts wants."""
    body = _creds()
    await api.post("/auth/signup", json=body)

    wrong = await api.post("/auth/session", json={**body, "password": "not-the-password"})
    unknown = await api.post("/auth/session", json=_creds())

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"]


@pytest.mark.anyio
async def test_a_weak_password_is_refused_at_signup(api):
    r = await api.post(
        "/auth/signup", json={"email": f"{uuid.uuid4()}@example.com", "password": "short"}
    )
    assert r.status_code in (400, 422)


@pytest.mark.anyio
async def test_an_address_that_cannot_receive_mail_is_not_an_account(api):
    r = await api.post(
        "/auth/signup", json={"email": "not-an-email", "password": "a-real-password"}
    )
    assert r.status_code == 422


@pytest.mark.anyio
async def test_email_case_and_space_do_not_make_a_second_account(api):
    """Otherwise `Ada@example.com` and `ada@example.com` are two accounts, two welcome grants,
    and a person who cannot sign in to the one they made."""
    stem = uuid.uuid4()
    made = (
        await api.post(
            "/auth/signup", json={"email": f"{stem}@Example.COM", "password": "a-real-password"}
        )
    ).json()

    back = await api.post(
        "/auth/session", json={"email": f"  {stem}@example.com  ", "password": "a-real-password"}
    )
    assert back.status_code == 200, back.text
    assert back.json()["user_id"] == made["user_id"]


@pytest.mark.anyio
async def test_the_token_is_what_authorises_a_request(api):
    body = _creds()
    token = (await api.post("/auth/signup", json=body)).json()["token"]

    me = await api.get("/auth/session", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, me.text
    assert me.json()["email"] == body["email"].lower()


@pytest.mark.anyio
async def test_a_tampered_or_missing_token_is_refused(api):
    body = _creds()
    token = (await api.post("/auth/signup", json=body)).json()["token"]

    assert (await api.get("/auth/session")).status_code == 401
    assert (
        await api.get("/auth/session", headers={"Authorization": f"Bearer {token}x"})
    ).status_code == 401
    assert (await api.get("/auth/session", headers={"Authorization": token})).status_code == 401, (
        "a bare token without the Bearer scheme is not a credential"
    )


@pytest.mark.anyio
async def test_an_expired_token_is_refused(api, monkeypatch):
    """A session that never expires is a credential that can never be revoked."""
    from datetime import timedelta

    from lumina.platform import auth as platform_auth

    body = _creds()
    await api.post("/auth/signup", json=body)

    monkeypatch.setattr(platform_auth, "SESSION_TTL", timedelta(seconds=-1))
    stale = platform_auth.issue(uuid.uuid4())
    r = await api.get("/auth/session", headers={"Authorization": f"Bearer {stale}"})
    assert r.status_code == 401


@pytest.mark.anyio
async def test_a_token_signed_with_another_secret_is_refused(api):
    """Pinning the algorithm and the secret is what stops a token asserting its own terms."""
    import jwt

    from lumina.platform import auth as platform_auth

    forged = jwt.encode(
        {"sub": str(uuid.uuid4()), "exp": 2**31}, "not-our-secret", algorithm="HS256"
    )
    assert platform_auth.read(forged) is None

    r = await api.get("/auth/session", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401


# --------------------------------------------------------------------------- identity kit


@pytest.mark.anyio
async def test_identity_merges_so_one_setting_does_not_drop_another(api, user_id):
    """A whole-document PUT loses the caption style the first time two settings screens are
    open at once. The merge is the whole reason this is a PATCH."""
    await api.patch("/channel", headers=auth(user_id), json={"identity": {"caption_style": "pop"}})
    await api.patch("/channel", headers=auth(user_id), json={"identity": {"palette": ["#ffc36b"]}})

    kit = (await api.get("/channel", headers=auth(user_id))).json()
    assert kit["identity"] == {"caption_style": "pop", "palette": ["#ffc36b"]}


@pytest.mark.anyio
async def test_identity_edit_leaves_absent_fields_alone(api, user_id):
    before = (await api.get("/channel", headers=auth(user_id))).json()
    await api.patch("/channel", headers=auth(user_id), json={"name": "Renamed"})
    after = (await api.get("/channel", headers=auth(user_id))).json()
    assert after["name"] == "Renamed"
    assert after["language"] == before["language"]


@pytest.mark.anyio
async def test_adding_a_scene_keeps_the_index_dense(api, user_id):
    """The plan is user-editable, not hidden reasoning: the user adds the shot the planner
    did not think of. Inserting shifts everything after it, which is where the unique index
    constraint gets tested."""
    plan = await _plan(api, user_id)
    before = len(plan["scenes"])

    r = await api.post(
        f"/scenes/plan/{plan['id']}",
        headers=auth(user_id),
        json={"index": 1, "script_line": "An inserted line.", "duration_ms": 3000},
    )
    assert r.status_code == 201, r.text
    assert r.json()["index"] == 1

    project = (await api.get("/projects", headers=auth(user_id))).json()[0]
    fresh = (await api.get(f"/projects/{project['id']}/plan", headers=auth(user_id))).json()
    assert len(fresh["scenes"]) == before + 1
    assert [s["index"] for s in fresh["scenes"]] == list(range(before + 1))
    assert fresh["scenes"][1]["script_line"] == "An inserted line."


@pytest.mark.anyio
async def test_adding_without_an_index_appends(api, user_id):
    plan = await _plan(api, user_id)
    r = await api.post(
        f"/scenes/plan/{plan['id']}", headers=auth(user_id), json={"script_line": "Last."}
    )
    assert r.status_code == 201
    assert r.json()["index"] == len(plan["scenes"])


@pytest.mark.anyio
async def test_deleting_a_scene_closes_the_gap(api, user_id):
    plan = await _plan(api, user_id)
    victim = plan["scenes"][0]

    r = await api.delete(f"/scenes/{victim['id']}", headers=auth(user_id))
    assert r.status_code == 204, r.text

    project = (await api.get("/projects", headers=auth(user_id))).json()[0]
    fresh = (await api.get(f"/projects/{project['id']}/plan", headers=auth(user_id))).json()
    assert [s["index"] for s in fresh["scenes"]] == list(range(len(plan["scenes"]) - 1))
    assert victim["id"] not in {s["id"] for s in fresh["scenes"]}


@pytest.mark.anyio
async def test_the_last_scene_cannot_be_deleted(api, user_id):
    """A plan with no scenes cannot be rendered, and the storyboard becomes an empty screen
    with no way back to a video."""
    plan = await _plan(api, user_id)
    project = (await api.get("/projects", headers=auth(user_id))).json()[0]

    for scene in plan["scenes"][:-1]:
        assert (
            await api.delete(f"/scenes/{scene['id']}", headers=auth(user_id))
        ).status_code == 204

    last = (await api.get(f"/projects/{project['id']}/plan", headers=auth(user_id))).json()[
        "scenes"
    ]
    assert len(last) == 1
    r = await api.delete(f"/scenes/{last[0]['id']}", headers=auth(user_id))
    assert r.status_code == 409


def _frontend_recipe_ids() -> set[str] | None:
    """The ids declared in the web app's RECIPES table, or None if it is not checked out.

    Split out of the test because reading a file is blocking, and blocking IO inside an async
    function is the same mistake that matters for real in a worker.
    """
    import re
    from pathlib import Path

    data = Path(__file__).resolve().parents[4] / "apps" / "web" / "src" / "app" / "data.ts"
    if not data.is_file():
        return None

    # The RECIPES table only; other tables in the file also use `id:`.
    body = data.read_text()
    table = body[body.index("export const RECIPES") : body.index("export type BeatKind")]
    return set(re.findall(r"id: '([a-z_]+)'", table))


@pytest.mark.anyio
async def test_the_frontends_recipe_ids_are_the_api_s_recipe_ids(api):
    """The web app sends `recipe` verbatim to POST /projects, so a display slug that is not an
    API value is a 400 the user cannot act on — and it fails silently at build time, because
    the id is a plain string on both sides.

    Three of them were wrong (`talking`, `clip`, `subtitle`), which broke three of the five
    lanes on the Recipe screen. This reads the frontend's own table and compares.
    """
    declared = _frontend_recipe_ids()
    if declared is None:
        pytest.skip("frontend not present in this checkout")

    served = {r["id"] for r in (await api.get("/projects/recipes")).json()}
    assert declared == served, (
        f"frontend recipe ids {sorted(declared)} != API {sorted(served)}; "
        "the id is sent verbatim to POST /projects"
    )


# --------------------------------------------------------------------------- source footage


@pytest.mark.anyio
async def test_a_subtitled_video_is_as_long_as_the_video(api, user_id, clip, maker, hears):
    """Scene durations come from the transcript, not from reading speed.

    Letting the planner choose them produced a two-second subtitle track for an eight-second
    video — correct arithmetic, wrong question. A subtitle track is exactly as long as the
    thing it subtitles.
    """
    from sqlalchemy import select

    hears("Light is fast.", "But it is not instant.", "That changes everything.")
    with clip.open("rb") as fh:
        up = (
            await api.post(
                "/uploads", headers=auth(user_id), files={"file": ("a.mp4", fh, "video/mp4")}
            )
        ).json()

    r = await api.post(
        "/projects",
        headers=auth(user_id),
        json={
            "brief": "Subtitles",
            "recipe": "subtitle_only",
            "source_asset_id": up["asset_id"],
        },
    )
    assert r.status_code == 201, r.text
    plan = r.json()

    async with maker() as s:
        scenes = list(
            await s.scalars(
                select(Scene).where(Scene.plan_id == uuid.UUID(plan["id"])).order_by(Scene.index)
            )
        )

    assert scenes, "the transcript produced at least one line"
    assert all(x.source_start_ms is not None for x in scenes), (
        "every scene must know where in the source it sits, or compose cuts the wrong part"
    )
    covered = sum(x.duration_ms for x in scenes)
    assert abs(covered - up["duration_ms"]) <= up["duration_ms"] * 0.2, (
        f"subtitles covered {covered}ms of a {up['duration_ms']}ms video"
    )


@pytest.mark.anyio
async def test_a_source_lane_renders_without_generating_a_frame(api, user_id, clip, maker, hears):
    """The picture for these lanes *is* the footage, so the render gate must not demand a
    generated asset. It used to, which made the cheapest thing the product sells impossible
    to actually deliver."""
    hears("Light is fast.", "But it is not instant.", "That changes everything.")
    with clip.open("rb") as fh:
        up = (
            await api.post(
                "/uploads", headers=auth(user_id), files={"file": ("a.mp4", fh, "video/mp4")}
            )
        ).json()
    await api.post(
        "/projects",
        headers=auth(user_id),
        json={
            "brief": "Subtitles",
            "recipe": "subtitle_only",
            "source_asset_id": up["asset_id"],
        },
    )
    project = (await api.get("/projects", headers=auth(user_id))).json()[0]

    r = await api.post(
        f"/renders/project/{project['id']}", headers=auth(user_id), json={"aspects": ["9:16"]}
    )
    assert r.status_code == 200, r.text
    assert r.json()[0]["status"] == "queued"


@pytest.mark.anyio
async def test_the_planner_never_invents_lines_for_a_transcribed_lane(api, user_id, clip, hears):
    """Padding a short script to three beats is fine where the lines are being *written*. On a
    lane that transcribes, an invented line is a subtitle for something nobody said — and it
    gets cut against a stretch of video chosen at random."""
    hears("Light is fast.", "But it is not instant.", "That changes everything.")
    with clip.open("rb") as fh:
        up = (
            await api.post(
                "/uploads", headers=auth(user_id), files={"file": ("a.mp4", fh, "video/mp4")}
            )
        ).json()

    plan = (
        await api.post(
            "/projects",
            headers=auth(user_id),
            json={
                "brief": "Subtitles",
                "recipe": "subtitle_only",
                "source_asset_id": up["asset_id"],
            },
        )
    ).json()

    # Three lines were heard, so the plan has three scenes. Not four, not the three-beat
    # minimum the generating lanes pad to: on a lane that transcribes, an invented line is a
    # subtitle for something nobody said.
    assert len(plan["scenes"]) == 3, f"planner invented {len(plan['scenes']) - 3} extra lines"
    assert [s["script_line"] for s in plan["scenes"]] == [
        "Light is fast.",
        "But it is not instant.",
        "That changes everything.",
    ]


# --------------------------------------------------------------------------- credits


@pytest.mark.anyio
@pytest.mark.parametrize("recipe", ["explainer"])
async def test_the_quote_is_exactly_what_gets_held(api, user_id, recipe):
    """The number shown and the number taken must be the same number.

    They are computed by one function for exactly this reason. If `/credits/quote` and the
    draft's reservation drifted apart, the way a user would find out is being quoted 36 and
    charged 41 — which is the single worst thing a product selling credits can do.
    """
    plan = await _plan(api, user_id, recipe=recipe)
    project = (await api.get("/projects", headers=auth(user_id))).json()[0]

    quoted = (await api.get(f"/credits/quote/{project['id']}", headers=auth(user_id))).json()
    before = (await api.get("/credits", headers=auth(user_id))).json()

    r = await api.post(f"/projects/{project['id']}/draft", headers=auth(user_id))
    assert r.status_code == 202, r.text

    after = (await api.get("/credits", headers=auth(user_id))).json()
    assert after["reserved"] - before["reserved"] == quoted["credits"]
    assert len(quoted["per_scene"]) == len(plan["scenes"])


@pytest.mark.anyio
async def test_a_lane_that_generates_nothing_is_quoted_nothing(api, user_id, clip, hears):
    """Subtitles keeps the creator's footage: no frame is drawn and no provider is called.

    Pricing its scenes as though one would be quoted ten credits for work that costs zero and
    never runs — and the ledger then held nothing, so the screen and the ledger disagreed.
    """
    hears("Light is fast.", "But it is not instant.", "That changes everything.")
    with clip.open("rb") as fh:
        up = (
            await api.post(
                "/uploads", headers=auth(user_id), files={"file": ("a.mp4", fh, "video/mp4")}
            )
        ).json()
    await api.post(
        "/projects",
        headers=auth(user_id),
        json={
            "brief": "Subtitles",
            "recipe": "subtitle_only",
            "source_asset_id": up["asset_id"],
        },
    )
    project = (await api.get("/projects", headers=auth(user_id))).json()[0]

    quoted = (await api.get(f"/credits/quote/{project['id']}", headers=auth(user_id))).json()
    assert quoted["credits"] == 0
    assert all(c == 0 for c in quoted["per_scene"])


@pytest.mark.anyio
async def test_drafting_twice_does_not_hold_the_money_twice(api, user_id, maker):
    """Enqueuing is idempotent; reserving is not.

    Re-drafting returns the jobs that already ran, but a hold placed against a finished job is
    never discharged — nothing is going to run again to release it. Drafting twice used to
    cost the user the whole quote a second time, permanently.
    """
    from lumina.domain.assets import AssetKind
    from lumina.state.assets import AssetStore

    plan = await _plan(api, user_id)
    project = (await api.get("/projects", headers=auth(user_id))).json()[0]
    await api.post(f"/projects/{project['id']}/draft", headers=auth(user_id))

    # Stand in for the worker finishing: every scene now has a picture.
    async with maker() as s:
        asset = await AssetStore(s).put_bytes(b"done", kind=AssetKind.IMAGE, mime="image/png")
        for scene in plan["scenes"]:
            row = await s.get(Scene, uuid.UUID(scene["id"]))
            assert row is not None
            row.preview_asset_id = asset.id
        await s.commit()

    before = (await api.get("/credits", headers=auth(user_id))).json()
    again = await api.post(f"/projects/{project['id']}/draft", headers=auth(user_id))
    after = (await api.get("/credits", headers=auth(user_id))).json()

    assert again.status_code == 202
    assert again.json()["queued"] == 0, "there is nothing left to make"
    assert after["reserved"] == before["reserved"], "the re-draft held credits nothing releases"


@pytest.mark.anyio
async def test_regenerating_without_the_credits_is_refused_not_queued(api, user_id, maker):
    """A refusal the user can act on, rather than a job that dies in a worker."""
    from sqlalchemy import delete

    from lumina.db.models import LedgerEntry

    plan = await _plan(api, user_id)
    async with maker() as s:
        await s.execute(delete(LedgerEntry).where(LedgerEntry.user_id == user_id))
        await s.commit()

    r = await api.post(f"/scenes/{plan['scenes'][0]['id']}/regenerate", headers=auth(user_id))
    assert r.status_code == 402, r.text
    assert r.json()["detail"]["short_by"] > 0

    balance = (await api.get("/credits", headers=auth(user_id))).json()
    assert balance["reserved"] == 0, "a refusal must not leave money held"


# --------------------------------------------------------------------------- cors


@pytest.mark.anyio
@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:5175",
        "http://127.0.0.1:5175",
        "http://127.0.1.1:5175",  # what `hostname` resolves to on many Linux boxes
        "http://192.168.1.40:5175",  # a phone on the same wifi
        "http://172.24.80.1:5175",  # WSL, opened from Windows
    ],
)
async def test_development_accepts_a_browser_that_is_not_on_this_machine(api, origin: str):
    """ "localhost" means *the browser's* machine, and the browser is not always on the server's.

    The origin pattern used to be `localhost|127.0.0.1`, which broke the moment the app was
    opened from Windows under WSL or from a phone: the preflight 400s with no
    `access-control-allow-origin`, the browser blocks the request, and `fetch` rejects with no
    response body — so the app cannot tell a refused origin from an unplugged cable and shows
    "check your connection" for a server that answered in 60 milliseconds.
    """
    r = await api.options(
        "/auth/signup",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert r.status_code == 200, f"preflight refused for {origin}"
    assert r.headers.get("access-control-allow-origin"), f"no allow-origin for {origin}"


@pytest.mark.anyio
async def test_development_cors_does_not_also_allow_credentials(api):
    """A wildcard origin together with credentials is the combination browsers refuse outright,
    and it would be a real hole if they did not. The app carries a bearer token, not a cookie,
    so it needs neither."""
    r = await api.options(
        "/auth/signup",
        headers={
            "Origin": "http://192.168.1.40:5175",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert r.headers.get("access-control-allow-credentials") != "true"


@pytest.mark.anyio
async def test_the_plan_says_which_video_it_was_made_from(api, user_id, clip, hears):
    """Otherwise the player has nothing to show until a render exists.

    Someone uploads a video to be subtitled or clipped and then cannot watch the video they
    uploaded — they get a grey plate, because the client was never told the file exists.
    """
    hears("Light is fast.", "But it is not instant.", "That changes everything.")
    with clip.open("rb") as fh:
        up = (
            await api.post(
                "/uploads", headers=auth(user_id), files={"file": ("a.mp4", fh, "video/mp4")}
            )
        ).json()

    plan = (
        await api.post(
            "/projects",
            headers=auth(user_id),
            json={
                "brief": "Subtitles",
                "recipe": "subtitle_only",
                "source_asset_id": up["asset_id"],
            },
        )
    ).json()

    assert plan["source_asset_id"] == up["asset_id"], "the plan must name its source video"
    assert all(s["source_start_ms"] is not None for s in plan["scenes"]), (
        "every scene must say where in the source it is, or the player seeks to the wrong place"
    )


@pytest.mark.anyio
async def test_a_generated_lane_has_no_source_to_play(api, user_id):
    """The lanes that draw their own frames have no footage, and must not claim to."""
    plan = await _plan(api, user_id)
    assert plan["source_asset_id"] is None
    assert all(s["source_start_ms"] is None for s in plan["scenes"])


# --------------------------------------------------------------------------- wrong input


@pytest.mark.anyio
@pytest.mark.parametrize("recipe", ["explainer"])
async def test_a_generating_lane_refuses_footage(api, user_id, clip, recipe):
    """Explainer draws its own frames; someone else's video has no part to play.

    Passing one through anyway sent it to the transcriber, so dropping a file into Recap
    failed with a message about speech-to-text engines — for a file it never intended to
    listen to.
    """
    with clip.open("rb") as fh:
        up = (
            await api.post(
                "/uploads", headers=auth(user_id), files={"file": ("a.mp4", fh, "video/mp4")}
            )
        ).json()

    r = await api.post(
        "/projects",
        headers=auth(user_id),
        json={"brief": "A thing", "recipe": recipe, "source_asset_id": up["asset_id"]},
    )
    assert r.status_code == 400
    assert "footage" in r.json()["detail"]


@pytest.mark.anyio
async def test_a_video_can_be_generated_from_a_picture(api, user_id, tmp_path):
    """ "Make a video from this image" is the most ordinary thing to ask of a video tool, and
    it was refused outright.

    It needs nothing new downstream: composition already animates a still, so the picture
    becomes every scene's frame and the typed words become the script. It also costs nothing —
    there is no frame to generate, because the creator supplied it.
    """
    from lumina.execution.compose import ffmpeg

    still = tmp_path / "pic.png"
    await ffmpeg.run(
        "-f",
        "lavfi",
        "-i",
        "gradients=s=800x600:c0=0x1b2a63:c1=0x5c3410:d=1",
        "-frames:v",
        "1",
        str(still),
    )
    with still.open("rb") as fh:
        up = (
            await api.post(
                "/uploads", headers=auth(user_id), files={"file": ("pic.png", fh, "image/png")}
            )
        ).json()

    r = await api.post(
        "/projects",
        headers=auth(user_id),
        json={
            "brief": "This place at sunrise. The light lasts ten minutes. Get there early.",
            "recipe": "explainer",
            "source_asset_id": up["asset_id"],
        },
    )
    assert r.status_code == 201, r.text
    plan = r.json()

    assert plan["scenes"], "the typed words became the script"
    assert all(s["preview_asset_id"] == up["asset_id"] for s in plan["scenes"]), (
        "every scene should show the picture that was supplied"
    )
    assert all(s["state"] == "preview_ready" for s in plan["scenes"])

    # And nothing is queued or charged, because there is no frame left to make.
    project = (await api.get("/projects", headers=auth(user_id))).json()[0]
    quoted = (await api.get(f"/credits/quote/{project['id']}", headers=auth(user_id))).json()
    draft = await api.post(f"/projects/{project['id']}/draft", headers=auth(user_id))
    assert draft.json()["queued"] == 0
    assert quoted["credits"] >= 0

    # It can go straight to a render.
    started = await api.post(
        f"/renders/project/{project['id']}", headers=auth(user_id), json={"aspects": ["9:16"]}
    )
    assert started.status_code == 200, started.text


@pytest.mark.anyio
async def test_the_source_lanes_need_something_with_a_picture_in_it(api, user_id, tmp_path):
    """Cutting an audio-only file does not fail — it produces a 0x0 MP4.

    A video with no picture, handed to someone who cannot post it. That is worse than an
    error, so the source is checked for frames before any of it is read. A still is refused
    from the other direction: nothing to transcribe, no moments to rank.
    """
    from lumina.execution.compose import ffmpeg

    sound = tmp_path / "sound.m4a"
    await ffmpeg.run(
        "-f", "lavfi", "-i", "sine=frequency=300:duration=4", "-c:a", "aac", str(sound)
    )
    still = tmp_path / "pic.png"
    await ffmpeg.run(
        "-f",
        "lavfi",
        "-i",
        "gradients=s=320x240:c0=0x111111:c1=0x222222:d=1",
        "-frames:v",
        "1",
        str(still),
    )

    for path, mime in ((sound, "audio/mp4"), (still, "image/png")):
        with path.open("rb") as fh:
            up = (
                await api.post(
                    "/uploads", headers=auth(user_id), files={"file": (path.name, fh, mime)}
                )
            ).json()

        for recipe in ("subtitle_only", "clip_long_video"):
            r = await api.post(
                "/projects",
                headers=auth(user_id),
                json={"brief": "x", "recipe": recipe, "source_asset_id": up["asset_id"]},
            )
            assert r.status_code == 400, f"{path.suffix} was accepted by {recipe}"
            assert "picture" in r.json()["detail"]


# --------------------------------------------------- what the browser believes about the API

WEB = Path(__file__).parents[4] / "apps" / "web" / "src" / "app"


def _brace(text: str, open_at: int) -> str:
    """The `{…}` starting at `open_at`, balanced. Enough for a literal with no strings in it
    containing braces, which is all these tables are."""
    depth = 0
    for i in range(open_at, len(text)):
        depth += (text[i] == "{") - (text[i] == "}")
        if depth == 0:
            return text[open_at : i + 1]
    raise AssertionError("unbalanced")


def _fields(body: str) -> dict[str, str]:
    """`key: 'value'` and `key: true` out of one object literal.

    Quoted values and bare ones are matched separately rather than by one pattern with an
    optional quote. A single character class wide enough to hold `image/*,video/*` also
    swallows the comma after `required: true`, which then reads as `"true,"` and compares
    unequal to every boolean — a parser bug that would have been reported as a mismatch
    between the browser and the server.
    """
    out = dict(re.findall(r"(\w+): '([^']*)'", body))
    out.update(dict(re.findall(r"(\w+): (true|false)\b", body)))
    return out


def _takes() -> dict[str, dict[str, str]]:
    """The Brief screen's table of what each task accepts, as data.

    Parsed rather than duplicated here: the point of the test is that this exact table agrees
    with the server, so reading anything else would defeat it.
    """
    src = (WEB / "screens" / "Brief.tsx").read_text()

    def literal(decl: str) -> str:
        at = src.index(decl)
        return _brace(src, at + src[at:].index("{"))

    defaults = _fields(literal("const PICTURE: Takes ="))
    table = literal("const TAKES: Record")
    out: dict[str, dict[str, str]] = {}
    for m in re.finditer(r"(?m)^  (\w+): (PICTURE,|\{)", table):
        name = m.group(1)
        if m.group(2) == "PICTURE,":
            out[name] = dict(defaults)
            continue
        body = _brace(table, m.start(2))
        row = dict(defaults) if "...PICTURE" in body else {}
        row.update(_fields(body))
        out[name] = row
    return out


@pytest.mark.anyio
async def test_the_browser_asks_for_exactly_the_files_the_server_accepts(api):
    """A file picker that offers a type the server refuses is a 400 the creator could not
    have predicted — it offered them the choice.

    Two tables have already drifted this way without anything failing: the recipe ids, and
    the per-recipe stage list, which drew Voice over as three steps while it ran five. The
    stage copy is gone (the screen asks the server now); this one cannot be, because the
    server has no opinion about MIME types, so it is checked instead.
    """
    lanes = {x["id"]: x for x in (await api.get("/projects/recipes")).json()}
    takes = _takes()

    assert set(takes) == set(lanes), "every task the browser draws must be one the server runs"

    for name, lane in lanes.items():
        row = takes[name]
        needs = lane["needs_source"]
        assert (row["required"] == "true") is needs, f"{name}: file requiredness disagrees"
        # A generating lane takes an optional still and refuses footage; a source lane is the
        # other way round. Both refusals live in POST /projects, so the picker must match.
        #
        # Narrate is the one lane that takes both, and it is not an exception to the rule but
        # the rule stated properly: the picker must offer exactly what `POST /projects` will
        # accept for that lane. It cuts a *sequence* — a picture or a clip per line — so both
        # are legitimate input and refusing either in the browser would hide half the feature.
        expected = "image/*,video/*" if name == "narrate" else "video/*" if needs else "image/*"
        assert row["accept"] == expected, f"{name}: wrong accept"


def test_the_browser_and_the_server_name_the_same_tasks() -> None:
    """`data.ts` sends `recipe` as an id the server has to recognise. Three of five once did
    not, so three of the five tasks 400'd on the first click."""
    src = (WEB / "data.ts").read_text()
    table = src[src.index("export const RECIPES") :].split("\nexport ")[0]
    ids = set(re.findall(r"(?m)^    id: '(\w+)',", table))
    assert ids == {x.value for x in Recipe}


@pytest.mark.anyio
async def test_the_browser_can_draw_every_stage_the_server_runs(api):
    """`transcribe` shipped on the server and was never added to the strip's table, which
    filtered by it — so Subtitle drew three steps for four and nothing lit up for the whole
    time it was listening. The strip renders the server's order now and shows an unknown
    stage rather than dropping it, but a stage nobody has drawn is still a bug worth failing
    on: it would appear with a fallback icon and a machine-derived label."""
    src = (WEB / "Pipeline.tsx").read_text()
    drawn = set(re.findall(r"\{ id: '(\w+)', label:", src))

    lanes = (await api.get("/projects/recipes")).json()
    runs = {stage for lane in lanes for stage in lane["stages"]}
    assert runs <= drawn, f"no icon or label for {sorted(runs - drawn)}"


@pytest.mark.anyio
async def test_the_project_list_carries_a_frame_and_a_date_to_recognise_it_by(api, user_id, maker):
    """The sequel picker shows four past projects at once. Their lane is the same word on
    every one, and their titles are near-identical by design — episodes of a series. A frame
    and a date are what actually tell them apart, so the list carries both, joined here
    rather than fetched per project by the browser."""
    made = await api.post(
        "/projects",
        headers=auth(user_id),
        json={"brief": "Why the sky is blue", "recipe": "explainer"},
    )
    assert made.status_code == 201
    project_id = made.json()["project_id"]

    listed = (await api.get("/projects", headers=auth(user_id))).json()
    row = next(p for p in listed if p["id"] == project_id)
    assert row["created_at"], "no date to sort or label by"
    assert row["poster_asset_id"] is None, "nothing has been drawn yet"

    # Draw the opening scene, the way the generate stage does.
    async with maker() as s:
        opening = select(Scene).where(Scene.index == 0).order_by(Scene.created_at.desc())
        scene = await s.scalar(opening)
        assert scene is not None
        asset = Asset(
            kind="image", mime="image/png", bytes=1, sha256=uuid.uuid4().hex, storage_key="x.png"
        )
        s.add(asset)
        await s.flush()
        scene.preview_asset_id = asset.id
        await s.commit()
        drawn = asset.id

    listed = (await api.get("/projects", headers=auth(user_id))).json()
    row = next(p for p in listed if p["id"] == project_id)
    assert row["poster_asset_id"] == str(drawn), "the opening frame is the poster"


@pytest.mark.anyio
async def test_a_planned_project_is_titled_by_its_plan_not_its_brief(api, user_id, clip, hears):
    """For the lanes whose whole input is a file the brief *is* the filename, so projects
    listed as `ch11_20260825_1600-1605.mp4` and the sequel picker offered chips that read as
    filenames. The planner writes a real title; the project takes it."""
    hears("Light is fast.", "But it is not instant.", "That changes everything.")
    up = await api.post(
        "/uploads",
        headers=auth(user_id),
        files={"file": ("ch11_20260825_1600-1605.mp4", clip.read_bytes(), "video/mp4")},
    )
    assert up.status_code == 201
    made = await api.post(
        "/projects",
        headers=auth(user_id),
        json={
            "brief": "ch11_20260825_1600-1605.mp4",
            "recipe": "subtitle_only",
            "source_asset_id": up.json()["asset_id"],
        },
    )
    assert made.status_code == 201

    listed = (await api.get("/projects", headers=auth(user_id))).json()
    row = next(p for p in listed if p["id"] == made.json()["project_id"])
    assert ".mp4" not in row["title"], f"still named after the file: {row['title']!r}"
    assert row["title"].strip()


# ------------------------------------------------- subtitles from a file, and other languages


@pytest.mark.anyio
async def test_subtitle_lane_works_with_no_speech_engine_when_a_file_is_attached(
    api, user_id, clip
):
    """The whole point of reading timed text.

    Note what this test does *not* use: the `hears` fixture. No speech engine is configured,
    which is the state every server this runs on is in — and until now that meant Subtitle
    refused outright with a message about ASR_ENGINE. A creator who has the .srt should never
    have needed one.
    """
    video = await api.post(
        "/uploads",
        headers=auth(user_id),
        files={"file": ("ep1.mp4", clip.read_bytes(), "video/mp4")},
    )
    srt = await api.post(
        "/uploads",
        headers=auth(user_id),
        files={
            "file": (
                "ep1.srt",
                b"1\n00:00:00,200 --> 00:00:01,400\nLight is fast.\n\n"
                b"2\n00:00:01,600 --> 00:00:03,000\nBut it is not instant.\n",
                "application/x-subrip",
            )
        },
    )
    assert srt.status_code == 201, srt.text

    made = await api.post(
        "/projects",
        headers=auth(user_id),
        json={
            "brief": "ep1",
            "recipe": "subtitle_only",
            "source_asset_id": video.json()["asset_id"],
            "transcript_asset_id": srt.json()["asset_id"],
        },
    )
    assert made.status_code == 201, made.text

    scenes = made.json()["scenes"]
    assert [s["script_line"] for s in scenes] == ["Light is fast.", "But it is not instant."]
    # The file's timings, not ours: the second caption starts when the file says it does.
    assert scenes[1]["duration_ms"] == 1_400


@pytest.mark.anyio
async def test_a_supplied_transcript_beats_the_engine_rather_than_supplementing_it(
    api, user_id, clip, hears
):
    """Order matters, and it is "read, else listen" — never both. A server *with* an engine
    must still prefer the creator's own file, which is exact where the engine is a guess."""
    hears("Something the engine invented.")
    video = await api.post(
        "/uploads", headers=auth(user_id), files={"file": ("a.mp4", clip.read_bytes(), "video/mp4")}
    )
    srt = await api.post(
        "/uploads",
        headers=auth(user_id),
        files={"file": ("a.srt", b"1\n00:00:00,000 --> 00:00:02,000\nWhat was really said.\n", "")},
    )
    made = await api.post(
        "/projects",
        headers=auth(user_id),
        json={
            "brief": "a",
            "recipe": "subtitle_only",
            "source_asset_id": video.json()["asset_id"],
            "transcript_asset_id": srt.json()["asset_id"],
        },
    )
    assert made.status_code == 201, made.text
    lines = [s["script_line"] for s in made.json()["scenes"]]
    assert lines == ["What was really said."]


@pytest.mark.anyio
async def test_refusing_to_listen_points_at_the_subtitle_file_as_the_way_out(api, user_id, clip):
    """The error the creator actually met. It named an environment variable they cannot set
    and offered them nothing to do — while the thing that would have worked was a file they
    almost certainly had."""
    video = await api.post(
        "/uploads", headers=auth(user_id), files={"file": ("a.mp4", clip.read_bytes(), "video/mp4")}
    )
    r = await api.post(
        "/projects",
        headers=auth(user_id),
        json={
            "brief": "a",
            "recipe": "subtitle_only",
            "source_asset_id": video.json()["asset_id"],
        },
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert ".srt" in detail, "the message offers the way out, not just the diagnosis"


@pytest.mark.anyio
async def test_a_subtitle_file_is_refused_for_a_lane_that_writes_its_own_script(api, user_id):
    up = await api.post(
        "/uploads",
        headers=auth(user_id),
        files={"file": ("s.srt", b"1\n00:00:00,000 --> 00:00:01,000\nHi\n", "")},
    )
    r = await api.post(
        "/projects",
        headers=auth(user_id),
        json={
            "brief": "why the sky is blue",
            "recipe": "explainer",
            "transcript_asset_id": up.json()["asset_id"],
        },
    )
    assert r.status_code == 400
    assert "writes its own script" in r.json()["detail"]


@pytest.mark.anyio
async def test_a_language_with_no_pack_is_refused_before_anything_is_charged(api, user_id):
    """A project planned into a language nothing can render fails at the last stage, after
    the creator has paid for every scene in it."""
    r = await api.post(
        "/projects",
        headers=auth(user_id),
        json={"brief": "hello", "recipe": "explainer", "language": "kl"},
    )
    assert r.status_code == 400
    assert "language pack" in r.json()["detail"]


@pytest.mark.anyio
async def test_translating_without_an_engine_says_so_instead_of_returning_the_source(
    api, user_id, clip
):
    """The dangerous failure: captions that are correctly timed, labelled Burmese, and still
    in English. Nobody who cannot read Burmese would catch it."""
    video = await api.post(
        "/uploads", headers=auth(user_id), files={"file": ("a.mp4", clip.read_bytes(), "video/mp4")}
    )
    srt = await api.post(
        "/uploads",
        headers=auth(user_id),
        files={"file": ("a.srt", b"1\n00:00:00,000 --> 00:00:02,000\nLight is fast.\n", "")},
    )
    r = await api.post(
        "/projects",
        headers=auth(user_id),
        json={
            "brief": "a",
            "recipe": "subtitle_only",
            "language": "my",
            "source_language": "en",
            "source_asset_id": video.json()["asset_id"],
            "transcript_asset_id": srt.json()["asset_id"],
        },
    )
    assert r.status_code == 422
    assert "translation engine" in r.json()["detail"]


@pytest.mark.anyio
async def test_languages_separates_what_can_be_heard_from_what_can_be_written(api):
    """Conflating these is what made Subtitle look broken: this server renders captions in
    six languages perfectly and cannot listen to anything at all."""
    r = await api.get("/projects/languages")
    assert r.status_code == 200
    langs = {x["code"]: x for x in r.json()}

    assert set(langs) == {"en", "ja", "zh", "ko", "my", "th"}
    assert langs["my"]["name"] == "မြန်မာ", "a Burmese creator looks for မြန်မာ, not 'Burmese'"
    assert langs["ja"]["english"] == "Japanese", "and the English name for someone who is not"
    assert all(x["can_listen"] is False for x in langs.values()), "no engine in the test env"
    assert all(x["can_render"] for x in langs.values()), "make fonts has run"


@pytest.mark.anyio
async def test_languages_lead_with_the_common_answer_for_each_side(api):
    """What people film in is not what they subtitle into — Chinese and Korean are common
    sources and rare targets, Burmese and Thai the reverse. One alphabetical list would put
    the usual answer somewhere different on each side, and the list is going to keep growing.

    Ordering only: every language stays selectable on both sides, because an unusual pair is
    still a pair somebody wants.
    """
    rows = (await api.get("/projects/languages")).json()

    sources = [x["code"] for x in rows if x["common_source"]]
    targets = [x["code"] for x in rows if x["common_target"]]
    assert sources == ["en", "ja", "zh", "ko"]
    assert targets == ["en", "ja", "my", "th"]
    assert "ja" in sources and "ja" in targets, "filmed in and subtitled into"

    # The common ones come first in the payload, so a picker that does not sort still leads
    # with them.
    common = [i for i, x in enumerate(rows) if x["common_source"] or x["common_target"]]
    assert common == list(range(len(common))), [x["code"] for x in rows]


@pytest.mark.anyio
async def test_every_subtitle_line_keeps_its_own_moment(api, user_id, clip):
    """The planner must not re-derive cue boundaries.

    Subtitles were planned by handing the joined transcript to the planner as a brief, which
    split it on full stops to find its scenes. That is right for a lane writing a script and
    wrong for one reading a file: a cue ending in "!" merged with the next, a cue containing a
    "." split in two, and the scene count stopped matching the timing count. The pairing then
    truncated silently, so every caption after the first mismatch was drawn at a moment it did
    not belong to — with the words still looking perfectly correct.
    """
    # Inside the fixture's three seconds. The texts are the shapes that broke it: one ending
    # in "!" (merged with the next), and one carrying a "." in the middle (split in two).
    cues = [
        (100, 600, "And that's how you create an SRT subtitle file!"),
        (800, 600, "Keep each line short."),
        (1500, 600, "The format is hours:minutes:seconds.milliseconds."),
        (2200, 700, "Always check names and numbers."),
    ]

    def stamp(ms: int) -> str:
        m, ms = divmod(ms, 60_000)
        return f"00:{m:02d}:{ms // 1000:02d},{ms % 1000:03d}"

    srt = "\n".join(
        f"{i}\n{stamp(at)} --> {stamp(at + length)}\n{text}\n"
        for i, (at, length, text) in enumerate(cues, 1)
    )
    video = await api.post(
        "/uploads",
        headers=auth(user_id),
        files={"file": ("a.mp4", clip.read_bytes(), "video/mp4")},
    )
    track = await api.post(
        "/uploads", headers=auth(user_id), files={"file": ("a.srt", srt.encode(), "")}
    )
    made = await api.post(
        "/projects",
        headers=auth(user_id),
        json={
            "brief": "a",
            "recipe": "subtitle_only",
            "source_asset_id": video.json()["asset_id"],
            "transcript_asset_id": track.json()["asset_id"],
        },
    )
    assert made.status_code == 201, made.text

    scenes = made.json()["scenes"]
    assert len(scenes) == len(cues), "one scene per cue, never merged or split"
    for scene, (at, length, text) in zip(scenes, cues, strict=True):
        assert scene["script_line"] == text, "the speaker's words, untouched"
        assert scene["source_start_ms"] == at, f"{text!r} moved to {scene['source_start_ms']}"
        assert scene["duration_ms"] == length


@pytest.mark.anyio
async def test_a_second_caption_track_shares_the_first_track_s_timings(
    api, user_id, clip, monkeypatch
):
    """A bilingual subtitle is one video, not two.

    The translation appears at the moment the thing it translates is said, for as long as it
    is said — so there is only ever one set of timings, and no second track that can drift
    out of sync with the first. That is why translations hang off the scenes rather than
    living in a plan of their own.
    """
    from lumina.intelligence import translate

    class Shouty:
        name = "fake"

        async def translate(self, lines, *, source, target, keep=()):
            return [f"[{target}] {line}" for line in lines]

    monkeypatch.setattr(translate, "translator", Shouty)

    srt = b"1\n00:00:00,100 --> 00:00:00,900\nFirst.\n\n2\n00:00:01,200 --> 00:00:02,000\nSecond.\n"
    video = await api.post(
        "/uploads", headers=auth(user_id), files={"file": ("a.mp4", clip.read_bytes(), "video/mp4")}
    )
    track = await api.post("/uploads", headers=auth(user_id), files={"file": ("a.srt", srt, "")})
    made = await api.post(
        "/projects",
        headers=auth(user_id),
        json={
            "brief": "a",
            "recipe": "subtitle_only",
            "source_asset_id": video.json()["asset_id"],
            "transcript_asset_id": track.json()["asset_id"],
        },
    )
    assert made.status_code == 201, made.text
    project_id = made.json()["project_id"]
    before = made.json()["scenes"]

    added = await api.post(f"/projects/{project_id}/tracks/my", headers=auth(user_id))
    assert added.status_code == 200, added.text
    plan = added.json()

    assert plan["caption_languages"] == ["en", "my"], "primary first, then the new one"
    after = plan["scenes"]
    assert [s["source_start_ms"] for s in after] == [s["source_start_ms"] for s in before]
    assert [s["duration_ms"] for s in after] == [s["duration_ms"] for s in before]
    assert [s["script_line"] for s in after] == ["First.", "Second."], "the original untouched"
    assert [s["translations"]["my"] for s in after] == ["[my] First.", "[my] Second."]


@pytest.mark.anyio
async def test_adding_a_track_twice_does_not_translate_it_twice(api, user_id, clip, monkeypatch):
    """Translation costs money. Asking for a track that is already there returns the plan."""
    from lumina.intelligence import translate

    calls = {"n": 0}

    class Counting:
        name = "fake"

        async def translate(self, lines, *, source, target, keep=()):
            calls["n"] += 1
            return [f"[{target}] {line}" for line in lines]

    monkeypatch.setattr(translate, "translator", Counting)

    srt = b"1\n00:00:00,100 --> 00:00:00,900\nOnly line.\n"
    video = await api.post(
        "/uploads", headers=auth(user_id), files={"file": ("a.mp4", clip.read_bytes(), "video/mp4")}
    )
    track = await api.post("/uploads", headers=auth(user_id), files={"file": ("a.srt", srt, "")})
    made = await api.post(
        "/projects",
        headers=auth(user_id),
        json={
            "brief": "a",
            "recipe": "subtitle_only",
            "source_asset_id": video.json()["asset_id"],
            "transcript_asset_id": track.json()["asset_id"],
        },
    )
    project_id = made.json()["project_id"]

    await api.post(f"/projects/{project_id}/tracks/th", headers=auth(user_id))
    await api.post(f"/projects/{project_id}/tracks/th", headers=auth(user_id))
    assert calls["n"] == 1, "the second ask reused what was already translated"


@pytest.mark.anyio
async def test_a_video_cannot_end_up_with_no_caption_track(api, user_id, clip):
    """Removing the last one would render a subtitle job with no subtitles."""
    srt = b"1\n00:00:00,100 --> 00:00:00,900\nOnly line.\n"
    video = await api.post(
        "/uploads", headers=auth(user_id), files={"file": ("a.mp4", clip.read_bytes(), "video/mp4")}
    )
    track = await api.post("/uploads", headers=auth(user_id), files={"file": ("a.srt", srt, "")})
    made = await api.post(
        "/projects",
        headers=auth(user_id),
        json={
            "brief": "a",
            "recipe": "subtitle_only",
            "source_asset_id": video.json()["asset_id"],
            "transcript_asset_id": track.json()["asset_id"],
        },
    )
    project_id = made.json()["project_id"]

    r = await api.delete(f"/projects/{project_id}/tracks/en", headers=auth(user_id))
    assert r.status_code == 400
    assert "at least one" in r.json()["detail"]


@pytest.mark.anyio
async def test_deleting_a_project_gives_back_what_it_was_holding(api, user_id, maker):
    """The credits would otherwise be held for ever.

    A reservation is released by a later commit or refund *against the job that made it*.
    Deleting a project cascades its jobs away, so nothing is left that could ever discharge
    the hold — the creator's balance would stay short, permanently, for a project that no
    longer exists.
    """
    made = await api.post("/projects", headers=auth(user_id), json={"brief": "why the sky is blue"})
    assert made.status_code == 201
    project_id = made.json()["project_id"]

    before = (await api.get("/credits", headers=auth(user_id))).json()
    drafted = await api.post(f"/projects/{project_id}/draft", headers=auth(user_id))
    assert drafted.status_code == 202, drafted.text

    held = (await api.get("/credits", headers=auth(user_id))).json()
    assert held["reserved"] > 0, "the draft reserved something to hold"
    assert held["available"] < before["available"]

    gone = await api.delete(f"/projects/{project_id}", headers=auth(user_id))
    assert gone.status_code == 204

    after = (await api.get("/credits", headers=auth(user_id))).json()
    assert after["reserved"] == 0, "nothing left holding"
    assert after["available"] == before["available"], "the creator is whole again"

    plan = await api.get(f"/projects/{project_id}/plan", headers=auth(user_id))
    assert plan.status_code == 404
    listed = (await api.get("/projects", headers=auth(user_id))).json()
    assert all(row["id"] != project_id for row in listed)


@pytest.mark.anyio
async def test_deleting_a_project_keeps_the_record_of_what_was_spent(api, user_id, maker):
    """A spend does not stop having happened because the thing it paid for was deleted.

    The ledger is the audit trail and the basis of the balance; cascading it away would
    silently hand back money that was really spent with a provider.
    """
    from lumina.db.models import LedgerEntry

    made = await api.post("/projects", headers=auth(user_id), json={"brief": "a short"})
    project_id = made.json()["project_id"]
    await api.post(f"/projects/{project_id}/draft", headers=auth(user_id))

    async with maker() as s:
        before = len(list(await s.scalars(select(LedgerEntry))))

    assert (await api.delete(f"/projects/{project_id}", headers=auth(user_id))).status_code == 204

    async with maker() as s:
        rows = list(await s.scalars(select(LedgerEntry)))
    assert len(rows) > before, "the refunds were added, and nothing was removed"
    assert any(r.kind == "refund" and r.reason == "project_deleted" for r in rows), (
        "the release is recorded as a refund, not achieved by deleting the hold"
    )


@pytest.mark.anyio
async def test_a_project_on_another_channel_cannot_be_deleted(api, user_id, maker):
    from lumina.db.models import Channel, User

    async with maker() as s:
        other = User(email=f"{uuid.uuid4()}@example.com")
        s.add(other)
        await s.flush()
        s.add(Channel(user_id=other.id, name="Theirs", language="en", identity={}))
        await Ledger(s).grant(other.id, Cents(2000))
        await s.commit()
        theirs = other.id

    made = await api.post("/projects", headers=auth(theirs), json={"brief": "not yours"})
    project_id = made.json()["project_id"]

    r = await api.delete(f"/projects/{project_id}", headers=auth(user_id))
    assert r.status_code == 404, "someone else's project is not found, not forbidden"
    assert (await api.get(f"/projects/{project_id}/plan", headers=auth(theirs))).status_code == 200


@pytest.mark.anyio
async def test_a_translation_can_be_corrected_by_hand(api, user_id, clip, hears):
    """A machine translation is a draft.

    Until `SceneEdit.translations` existed there was no way for a person to change one — the
    map was written by the translator and by nothing else — so a Burmese line that came back
    wrong stayed wrong all the way into the burnt-in video, where the only people who could
    see the mistake were the audience.
    """
    hears("Light is fast.", "But it is not instant.")
    with clip.open("rb") as fh:
        up = (
            await api.post(
                "/uploads", headers=auth(user_id), files={"file": ("a.mp4", fh, "video/mp4")}
            )
        ).json()
    plan = (
        await api.post(
            "/projects",
            headers=auth(user_id),
            json={
                "brief": "subtitle it",
                "recipe": "subtitle_only",
                "source_asset_id": up["asset_id"],
            },
        )
    ).json()
    scene = plan["scenes"][0]

    # Two tracks in one map, then a correction to one of them.
    await api.patch(
        f"/scenes/{scene['id']}",
        headers=auth(user_id),
        json={"translations": {"my": "မှားနေတယ်", "ja": "日本語"}},
    )
    fixed = await api.patch(
        f"/scenes/{scene['id']}",
        headers=auth(user_id),
        json={"translations": {"my": "ပြင်ပြီးပြီ"}},
    )

    assert fixed.status_code == 200
    got = fixed.json()
    assert got["translations"]["my"] == "ပြင်ပြီးပြီ"
    # Merged, not replaced: fixing Burmese must not delete the Japanese beside it.
    assert got["translations"]["ja"] == "日本語"
    # And the source line is what the translation is checked against, so it does not move.
    assert got["script_line"] == scene["script_line"]


@pytest.mark.anyio
async def test_a_translated_video_burns_in_the_translation(api, user_id, clip, hears, monkeypatch):
    """The captions that reach the video are the ones the creator asked for.

    The stage passed the *target* language where `line_for` expects the *source*, which made the
    two equal on every project — so `line_for` took its "this is not a translation" branch and
    burnt in `script_line`. A creator who asked for Burmese captions, corrected them, and paid
    for the render got the English transcript rendered onto their video instead, and nothing
    upstream of the finished file showed it.
    """
    from lumina.db.models import Scene
    from lumina.execution.stages import line_for

    hears("Light is fast.", "But it is not instant.")
    with clip.open("rb") as fh:
        up = (
            await api.post(
                "/uploads", headers=auth(user_id), files={"file": ("a.mp4", fh, "video/mp4")}
            )
        ).json()
    plan = (
        await api.post(
            "/projects",
            headers=auth(user_id),
            json={
                "brief": "subtitle it",
                "recipe": "subtitle_only",
                "source_asset_id": up["asset_id"],
            },
        )
    ).json()

    scene = Scene(
        plan_id=uuid.uuid4(),
        index=0,
        prompt="",
        script_line="Light is fast.",
        translations={"my": "အလင်းက မြန်တယ်။"},
        duration_ms=1000,
    )

    # Target as source — the bug — gives back the English.
    assert line_for(scene, "my", "my") == "Light is fast."
    # The real source gives back what was asked for.
    assert line_for(scene, "my", "en") == "အလင်းက မြန်တယ်။"
    assert plan["scenes"], "the lane still plans"


def test_an_asset_is_named_and_saved_rather_than_displayed() -> None:
    """Pressing Download used to navigate the tab to the video instead of saving it.

    `<a download="...">` is ignored unless the link is same-origin, and the app and the API are
    not — so the creator left the product, landed on a bare video URL, and the browser named
    the file after the twelve characters of hash we sent as its filename, with no extension.
    """
    from lumina.api.routers.assets import _disposition

    class Fake:
        mime = "video/mp4"
        sha256 = "5baadf1234567890"

    asset = Fake()
    # Watching it: displayed, but with a name a person could live with.
    assert _disposition(asset, None) == 'inline; filename="lumina-5baadf123456.mp4"'
    # Saving it: an attachment, so the tab stays where it is.
    assert _disposition(asset, "lumina 9x16") == 'attachment; filename="lumina 9x16.mp4"'


def test_a_download_name_cannot_forge_a_header_or_escape_the_folder() -> None:
    """A header value is the wrong place to be clever: a quote or a newline in it splits the
    response, and a slash makes the browser write outside the folder it was given."""
    from lumina.api.routers.assets import _disposition

    class Fake:
        mime = "video/mp4"
        sha256 = "5baadf1234567890"

    got = _disposition(Fake(), '../../etc/passwd"; x\r\nSet-Cookie: a=b')
    #: The characters that would do harm are dropped, not replaced — so the text either side
    #: of a newline runs together, which is ugly and harmless.
    assert got == 'attachment; filename="etcpasswd xSet-Cookie ab.mp4"'
    assert '"' not in got.removeprefix('attachment; filename="').removesuffix('"')
    assert "\r" not in got and "\n" not in got and "/" not in got


@pytest.mark.anyio
async def test_a_name_the_creator_typed_is_never_overwritten(api, user_id, clip, hears):
    """Naming a video used to do nothing.

    The box on a file lane asks what to call it, the answer travelled as the brief, became the
    project's title, and was then overwritten by the plan's — which on a transcribed lane is
    that video's first caption. So a creator who named their video saw a line of their own
    transcript at the top of every screen instead, in whatever language it was spoken in.
    """
    hears("心を燃やせ。", "限界を超えろ。")
    up = await api.post(
        "/uploads",
        headers=auth(user_id),
        files={"file": ("ds.mp4", clip.read_bytes(), "video/mp4")},
    )
    made = await api.post(
        "/projects",
        headers=auth(user_id),
        json={
            "brief": "ds.mp4",
            "title": "Rengoku edit",
            "recipe": "subtitle_only",
            "source_asset_id": up.json()["asset_id"],
        },
    )
    assert made.status_code == 201

    listed = (await api.get("/projects", headers=auth(user_id))).json()
    row = next(p for p in listed if p["id"] == made.json()["project_id"])
    assert row["title"] == "Rengoku edit", f"the plan overwrote it: {row['title']!r}"


@pytest.mark.anyio
async def test_a_speakable_language_offers_voices_and_says_why_when_one_is_not_usable(api, user_id):
    """The picker's whole job is answering "how will this sound", so it has to be honest
    about the option it cannot deliver as well as the ones it can."""
    r = await api.get("/projects/voices?language=my", headers=auth(user_id))
    assert r.status_code == 200
    voices = r.json()
    assert voices, "Burmese is speakable here and offered nothing"
    assert all(v["character"] for v in voices), "a voice with no description is unpickable"

    usable = [v for v in voices if not v["unavailable"]]
    assert usable, "every voice was marked unusable"
    assert all(not v["mine"] for v in usable), "the creator's own voice cannot be synthesized yet"


def test_the_browser_routes_dub_past_the_storyboard() -> None:
    """`data.ts` decides which screens a lane walks through, and Dub has neither a script nor
    a storyboard: its words are transcribed from the footage and its frames are the footage.

    It shipped without a funnel of its own, fell through to the generating lanes\' default,
    and put 274 transcribed lines on a screen headed "Say it out loud" under a button reading
    "Make the storyboard" — every one of them a beat to rewrite, for a video already made.
    """
    src = (WEB / "data.ts").read_text()
    table = src[src.index("const FUNNELS") : src.index("const DEFAULT_FUNNEL")]
    dub = re.search(r"(?m)^  dub: \[(.*?)\],", table)
    assert dub, "Dub has no funnel and would fall through to the generating lanes' default"

    steps = re.findall(r"\'(\w+)\'", dub.group(1))
    assert "script" not in steps, "a dub has no script to write — it is transcribed"
    assert "board" not in steps, "a dub has no storyboard — the frames are the creator's"
    assert steps[-1] == "deliver" and "captions" in steps, steps


@pytest.mark.anyio
async def test_choosing_a_voice_survives_the_request_that_chose_it(api, user_id, clip, hears):
    """A write that answers with the new value and then rolls back is the worst shape of bug:
    the screen updates, the creator believes it, and the render uses the old value.

    `get_session` hands each endpoint a session it must commit itself. This one flushed, so
    the response carried the chosen voice and the next read carried the previous one.
    """
    hears("Ancient China.", "The sun is shining.")
    with clip.open("rb") as fh:
        up = await api.post(
            "/uploads", headers=auth(user_id), files={"file": ("v.mp4", fh, "video/mp4")}
        )
    created = await api.post(
        "/projects",
        headers=auth(user_id),
        json={
            "brief": "dub it",
            "recipe": "dub",
            #: Not a translation: the suite runs with no keys, and a dub that also translates
            #: needs an engine. What is under test is the voice write, not the words.
            "language": "my",
            "source_asset_id": up.json()["asset_id"],
        },
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["project_id"]

    said = await api.put(f"/projects/{project_id}/voice/Kore", headers=auth(user_id))
    assert said.status_code == 200, said.text
    assert said.json()["voice_id"] == "Kore"

    listed = await api.get("/projects", headers=auth(user_id))
    mine = next(p for p in listed.json() if p["id"] == project_id)
    assert mine["voice_id"] == "Kore", "the choice was rolled back after the response was sent"

    cleared = await api.put(f"/projects/{project_id}/voice/-", headers=auth(user_id))
    assert cleared.json()["voice_id"] is None, "the picker cannot deselect"


@pytest.mark.anyio
async def test_a_voice_that_does_not_exist_is_refused(api, user_id, clip, hears):
    """The id is sent by the browser and stored verbatim, so it is worth checking here rather
    than discovering at render time that nothing can say the words."""
    hears("Ancient China.", "The sun is shining.")
    with clip.open("rb") as fh:
        up = await api.post(
            "/uploads", headers=auth(user_id), files={"file": ("v.mp4", fh, "video/mp4")}
        )
    created = await api.post(
        "/projects",
        headers=auth(user_id),
        json={
            "brief": "dub it",
            "recipe": "dub",
            "language": "my",
            "source_asset_id": up.json()["asset_id"],
        },
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["project_id"]

    bad = await api.put(
        f"/projects/{project_id}/voice/Definitely-Not-A-Voice", headers=auth(user_id)
    )
    assert bad.status_code == 400, bad.text


@pytest.mark.anyio
async def test_a_plan_says_which_lane_it_belongs_to(api, user_id, clip, hears):
    """The editor renders a plan, and it has to know the lane to render it correctly.

    It used to look the lane up in the project *list* — a different request, with a different
    lifetime — and answer "explainer" until that arrived. The Subtitle and Dub editors are the
    same screen with different words and different controls, so on a fresh load a Dub project
    drew the Subtitle one: headed "Subtitles", offering to burn in captions, with no voice
    picker and no way to hear the dub, for as long as a list of every project on the channel
    took to come back. Sending the lane with the plan closes that window.
    """
    hears("Ancient China.", "The sun is shining.")
    with clip.open("rb") as fh:
        up = await api.post(
            "/uploads", headers=auth(user_id), files={"file": ("v.mp4", fh, "video/mp4")}
        )
    created = await api.post(
        "/projects",
        headers=auth(user_id),
        json={
            "brief": "dub it",
            "recipe": "dub",
            "language": "my",
            "source_asset_id": up.json()["asset_id"],
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["recipe"] == "dub"

    fetched = await api.get(f"/projects/{created.json()['project_id']}/plan", headers=auth(user_id))
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["recipe"] == "dub", "the editor cannot tell which screen to be"
