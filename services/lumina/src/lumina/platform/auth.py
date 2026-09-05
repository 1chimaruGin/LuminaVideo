"""Passwords and sessions.

The Platform plane's job. Two things live here and nothing else does: how a password becomes
a hash, and how a signed-in user becomes a token the client can carry.

Both are deliberately boring choices.

**Argon2id** because it is the current password-hashing recommendation and `argon2-cffi`
picks sane parameters that it also keeps current — a hand-tuned cost that was right in 2022 is
wrong now, and `check_needs_rehash` lets an old hash be upgraded on the next successful login
without anyone being locked out.

**A signed token rather than the user id.** The id is a database key: it turns up in logs, in
error reports, in URLs someone pastes into a ticket. Handing it out as the credential means
every one of those is a credential leak. A token is scoped, expires, and can be rotated by
changing one secret.

Not here: OAuth and email OTP. When they land they produce a session through `issue()` exactly
as a password does, and nothing downstream of `current_user` changes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from lumina.config import get_settings

#: One hasher, reused. Constructing it is cheap but not free, and its parameters are the
#: library's current recommendation rather than ours to guess at.
_hasher = PasswordHasher()

#: How long a session lasts. Long, because `design-brief.md` assumes a creator on a phone
#: publishing frequently — being signed out mid-project is a worse failure than a stale token.
SESSION_TTL = timedelta(days=30)

_ALGORITHM = "HS256"

#: The shortest password accepted. Length is the only requirement that reliably matters; a
#: composition rule ("one symbol") mostly teaches people to write Password1!.
MIN_PASSWORD = 8


class WeakPasswordError(ValueError):
    def __init__(self) -> None:
        super().__init__(f"password must be at least {MIN_PASSWORD} characters")


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD:
        raise WeakPasswordError
    return _hasher.hash(password)


def verify_password(stored: str | None, password: str) -> bool:
    """Check a password against its hash.

    Returns False rather than raising for every failure mode — wrong password, malformed hash,
    or an account with no password at all (OAuth-only). The caller must not be able to tell
    those apart, because the difference is exactly what an attacker enumerating accounts wants.
    """
    if not stored:
        return False
    try:
        return _hasher.verify(stored, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(stored: str) -> bool:
    """Whether a stored hash was made with parameters we have since moved on from."""
    try:
        return _hasher.check_needs_rehash(stored)
    except InvalidHashError:
        return True


def issue(user_id: uuid.UUID) -> str:
    """A session token for this user."""
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
            "iat": int(now.timestamp()),
            "exp": int((now + SESSION_TTL).timestamp()),
        },
        get_settings().secret_key,
        algorithm=_ALGORITHM,
    )


def read(token: str) -> uuid.UUID | None:
    """The user a token names, or None if it does not name one we will accept.

    Every failure collapses to None on purpose: expired, tampered with, signed by a different
    secret, or simply not a token. The caller answers 401 to all of them, because telling a
    client *which* it was is telling an attacker how close they got.

    The algorithm is pinned. Accepting whatever the token's own header claims is the classic
    JWT hole — a token can then assert `alg: none`, or ask to be verified with HMAC against a
    public key it already knows.
    """
    try:
        claims = jwt.decode(
            token,
            get_settings().secret_key,
            algorithms=[_ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
        return uuid.UUID(claims["sub"])
    except (jwt.PyJWTError, ValueError, KeyError):
        return None
