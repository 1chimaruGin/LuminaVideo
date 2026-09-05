"""The synthesized dub for a project, stored once and reused.

Speech is quota'd per request and a dub is one request per line, so the same track must never
be synthesized twice. It was: the preview built the whole dub so the creator could hear it,
and then the render built it again from the same lines in the same voice — one video, two full
allowances. On a key permitting a hundred requests a day and a video of thirty-six lines, that
is the difference between dubbing two videos a day and dubbing one.

Keyed by what actually determines the audio: the voice, and every line with its timing. Edit a
caption or move a cue and the fingerprint changes, so the stale track is not served for words
that are no longer on screen — which is the failure the naive version of this has, and it is
worse than re-synthesizing, because the video ships saying something the captions do not.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import select

from lumina.db.models import Asset
from lumina.domain.assets import AssetKind
from lumina.execution import speak

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)


def fingerprint(cues: list[speak.Cue], *, voice: str, language: str) -> str:
    """What makes this dub this dub.

    The words, where each one sits, how long it has, the voice saying it and the language it
    is in. Anything that changes the audio has to be in here; anything that does not must stay
    out, or the cache misses on edits that do not matter.
    """
    #: Length-prefixed rather than joined with a separator, so no separator can appear inside
    #: a line and make two different scripts hash the same. Subtitles are user text; assuming
    #: a character will not appear in them is how that assumption gets violated.
    digest = hashlib.sha256()
    digest.update(f"{len(voice)}:{voice}{len(language)}:{language}".encode())
    for cue in cues:
        digest.update(f"{cue.start_ms}:{cue.duration_ms}:{len(cue.text)}:{cue.text}".encode())
    return digest.hexdigest()


async def find(session: AsyncSession, key: str) -> Asset | None:
    """A dub already synthesized for this exact fingerprint, if there is one."""
    found: Asset | None = await session.scalar(
        select(Asset).where(
            Asset.kind == AssetKind.AUDIO.value,
            Asset.provenance["dub"].astext == key,
        )
    )
    return found


async def build(
    session: AsyncSession,
    assets: Any,
    cues: list[speak.Cue],
    *,
    voice: str,
    language: str,
    total_ms: int,
) -> tuple[Asset, bool]:
    """The dub for these lines, synthesized only if it has not been already.

    Returns the asset and whether it had to be made. The caller wants to know: making one
    spends quota and takes minutes, and a screen that cannot tell the difference will show a
    progress message for work that finished instantly.
    """
    key = fingerprint(cues, voice=voice, language=language)

    found = await find(session, key)
    if found is not None:
        log.info("dubtrack.reused", voice=voice, language=language, lines=len(cues))
        return found, False

    audio = await speak.dub(cues, language=language, voice=voice, total_ms=total_ms)
    asset = await assets.put_bytes(
        audio, kind=AssetKind.AUDIO, mime="audio/wav", duration_ms=total_ms
    )
    #: Written after storing rather than passed in, because `put_bytes` deduplicates on the
    #: bytes. Never overwritten: if some other fingerprint already claimed this exact audio,
    #: reassigning the key would make *that* one miss forever. Two different line sets
    #: producing byte-identical speech is not a real scenario, and the cost of the collision
    #: is one re-synthesis rather than a wrong answer.
    if not (asset.provenance or {}).get("dub"):
        asset.provenance = {**(asset.provenance or {}), "dub": key}
    await session.flush()
    log.info("dubtrack.built", voice=voice, language=language, lines=len(cues))
    return asset, True
