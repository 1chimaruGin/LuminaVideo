"""Serving stored bytes back.

In production this endpoint should not carry the bytes at all — assets live in R2, and video
egress through the API is the line item that destroys margin. The right shape there is a
signed URL and a redirect, which is why `AssetStore.url()` already exists. Locally there is no
CDN to sign for, so this streams from disk.

Range support is not an optimisation here. A `<video>` element issues `Range: bytes=0-` and
then seeks by asking for byte windows; an endpoint that only ever returns 200 with the whole
body gives you a player that cannot scrub and, on Safari, one that will not start at all.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Response, status
from fastapi.responses import RedirectResponse, StreamingResponse

from lumina.api.deps import Session
from lumina.config import get_settings
from lumina.db.models import Asset
from lumina.state.assets import LocalBlobs, blobs

router = APIRouter(prefix="/assets", tags=["assets"])

#: Streaming window. Matches the order of magnitude a browser asks for when scrubbing.
_CHUNK = 1 << 18

_RANGE = re.compile(r"bytes=(\d*)-(\d*)")


@router.get("/{asset_id}")
async def read_asset(
    asset_id: uuid.UUID,
    session: Session,
    range: Annotated[str | None, Header()] = None,
    download: str | None = None,
) -> Response:
    """Stream one asset.

    Assets are immutable and content-addressed, so the URL for a given asset can never return
    different bytes. That makes an unconditional immutable cache header correct rather than
    optimistic — the id changes whenever the content does.

    `download=<name>` asks for it as a file rather than as something to display.

    That parameter exists because the obvious approach does not work: an `<a download="...">`
    is ignored unless the link is same-origin, and the app and the API are not. So pressing
    Download navigated the tab to the asset instead of saving it — the creator left the
    product, landed on a bare video URL, and the browser named the file after the twelve
    characters of hash this used to send as its filename, with no extension. The name has to
    come from the server, and so does the disposition.
    """
    asset = await session.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such asset")

    store = blobs()
    if not isinstance(store, LocalBlobs):
        # Object storage signs its own URLs and serves them from the edge. Proxying those
        # bytes through the API would pay egress twice and add a hop for no benefit.
        return RedirectResponse(store.url(asset.storage_key), status_code=status.HTTP_302_FOUND)

    path = Path(get_settings().local_media_root) / asset.storage_key
    if not path.is_file():
        # The row exists but the object does not. Worth distinguishing from a 404 on the id:
        # it means the blob store and the database have diverged, which is an operational
        # problem, not a bad request.
        raise HTTPException(status.HTTP_410_GONE, "asset row exists but its bytes are missing")

    size = path.stat().st_size
    headers = {
        "accept-ranges": "bytes",
        "cache-control": "public, max-age=31536000, immutable",
        "content-disposition": _disposition(asset, download),
    }

    span = _parse_range(range, size)
    if span is None:
        return StreamingResponse(
            _stream(path, 0, size - 1),
            media_type=asset.mime,
            headers={**headers, "content-length": str(size)},
        )

    start, end = span
    return StreamingResponse(
        _stream(path, start, end),
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        media_type=asset.mime,
        headers={
            **headers,
            "content-range": f"bytes {start}-{end}/{size}",
            "content-length": str(end - start + 1),
        },
    )


#: Extension per mime type, for naming a file the creator did not name themselves.
_SUFFIX = {
    "video/mp4": ".mp4",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "text/plain": ".txt",
    "application/x-subrip": ".srt",
}


def _disposition(asset: Asset, download: str | None) -> str:
    """The `content-disposition` header: display it, or save it under a given name.

    The name is rebuilt from scratch rather than sanitised, because a header value is not the
    place to be clever: a quote or a newline in it splits the response, and a slash makes the
    browser write outside the folder it was told to use. Only the characters a filename needs
    survive, and the extension comes from the mime type we recorded — not from the string a
    caller passed in.
    """
    stem = "".join(c for c in (download or "") if c.isalnum() or c in "-_ ").strip()[:80]
    suffix = _SUFFIX.get(asset.mime, "")
    if not download:
        #: Even inline, a name with an extension beats twelve characters of hash: it is what
        #: the browser puts in the title bar and what "Save as" offers.
        return f'inline; filename="lumina-{asset.sha256[:12]}{suffix}"'
    return f'attachment; filename="{stem or f"lumina-{asset.sha256[:12]}"}{suffix}"'


def _parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """The one range form that matters, or None for "send the whole thing".

    Multipart ranges are legal and no browser sends them for media. A malformed or
    unsatisfiable range degrades to a full response rather than a 416: a player that gets
    the bytes it asked about is better off than one handed an error it will not retry.
    """
    if not header or size == 0:
        return None
    match = _RANGE.fullmatch(header.strip())
    if match is None:
        return None

    raw_start, raw_end = match.groups()
    if raw_start:
        start = int(raw_start)
        end = int(raw_end) if raw_end else size - 1
    elif raw_end:
        # A suffix range: the last N bytes. Players use this to read an MP4's moov atom when
        # it was not written at the front.
        start, end = max(0, size - int(raw_end)), size - 1
    else:
        return None

    end = min(end, size - 1)
    if start > end or start >= size:
        return None
    return start, end


async def _stream(path: Path, start: int, end: int) -> AsyncIterator[bytes]:
    """Read the window in slices.

    Sliced rather than one `read()` of the range: the whole point is that a two-gigabyte asset
    never lands in memory, and a single read would defeat that on the opening `bytes=0-` a
    player sends before it knows how big the file is.
    """
    remaining = end - start + 1
    with path.open("rb") as fh:
        fh.seek(start)
        while remaining > 0:
            block = fh.read(min(_CHUNK, remaining))
            if not block:
                return
            remaining -= len(block)
            yield block
