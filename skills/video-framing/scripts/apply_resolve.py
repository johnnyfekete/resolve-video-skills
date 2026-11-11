"""Apply a framing solution to Resolve through resolve-bridge.

Forks the current timeline (`begin_step`), sets Zoom/Pan/Tilt on every solved item, adds the
push-in Fusion comps (recipe and call forms in references/resolve-notes.md), saves and records the
step (`finish_step`), then writes applied.json and turns the flags into checklist issues. On any
error the step is abandoned: the forked timeline stays in Resolve for inspection, the previous
version stays current.
"""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path

import decisions as dec
import rvs

PUSH_TOOL = "rvs_pushin"
PUSH_COMP = "rvs_pushin"   # framing names its own comps; old ones are renamed PUSH_COMP_old<n>
NEUTRAL_COMP = rvs.NEUTRAL_COMP  # a neutralised comp: pass-through, ignored by the bridge
SCALING_KEY = "timelineInputResMismatchBehavior"
SCALING_OK = "scaleToCrop"
SCALING_FIT = "scaleToFit"   # the same scale factor as scaleToCrop when a source has the timeline's aspect
ASPECT_TOL = 0.005           # "the timeline's aspect": within 0.5%
FRAMING_SET = ("ZoomX", "ZoomY", "Pan", "Tilt")
READBACK_TOL = 0.01


class ApplyError(RuntimeError):
    """Expected failure with a message for the user."""


def scaling_mode(timeline, project) -> str | None:
    v = timeline.GetSetting(SCALING_KEY) if timeline is not None else None
    if not v and project is not None:
        v = project.GetSetting(SCALING_KEY)
    return v or None


def scaling_problem(mode: str | None, sources: dict, tl_size) -> str | None:
    """Why the timeline's scaling mode does not fit the calibration, or None.

    'scaleToCrop' always fits. 'scaleToFit' gives the same scale factor when the source has the
    timeline's aspect (references/resolve-notes.md), so it fits when every framed source
    ({name: (width, height)}) is within ASPECT_TOL of the timeline aspect.
    """
    fix = ("(Timeline settings > Input scaling > Mismatched resolution files). Change it in Resolve and "
           "run apply again; framing does not change your timeline settings.")
    if mode == SCALING_OK:
        return None
    if mode == SCALING_FIT:
        tw, th = tl_size
        odd = [f"{name} ({w}x{h})" for name, (w, h) in sorted(sources.items())
               if abs((w / h) / (tw / th) - 1) > ASPECT_TOL]
        if not odd:
            return None
        return (f"the timeline scales mismatched-resolution sources with 'scaleToFit', which matches the "
                f"calibrated 'scale full frame with crop' only for sources with the timeline's aspect "
                f"({tw}x{th}). Another aspect: {', '.join(odd)}. Switch to 'Scale full frame with crop' "
                + fix)
    return (f"the timeline scales mismatched-resolution sources with '{mode or 'unknown'}'. video-framing "
            "is calibrated for 'scale full frame with crop' (and 'scale entire image to fit' when every "
            "framed source has the timeline's aspect) " + fix)


def check_scaling(timeline, project, sources: dict | None = None, tl_size=None) -> None:
    """Refuse unless the scaling mode fits the calibration for these framed sources."""
    problem = scaling_problem(scaling_mode(timeline, project), sources or {}, tl_size or (16, 9))
    if problem:
        raise ApplyError(problem)


def _index(tl) -> dict:
    t0 = tl.GetStartFrame()
    out = {}
    for t in range(1, (tl.GetTrackCount("video") or 0) + 1):
        for it in tl.GetItemListInTrack("video", t) or []:
            out[(t, it.GetStart() - t0)] = it
    return out


def _comp_names(item) -> list[str]:
    if not int(item.GetFusionCompCount() or 0):
        return []
    return list(item.GetFusionCompNameList() or [])


def _own_comps(item) -> list[str]:
    """Names of framing's own comps on the item: named PUSH_COMP* or NEUTRAL_COMP*, or holding the
    PUSH_TOOL. Every other comp is the user's and is never touched."""
    out = []
    for name in _comp_names(item):
        comp = item.GetFusionCompByName(name)
        if name.startswith((PUSH_COMP, NEUTRAL_COMP)) or (comp is not None and comp.FindTool(PUSH_TOOL) is not None):
            out.append(name)
    return out


def _neutralise(item, name: str) -> None:
    """Make one of framing's comps a pass-through named NEUTRAL_COMP: Resolve refuses to delete an
    item's only comp, and the bridge ignores comps with that name."""
    comp = item.GetFusionCompByName(name)
    tr = comp.FindTool(PUSH_TOOL)
    if tr is not None:
        tr.Delete()
    mi, mo = comp.FindTool("MediaIn1"), comp.FindTool("MediaOut1")
    if mi is None or mo is None:
        raise ApplyError(f"framing's comp '{name}' on {item.GetName()} has no MediaIn1/MediaOut1")
    mo.ConnectInput("Input", mi)
    if comp.FindTool(PUSH_TOOL) is not None:
        raise ApplyError(f"could not remove the push-in from comp '{name}' on {item.GetName()}")
    if name != NEUTRAL_COMP:
        _rename(item, name, NEUTRAL_COMP)


def _rename(item, old: str, new: str) -> None:
    if not item.RenameFusionCompByName(old, new):
        raise ApplyError(f"could not rename the Fusion comp '{old}' to '{new}' on {item.GetName()}")


def _with_tool(item) -> list[str]:
    out = []
    for name in _comp_names(item):
        comp = item.GetFusionCompByName(name)
        if comp is not None and comp.FindTool(PUSH_TOOL) is not None:
            out.append(name)
    return out


def _comp_length(comp) -> float | None:
    """Rendered length of a comp in source frames (COMPN_RenderStart..End, inclusive), if known."""
    try:
        a = comp.GetAttrs() or {}
        return float(a["COMPN_RenderEnd"]) - float(a["COMPN_RenderStart"]) + 1
    except Exception:
        return None


def _drop_comps(item, names: list[str]) -> None:
    """Delete framing's old comps; the item's only comp is neutralised instead."""
    for name in names:
        if int(item.GetFusionCompCount() or 0) > 1 and item.DeleteFusionCompByName(name):
            continue
        if int(item.GetFusionCompCount() or 0) > 1:
            raise ApplyError(f"could not remove framing's old comp '{name}' from {item.GetName()}")
        _neutralise(item, name)


def _set_props(item, key: str, props: dict) -> dict:
    """Set the framing properties and return them as Resolve reads them back."""
    ok = item.SetProperties(props)
    if not ok:
        ok = all(item.SetProperty(k, v) for k, v in props.items())
    got = {k: float(item.GetProperty(k)) for k in props}
    bad = {k: (props[k], got[k]) for k in props if abs(got[k] - props[k]) > READBACK_TOL}
    if not ok or bad:
        raise ApplyError(f"Resolve did not take the framing on {key} ({item.GetName()}): {bad or props}")
    return got


def _recorded(got: dict) -> dict:
    """What applied.json keeps: values a plan export would show (near-defaults read as the default)."""
    out = {}
    for k, v in got.items():
        d = dec._DEFAULT[k]
        out[k] = d if math.isclose(v, d, abs_tol=1e-3) else v
    return out


def add_pushin(item, push: dict):
    """A keyframed, eased Fusion Transform about the eyes, per references/resolve-notes.md."""
    comp = item.AddFusionComp()
    if comp is None:
        raise ApplyError(f"Resolve could not add a Fusion comp to {item.GetName()}")
    mi, mo = comp.FindTool("MediaIn1"), comp.FindTool("MediaOut1")
    if mi is None or mo is None:
        raise ApplyError(f"the new Fusion comp on {item.GetName()} has no MediaIn1/MediaOut1")
    tr = comp.AddTool("Transform", -32768, -32768)
    tr.SetAttrs({"TOOLS_Name": PUSH_TOOL})
    tr.ConnectInput("Input", mi)
    mo.ConnectInput("Input", tr)
    u, v = push["pivot"]
    tr.SetInput("Pivot", {1: u, 2: 1.0 - v})
    t0, t1, size = float(push["comp_start"]), float(push["comp_end"]), float(push["size"])
    tr.AddModifier("Size", "BezierSpline")
    tr.SetInput("Size", 1.0, t0)
    tr.SetInput("Size", size, t1)
    spline = tr.Size.GetConnectedOutput().GetTool()
    dt = (t1 - t0) / 3
    spline.SetKeyFrames({t0: {1: 1.0, "RH": {1: dt, 2: 0.0}},
                         t1: {1: size, "LH": {1: -dt, 2: 0.0}}}, True)
    if comp.FindTool(PUSH_TOOL) is None:
        raise ApplyError(f"the push-in tool did not stick on {item.GetName()}")
    return comp


def _timing_flag(key: str, e: dict, push: dict, comp, tl_fps: float) -> list[dict]:
    """A flag when the comp's length differs from the item's length by more than one timeline frame
    (variable frame rate sources): the push is kept, its timing may be off. Lengths are in source
    frames; the difference is converted to timeline frames, so rounding at a source frame rate
    above the timeline's is not flagged."""
    want, got = push.get("comp_len"), _comp_length(comp)
    if want is None or got is None:
        return []
    src_fps = float(push.get("src_fps") or tl_fps)
    if abs(got - float(want)) * tl_fps / src_fps <= 1:
        return []
    return [{"key": key, "setup": e.get("setup"),
             "where": f"{e['clip']} at {e.get('timecode', '')}".strip(),
             "text": f"push-in timing: the Fusion comp of {e['clip']} is {got:.1f} source frames long, the "
                     f"item {float(want):.1f}; the source may have a variable frame rate. Check that the "
                     f"push-in lands on its line"}]


def _applied(path: Path, solution: dict, entries: dict) -> dict:
    """The new applied record: old entries of items still on camera (hand-framed ones included),
    updated with this run's entries. Entries of items no longer on camera are pruned."""
    old = dec.load_applied(path) or {}
    keep = solution.get("on_camera_matches")
    if keep is not None:
        old = {k: v for k, v in old.items() if k in set(keep)}
    old.update(entries)
    return old


def _summary(solution: dict) -> str:
    zooms = [s["zoom"] for s in solution["setups"].values() if s.get("zoom")]
    punches = sum(e.get("kind") == "punch" for e in solution["items"].values())
    zr = f"zoom {min(zooms):.2f}-{max(zooms):.2f}" if zooms else "no zoom"
    return (f"{len(zooms)} setups framed ({zr}), {punches} punch-ins, {len(solution['pushes'])} push-ins, "
            f"{len(solution.get('hand_framed', []))} hand-framed left alone")


def apply(session, solution: dict, applied_path=None) -> tuple[dict, list[dict]]:
    """Fork, frame, push, save and record. Returns (history entry, issues added)."""
    applied_path = Path(applied_path) if applied_path else Path(session.dir) / "framing" / "applied.json"
    pending = applied_path.with_name(applied_path.name + ".pending")
    pushes = {p["key"]: p for p in solution.get("pushes", [])}
    tl_fps = float((solution.get("timeline") or {}).get("fps") or 24.0)
    extra: list[dict] = []
    tl = session.begin_step("framing")
    try:
        index = _index(tl)
        entries = {}
        for key, e in solution["items"].items():
            item = index.get((e["track"], e["start"]))
            if item is None or item.GetName() != e["clip"]:
                found = item.GetName() if item is not None else "nothing"
                raise ApplyError(f"{key} ({e['clip']}) is not on the new timeline (found {found}); "
                                 "the timeline changed since solve")
            props = {k: e["props"][k] for k in FRAMING_SET}
            old = _own_comps(item)
            for n, name in enumerate(old):  # free the name for the new comp
                if name == PUSH_COMP:
                    _rename(item, name, f"{PUSH_COMP}_old{n}")
                    old[n] = f"{PUSH_COMP}_old{n}"
            got = _set_props(item, key, props)
            if key in pushes:
                before = set(_comp_names(item))
                comp = add_pushin(item, pushes[key])
                added = [x for x in _comp_names(item) if x not in before]
                if len(added) != 1:
                    raise ApplyError(f"could not tell which Fusion comp is the new push-in on {key}")
                _rename(item, added[0], PUSH_COMP)
                extra += _timing_flag(key, e, pushes[key], comp, tl_fps)
            _drop_comps(item, old)  # after the new comp exists: Resolve keeps an item's only comp
            holders = _with_tool(item)
            if holders != ([PUSH_COMP] if key in pushes else []):
                raise ApplyError(f"{key}: expected {'one push-in comp' if key in pushes else 'no push-in'}, "
                                 f"found the push-in tool in {holders or 'no comp'}")
            entries[e["match"]] = {"props": _recorded(got), "fusion_comps": rvs.fusion_comp_count(item)}
        applied = _applied(applied_path, solution, entries)
        dec.save_applied(pending, applied)   # survives a failure right after finish_step
        entry = session.finish_step(_summary(solution))
        os.replace(pending, applied_path)
    except BaseException:
        if pending.exists():
            pending.unlink()
        session.abandon_step()
        print("framing apply failed: the step was abandoned. The forked timeline stays in Resolve for "
              "inspection; the previous version is still current.", file=sys.stderr)
        raise
    added = []
    for f in solution.get("flags", []) + extra:
        if len(f.get("keys") or []) > 1 and f.get("setup"):
            who = f"setup {f['setup']}"
        else:
            who = f.get("key") or (f"setup {f['setup']}" if f.get("setup") else "timeline")
        issue = session.add_issue("framing", f"{who}: {f['text']}", f.get("where"))
        if issue:
            added.append(issue)
    for i in solution.get("issues", []):   # the agent's uncertain calls, from decisions.json
        issue = session.add_issue("framing", i["text"], i["where"])
        if issue:
            added.append(issue)
    return entry, added
