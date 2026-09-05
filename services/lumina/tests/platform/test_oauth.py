"""The OAuth handshake.

Everything here is the part that runs on our side: what URL we send the browser to, what we
accept back, and who the callback ends up signing in. The provider round trip itself needs
real credentials and a real browser, so it is exercised separately — but a mistake in any of
these is a sign-in that either does not work or works for the wrong person.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from lumina.config import get_settings
from lumina.platform import oauth


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "google_client_id", "test-client", raising=False)
    monkeypatch.setattr(settings, "google_client_secret", "test-secret", raising=False)
    return settings


def test_a_provider_with_no_credentials_is_offered_but_not_enabled() -> None:
    """Listed rather than hidden: a button that vanishes leaves someone wondering whether they
    misremembered, and tells whoever is deploying nothing about what is missing."""
    rows = {p["id"]: p for p in oauth.available()}
    assert set(rows) == {"google", "github", "apple"}
    assert all(not r["enabled"] for r in rows.values()), "nothing is configured by default"


def test_configuring_one_enables_only_that_one(configured) -> None:
    rows = {p["id"]: p["enabled"] for p in oauth.available()}
    assert rows["google"] is True
    assert rows["github"] is False


def test_the_authorize_url_carries_what_the_provider_needs(configured) -> None:
    url = oauth.authorize_url("google", oauth.issue_state("google"))
    parsed = urlparse(url)
    q = {k: v[0] for k, v in parse_qs(parsed.query).items()}

    assert parsed.netloc == "accounts.google.com"
    assert q["client_id"] == "test-client"
    assert q["response_type"] == "code"
    assert "email" in q["scope"]
    assert q["redirect_uri"] == oauth.redirect_uri("google")
    assert q["state"], "without state the callback is forgeable"


@pytest.mark.parametrize("base", ["http://x.example", "http://x.example/"])
def test_the_redirect_uri_is_stable_whether_or_not_the_base_url_has_a_slash(
    base: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It must match what the provider has registered *exactly*.

    A trailing slash on the configured base URL produces `//auth/oauth/...`, which is a
    different string to the provider — and the error it returns for a redirect_uri mismatch
    says nothing about a slash.
    """
    monkeypatch.setattr(get_settings(), "public_base_url", base, raising=False)
    assert oauth.redirect_uri("google") == "http://x.example/auth/oauth/google/callback"


def test_state_round_trips(configured) -> None:
    """The return address survives the trip, normalised to an origin.

    An outside address does not survive it at all — see `test_an_outside_address_is_never_
    returned_to`, which is why this uses a local one.
    """
    state = oauth.issue_state("google", "http://127.0.0.1:5175/some/path")
    claims = oauth.read_state(state, "google")
    assert claims["next"] == "http://127.0.0.1:5175"


def test_state_minted_for_one_provider_is_refused_at_another() -> None:
    """Without this check a state is replayable across providers, and the account that gets
    linked is not the one the person chose."""
    state = oauth.issue_state("google")
    with pytest.raises(oauth.OAuthError, match="different provider"):
        oauth.read_state(state, "github")


def test_a_tampered_state_is_refused() -> None:
    """State is what stops a third party handing your browser a callback that signs you into
    their account."""
    state = oauth.issue_state("google")
    with pytest.raises(oauth.OAuthError):
        oauth.read_state(state + "x", "google")


def test_an_expired_state_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import timedelta

    monkeypatch.setattr(oauth, "STATE_TTL", timedelta(seconds=-1))
    stale = oauth.issue_state("google")
    with pytest.raises(oauth.OAuthError):
        oauth.read_state(stale, "google")


def test_state_signed_with_another_secret_is_refused() -> None:
    import jwt

    forged = jwt.encode({"provider": "google", "exp": 2**31}, "not-ours", algorithm="HS256")
    with pytest.raises(oauth.OAuthError):
        oauth.read_state(forged, "google")


@pytest.mark.anyio
async def test_exchange_refuses_an_unconfigured_provider() -> None:
    with pytest.raises(oauth.OAuthError, match="not configured"):
        await oauth.exchange("github", "some-code")


def test_github_asks_for_the_scope_that_actually_returns_an_email() -> None:
    """GitHub's profile endpoint returns null for email whenever the address is private —
    which is the default — so `read:user` alone yields an account with no address."""
    assert "user:email" in oauth.PROVIDERS["github"].scopes


def test_google_forces_the_account_chooser(configured) -> None:
    """Silently reusing whichever Google account the browser last used means signing in as the
    wrong person, which is a confusing thing to discover and to undo."""
    q = parse_qs(urlparse(oauth.authorize_url("google", "s")).query)
    assert q["prompt"] == ["select_account"]


# --------------------------------------------------------------------------- linking

import os  # noqa: E402
import uuid  # noqa: E402

from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from lumina.api.main import app  # noqa: E402
from lumina.api.routers import auth as auth_router  # noqa: E402
from lumina.db.base import get_session  # noqa: E402
from lumina.db.models import Channel, OAuthIdentity, User  # noqa: E402
from lumina.domain.money import Cents  # noqa: E402
from lumina.state.ledger import Ledger  # noqa: E402

DB = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not DB, reason="TEST_DATABASE_URL not set")


@pytest.fixture
async def maker():
    engine = create_async_engine(DB or "")
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def api(maker):
    async def override():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _link(maker, identity: oauth.Identity) -> uuid.UUID:
    async with maker() as s:
        user = await auth_router._link(s, identity)
        await s.commit()
        return user.id


@needs_db
@pytest.mark.anyio
async def test_a_first_sign_in_creates_the_account_its_channel_and_its_grant(maker) -> None:
    email = f"{uuid.uuid4()}@example.com"
    user_id = await _link(
        maker,
        oauth.Identity("google", f"sub-{uuid.uuid4()}", email, True, "Ada"),
    )

    async with maker() as s:
        channel = await s.scalar(select(Channel).where(Channel.user_id == user_id))
        assert channel is not None, "a user without a channel has nothing to inherit"
        available, _, _ = await Ledger(s).balance(user_id)
        assert available == auth_router.WELCOME_CREDITS


@needs_db
@pytest.mark.anyio
async def test_signing_in_twice_with_the_same_provider_account_is_one_user(maker) -> None:
    subject = f"sub-{uuid.uuid4()}"
    email = f"{uuid.uuid4()}@example.com"
    first = await _link(maker, oauth.Identity("google", subject, email, True, "Ada"))

    # Same subject, different address: exactly what happens when someone changes their email
    # at the provider. Matching on the address would make them a stranger.
    second = await _link(
        maker, oauth.Identity("google", subject, f"{uuid.uuid4()}@example.com", True, "Ada")
    )
    assert first == second

    async with maker() as s:
        available, _, _ = await Ledger(s).balance(first)
        assert available == auth_router.WELCOME_CREDITS, "granted twice"


@needs_db
@pytest.mark.anyio
async def test_a_verified_address_adopts_the_existing_password_account(maker) -> None:
    """Someone who signed up with a password and later presses "Continue with Google" is the
    same person, and must land in the same account rather than a duplicate."""
    email = f"{uuid.uuid4()}@example.com"
    async with maker() as s:
        user = User(email=email, password_hash="x", display_name="Ada")
        s.add(user)
        await s.flush()
        s.add(Channel(user_id=user.id, name="Ada", language="en", identity={}))
        await s.commit()
        original = user.id

    linked = await _link(maker, oauth.Identity("google", f"sub-{uuid.uuid4()}", email, True, "Ada"))
    assert linked == original


@needs_db
@pytest.mark.anyio
async def test_an_unverified_address_cannot_take_over_an_existing_account(maker) -> None:
    """The one that matters.

    Without this check, anyone can register someone else's address at a provider that does not
    verify it, press the button, and inherit that account — its projects, its identity kit and
    its credits.

    Refusing is the only safe answer. Adopting is the takeover; creating a second account is
    impossible because the address is unique. So the person is told to sign in the way they
    originally signed up.
    """
    email = f"{uuid.uuid4()}@example.com"
    async with maker() as s:
        victim = User(email=email, password_hash="x", display_name="Ada")
        s.add(victim)
        await s.flush()
        s.add(Channel(user_id=victim.id, name="Ada", language="en", identity={}))
        await Ledger(s).grant(victim.id, Cents(999), reason="welcome")
        await s.commit()
        victim_id = victim.id

    with pytest.raises(auth_router.EmailTakenError):
        await _link(maker, oauth.Identity("github", f"sub-{uuid.uuid4()}", email, False, "Not Ada"))

    async with maker() as s:
        available, _, _ = await Ledger(s).balance(victim_id)
        assert available == 999, "the victim's credits moved"
        linked = (
            await s.scalars(select(OAuthIdentity).where(OAuthIdentity.user_id == victim_id))
        ).all()
        assert not linked, "an unverified identity was attached to someone else's account"


@needs_db
@pytest.mark.anyio
async def test_the_callback_tells_the_person_the_address_is_already_used(api, maker) -> None:
    """`#error=email_taken` and not a generic failure: the screen can say "sign in with your
    password instead", which is the only thing that actually helps."""
    email = f"{uuid.uuid4()}@example.com"
    async with maker() as s:
        user = User(email=email, password_hash="x")
        s.add(user)
        await s.flush()
        s.add(Channel(user_id=user.id, name="A", language="en", identity={}))
        await s.commit()

    async def taken(provider: str, code: str) -> oauth.Identity:
        return oauth.Identity("github", f"sub-{uuid.uuid4()}", email, False, "Not Ada")

    import unittest.mock

    with unittest.mock.patch.object(oauth, "exchange", taken):
        state = oauth.issue_state("github")
        r = await api.get(
            "/auth/oauth/github/callback",
            params={"code": "c", "state": state},
            follow_redirects=False,
        )
    assert r.status_code == 303
    assert "#error=email_taken" in r.headers["location"]
    assert "token=" not in r.headers["location"]


@needs_db
@pytest.mark.anyio
async def test_two_providers_one_verified_address_are_one_account(maker) -> None:
    email = f"{uuid.uuid4()}@example.com"
    via_google = await _link(maker, oauth.Identity("google", f"g-{uuid.uuid4()}", email, True, "A"))
    via_github = await _link(maker, oauth.Identity("github", f"h-{uuid.uuid4()}", email, True, "A"))
    assert via_google == via_github

    async with maker() as s:
        rows = (
            await s.scalars(select(OAuthIdentity).where(OAuthIdentity.user_id == via_google))
        ).all()
        assert {r.provider for r in rows} == {"google", "github"}


@needs_db
@pytest.mark.anyio
async def test_start_is_503_when_the_provider_is_not_configured(api) -> None:
    """Not a 500 and not a redirect into a broken handshake: the server is telling the client
    that this button cannot work here, which is what `/auth/providers` also says."""
    r = await api.get("/auth/oauth/google/start")
    assert r.status_code == 503


@needs_db
@pytest.mark.anyio
async def test_an_unknown_provider_is_a_404(api) -> None:
    assert (await api.get("/auth/oauth/myspace/start")).status_code == 404


@needs_db
@pytest.mark.anyio
async def test_a_cancelled_sign_in_returns_to_the_app_not_a_json_error(api) -> None:
    """Pressing "cancel" at Google is not an error worth a page of its own — and stranding
    someone on a raw API response leaves them no way back into the app."""
    r = await api.get(
        "/auth/oauth/google/callback", params={"error": "access_denied"}, follow_redirects=False
    )
    assert r.status_code == 303
    assert "#error=access_denied" in r.headers["location"]


@needs_db
@pytest.mark.anyio
async def test_a_callback_with_a_forged_state_does_not_sign_anyone_in(api) -> None:
    r = await api.get(
        "/auth/oauth/google/callback",
        params={"code": "whatever", "state": "forged"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "#error=signin_failed" in r.headers["location"]
    assert "token=" not in r.headers["location"]


# --------------------------------------------------------------------------- return address


def test_the_default_return_is_the_configured_app() -> None:
    assert oauth.safe_next(None) == get_settings().web_base_url.rstrip("/")


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:5175",
        "http://127.0.0.1:5175",
        "http://192.168.223.205:5175",
        "http://172.24.80.1:5175",
    ],
)
def test_development_returns_to_whichever_local_address_the_app_was_opened_at(
    origin: str,
) -> None:
    """`localhost` and `127.0.0.1` are different origins to a browser.

    Sending everyone back to one configured address means the token is stored under one origin
    and read under the other — the handshake completes and the person is still signed out,
    which is a maddening thing to debug.
    """
    assert oauth.safe_next(origin) == origin


@pytest.mark.parametrize(
    "hostile",
    [
        "https://evil.example/steal",
        "http://evil.example",
        "//evil.example",
        "javascript:alert(1)",
        "",
    ],
)
def test_an_outside_address_is_never_returned_to(hostile: str) -> None:
    """An unchecked `next` is an open redirect — a link to our own domain that lands on
    someone else's, arriving with a live session token in the fragment."""
    assert oauth.safe_next(hostile) == get_settings().web_base_url.rstrip("/")


def test_production_returns_only_to_the_configured_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """The local-network allowance is a development convenience and must not survive into
    production, where a private address is not evidence of anything."""
    settings = get_settings()
    monkeypatch.setattr(settings, "env", "production", raising=False)
    monkeypatch.setattr(settings, "web_base_url", "https://app.lumina.test", raising=False)

    assert oauth.safe_next("http://192.168.1.9:5175") == "https://app.lumina.test"
    assert oauth.safe_next("https://app.lumina.test") == "https://app.lumina.test"


def test_the_return_address_travels_inside_the_signed_state() -> None:
    """Carried in the state rather than as its own parameter, so it cannot be swapped between
    the two legs of the handshake."""
    state = oauth.issue_state("github", "http://127.0.0.1:5175")
    assert oauth.read_state(state, "github")["next"] == "http://127.0.0.1:5175"
