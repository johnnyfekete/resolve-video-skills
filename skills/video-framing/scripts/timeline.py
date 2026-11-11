"""Timeline reading for video-framing: on-camera items, visible jump cuts, runs.

Pure functions over resolve-bridge plan dicts (rvs.plan/1).
"""
from __future__ import annotations

import re

_PREFIX_RE = re.compile(r"^(?:S\d+_)+")


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


def on_camera(plan: dict) -> list[dict]:
    """Every `both` video item, plus video-only items whose sound is found.

    A video-only item counts when an overlapping audio or `both` item has a
    matching shot prefix (token sets intersect) or the same path.
    """
    items = plan.get("items", [])
    sound = [i for i in items if i.get("media") in ("audio", "both")]
    out = []
    for it in items:
        media = it.get("media")
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


def visible_cuts(plan: dict, cam_items: list[dict],
                 setup_of: dict[str, str]) -> list[tuple[str, str]]:
    """Pairs (key_a, key_b) of adjacent same-setup items with a visible cut.

    The cut is hidden when a b-roll item (any video item that is not
    on-camera) on a higher track than b covers frame b.start or b.start - 1.
    Only those two frames matter; partial coverage elsewhere is ignored.
    """
    cam_ids = {id(i) for i in cam_items}
    broll = [i for i in plan.get("items", [])
             if i.get("media") in ("video", "both") and i.get("track") is not None
             and id(i) not in cam_ids]
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
            covered = any(
                r["track"] > b["track"]
                and any(r["start"] <= f < r["end"] for f in (b["start"], b["start"] - 1))
                for r in broll)
            if not covered:
                cuts.append((ka, kb))
    return cuts


def runs(cam_items: list[dict], cuts: list[tuple[str, str]]) -> list[list[str]]:
    """Maximal chains of item keys joined by visible cuts, in timeline order."""
    nxt: dict[str, list[str]] = {}
    has_prev = set()
    for a, b in cuts:
        nxt.setdefault(a, []).append(b)
        has_prev.add(b)
    out = []
    for it in cam_items:
        k = item_key(it)
        if k in has_prev:
            continue
        chain = [k]
        while nxt.get(chain[-1]):
            chain.append(nxt[chain[-1]][0])
        out.append(chain)
    return out
