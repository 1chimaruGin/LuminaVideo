"""Fetch the SeamlessM4T weights in parallel chunks, resuming what is already there.

One connection to the mirror sustains about 0.6 MB/s; eight sustain about 4.5. The limit is
per connection, not per client, so the file is cut into ranges and each range is fetched on
its own — turning four hours into forty minutes. Every chunk is a file on disk, so an
interrupted run resumes at chunk granularity rather than starting again.
"""

from __future__ import annotations

import concurrent.futures as cf
import subprocess
import sys
from pathlib import Path

REPO = "AI-ModelScope/seamless-m4t-v2-large"
BASE = f"https://www.modelscope.cn/api/v1/models/{REPO}/repo?Revision=master&FilePath="
CHUNK = 64 * 1024 * 1024
WORKERS = 8

BIG = ("model-00001-of-00002.safetensors", "model-00002-of-00002.safetensors")


def true_size(url: str) -> int:
    """Ask the server how big the file is.

    Not a constant in this file. A hardcoded size taken from a listing was 53,728 bytes too
    large for one of these, so a download that had in fact finished looked permanently short
    and retried forty times against a target that did not exist. A one-byte ranged request
    answers it exactly, from the host that will serve the rest.
    """
    out = subprocess.run(
        ["curl", "-sL", "-m", "30", "-r", "0-0", "-D", "-", "-o", "/dev/null", url],
        capture_output=True,
        text=True,
    ).stdout
    for line in out.splitlines():
        if line.lower().startswith("content-range:"):
            return int(line.split("/")[-1].strip())
    raise RuntimeError(f"no size for {url}")


def grab(url: str, out: Path, start: int, end: int) -> int:
    want = end - start + 1
    for _ in range(40):
        if out.exists() and out.stat().st_size == want:
            return want
        subprocess.run(
            [
                "curl",
                "-fsL",
                "--speed-time",
                "30",
                "--speed-limit",
                "1024",
                "-r",
                f"{start}-{end}",
                "-o",
                str(out),
                url,
            ],
            capture_output=True,
        )
    return out.stat().st_size if out.exists() else 0


def main() -> int:
    dest = Path(sys.argv[1])
    parts = dest / ".parts"
    parts.mkdir(parents=True, exist_ok=True)

    for name in BIG:
        url = BASE + name
        size = true_size(url)
        final = dest / name
        if final.exists() and final.stat().st_size == size:
            print(f"ok   {name}", flush=True)
            continue

        jobs = [(i, o, min(o + CHUNK - 1, size - 1)) for i, o in enumerate(range(0, size, CHUNK))]
        print(f"get  {name}: {len(jobs)} chunks", flush=True)

        with cf.ThreadPoolExecutor(WORKERS) as pool:
            futures = {
                pool.submit(grab, url, parts / f"{name}.{i:04d}", a, b): (i, b - a + 1)
                for i, a, b in jobs
            }
            for done, fut in enumerate(cf.as_completed(futures), 1):
                i, want = futures[fut]
                got = fut.result()
                if got != want:
                    print(f"  chunk {i} short: {got}/{want}", flush=True)
                if done % 10 == 0:
                    print(f"  {done}/{len(jobs)} chunks", flush=True)

        with final.open("wb") as out:
            for i, _, _ in jobs:
                out.write((parts / f"{name}.{i:04d}").read_bytes())
        if final.stat().st_size != size:
            print(f"FAIL {name}: {final.stat().st_size} != {size}", flush=True)
            return 1
        for i, _, _ in jobs:
            (parts / f"{name}.{i:04d}").unlink(missing_ok=True)
        print(f"ok   {name}", flush=True)

    print("DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
