"""Assets are content-addressed and immutable (invariant 3)."""

from __future__ import annotations

import hashlib
from enum import StrEnum
from pathlib import Path


class AssetKind(StrEnum):
    IMAGE = "image"
    CLIP = "clip"
    AUDIO = "audio"
    VIDEO = "video"
    TEXT = "text"


#: Extension per kind, used to build the storage key.
_EXT: dict[AssetKind, str] = {
    AssetKind.IMAGE: "png",
    AssetKind.CLIP: "mp4",
    AssetKind.AUDIO: "wav",
    AssetKind.VIDEO: "mp4",
    AssetKind.TEXT: "txt",
}


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_file(path: Path, *, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def storage_key(sha256: str, kind: AssetKind) -> str:
    """Fan out over the first two bytes so no single prefix accumulates every object."""
    if len(sha256) != 64:
        raise ValueError(f"expected a sha256 hex digest, got {sha256!r}")
    return f"{kind.value}/{sha256[:2]}/{sha256[2:4]}/{sha256}.{_EXT[kind]}"
