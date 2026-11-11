"""Camera setups for video-framing: group on-camera items that share a camera position.

`signature` reads a sampled frame through ffmpeg (I/O). `group_setups` is pure: items are grouped by
source path first, then path groups whose frames look alike are merged.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from string import ascii_uppercase

from timeline import item_key

SIG_W, SIG_H = 16, 9


def signature(frame_path) -> list[int]:
    """A 16x9 grayscale thumbnail of a frame image, as 144 ints 0..255 (row-major)."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is not installed")
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(Path(frame_path)),
                        "-vf", f"scale={SIG_W}:{SIG_H}:flags=area,format=gray",
                        "-frames:v", "1", "-f", "rawvideo", "-"], capture_output=True)
    data = r.stdout
    if r.returncode != 0 or len(data) != SIG_W * SIG_H:
        raise RuntimeError(f"ffmpeg could not read {frame_path}:\n{r.stderr.decode(errors='replace').strip()}")
    return list(data)


def _setup_id(n: int) -> str:
    """A, B, ..., Z, AA, AB, ..."""
    s = ""
    n += 1
    while n:
        n, rem = divmod(n - 1, 26)
        s = ascii_uppercase[rem] + s
    return s


def _mean(sigs: list[list[int]]) -> list[float] | None:
    if not sigs:
        return None
    return [sum(col) / len(sigs) for col in zip(*sigs)]


def _diff(a: list[float], b: list[float]) -> float:
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def group_setups(cam_items: list[dict], signatures: dict[str, list[int]],
                 threshold: float = 12.0) -> dict[str, list[str]]:
    """Setup id -> item keys, both in timeline order.

    Items with the same source path always share a setup. A path group joins the earliest setup
    whose mean signature differs from its own by less than `threshold` (mean absolute difference,
    0..255). Groups without any signature stay on their own.
    """
    ordered = sorted(cam_items, key=lambda d: (d["start"], d["track"]))
    groups: list[list[str]] = []
    by_path: dict[str, list[str]] = {}
    for it in ordered:
        k = item_key(it)
        p = it.get("path") or k
        if p not in by_path:
            by_path[p] = []
            groups.append(by_path[p])
        by_path[p].append(k)

    setups: list[tuple[list[str], list[list[int]]]] = []
    for keys in groups:
        sigs = [signatures[k] for k in keys if signatures.get(k)]
        mean = _mean(sigs)
        for members, member_sigs in setups:
            other = _mean(member_sigs)
            if mean is not None and other is not None and _diff(mean, other) < threshold:
                members.extend(keys)
                member_sigs.extend(sigs)
                break
        else:
            setups.append((list(keys), list(sigs)))

    order = {item_key(it): i for i, it in enumerate(ordered)}
    return {_setup_id(i): sorted(members, key=order.__getitem__)
            for i, (members, _) in enumerate(setups)}
