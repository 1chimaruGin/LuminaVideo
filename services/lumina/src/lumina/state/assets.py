"""Asset store.

Assets are content-addressed and immutable (invariant 3): the sha256 of the bytes *is* the
identity. Writing the same bytes twice is one row and one object, which is what makes
regeneration a cache hit rather than a second charge.

Two backends behind one interface. Local disk is the development default so that a job can
be run end to end with no infrastructure at all; R2 is the same S3 API in production.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lumina.config import get_settings
from lumina.db.models import Asset
from lumina.domain.assets import AssetKind, content_hash, hash_file, storage_key


class Blobs(Protocol):
    """Where bytes live. Deliberately tiny — anything richer leaks storage semantics upward.

    Backends are synchronous on purpose: disk and S3 are both blocking, and pretending
    otherwise just hides where the blocking happens. `AssetStore` is the async boundary and
    hands this work to a thread, so the `light` pool's event loop is never stalled by a
    write — that loop's whole value is holding thousands of in-flight provider calls.
    """

    def put(self, key: str, data: bytes) -> str: ...
    def put_file(self, key: str, path: Path) -> str: ...
    def get(self, key: str) -> bytes: ...
    def url(self, key: str) -> str: ...


class LocalBlobs:
    """Filesystem backend. The dev default: no MinIO, no credentials, no network."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path(get_settings().local_media_root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = self.root / key
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def put(self, key: str, data: bytes) -> str:
        self._path(key).write_bytes(data)
        return key

    def put_file(self, key: str, path: Path) -> str:
        shutil.copyfile(path, self._path(key))
        return key

    def get(self, key: str) -> bytes:
        return (self.root / key).read_bytes()

    def url(self, key: str) -> str:
        return f"/media/{key}"


class S3Blobs:
    """R2 (or any S3) backend. Same interface, so nothing above this notices the swap."""

    def __init__(self) -> None:
        import boto3

        s = get_settings()
        self._bucket = s.s3_bucket
        self._public = s.s3_public_base_url
        self._client = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint_url,
            aws_access_key_id=s.s3_access_key_id,
            aws_secret_access_key=s.s3_secret_access_key,
            region_name=s.s3_region,
        )

    def put(self, key: str, data: bytes) -> str:
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data)
        return key

    def put_file(self, key: str, path: Path) -> str:
        self._client.upload_file(str(path), self._bucket, key)
        return key

    def get(self, key: str) -> bytes:
        obj = self._client.get_object(Bucket=self._bucket, Key=key)
        return bytes(obj["Body"].read())

    def url(self, key: str) -> str:
        return f"{self._public}/{key}"


def blobs() -> Blobs:
    return LocalBlobs() if get_settings().storage == "local" else S3Blobs()


class AssetStore:
    """Content-addressed writes over a `Blobs` backend and the `assets` table."""

    def __init__(self, session: AsyncSession, store: Blobs | None = None) -> None:
        self._session = session
        self._blobs = store or blobs()

    async def put_bytes(
        self, data: bytes, *, kind: AssetKind, mime: str, **meta: int | None
    ) -> Asset:
        sha = await asyncio.to_thread(content_hash, data)
        return await self._record(sha, len(data), kind, mime, meta, data=data)

    async def put_path(
        self, path: Path, *, kind: AssetKind, mime: str, **meta: int | None
    ) -> Asset:
        # Hashing a large upload reads the whole file; never do that on the event loop.
        sha, size = await asyncio.to_thread(lambda: (hash_file(path), path.stat().st_size))
        return await self._record(sha, size, kind, mime, meta, path=path)

    async def _record(
        self,
        sha: str,
        size: int,
        kind: AssetKind,
        mime: str,
        meta: dict[str, int | None],
        *,
        data: bytes | None = None,
        path: Path | None = None,
    ) -> Asset:
        # Same bytes, same kind: reuse the row and skip the upload. This is the dedup that
        # DEVELOPMENT.md calls "a meaningful fraction of COGS", since users regenerate a lot.
        existing = await self._session.scalar(
            select(Asset).where(Asset.sha256 == sha, Asset.kind == kind.value)
        )
        if existing is not None:
            return existing

        key = storage_key(sha, kind)
        if data is not None:
            await asyncio.to_thread(self._blobs.put, key, data)
        elif path is not None:
            await asyncio.to_thread(self._blobs.put_file, key, path)

        asset = Asset(
            sha256=sha,
            kind=kind.value,
            storage_key=key,
            bytes=size,
            mime=mime,
            duration_ms=meta.get("duration_ms"),
            width=meta.get("width"),
            height=meta.get("height"),
            provenance={},
        )
        self._session.add(asset)
        await self._session.flush()
        return asset

    async def read(self, asset_id: uuid.UUID) -> bytes:
        """The bytes behind an asset. Used by composition, which needs the real frames."""
        asset = await self._session.get(Asset, asset_id)
        if asset is None:
            raise KeyError(asset_id)
        return await asyncio.to_thread(self._blobs.get, asset.storage_key)

    def url(self, asset: Asset) -> str:
        return self._blobs.url(asset.storage_key)
