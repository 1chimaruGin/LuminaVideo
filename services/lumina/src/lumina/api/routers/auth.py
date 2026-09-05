"""Sign up, sign in, sign out.

Two endpoints, not one. The previous version had a single "session" call that created the
account if it did not exist and returned it if it did — which meant typing any address into
the sign-in box signed you in as its owner, and there was no such thing as a wrong password
because the password was never sent.

Sign-up and sign-in fail differently on purpose:

  - Signing up with an address that already exists is a **409**. The person needs to know they
    already have an account so they can sign in instead.
  - Signing in with an unknown address, or the wrong password, is the **same 401** with the
    same message. Distinguishing them turns this endpoint into an oracle for which addresses
    are registered.

That asymmetry is deliberate and standard: sign-up already leaks existence by its nature (it
cannot both create the account and hide that it could not), so hiding it at sign-in is what
actually matters.
"""

from __future__ import annotations

import asyncio
import secrets
from typing import Any
from urllib.parse import quote

import structlog
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from lumina.api.deps import CurrentUser, Session
from lumina.api.schemas import ProviderOut, SessionOut, SignIn, SignUp
from lumina.db.models import Channel, OAuthIdentity, User
from lumina.domain.money import Cents
from lumina.platform import auth, oauth
from lumina.state.ledger import Ledger

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

#: The free tier, in credits. `design-brief.md` requires it to be "genuinely usable": at the
#: default image-plus-motion tier this is several complete previews, not a taste.
WELCOME_CREDITS = Cents(500)

#: One message for both sign-in failures. See the module docstring.
_REFUSED = "That email and password do not match an account"


@router.post("/signup", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def sign_up(body: SignUp, session: Session) -> SessionOut:
    """Create an account, its channel, and its opening balance."""
    email = _normalise(body.email)

    if await _find(session, email) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "There is already an account with that email")

    try:
        # Hashing is deliberately slow — that is the point of it — so it goes to a thread
        # rather than blocking the event loop for every other request in flight.
        hashed = await asyncio.to_thread(auth.hash_password, body.password)
    except auth.WeakPasswordError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None

    user = User(
        email=email,
        password_hash=hashed,
        display_name=body.display_name,
        locale=body.language,
    )
    session.add(user)
    await session.flush()

    # Every user has a channel, always. The identity kit is what makes episode two inherit
    # from episode one, and a user without one would have nothing to inherit.
    channel = Channel(
        user_id=user.id,
        name=body.display_name or email.split("@")[0],
        language=body.language,
        identity={},
    )
    session.add(channel)
    await session.flush()

    ledger = Ledger(session)
    await ledger.grant(user.id, WELCOME_CREDITS, reason="welcome")
    await session.commit()

    log.info("auth.signup", user=str(user.id))
    return await _session_for(session, user, channel)


@router.post("/session", response_model=SessionOut)
async def sign_in(body: SignIn, session: Session) -> SessionOut:
    """Sign in to an existing account."""
    email = _normalise(body.email)
    user = await _find(session, email)

    if user is None:
        # Hash anyway. Returning immediately for an unknown address makes the response
        # measurably faster than for a known one, which is the same enumeration oracle the
        # shared error message exists to close.
        await asyncio.to_thread(auth.verify_password, _DUMMY_HASH, body.password)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _REFUSED)

    ok = await asyncio.to_thread(auth.verify_password, user.password_hash, body.password)
    if not ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _REFUSED)

    if user.password_hash and auth.needs_rehash(user.password_hash):
        # Upgrade in place on a successful sign-in. The alternative is that hashes made with
        # yesterday's parameters stay that way until the account is abandoned.
        user.password_hash = await asyncio.to_thread(auth.hash_password, body.password)

    channel = await _channel(session, user)
    await session.commit()
    log.info("auth.signin", user=str(user.id))
    return await _session_for(session, user, channel)


@router.get("/session", response_model=SessionOut)
async def whoami(session: Session, user: CurrentUser) -> SessionOut:
    """Who the caller is, and what they can spend. One round trip on app open.

    Reissues the token, so an app left open for a month renews rather than expiring under
    someone mid-project.
    """
    return await _session_for(session, user, await _channel(session, user))


# --------------------------------------------------------------------------- oauth


@router.get("/providers", response_model=list[ProviderOut])
async def providers() -> list[ProviderOut]:
    """Which sign-in providers exist, and which can actually be used.

    Unconfigured ones are listed too rather than hidden. A button that disappears leaves
    someone wondering whether they misremembered; a button that is visibly off, with a reason,
    tells the truth — and tells whoever is deploying this what is missing.
    """
    return [ProviderOut(**p) for p in oauth.available()]


@router.get("/oauth/{provider}/start")
async def oauth_start(provider: str, next: str | None = None) -> RedirectResponse:
    """Send the browser to the provider.

    `next` is where to come back to, and it is carried inside the signed state rather than as
    its own parameter so it cannot be swapped between the two legs. It is validated when the
    state is minted — an unchecked one is an open redirect that would arrive carrying a live
    session token.
    """
    if provider not in oauth.PROVIDERS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown provider {provider}")
    if not oauth.configured(provider):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"{provider} sign-in is not configured on this server",
        )

    state = oauth.issue_state(provider, next)
    return RedirectResponse(
        oauth.authorize_url(provider, state), status_code=status.HTTP_307_TEMPORARY_REDIRECT
    )


@router.api_route(
    "/oauth/{provider}/callback",
    methods=["GET", "POST"],
    # Kept out of the schema. The browser navigates here; no client ever calls it, and one
    # path serving two methods generates two operations with the same id — which collides in
    # the generated TypeScript rather than describing anything useful.
    include_in_schema=False,
)
async def oauth_callback(
    provider: str,
    request: Request,
    session: Session,
) -> RedirectResponse:
    """Where the provider sends the browser back.

    GET and POST both: Apple form-POSTs the callback whenever name or email are requested,
    while Google and GitHub use a query string.

    Every failure ends the same way — back at the sign-in screen with a short reason in the
    fragment. Returning a JSON error here would strand the person on a blank API response with
    no way back into the app.
    """
    if provider not in oauth.PROVIDERS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown provider {provider}")

    form = await request.form() if request.method == "POST" else {}
    params: dict[str, Any] = {**request.query_params, **form}

    if params.get("error"):
        # The person pressed "cancel", which is not an error worth a page of its own.
        return _back(f"#error={quote(str(params.get('error')))}")

    code, state = str(params.get("code", "")), str(params.get("state", ""))
    if not code or not state:
        return _back("#error=incomplete")

    try:
        claims = oauth.read_state(state, provider)
        identity = await oauth.exchange(provider, code)
    except oauth.OAuthError as exc:
        # Logged in full, shown as one word. The detail is for us; the person needs a retry.
        log.warning("auth.oauth.failed", provider=provider, error=str(exc))
        return _back("#error=signin_failed")

    if not identity.email:
        return _back("#error=no_email")

    try:
        user = await _link(session, identity)
    except EmailTakenError:
        await session.rollback()
        # Named specifically so the screen can say "sign in with your password instead"
        # rather than "something went wrong", which would leave no way forward.
        return _back("#error=email_taken")
    await session.commit()
    log.info("auth.oauth", provider=provider, user=str(user.id))

    token = auth.issue(user.id)
    # The fragment, never the query: a fragment is not sent to any server, so the token stays
    # out of access logs, Referer headers and analytics.
    return _back(f"#token={quote(token)}", next_url=str(claims.get("next") or ""))


class EmailTakenError(Exception):
    """The provider gave an address that already belongs to someone, and did not verify it.

    Neither outcome is available: adopting the account is the takeover this whole check
    exists to prevent, and creating a second one collides with the unique address. The person
    is told to sign in the way they originally signed up.
    """


async def _link(session: Session, identity: oauth.Identity) -> User:
    """Find, link, or create the account this provider identity belongs to.

    In that order, and the middle step is where the security lives: an existing account is
    only ever adopted when the provider says the address is **verified**. Otherwise anyone can
    register someone else's address at a provider that does not check and inherit their
    account, their projects and their credits.
    """
    existing = await session.scalar(
        select(OAuthIdentity).where(
            OAuthIdentity.provider == identity.provider,
            OAuthIdentity.subject == identity.subject,
        )
    )
    if existing is not None:
        user = await session.get(User, existing.user_id)
        if user is not None:
            # Keep the recorded address current for support; it is never used for matching.
            existing.email = identity.email
            return user

    email = _normalise(identity.email)
    match = await _find(session, email)

    if match is not None:
        if not identity.email_verified:
            # The address is spoken for and the provider will not vouch for it. Refusing is
            # the only safe answer — and a second account cannot be made either, because the
            # address is unique.
            raise EmailTakenError
        user = match
    else:
        user = User(
            email=email,
            password_hash=None,  # OAuth-only until they choose to set one
            display_name=identity.display_name,
            locale="en",
        )
        session.add(user)
        await session.flush()

        session.add(
            Channel(
                user_id=user.id,
                name=identity.display_name or email.split("@")[0],
                language="en",
                identity={},
            )
        )
        await session.flush()
        await Ledger(session).grant(user.id, WELCOME_CREDITS, reason="welcome")

    session.add(
        OAuthIdentity(
            user_id=user.id,
            provider=identity.provider,
            subject=identity.subject,
            email=identity.email,
        )
    )
    await session.flush()
    return user


def _back(fragment: str, next_url: str = "") -> RedirectResponse:
    """Back to the app, carrying the outcome in the fragment.

    `next_url` came out of the signed state, which validated it when it was minted — so it is
    re-validated here rather than trusted, because a value that only *used* to be checked is
    one refactor away from not being.
    """
    base = oauth.safe_next(next_url or None)
    return RedirectResponse(f"{base}/{fragment}", status_code=status.HTTP_303_SEE_OTHER)


def _normalise(email: str) -> str:
    """Fold case and strip space.

    Without this `Ada@example.com` and `ada@example.com` are two accounts — two welcome grants,
    and a person who cannot sign in to the one they made.
    """
    return email.strip().lower()


async def _find(session: Session, email: str) -> User | None:
    # Compared case-folded on both sides: existing rows predate normalisation, and a duplicate
    # that slipped in before it must still be found rather than shadowed by a new signup.
    found: User | None = await session.scalar(select(User).where(func.lower(User.email) == email))
    return found


async def _channel(session: Session, user: User) -> Channel:
    channel = await session.scalar(select(Channel).where(Channel.user_id == user.id).limit(1))
    if channel is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no channel")
    return channel


async def _session_for(session: Session, user: User, channel: Channel) -> SessionOut:
    available, _, _ = await Ledger(session).balance(user.id)
    return SessionOut(
        token=auth.issue(user.id),
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        channel_id=channel.id,
        language=channel.language,
        credits=int(available),
    )


#: A real Argon2 hash of a value nobody knows, used only to spend the same time on an unknown
#: address as on a known one. Generated once at import so it is not itself a constant an
#: attacker could recognise in a timing profile.
_DUMMY_HASH = auth.hash_password(secrets.token_urlsafe(32))
