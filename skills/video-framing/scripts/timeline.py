"""Timeline reading for video-framing: on-camera items, visible jump cuts, runs.

Pure functions over resolve-bridge plan dicts (rvs.plan/1).
"""
from __future__ import annotations

import re

_PREFIX_RE = re.compile(r"^(?:S\d+_)+")
_BROLL_TRACK = re.compile(r"b[- ]?roll", re.IGNORECASE)   # "B-roll", "broll", "b roll"
_BROLL_FILE = re.compile(r"b-?roll", re.IGNORECASE)       # "broll", "b-roll"


def item_key(d: dict) -> str:
    """Stable key for a video item within one plan."""
    return f"V{d['track']}@{d['start']}"


def shot_prefix(clip: str) -> str | None:
    """Leading run of S<digits> tokens joined by '_', or None."""
    m = _PREFIX_RE.match(clip or "")
    if not m:
        return None
    return m.group(0).rstrip("_")


def _prefixes_match(a: str | None, b: str | None) -> bool:
    return bool(a and b and set(a.split("_")) & set(b.split("_")))


def _overlaps(a: dict, b: dict) -> bool:
    return a["start"] < b["end"] and b["start"] < a["end"]


def _broll_tracks(plan: dict) -> set[int]:
    """Video track indexes whose name says b-roll."""
    tracks = (plan.get("tracks") or {}).get("video") or []
    return {t["index"] for t in tracks if _BROLL_TRACK.search(t.get("name") or "")}


def _broll_file(it: dict) -> bool:
    names = [it.get("clip") or "", (it.get("path") or "").rsplit("/", 1)[-1]]
    return any(_BROLL_FILE.search(n) for n in names)


def on_camera(plan: dict) -> list[dict]:
    """Every `both` video item, plus video-only items whose sound is found.

    A video-only item counts when an overlapping audio or `both` item has a
    matching shot prefix (token sets intersect) or the same path. B-roll by name
    never counts: a file whose name contains "broll" or "b-roll", or a video-only
    item on a track whose name contains "b-roll", "broll" or "b roll".
    """
    items = plan.get("items", [])
    sound = [i for i in items if i.get("media") in ("audio", "both")]
    broll_tracks = _broll_tracks(plan)
    out = []
    for it in items:
        media = it.get("media")
        if media in ("video", "both") and _broll_file(it):
            continue
        if media == "video" and it.get("track") in broll_tracks:
            continue
        if media == "both":
            out.append(it)
        elif media == "video":
            pre = shot_prefix(it.get("clip", ""))
            for s in sound:
                if not _overlaps(it, s):
                    continue
                if (it.get("path") and it.get("path") == s.get("path")) or \
                        _prefixes_match(pre, shot_prefix(s.get("clip", ""))):
                    out.append(it)
                    break
    return sorted(out, key=lambda i: (i["start"], i["track"]))


def _top_at(items: list[dict], frame: int) -> dict | None:
    """The topmost enabled video item covering `frame` (what the viewer sees)."""
    best = None
    for it in items:
        if it["start"] <= frame < it["end"]:
            if best is None or (it["track"], it["start"]) > (best["track"], best["start"]):
                best = it
    return best


def _enabled_video(plan: dict) -> list[dict]:
    return [i for i in plan.get("items", [])
            if i.get("media") in ("video", "both") and i.get("track") is not None
            and i.get("enabled", True) is not False]


def hidden(plan: dict, cam_items: list[dict]) -> set[str]:
    """`item_key`s of items the viewer never sees: at no frame are they the top item (`_top_at`).

    The top item only changes where some item starts or ends, so checking the item's first frame
    and every start or end inside it covers every frame.
    """
    video = _enabled_video(plan)
    out = set()
    for it in cam_items:
        a, b = it["start"], it["end"]
        frames = {a} | {f for v in video for f in (v["start"], v["end"]) if a < f < b}
        if all(_top_at(video, f) is not it for f in frames):
            out.add(item_key(it))
    return out


def visible_cuts(plan: dict, cam_items: list[dict],
                 setup_of: dict[str, str]) -> list[tuple[str, str]]:
    """Pairs (key_a, key_b) of adjacent same-setup items with a visible cut.

    The viewer sees the topmost video item at each frame (all enabled video
    items count, on-camera or b-roll). A cut a->b (a.end == b.start) is
    visible iff the top item at b.start - 1 is a and the top at b.start is b.
    Only those two frames matter. Items whose key is missing from `setup_of`
    are skipped silently: they form no cuts.
    """
    video = _enabled_video(plan)
    by_start: dict[int, list[dict]] = {}
    for it in cam_items:
        by_start.setdefault(it["start"], []).append(it)

    cuts = []
    for a in cam_items:
        ka = item_key(a)
        for b in by_start.get(a["end"], []):
            kb = item_key(b)
            if ka not in setup_of or setup_of.get(ka) != setup_of.get(kb):
                continue
            if _top_at(video, b["start"] - 1) is a and _top_at(video, b["start"]) is b:
                cuts.append((ka, kb))
    return cuts


def runs(cam_items: list[dict], cuts: list[tuple[str, str]]) -> list[list[str]]:
    """Maximal chains of item keys joined by visible cuts, in timeline order.

    Each item appears in exactly one run. If cuts branch, the first cut (in
    the given order) for an item as predecessor or successor wins.
    """
    nxt: dict[str, str] = {}
    has_prev: set[str] = set()
    for a, b in cuts:
        if a in nxt or b in has_prev or a == b:
            continue
        nxt[a] = b
        has_prev.add(b)
    out = []
    seen: set[str] = set()
    for it in cam_items:
        k = item_key(it)
        if k in has_prev or k in seen:
            continue
        chain = [k]
        while chain[-1] in nxt and nxt[chain[-1]] not in seen and nxt[chain[-1]] not in chain:
            chain.append(nxt[chain[-1]])
        seen.update(chain)
        out.append(chain)
    return out
