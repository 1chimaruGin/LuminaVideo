"""Bringing in media the user already has.

The entry point for the two recipes that start from a file rather than an idea — Subtitles
only and Clip my long video — and for the reference images and voice samples the identity kit
holds. Without this the Start screen can detect a dropped video and do nothing with it.

Two things this deliberately does not do:

  - It never reads the upload into memory. A creator clipping a forty-minute video is sending
    gigabytes; `await file.read()` on that is an OOM, and the failure lands on whichever
    request happens to be unlucky rather than on the one at fault.
  - It never trusts the client. Size is counted as bytes arrive rather than read from
    Content-Length, and the type is probed with ffprobe rather than taken from the filename —
    both are attacker-controlled, and the duration in particular is load-bearing for every
    caption timing downstream.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Annotated

import structlog
from fastapi import APIRouter, File, HTTPException, UploadFile, status

from lumina.api.deps import CurrentUser, Session
from lumina.api.schemas import UploadOut
from lumina.config import get_settings
from lumina.domain.assets import AssetKind
from lumina.execution.compose import ffmpeg
from lumina.state.assets import AssetStore

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/uploads", tags=["uploads"])

#: What each accepted top-level type becomes in the asset store. Anything not listed is
#: refused rather than stored as an opaque blob: an asset whose kind we cannot name is an
#: asset no stage knows how to consume.
_KINDS: dict[str, AssetKind] = {
    "video": AssetKind.VIDEO,
    "audio": AssetKind.AUDIO,
    "image": AssetKind.IMAGE,
    "text": AssetKind.TEXT,
}

#: Subtitle files browsers have no `text/*` type for. `.srt` in particular arrives as
#: `application/x-subrip`, `application/octet-stream`, or an empty string depending on the
#: platform, so the extension is the only signal left — which is exactly why what is *inside*
#: is sniffed later rather than trusted from here.
_TEXT_TYPES = frozenset({"application/x-subrip", "application/octet-stream", ""})
_TEXT_SUFFIXES = (".srt", ".vtt", ".txt", ".sbv", ".sub", ".text")

#: Read size. Large enough that a multi-gigabyte upload is not millions of syscalls, small
#: enough that the copy stays off the event loop in reasonable slices.
_CHUNK = 1 << 20


@router.post("", response_model=UploadOut, status_code=status.HTTP_201_CREATED)
async def upload(
    file: Annotated[UploadFile, File()],
    session: Session,
    user: CurrentUser,
) -> UploadOut:
    """Store a file and tell the caller what it turned out to be.

    Returns the probed truth — real duration, real dimensions, whether there is any speech to
    transcribe — because the Start screen's next question ("can I subtitle this?") is answered
    by those numbers and not by the file extension.
    """
    declared = (file.content_type or "application/octet-stream").split(";")[0].strip()
    kind = _KINDS.get(declared.split("/")[0])
    if kind is None and _looks_like_text(file.filename, declared):
        kind = AssetKind.TEXT
        declared = "text/plain"
    if kind is None:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"cannot use {declared}; upload a video, an audio file, an image, "
            "or a subtitle file (.srt, .vtt, .txt)",
        )

    limit = get_settings().max_upload_bytes
    # A named temporary directory rather than SpooledTemporaryFile: ffprobe needs a real path,
    # and content-addressing needs to read the bytes back to hash them.
    with tempfile.TemporaryDirectory(prefix="lumina-upload-") as tmp:
        target = Path(tmp) / "upload"
        size = await _spill(file, target, limit)

        media = await _describe(target, kind)
        asset = await AssetStore(session).put_path(
            target,
            kind=kind,
            mime=declared,
            duration_ms=media.duration_ms or None,
            width=media.width or None,
            height=media.height or None,
        )
        await session.commit()

    log.info(
        "upload",
        user=str(user.id),
        kind=kind.value,
        bytes=size,
        ms=media.duration_ms,
        asset=str(asset.id),
    )
    return UploadOut(
        asset_id=asset.id,
        kind=kind.value,
        mime=declared,
        bytes=size,
        filename=file.filename or "upload",
        duration_ms=media.duration_ms,
        width=media.width,
        height=media.height,
        has_audio=media.has_audio,
        aspect=media.aspect,
    )


async def _spill(file: UploadFile, target: Path, limit: int) -> int:
    """Stream the upload to disk, counting as it goes.

    The count is the enforcement. Content-Length is a claim by the sender, and a chunked
    request need not send one at all — checking it would reject honest large files and admit
    dishonest ones.
    """
    size = 0
    with target.open("wb") as out:
        while chunk := await file.read(_CHUNK):
            size += len(chunk)
            if size > limit:
                raise HTTPException(
                    status.HTTP_413_CONTENT_TOO_LARGE,
                    f"file is over the {limit // 1024**3} GB limit",
                )
            out.write(chunk)
    if size == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty file")
    return size


def _looks_like_text(filename: str | None, declared: str) -> bool:
    """Whether an unrecognised type is a subtitle file by another name."""
    if declared not in _TEXT_TYPES:
        return False
    return (filename or "").lower().endswith(_TEXT_SUFFIXES)


async def _describe(path: Path, kind: AssetKind) -> ffmpeg.Probe:
    """Measure the file. An image has no duration and no audio, so it is not probed."""
    if kind in (AssetKind.IMAGE, AssetKind.TEXT):
        return ffmpeg.Probe(duration_ms=0, width=0, height=0, has_audio=False)
    try:
        return await ffmpeg.probe(path)
    except ffmpeg.FfmpegError as exc:
        # A file that ffprobe cannot open is not media, whatever it claimed to be. Say so
        # here rather than storing it and failing four stages later inside a worker.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "could not read that file as media"
        ) from exc
