"""Fetch the caption faces the registered language packs need.

Driven by the registry, not by a list here: a pack declares `typography.bundled` and names its
family, `execution.compose.fonts.CATALOGUE` says where that family comes from, and this walks
the two. Adding a language therefore cannot leave a font requirement behind in a script nobody
remembered to update — and if a pack asks for a family the catalogue does not know, this fails
saying so rather than silently fetching nothing.

Not vendored into git: these are stable, versioned upstream releases, and the CJK subsets are
several megabytes each.
"""

from __future__ import annotations

import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from lumina.execution.compose import fonts

#: The CJK subsets are 4-8 MB each and raw.githubusercontent drops large transfers often
#: enough that a single attempt fails most runs.
_ATTEMPTS = 4


def _download(url: str, spool: Path) -> int | None:
    """Stream to `spool`, retrying. Returns the byte count, or None if every attempt failed.

    Streamed rather than `read()` into memory because these are multi-megabyte files, and
    retried because a dropped connection part-way through is the common case rather than the
    exceptional one. `OSError` is the catch: `RemoteDisconnected` is a `ConnectionResetError`,
    not a `URLError`, so catching the latter missed the failure that actually happens.
    """
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(url, timeout=120) as response, spool.open("wb") as out:
                shutil.copyfileobj(response, out, length=1 << 20)
            return spool.stat().st_size
        except (OSError, urllib.error.URLError) as exc:
            spool.unlink(missing_ok=True)
            if attempt == _ATTEMPTS:
                print(f"\n  FAILED after {_ATTEMPTS} tries: {exc}", file=sys.stderr)
                return None
            print(f" retry {attempt}", end="", flush=True)
            time.sleep(attempt * 2)
    return None


def main() -> int:
    fonts.DIR.mkdir(parents=True, exist_ok=True)
    wanted = fonts.required()
    if not wanted:
        print("No pack needs a bundled face.")
        return 0

    print(f"{len(wanted)} language(s) need a face: {', '.join(sorted(wanted))}\n")
    failed: list[str] = []

    # Several packs can share a face, and two languages sharing one should not fetch it twice.
    seen: set[str] = set()
    for code, face in sorted(wanted.items()):
        for name, url in face.files():
            if name in seen:
                continue
            seen.add(name)
            target = fonts.DIR / name
            if target.is_file() and target.stat().st_size > 0:
                print(f"  have   {name}")
                continue
            print(f"  fetch  {name} ({code})", end="", flush=True)
            # Spooled, then moved: a half-downloaded font is a file that exists, passes the
            # `installed` check, and renders as boxes.
            spool = target.with_suffix(target.suffix + ".part")
            size = _download(url, spool)
            if size is None:
                failed.append(name)
                continue
            spool.replace(target)
            print(f"  {size // 1024} KB")

    print(f"\nFonts in {fonts.DIR}:")
    for path in sorted(fonts.DIR.glob("*")):
        print(f"  {path.name:<32} {path.stat().st_size // 1024:>7} KB")

    missing = [code for code in wanted if not fonts.installed(code)]
    if missing or failed:
        print(f"\nStill missing: {', '.join(sorted(missing)) or 'none'}", file=sys.stderr)
        return 1
    print("\nEvery registered pack has its face.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
