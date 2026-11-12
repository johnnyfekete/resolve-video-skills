"""Framing decisions, the applied record and hand-framed protection.

`decisions.json` is written by the agent after looking at setup stills.
`applied.json` records what framing set per item, so a re-run can tell its own
values from the user's hand edits. Pure helpers plus small JSON I/O.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

from timeline import item_key

LABELS = ("plain", "dynamic")
FRAMING_PROPS = ["ZoomX", "ZoomY", "Pan", "Tilt", "CropLeft", "CropRight", "CropTop", "CropBottom",
                 "RotationAngle"]
_DEFAULT = {"ZoomX": 1.0, "ZoomY": 1.0, "Pan": 0.0, "Tilt": 0.0, "CropLeft": 0.0, "CropRight": 0.0,
            "CropTop": 0.0, "CropBottom": 0.0, "RotationAngle": 0.0}
TOL = 1e-4


def _empty_setup(items) -> dict:
    return {"items": list(items), "keep_out": [], "keep_in": [], "label": None, "reason": ""}


def template(setups: dict[str, list[str]]) -> dict:
    return {"setups": {sid: _empty_setup(items) for sid, items in setups.items()},
            "reclassify": {}, "push_ins": [], "issues": []}


def _check_box(where: str, box) -> None:
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        raise ValueError(f"{where}: box must be [left, top, right, bottom], got {box!r}")
    l, t, r, b = box
    if not all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in box):
        raise ValueError(f"{where}: box {list(box)} has a value outside 0..1")
    if not (l < r and t < b):
        raise ValueError(f"{where}: box {list(box)} needs left<right and top<bottom")


def validate(data: dict) -> dict:
    reclass = data.get("reclassify", {})
    for sid, s in data.get("setups", {}).items():
        label = s.get("label")
        items = s.get("items") or []
        if label is None and items and all(reclass.get(k) == "broll" for k in items):
            continue  # nothing left to frame: no label needed
        if label is None:
            raise ValueError(f"setup {sid}: label is missing (plain or dynamic)")
        if label not in LABELS:
            raise ValueError(f"setup {sid}: label {label!r} must be plain or dynamic")
        if "bottom_hands" in s and s["bottom_hands"] != "gesture":
            raise ValueError(f"setup {sid}: bottom_hands {s['bottom_hands']!r} must be \"gesture\" "
                             "or left out")
        for field in ("keep_out", "keep_in"):
            for n, box in enumerate(s.get(field, [])):
                _check_box(f"setup {sid} {field}[{n}]", box)
    for key, value in data.get("reclassify", {}).items():
        if value not in ("broll", "on_camera"):
            raise ValueError(f"reclassify {key}: {value!r} must be broll or on_camera")
    for n, p in enumerate(data.get("push_ins", [])):
        if p["end_s"] <= p["start_s"]:
            raise ValueError(f"push_ins[{n}]: end_s must be greater than start_s")
    issues = data.get("issues", [])
    if not isinstance(issues, list):
        raise ValueError("issues must be a list of {\"text\", \"where\"}")
    for n, i in enumerate(issues):
        if not isinstance(i, dict) or not all(isinstance(i.get(f), str) and i[f].strip()
                                              for f in ("text", "where")):
            raise ValueError(f"issues[{n}]: needs non-empty strings \"text\" and \"where\" "
                             f"(clip name and timecode), got {i!r}")
    return data


def load(path) -> dict:
    data = json.loads(Path(path).read_text())
    data.setdefault("setups", {})
    data.setdefault("reclassify", {})
    data.setdefault("push_ins", [])
    data.setdefault("issues", [])
    return validate(data)


def _write(path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def save(path, data: dict) -> None:
    _write(path, data)


def merge(old: dict, new_setups: dict[str, list[str]]) -> dict:
    """Keep decisions of setups whose item set is unchanged; new ones start empty.

    Items that moved to another setup are reported as a new item set, so that
    setup is re-decided, but reclassify and push-ins always carry over.
    """
    out = template(new_setups)
    for sid, items in new_setups.items():
        prev = old.get("setups", {}).get(sid)
        if prev is not None and set(prev.get("items", [])) == set(items):
            out["setups"][sid] = {**_empty_setup(items), **prev, "items": list(items)}
    out["reclassify"] = dict(old.get("reclassify", {}))
    out["push_ins"] = list(old.get("push_ins", []))
    out["issues"] = list(old.get("issues", []))
    return out


_WORD = re.compile(r"[\w']+")


def _norm(text: str) -> list[str]:
    """Lower-case words without punctuation (curly apostrophes read as straight ones)."""
    return _WORD.findall(text.lower().replace("\u2019", "'"))


def push_still_matches(text: str, words, start_s: float, end_s: float, slack: float = 0.5,
                       share: float = 0.6) -> bool:
    """Whether a push-in's line is still said in its range (timeline seconds).

    Push-ins are recorded in timeline seconds, so a timeline change after they were picked moves
    the line away from its range. `words` are (start_s, end_s, word) in timeline seconds. The line
    still matches when at least `share` of its words are heard within the range +- `slack`, in any
    order (a repeated word must be heard as often). A line with no words always matches.
    """
    want = _norm(text)
    if not want:
        return True
    heard = Counter(w for a, b, word in words if b > start_s - slack and a < end_s + slack
                    for w in _norm(word))
    found = sum(min(n, heard[w]) for w, n in Counter(want).items())
    return found >= share * len(want) - 1e-9


def match_key(item: dict) -> str:
    return f"{item['path']}|{item['src_start']}|{item['end'] - item['start']}"


def _framing(item: dict) -> dict:
    props = item.get("props") or {}
    return {k: props.get(k, _DEFAULT[k]) for k in FRAMING_PROPS}


def _same(a: dict, b: dict) -> bool:
    return all(math.isclose(a[k], b[k], rel_tol=TOL, abs_tol=TOL) for k in FRAMING_PROPS)


def hand_framed(cam_items, applied: dict | None) -> set[str]:
    """`item_key`s of on-camera items framing must leave alone. An item with Inspector keyframes
    is always left alone, framing's own keyframed push-ins too: setting a property by script
    deletes an item's keyframes."""
    out = set()
    for it in cam_items:
        if it.get("keyframes"):
            out.add(item_key(it))
            continue
        cur, comps = _framing(it), int(it.get("fusion_comps") or 0)
        entry = (applied or {}).get(match_key(it))
        if entry is None:
            mine = _same(cur, _DEFAULT) and comps == 0
        else:
            mine = _same(cur, {**_DEFAULT, **entry.get("props", {})}) \
                and comps == int(entry.get("fusion_comps") or 0)
        if not mine:
            out.add(item_key(it))
    return out


def save_applied(path, entries: dict) -> None:
    _write(path, entries)


def load_applied(path) -> dict | None:
    """The applied record. A leftover `<name>.pending` newer than it (an apply that saved and
    recorded its step, then failed to rename the record) is promoted first, with a warning."""
    p = Path(path)
    pending = p.with_name(p.name + ".pending")
    if pending.exists() and (not p.exists() or pending.stat().st_mtime > p.stat().st_mtime):
        print(f"warning: {pending.name} is newer than {p.name}: a framing apply saved its step but "
              f"did not finish the record; using it as {p.name}.", file=sys.stderr)
        os.replace(pending, p)
    return json.loads(p.read_text()) if p.exists() else None
