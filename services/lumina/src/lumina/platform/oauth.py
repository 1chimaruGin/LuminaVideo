"""Sign in with Google, GitHub or Apple.

Three providers, one flow, because they only differ in four URLs and how they answer "who is
this". Everything vendor-specific is in `PROVIDERS`; nothing above this file names a provider.

The security decisions, none of which are optional:

**State is signed, not stored.** The `state` parameter is what stops a third party handing
your browser a callback URL that signs you into *their* account. It is a short-lived JWT
signed with the app secret rather than a row in a table or a server-side session: it needs to
survive a redirect to another origin and back, and it needs to work when the API has more than
one process. Signing it means any process can verify it and none of them have to share memory.

**An account is only linked to an existing one on a VERIFIED email.** Otherwise anyone can
register `you@yourcompany.com` at a provider that does not check, sign in here, and inherit
your account and its credits. Google states `email_verified`; GitHub is asked for the verified
primary address specifically; Apple states `email_verified` in its id token.

**The token goes back in the URL fragment, not the query.** A fragment is never sent to a
server, so it stays out of access logs, `Referer` headers and analytics. The app reads it and
immediately clears it from the address bar.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import structlog

from lumina.config import get_settings

log = structlog.get_logger(__name__)

#: How long a half-finished sign-in stays valid. Long enough to type a password and clear a
#: second factor, short enough that a leaked callback URL is worthless by the time it is found.
STATE_TTL = timedelta(minutes=15)

_ALGORITHM = "HS256"


class OAuthError(RuntimeError):
    """The handshake failed. The message is safe to log, never to show a user verbatim."""


@dataclass(frozen=True, slots=True)
class Provider:
    id: str
    name: str
    authorize_url: str
    token_url: str
    scopes: str
    #: Where to ask who the user is. Apple needs no call — its id token carries the claims.
    userinfo_url: str | None = None


PROVIDERS: dict[str, Provider] = {
    "google": Provider(
        id="google",
        name="Google",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        userinfo_url="https://openidconnect.googleapis.com/v1/userinfo",
        scopes="openid email profile",
    ),
    "github": Provider(
        id="github",
        name="GitHub",
        authorize_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        userinfo_url="https://api.github.com/user",
        # `user:email` and not just `read:user`: GitHub's profile endpoint returns null for
        # email whenever the user has kept theirs private, which is the default.
        scopes="read:user user:email",
    ),
    "apple": Provider(
        id="apple",
        name="Apple",
        authorize_url="https://appleid.apple.com/auth/authorize",
        token_url="https://appleid.apple.com/auth/token",
        userinfo_url=None,  # the id token is the answer
        scopes="name email",
    ),
}


@dataclass(frozen=True, slots=True)
class Identity:
    """Who the provider says this is."""

    provider: str
    subject: str
    email: str
    email_verified: bool
    display_name: str | None


def configured(provider_id: str) -> bool:
    client_id, client_secret = get_settings().oauth_credentials(provider_id)
    return bool(client_id and client_secret)


def available() -> list[dict[str, Any]]:
    """Every provider, and whether it can actually be used.

    Returned even when unconfigured so the sign-in screen can show the button and say why it
    is off — a button that vanishes leaves someone wondering whether they misremembered.
    """
    return [{"id": p.id, "name": p.name, "enabled": configured(p.id)} for p in PROVIDERS.values()]


def redirect_uri(provider_id: str) -> str:
    """Must match what the provider has registered exactly, trailing slash included."""
    return f"{get_settings().public_base_url.rstrip('/')}/auth/oauth/{provider_id}/callback"


def safe_next(candidate: str | None) -> str:
    """Where the callback may send the browser once it is done.

    Validated, because an unchecked `next` is an open redirect: a link to our own domain that
    quietly lands on someone else's, which is exactly what a phishing page wants to borrow —
    and here it would arrive carrying a live session token in the fragment.

    Development accepts any loopback or private-network origin, because the app is legitimately
    opened at `localhost`, at `127.0.0.1`, at the WSL address and from a phone, and those are
    all the same app. Production accepts the configured origin and nothing else.
    """
    settings = get_settings()
    configured = settings.web_base_url.rstrip("/")
    if not candidate:
        return configured

    from urllib.parse import urlparse

    parsed = urlparse(candidate)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return configured

    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin == configured:
        return origin

    if settings.is_production:
        log.warning("auth.oauth.next_refused", candidate=origin)
        return configured

    host = parsed.hostname
    private = (
        host in ("localhost", "127.0.0.1", "::1")
        or host.startswith(("10.", "192.168.", "127."))
        or any(host.startswith(f"172.{n}.") for n in range(16, 32))
    )
    if private:
        return origin

    log.warning("auth.oauth.next_refused", candidate=origin)
    return configured


def issue_state(provider_id: str, next_url: str | None = None) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "provider": provider_id,
            "next": safe_next(next_url),
            "nonce": uuid.uuid4().hex,
            "iat": int(now.timestamp()),
            "exp": int((now + STATE_TTL).timestamp()),
        },
        get_settings().secret_key,
        algorithm=_ALGORITHM,
    )


def read_state(state: str, provider_id: str) -> dict[str, Any]:
    """Verify a callback's state, or refuse.

    Checking the provider matches is not paranoia: without it a state minted for one provider
    is replayable at another, and the account that gets linked is not the one the user chose.
    """
    try:
        claims: dict[str, Any] = jwt.decode(
            state,
            get_settings().secret_key,
            algorithms=[_ALGORITHM],
            options={"require": ["exp", "provider"]},
        )
    except jwt.PyJWTError as exc:
        raise OAuthError(f"bad state: {exc}") from None
    if claims.get("provider") != provider_id:
        raise OAuthError("state was minted for a different provider")
    return claims


def authorize_url(provider_id: str, state: str) -> str:
    from urllib.parse import urlencode

    provider = _provider(provider_id)
    client_id, _ = get_settings().oauth_credentials(provider_id)
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri(provider_id),
        "response_type": "code",
        "scope": provider.scopes,
        "state": state,
    }
    if provider_id == "google":
        # Ask for a fresh consent screen rather than silently reusing whichever Google account
        # the browser last used — signing in as the wrong person is a confusing thing to undo.
        params["prompt"] = "select_account"
    if provider_id == "apple":
        # Apple POSTs the callback when scopes include name or email.
        params["response_mode"] = "form_post"
    return f"{provider.authorize_url}?{urlencode(params)}"


async def exchange(provider_id: str, code: str) -> Identity:
    """Turn an authorization code into who the person is."""
    provider = _provider(provider_id)
    client_id, client_secret = get_settings().oauth_credentials(provider_id)
    if not (client_id and client_secret):
        raise OAuthError(f"{provider_id} is not configured")

    async with httpx.AsyncClient(timeout=20) as http:
        token = await _token(http, provider, client_id, client_secret, code)
        if provider_id == "apple":
            return _from_apple(token)
        if provider_id == "github":
            return await _from_github(http, token)
        return await _from_google(http, token)


async def _token(
    http: httpx.AsyncClient, provider: Provider, client_id: str, client_secret: str, code: str
) -> dict[str, Any]:
    response = await http.post(
        provider.token_url,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri(provider.id),
        },
        # GitHub returns form-encoded unless asked otherwise, and answers 200 with an `error`
        # field rather than a 4xx — so the status alone cannot be trusted.
        headers={"Accept": "application/json"},
    )
    if response.status_code >= 400:
        raise OAuthError(f"{provider.id} token exchange returned {response.status_code}")
    payload: dict[str, Any] = response.json()
    if payload.get("error"):
        raise OAuthError(f"{provider.id}: {payload.get('error_description') or payload['error']}")
    return payload


async def _from_google(http: httpx.AsyncClient, token: dict[str, Any]) -> Identity:
    provider = PROVIDERS["google"]
    assert provider.userinfo_url is not None
    response = await http.get(
        provider.userinfo_url,
        headers={"Authorization": f"Bearer {token['access_token']}"},
    )
    if response.status_code >= 400:
        raise OAuthError(f"google userinfo returned {response.status_code}")
    me = response.json()
    return Identity(
        provider="google",
        subject=str(me["sub"]),
        email=str(me.get("email", "")),
        email_verified=bool(me.get("email_verified")),
        display_name=me.get("name"),
    )


async def _from_github(http: httpx.AsyncClient, token: dict[str, Any]) -> Identity:
    headers = {
        "Authorization": f"Bearer {token['access_token']}",
        "Accept": "application/vnd.github+json",
    }
    profile = await http.get("https://api.github.com/user", headers=headers)
    if profile.status_code >= 400:
        raise OAuthError(f"github user returned {profile.status_code}")
    me = profile.json()

    # The profile's `email` is null whenever the address is private, which is the default, and
    # it does not say whether it is verified. The emails endpoint answers both.
    emails = await http.get("https://api.github.com/user/emails", headers=headers)
    address, verified = "", False
    if emails.status_code < 400:
        rows = emails.json()
        primary = next(
            (r for r in rows if r.get("primary") and r.get("verified")),
            next((r for r in rows if r.get("verified")), None),
        )
        if primary:
            address, verified = str(primary["email"]), True
    if not address:
        address = str(me.get("email") or "")

    return Identity(
        provider="github",
        subject=str(me["id"]),
        email=address,
        email_verified=verified,
        display_name=me.get("name") or me.get("login"),
    )


def _from_apple(token: dict[str, Any]) -> Identity:
    """Apple answers with an id token and no userinfo endpoint.

    The signature is not checked here — the token came straight from Apple's token endpoint
    over TLS in a request we made, which is the case RFC 8725 allows reading claims without
    re-verifying. Verification would be required if it had arrived via the browser.
    """
    raw = token.get("id_token")
    if not raw:
        raise OAuthError("apple returned no id_token")
    claims = jwt.decode(raw, options={"verify_signature": False})
    return Identity(
        provider="apple",
        subject=str(claims["sub"]),
        email=str(claims.get("email", "")),
        # Apple sends this as the string "true" as often as a boolean.
        email_verified=str(claims.get("email_verified", "")).lower() == "true",
        # Apple sends the name once, on first authorisation, in the form post — never here.
        display_name=None,
    )


def _provider(provider_id: str) -> Provider:
    provider = PROVIDERS.get(provider_id)
    if provider is None:
        raise OAuthError(f"unknown provider {provider_id}")
    return provider
