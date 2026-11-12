"""Push-ins as Inspector keyframes.

Resolve's Scripting API cannot create keyframes on a timeline item (SetProperty sets one value,
and on a keyframed item it deletes the keyframes). So `apply` sets each push-in item to its start
frame and writes a to-do (keyframes_todo.json); the keyframes are set in the Inspector, by the
agent through computer use or by a person (SKILL.md has the procedure). `check` reads them back
through the bridge's FCPXML-based "keyframes" field of a plan and says which push-ins are done.

FCPXML positions are percent of the timeline height; Pan/Tilt are timeline pixels.
"""
from __future__ import annotations

import json
from pathlib import Path

TODO_NAME = "keyframes_todo.json"
ZOOM_TOL = 0.002
PX_TOL = 0.6
FRAME_TOL = 1


def _tc(sec: float) -> str:
    return f"{int(sec // 60)}:{sec % 60:05.2f}"


def todo_from_solution(solution: dict) -> list[dict]:
    fps = float((solution.get("timeline") or {}).get("fps") or 24.0)
    out = []
    for p in solution.get("pushes", []):
        e = solution["items"][p["key"]]
        f, t = p["from_props"], p["to_props"]
        out.append({
            "key": p["key"], "clip": p["clip"], "track": e["track"], "item_start": e["start"],
            "start_frame": p["start_frame"], "end_frame": p["end_frame"],
            "start_s": p["start_s"], "end_s": p["end_s"],
            "from": {k: f[k] for k in ("ZoomX", "Pan", "Tilt")},
            "to": {k: t[k] for k in ("ZoomX", "Pan", "Tilt")},
            "where": f"{p['clip']} at {_tc((e['start'] + p['start_frame']) / fps)}",
        })
    return out


def issue_text(p: dict) -> str:
    f, t = p["from"], p["to"]
    return (f"Push-in to keyframe in the Inspector (Zoom and Position, Ease In and Out): "
            f"at {_tc(p['start_s'])} Zoom {f['ZoomX']:.4f}, Position {f['Pan']:.2f} / {f['Tilt']:.2f}; "
            f"at {_tc(p['end_s'])} Zoom {t['ZoomX']:.4f}, Position {t['Pan']:.2f} / {t['Tilt']:.2f}. "
            f"The clip holds the start frame until then.")


def save(path, todo: list[dict]) -> None:
    Path(path).write_text(json.dumps(todo, indent=1) + "\n")


def load(path) -> list[dict]:
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else []


def _first_last(keys, idx: int, scale: float = 1.0):
    vals = [float(k[1].split()[idx]) * scale for k in (keys[0], keys[-1])]
    return (keys[0][0], vals[0]), (keys[-1][0], vals[1])


def problems(plan: dict, p: dict) -> list[str]:
    """What is missing or off for one to-do push-in on this plan (empty: done)."""
    h = float(plan["timeline"]["height"]) / 100.0
    d = next((d for d in plan.get("items", []) if d.get("track") == p["track"]
              and d["start"] == p["item_start"]), None)
    if d is None or d.get("clip") != p["clip"]:
        return [f"{p['clip']} is no longer at V{p['track']} frame {p['item_start']}"]
    kf = d.get("keyframes") or {}
    out = []
    checks = (("Zoom", "transform.scale", 0, 1.0, "ZoomX", ZOOM_TOL),
              ("Position X", "transform.position", 0, h, "Pan", PX_TOL),
              ("Position Y", "transform.position", 1, h, "Tilt", PX_TOL))
    for label, param, idx, scale, prop, tol in checks:
        keys = kf.get(param) or []
        if len(keys) < 2:
            out.append(f"{label}: needs keyframes at both ends")
            continue
        (f0, v0), (f1, v1) = _first_last(keys, idx, scale)
        if abs(f0 - p["start_frame"]) > FRAME_TOL or abs(f1 - p["end_frame"]) > FRAME_TOL:
            out.append(f"{label}: keyframes at frames {f0}-{f1} of the clip, want {p['start_frame']}-{p['end_frame']}")
        if abs(v0 - p["from"][prop]) > tol or abs(v1 - p["to"][prop]) > tol:
            out.append(f"{label}: {v0:.4f} -> {v1:.4f}, want {p['from'][prop]:.4f} -> {p['to'][prop]:.4f}")
    return out
