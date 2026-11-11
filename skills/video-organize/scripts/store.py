"""The takes store: stable take ids, shot letters and picks.

`takes.json` holds the current takes, the user's picks, the shot letter of every
file per scene and open questions. Ids are issued once (`S02A-T03`) and survive
re-runs: a new take inherits the id of an old take in the same file that it
overlaps by at least half. Takes without a scene (unmatched) get no id.
"""
from __future__ import annotations

import json
import re
import string
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from storyboard import Scene
from takes import Take

SCHEMA = "rvs.takes/1"
FILENAME = "takes.json"

INHERIT_OVERLAP = 0.5   # share of the longer take that must overlap to keep an id
NEW_SHOT_DIFF = 0.35    # middle-frame difference at or above this is a new shot
QUESTION_DIFF = 0.2     # ...between this and NEW_SHOT_DIFF the user is asked

_ID = re.compile(r"^(S\w+?)([A-Z])-T(\d+)$")


def empty() -> dict:
    return {"schema": SCHEMA, "takes": [], "picks": {}, "shots": {}, "questions": [], "issued": {}}


def load(dir: Path) -> dict:
    path = Path(dir) / FILENAME
    if not path.exists():
        return empty()
    data = json.loads(path.read_text())
    for key, value in empty().items():
        data.setdefault(key, value)
    return data


def save(dir: Path, data: dict) -> None:
    dir = Path(dir)
    dir.mkdir(parents=True, exist_ok=True)
    (dir / FILENAME).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


# --- filming order -----------------------------------------------------------

_creation_cache: dict[str, str] = {}


def _creation_time(file: str) -> str:
    """ISO creation_time from ffprobe, '' when unknown (sorts first)."""
    if file not in _creation_cache:
        value = ""
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format_tags=creation_time",
                 "-of", "default=nw=1:nk=1", file],
                capture_output=True, text=True, timeout=30)
            value = out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
        _creation_cache[file] = value
    return _creation_cache[file]


def filming_order(file: str) -> tuple:
    """Sort key for a file: creation time, then the camera number in its name."""
    numbers = re.findall(r"\d+", Path(file).stem)
    return (_creation_time(file), int(numbers[-1]) if numbers else 0, file)


# --- ids -----------------------------------------------------------------------

def _overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    """Overlap as a share of the longer of the two ranges."""
    shared = min(a_end, b_end) - max(a_start, b_start)
    longest = max(a_end - a_start, b_end - b_start)
    return shared / longest if shared > 0 and longest > 0 else 0.0


def assign_ids(old: list[dict], new: list[Take], shots: dict,
               order_key: Callable[[str], tuple] = filming_order,
               issued: dict | None = None) -> list[dict]:
    """Return `new` as dicts with an `id`, reusing ids from `old`.

    `issued` is the store's high-water mark per scene+shot ({"S02A": 3}). It is
    read as the starting counters and updated in place with every number issued,
    so the caller saves it (`data["issued"]`) and numbers never come back after
    their take vanishes. An id is inherited only by a take in the same scene and
    file overlapping the old one.

    Unmatched takes (no scene) are kept in the output with `id` None and take no
    part in numbering. Old takes that no longer exist are dropped, but their
    numbers are never issued again.
    """
    counters: dict[str, int] = dict(issued or {})  # "S02A" -> highest number issued so far
    for t in old:
        m = _ID.match(t.get("id") or "")
        if m:
            key = m.group(1) + m.group(2)
            counters[key] = max(counters.get(key, 0), int(m.group(3)))

    inherited: dict[int, str] = {}
    claimed: set[str] = set()
    for i, t in enumerate(new):
        if t.scene is None:
            continue
        best, best_share = None, 0.0
        for o in old:
            if not o.get("id") or o["id"] in claimed or o["file"] != t.file or o.get("scene") != t.scene:
                continue
            share = _overlap(t.start, t.end, o["start"], o["end"])
            if share >= INHERIT_OVERLAP and share > best_share:
                best, best_share = o["id"], share
        if best:
            inherited[i] = best
            claimed.add(best)

    ids: dict[int, str | None] = {i: inherited.get(i) for i in range(len(new))}
    fresh = sorted((i for i, t in enumerate(new) if t.scene is not None and i not in inherited),
                   key=lambda i: (order_key(new[i].file), new[i].start))
    for i in fresh:
        t = new[i]
        letter = shots.get(t.scene, {}).get(t.file, "A")
        key = f"{t.scene}{letter}"
        counters[key] = counters.get(key, 0) + 1
        ids[i] = f"{key}-T{counters[key]:02d}"

    if issued is not None:
        issued.update(counters)
    result = []
    for i, t in enumerate(new):
        d = asdict(t)
        d["id"] = ids[i]
        result.append(d)
    return result


# --- shot letters --------------------------------------------------------------

def frame_diff(file_a: str, t_a: float, file_b: str, t_b: float) -> float:
    """Mean absolute difference of two 64x36 grayscale frames, 0 (same) to 1."""
    def grab(file: str, t: float) -> bytes:
        out = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{max(t, 0):.3f}", "-i", file, "-frames:v", "1",
             "-vf", "scale=64:36", "-pix_fmt", "gray", "-f", "rawvideo", "-"],
            capture_output=True, timeout=60)
        if out.returncode != 0 or len(out.stdout) < 64 * 36:
            raise RuntimeError(f"could not read a frame from {file} at {t:.2f}s")
        return out.stdout[:64 * 36]

    a, b = grab(file_a, t_a), grab(file_b, t_b)
    return sum(abs(x - y) for x, y in zip(a, b)) / (len(a) * 255)


def shot_letters(scene: Scene, takes: list[Take], frame_diff: Callable[[str, float, str, float], float],
                 setup_letters: dict, prior: dict | None = None) -> tuple[dict, list[dict]]:
    """Letter every file of a scene's takes: {file: letter} and questions.

    A letter means a camera setup. Files in `prior` ({file: letter}, from the
    store) keep their letter. Each other file, taken in order of first appearance,
    is compared at the middle of its first take with one representative file per
    existing letter; the lowest diff decides: below 0.2 that letter, 0.2 to 0.35
    that letter plus a question {scene, files: [representative, file], diff},
    0.35 or more the next unused letter. With no letters yet, a scene whose
    `setup` was seen before starts on that setup's letter, else A.
    `setup_letters` ({setup: letter}) is updated in place.
    """
    first: dict[str, Take] = {}
    for t in takes:
        if t.file not in first or t.start < first[t.file].start:
            first[t.file] = t
    letters: dict[str, str] = {f: l for f, l in (prior or {}).items() if f in first}
    reps: dict[str, str] = {}  # letter -> representative file
    for f in first:
        if f in letters:
            reps.setdefault(letters[f], f)

    def middle(f: str) -> float:
        return (first[f].start + first[f].end) / 2

    questions: list[dict] = []
    for f in first:
        if f in letters:
            continue
        if not reps:
            letter = setup_letters.get(scene.setup, "A") if scene.setup else "A"
        else:
            diff, letter, rep = min((frame_diff(r, middle(r), f, middle(f)), l, r) for l, r in reps.items())
            if diff >= NEW_SHOT_DIFF:
                used = set(reps)
                letter = next(c for c in string.ascii_uppercase if c not in used)
            elif diff >= QUESTION_DIFF:
                questions.append({"scene": scene.id, "files": [rep, f], "diff": round(diff, 3)})
        letters[f] = letter
        reps.setdefault(letter, f)
    if scene.setup and letters:
        setup_letters.setdefault(scene.setup, letters[next(iter(first))])
    return letters, questions


# --- picks ---------------------------------------------------------------------

def apply_pick(data: dict, take_id: str) -> dict:
    """Record the user's pick for the take's scene. Raises KeyError for an unknown id."""
    for t in data["takes"]:
        if t.get("id") == take_id:
            data["picks"][t["scene"]] = {"id": take_id, "file": t["file"],
                                         "start": t["start"], "end": t["end"], "by": "user"}
            return data
    raise KeyError(take_id)


def stale_picks(data: dict) -> list[dict]:
    """Picks whose id, file and range no longer match a take: {"scene", id, file, start, end, by}."""
    stale = []
    for scene, pick in data["picks"].items():
        alive = any(
            t.get("id") == pick["id"] and t["file"] == pick["file"]
            and _overlap(t["start"], t["end"], pick["start"], pick["end"]) >= INHERIT_OVERLAP
            for t in data["takes"])
        if not alive:
            stale.append({"scene": scene, **pick})
    return stale
