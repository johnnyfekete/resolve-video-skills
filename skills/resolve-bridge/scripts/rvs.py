#!/usr/bin/env python3
"""rvs: the resolve-bridge library and CLI.

Every resolve-video-skills step goes through this file. It keeps three promises:
  1. A step never edits the previous timeline. It works on a new version.
  2. Every step ends with the Resolve project saved and the state file updated.
  3. Hand edits made in Resolve between steps are detected and recorded, never overwritten.

Two ways to make a new version:
  fork   duplicate the current timeline, then change it in place. Keeps grades, titles,
         effects and anything else Resolve stores on the timeline. Use for most steps.
  build  create a timeline from a plan file. Use only for structural edits (assembly,
         cutting). Items that are not media clips (titles, generators) are not rebuilt.

Usage from another skill:
    import sys; sys.path.insert(0, "<path to resolve-bridge>/scripts"); import rvs
    s = rvs.Session(workdir)              # connects, loads state
    tl = s.begin_step("framing")          # fork: new timeline, current in Resolve
    ... change tl through the Resolve API ...
    s.finish_step("punch-ins on 12 jump cuts")

CLI: python3 rvs.py --help
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import os
import re
import sys
from pathlib import Path

PLAN_SCHEMA = "rvs.plan/1"
STATE_SCHEMA = "rvs.state/1"
STATE_DIR = ".edit"

RESOLVE_MODULES = {
    "darwin": "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules",
    "win32": os.path.expandvars(r"%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\Modules"),
    "linux": "/opt/resolve/Developer/Scripting/Modules",
}

# Timeline item properties recorded in plans. Only values that differ from DEFAULTS are stored.
VIDEO_PROPS = ["Pan", "Tilt", "ZoomX", "ZoomY", "ZoomGang", "RotationAngle", "AnchorPointX", "AnchorPointY",
               "FlipX", "FlipY", "CropLeft", "CropRight", "CropTop", "CropBottom", "CropSoftness",
               "DynamicZoomEnabled", "DynamicZoomEase", "Opacity", "CompositeMode"]
AUDIO_PROPS = ["AudioVolume", "AudioPan", "AudioVoiceIsolationEnabled", "AudioVoiceIsolationAmount"]
DEFAULTS = {"Pan": 0.0, "Tilt": 0.0, "ZoomX": 1.0, "ZoomY": 1.0, "ZoomGang": True, "RotationAngle": 0.0,
            "AnchorPointX": 0.0, "AnchorPointY": 0.0, "FlipX": False, "FlipY": False, "CropLeft": 0.0,
            "CropRight": 0.0, "CropTop": 0.0, "CropBottom": 0.0, "CropSoftness": 0.0,
            "DynamicZoomEnabled": False, "DynamicZoomEase": 0, "Opacity": 100.0, "CompositeMode": 0,
            "AudioVolume": 0.0, "AudioPan": 0.0, "AudioVoiceIsolationEnabled": False,
            "AudioVoiceIsolationAmount": 0.0}


class RvsError(Exception):
    """Expected failure with a message meant for the user."""


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _close(a, b) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        try:
            return math.isclose(float(a), float(b), abs_tol=1e-3)
        except (TypeError, ValueError):
            return False
    return a == b


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower() or "step"


# ---------------------------------------------------------------- Resolve connection

def connect():
    """Return (resolve, project). Raises RvsError with a fix when Resolve is not reachable."""
    mod = os.environ.get("RESOLVE_SCRIPT_MODULES") or RESOLVE_MODULES.get(sys.platform)
    if mod and mod not in sys.path:
        sys.path.append(mod)
    try:
        import DaVinciResolveScript as dvr  # type: ignore
    except ImportError as e:
        raise RvsError(f"Resolve scripting module not found ({e}). Is DaVinci Resolve installed?")
    resolve = dvr.scriptapp("Resolve")
    if resolve is None:
        raise RvsError("Resolve is not reachable. Open DaVinci Resolve Studio and set "
                       "Preferences > System > General > External scripting using: Local.")
    if "Studio" not in (resolve.GetProductName() or ""):
        print("warning: this is not Resolve Studio; AI functions used by later skills will fail.", file=sys.stderr)
    project = resolve.GetProjectManager().GetCurrentProject()
    if project is None:
        raise RvsError("No project is open in Resolve.")
    return resolve, project


def find_timeline(project, name: str):
    for i in range(1, project.GetTimelineCount() + 1):
        tl = project.GetTimelineByIndex(i)
        if tl and tl.GetName() == name:
            return tl
    return None


def walk_folders(folder):
    yield folder
    for sub in folder.GetSubFolderList() or []:
        yield from walk_folders(sub)


def media_index(project) -> tuple[dict, dict]:
    """Media pool clips indexed by file path and by name (timelines excluded)."""
    by_path, by_name = {}, {}
    for f in walk_folders(project.GetMediaPool().GetRootFolder()):
        for c in f.GetClipList() or []:
            if c.GetClipProperty("Type") == "Timeline":
                continue
            p = c.GetClipProperty("File Path")
            if p:
                by_path[p] = c
            by_name.setdefault(c.GetName(), c)
    return by_path, by_name


def timeline_pool_item(project, name: str):
    """The media pool item (and its folder) that represents a timeline."""
    for f in walk_folders(project.GetMediaPool().GetRootFolder()):
        for c in f.GetClipList() or []:
            if c.GetClipProperty("Type") == "Timeline" and c.GetName() == name:
                return c, f
    return None, None


# ---------------------------------------------------------------- timeline -> plan

def _props(item, keys) -> dict:
    allp = item.GetProperties() or {}
    out = {}
    for k in keys:
        if k in allp and not _close(allp[k], DEFAULTS.get(k)):
            out[k] = allp[k]
    return out


def _fades(item) -> dict:
    try:
        f = item.GetFades() or {}
    except Exception:
        return {}
    return {k: f[k] for k in ("FadeIn", "FadeOut") if f.get(k)}


def export_plan(timeline) -> dict:
    """Read a Resolve timeline into a plan dict. Linked video+audio pairs become one item."""
    t0 = timeline.GetStartFrame()
    plan = {
        "schema": PLAN_SCHEMA,
        "timeline": {
            "name": timeline.GetName(),
            "fps": float(timeline.GetSetting("timelineFrameRate") or 24),
            "width": int(timeline.GetSetting("timelineResolutionWidth") or 1920),
            "height": int(timeline.GetSetting("timelineResolutionHeight") or 1080),
            "start_frame": t0,
        },
        "tracks": {k: [{"index": i, "name": timeline.GetTrackName(k, i)}
                       for i in range(1, timeline.GetTrackCount(k) + 1)] for k in ("video", "audio")},
        "items": [],
        "unsupported": [],
    }
    audio_seen = set()
    audio_by_uid = {}
    for t in range(1, timeline.GetTrackCount("audio") + 1):
        for it in timeline.GetItemListInTrack("audio", t) or []:
            audio_by_uid[it.GetUniqueId()] = (t, it)

    def base(it, kind, track):
        mpi = it.GetMediaPoolItem()
        d = {
            "clip": it.GetName(),
            "path": mpi.GetClipProperty("File Path") if mpi else None,
            "start": it.GetStart() - t0,
            "end": it.GetEnd() - t0,
            "src_start": it.GetSourceStartFrame(),
            "src_end": it.GetSourceEndFrame(),
            "src_fps": float(mpi.GetClipProperty("FPS") or 0) if mpi else None,
        }
        if not it.GetClipEnabled():
            d["enabled"] = False
        return d, mpi

    def audio_mapping(it):
        try:
            m = json.loads(it.GetSourceAudioChannelMapping() or "{}")
            return m.get("track_mapping") or None
        except (ValueError, TypeError):
            return None

    for t in range(1, timeline.GetTrackCount("video") + 1):
        for it in timeline.GetItemListInTrack("video", t) or []:
            d, mpi = base(it, "video", t)
            if mpi is None:
                plan["unsupported"].append({"track_type": "video", "track": t, "name": it.GetName(),
                                            "start": d["start"], "end": d["end"]})
                continue
            d.update({"media": "video", "track": t})
            if p := _props(it, VIDEO_PROPS):
                d["props"] = p
            if f := _fades(it):
                d["fades"] = f
            for li in it.GetLinkedItems() or []:
                uid = li.GetUniqueId()
                if uid in audio_by_uid and uid not in audio_seen:
                    at, ait = audio_by_uid[uid]
                    if ait.GetStart() == it.GetStart() and ait.GetEnd() == it.GetEnd():
                        audio_seen.add(uid)
                        d["media"] = "both"
                        d["audio_track"] = at
                        if m := audio_mapping(ait):
                            d["audio_mapping"] = m
                        if p := _props(ait, AUDIO_PROPS):
                            d["audio_props"] = p
                        if f := _fades(ait):
                            d["audio_fades"] = f
                        break
            plan["items"].append(d)
    for uid, (t, it) in audio_by_uid.items():
        if uid in audio_seen:
            continue
        d, mpi = base(it, "audio", t)
        if mpi is None:
            plan["unsupported"].append({"track_type": "audio", "track": t, "name": it.GetName(),
                                        "start": d["start"], "end": d["end"]})
            continue
        d.update({"media": "audio", "audio_track": t})
        if m := audio_mapping(it):
            d["audio_mapping"] = m
        if p := _props(it, AUDIO_PROPS):
            d["audio_props"] = p
        if f := _fades(it):
            d["audio_fades"] = f
        plan["items"].append(d)
    plan["items"].sort(key=lambda d: (d.get("track", 99), d.get("audio_track", 0), d["start"]))
    for n, d in enumerate(plan["items"], 1):
        d["id"] = f"i{n:03d}"
    return plan


# ---------------------------------------------------------------- plan comparison

# src_end is left out on purpose: with high-fps sources Resolve rounds it by up to one source frame
# (under half a timeline frame). Source start + timeline length pin the edit exactly.
_CMP_KEYS = ("media", "track", "audio_track", "path", "clip", "start", "end", "src_start",
             "enabled", "props", "audio_props", "fades", "audio_fades", "audio_mapping")


def _norm(d: dict) -> tuple:
    out = []
    for k in _CMP_KEYS:
        v = d.get(k)
        if k == "path" and v is None:
            continue
        if isinstance(v, dict):
            v = json.dumps({kk: round(vv, 3) if isinstance(vv, float) else vv for kk, vv in v.items()},
                           sort_keys=True)
        out.append((k, v))
    return tuple(out)


def _where(d: dict) -> str:
    tr = f"V{d['track']}" if d.get("track") else f"A{d.get('audio_track')}"
    return f"{d['clip']} {tr} @{d['start']}-{d['end']}"


def _field_changes(a: dict, b: dict) -> str:
    out = []
    for k in _CMP_KEYS:
        va, vb = a.get(k), b.get(k)
        if isinstance(va, dict) or isinstance(vb, dict):
            va, vb = va or {}, vb or {}
            for kk in sorted(set(va) | set(vb)):
                x, y = va.get(kk, DEFAULTS.get(kk)), vb.get(kk, DEFAULTS.get(kk))
                if not _close(x, y):
                    out.append(f"{kk} {x} -> {y}")
        elif not _close(va, vb) and not (k == "path" and None in (va, vb)):
            out.append(f"{k} {va} -> {vb}")
    return ", ".join(out)


def diff_plans(old: dict, new: dict) -> list[str]:
    """Human-readable differences between two plans (empty list = identical)."""
    a = {_norm(d): d for d in old.get("items", [])}
    b = {_norm(d): d for d in new.get("items", [])}
    gone = [a[k] for k in a.keys() - b.keys()]
    came = [b[k] for k in b.keys() - a.keys()]
    lines = []
    slot = lambda d: (d.get("track"), d.get("audio_track"), d["start"])
    came_by_slot = {slot(d): d for d in came}
    for d in gone:
        n = came_by_slot.pop(slot(d), None)
        if n is not None:
            lines.append(f"~ changed: {_where(d)}: {_field_changes(d, n)}")
        else:
            lines.append(f"- removed: {_where(d)}")
    lines += [f"+ added:   {_where(d)}" for d in came_by_slot.values()]
    ou = {(u["track_type"], u["track"], u["name"], u["start"], u["end"]) for u in old.get("unsupported", [])}
    nu = {(u["track_type"], u["track"], u["name"], u["start"], u["end"]) for u in new.get("unsupported", [])}
    lines += [f"- removed non-media item: {u}" for u in ou - nu]
    lines += [f"+ added non-media item:   {u}" for u in nu - ou]
    return sorted(lines)


# ---------------------------------------------------------------- state

class Session:
    """A connection to Resolve plus the project's edit state (<workdir>/.edit/state.json)."""

    def __init__(self, workdir: str | os.PathLike = ".", connect_now: bool = True):
        self.workdir = Path(workdir).resolve()
        self.dir = self.workdir / STATE_DIR
        self.state_path = self.dir / "state.json"
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else None
        self.resolve = self.project = None
        if connect_now:
            self.resolve, self.project = connect()
            if self.state and self.state["resolve_project"] != self.project.GetName():
                raise RvsError(f"Resolve has project '{self.project.GetName()}' open, but this folder's state "
                               f"belongs to '{self.state['resolve_project']}'. Open that project first.")

    # -- helpers
    def _require_state(self):
        if not self.state:
            raise RvsError(f"No edit state in {self.dir}. Run: rvs.py adopt --timeline '<name>'")

    def _save_state(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1, ensure_ascii=False))
        tmp.replace(self.state_path)

    def _write_plan(self, plan: dict, version: int, step: str) -> str:
        rel = f"plans/v{version:02d}_{_slug(step)}.json"
        p = self.dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(plan, indent=1, ensure_ascii=False))
        return rel

    def load_plan(self, rel: str | None = None) -> dict:
        self._require_state()
        return json.loads((self.dir / (rel or self.state["current"]["plan"])).read_text())

    def current_timeline(self):
        self._require_state()
        name = self.state["current"]["timeline"]
        tl = find_timeline(self.project, name)
        if tl is None:
            raise RvsError(f"Timeline '{name}' from the state file is not in the project (renamed or deleted?). "
                           "Run: rvs.py adopt --timeline '<name>' to continue from another timeline.")
        return tl

    def _save_project(self):
        if not self.project.GetName():
            raise RvsError("Project has no name; cannot save.")
        if not self.resolve.GetProjectManager().SaveProject():
            raise RvsError("Resolve refused to save the project. Nothing was recorded; check Resolve for a dialog.")

    def _record(self, step: str, timeline_name: str, plan: dict, summary: str, kind: str):
        version = self.state["current"]["version"] + 1 if self.state else 1
        rel = self._write_plan(plan, version, step)
        entry = {"version": version, "step": step, "kind": kind, "timeline": timeline_name, "plan": rel,
                 "date": _now(), "summary": summary}
        self.state["history"].append(entry)
        self.state["current"] = {k: entry[k] for k in ("version", "timeline", "plan")}
        self.state.pop("pending", None)
        return entry

    def _new_name(self, step: str) -> str:
        version = self.state["current"]["version"] + 1
        name = f"{self.state['base_name']}_v{version:02d}_{_slug(step)}"
        n = 2
        while self.project and find_timeline(self.project, name if n == 2 else f"{name}_{n - 1}"):
            n += 1
        return name if n == 2 else f"{name}_{n - 1}"

    def _archive(self, timeline_name: str):
        if not self.state.get("archive_folder"):
            return
        mp = self.project.GetMediaPool()
        item, folder = timeline_pool_item(self.project, timeline_name)
        if item is None:
            print(f"warning: could not find '{timeline_name}' in the media pool to archive it.", file=sys.stderr)
            return
        root = mp.GetRootFolder()
        target = next((f for f in root.GetSubFolderList() or [] if f.GetName() == self.state["archive_folder"]),
                      None) or mp.AddSubFolder(root, self.state["archive_folder"])
        if folder is not None and folder.GetUniqueId() == target.GetUniqueId():
            return
        if not mp.MoveClips([item], target):
            print(f"warning: could not move '{timeline_name}' to {self.state['archive_folder']}.", file=sys.stderr)

    def _put_new_timeline_at_top(self):
        mp = self.project.GetMediaPool()
        mp.SetCurrentFolder(mp.GetRootFolder())

    # -- public API
    def adopt(self, timeline_name: str | None, base_name: str | None, archive_folder: str = "Archive"):
        """Start (or restart) the state from an existing timeline. Changes nothing in Resolve."""
        tl = find_timeline(self.project, timeline_name) if timeline_name else self.project.GetCurrentTimeline()
        if tl is None:
            raise RvsError(f"Timeline '{timeline_name}' not found.")
        name = tl.GetName()
        if base_name is None:
            base_name = re.sub(r"_v\d+.*$", "", name) or name
        plan = export_plan(tl)
        if self.state is None:
            self.state = {"schema": STATE_SCHEMA, "resolve_project": self.project.GetName(),
                          "base_name": base_name, "archive_folder": archive_folder, "history": [],
                          "current": {"version": 0}}
        else:
            self.state["base_name"] = base_name
        entry = self._record("adopt", name, plan, f"started from existing timeline '{name}'", "adopt")
        self._save_state()
        return entry, plan

    def check(self) -> list[str]:
        """Differences between the current Resolve timeline and the last recorded plan."""
        tl = self.current_timeline()
        return diff_plans(self.load_plan(), export_plan(tl))

    def sync(self, summary: str = "hand edits in Resolve") -> dict | None:
        """Record hand edits on the current timeline as their own history entry (no new timeline)."""
        changes = self.check()
        if not changes:
            return None
        tl = self.current_timeline()
        entry = self._record("manual", tl.GetName(), export_plan(tl), f"{summary} ({len(changes)} changes)", "manual")
        self._save_state()
        return entry

    def begin_step(self, step: str):
        """Fork: duplicate the current timeline into a new version and make it current in Resolve."""
        self._require_state()
        if self.state.get("pending"):
            p = self.state["pending"]
            raise RvsError(f"Step '{p['step']}' (timeline '{p['timeline']}') was started on {p['date']} and never "
                           "finished. Finish it (rvs.py finish), or drop it (rvs.py abandon).")
        synced = self.sync()
        if synced:
            print(f"recorded hand edits as v{synced['version']:02d} before starting '{step}'")
        src = self.current_timeline()
        name = self._new_name(step)
        if find_timeline(self.project, name):
            raise RvsError(f"A timeline named '{name}' already exists.")
        self.project.SetCurrentTimeline(src)
        self._put_new_timeline_at_top()
        new = src.DuplicateTimeline(name)
        if new is None:
            raise RvsError(f"Resolve could not duplicate '{src.GetName()}'.")
        self.project.SetCurrentTimeline(new)
        self.state["pending"] = {"step": step, "timeline": name, "kind": "fork", "date": _now(),
                                 "from": src.GetName()}
        self._save_state()
        return new

    def finish_step(self, summary: str) -> dict:
        """Archive the previous version, save the Resolve project, record the step."""
        self._require_state()
        p = self.state.get("pending")
        if not p:
            raise RvsError("No step in progress.")
        tl = find_timeline(self.project, p["timeline"])
        if tl is None:
            raise RvsError(f"Timeline '{p['timeline']}' of the pending step is gone.")
        prev = self.state["current"]["timeline"]
        plan = export_plan(tl)
        self._archive(prev)
        self.project.SetCurrentTimeline(tl)
        self._save_project()
        entry = self._record(p["step"], tl.GetName(), plan, summary, p["kind"])
        self._save_state()
        return entry

    def abandon_step(self) -> dict:
        """Forget the pending step. Its timeline stays in Resolve for you to inspect or delete."""
        self._require_state()
        p = self.state.pop("pending", None)
        if not p:
            raise RvsError("No step in progress.")
        prev = find_timeline(self.project, self.state["current"]["timeline"])
        if prev:
            self.project.SetCurrentTimeline(prev)
        self._save_state()
        return p

    def build(self, plan: dict, step: str, summary: str) -> dict:
        """Rebuild: create a new timeline from a plan (structural steps). Refuses on unrecorded hand edits."""
        self._require_state()
        if self.state.get("pending"):
            raise RvsError("Another step is in progress. Finish or abandon it first.")
        changes = self.check()
        if changes:
            raise RvsError("The current timeline was edited by hand since the last step:\n  " +
                           "\n  ".join(changes[:20]) +
                           "\nRun: rvs.py sync   then make the new plan from the synced plan, so those edits are kept.")
        src_plan = self.load_plan()
        if src_plan.get("unsupported"):
            print(f"warning: {len(src_plan['unsupported'])} non-media items (titles, generators...) on the current "
                  "timeline are not rebuilt. They stay on the archived version.", file=sys.stderr)
        name = self._new_name(step)
        self._put_new_timeline_at_top()
        tl = build_timeline(self.project, plan, name)
        self.state["pending"] = {"step": step, "timeline": name, "kind": "build", "date": _now(),
                                 "from": self.state["current"]["timeline"]}
        self._save_state()
        got = export_plan(tl)
        mismatch = diff_plans(_strip_unsupported(plan), _strip_unsupported(got))
        if mismatch:
            print("warning: built timeline differs from the plan:\n  " + "\n  ".join(mismatch[:20]), file=sys.stderr)
        return self.finish_step(summary)


def _strip_unsupported(plan: dict) -> dict:
    return {**plan, "unsupported": []}


# ---------------------------------------------------------------- plan -> timeline

def build_timeline(project, plan: dict, name: str):
    """Create timeline `name` from a plan. Exact lengths are enforced by re-appending with a nudged out-point."""
    mp = project.GetMediaPool()
    by_path, by_name = media_index(project)
    missing = []
    for d in plan["items"]:
        clip = by_path.get(d.get("path")) or by_name.get(d["clip"])
        if clip is None:
            missing.append(d.get("path") or d["clip"])
        d["_clip"] = clip
    if missing:
        raise RvsError("Clips not in the media pool: " + ", ".join(sorted(set(missing))))
    tl = mp.CreateEmptyTimeline(name)
    if tl is None:
        raise RvsError(f"Could not create timeline '{name}' (name taken or invalid).")
    project.SetCurrentTimeline(tl)
    spec = plan.get("timeline", {})
    want_fps = spec.get("fps")
    if want_fps and abs(float(tl.GetSetting("timelineFrameRate") or 0) - want_fps) > 1e-3:
        tl.SetSetting("useCustomSettings", "1")
        tl.SetSetting("timelineFrameRate", str(int(want_fps) if float(want_fps).is_integer() else want_fps))
    for key, setting in (("width", "timelineResolutionWidth"), ("height", "timelineResolutionHeight")):
        if spec.get(key) and str(tl.GetSetting(setting)) != str(spec[key]):
            tl.SetSetting("useCustomSettings", "1")
            tl.SetSetting(setting, str(spec[key]))
    for kind in ("video", "audio"):
        tracks = plan.get("tracks", {}).get(kind, [])
        need = max([t["index"] for t in tracks] +
                   [d.get("track" if kind == "video" else "audio_track") or 0 for d in plan["items"]] + [1])
        while tl.GetTrackCount(kind) < need:
            tl.AddTrack(kind)
        for t in tracks:
            if t.get("name"):
                tl.SetTrackName(kind, t["index"], t["name"])
    t0 = tl.GetStartFrame()
    tl_fps = float(tl.GetSetting("timelineFrameRate"))
    failures = []
    created = {}  # item index -> {"video": item, "audio": item}
    # Video first. Audio second, by ascending track: Resolve spreads multi-channel clips over the target track
    # and the tracks above it, so those extra items are deleted right after each append, before higher tracks fill.
    order_v = [(n, d) for n, d in enumerate(plan["items"]) if d["media"] in ("both", "video")]
    order_a = sorted([(n, d) for n, d in enumerate(plan["items"]) if d["media"] in ("both", "audio")],
                     key=lambda x: (x[1]["audio_track"], x[1]["start"]))
    for kind, mtype, key, order in (("video", 1, "track", order_v), ("audio", 2, "audio_track", order_a)):
        for n, d in order:
            it = _append_exact(mp, tl, d, mtype, d[key], t0, tl_fps)
            if it is None:
                failures.append(f"{d['clip']} {kind} @{d['start']}")
                continue
            created.setdefault(n, {})[kind] = it
            props = d.get("audio_props" if kind == "audio" else "props")
            fades = d.get("audio_fades" if kind == "audio" else "fades")
            if kind == "audio" and d.get("audio_mapping"):
                m = json.dumps({"track_mapping": {"1": next(iter(d["audio_mapping"].values()))}})
                if not it.SetSourceAudioChannelMapping(m):
                    print(f"warning: audio channel mapping not applied on {d['clip']} @{d['start']}", file=sys.stderr)
            if props and not it.SetProperties(props):
                print(f"warning: properties not applied on {d['clip']} @{d['start']}: {props}", file=sys.stderr)
            if fades:
                it.SetFades(fades)
            if d.get("enabled") is False:
                it.SetClipEnabled(False)
    for n, pair in created.items():
        if len(pair) == 2:
            tl.SetClipsLinked([pair["video"], pair["audio"]], True)
    want_audio = max([t["index"] for t in plan.get("tracks", {}).get("audio", [])] + [1])
    for idx in range(tl.GetTrackCount("audio"), want_audio, -1):
        if not tl.GetItemListInTrack("audio", idx):
            tl.DeleteTrack("audio", idx)
    if failures:
        raise RvsError(f"Timeline '{name}' was created, but {len(failures)} items failed: " + ", ".join(failures[:10]))
    return tl


def _items_at(tl, rec: int, kind: str | None = None) -> list:
    out = []
    for k in ((kind,) if kind else ("video", "audio")):
        for t in range(1, tl.GetTrackCount(k) + 1):
            out += [x for x in tl.GetItemListInTrack(k, t) or [] if x.GetStart() == rec]
    return out


def _append_exact(mp, tl, d, media_type, track, t0, tl_fps):
    """Append one video (media_type 1) or audio (2) item at its exact length; return the item on `track`."""
    clip = d["_clip"]
    kind = "video" if media_type == 1 else "audio"
    want = d["end"] - d["start"]
    src_fps = float(clip.GetClipProperty("FPS") or d.get("src_fps") or tl_fps)
    s = int(d["src_start"])
    # endFrame is exclusive: length = (endFrame - startFrame) / (src_fps / tl_fps)
    e = int(d["src_end"]) if d.get("src_end") else s + int(math.ceil(want * src_fps / tl_fps))
    rec = t0 + d["start"]
    for _ in range(4):
        before = {x.GetUniqueId() for x in _items_at(tl, rec, kind)}
        info = {"mediaPoolItem": clip, "startFrame": s, "endFrame": e, "trackIndex": track,
                "recordFrame": rec, "mediaType": media_type}
        if not [x for x in (mp.AppendToTimeline([info]) or []) if x]:
            return None
        added = [x for x in _items_at(tl, rec, kind) if x.GetUniqueId() not in before]
        keep = next((x for x in added if x.GetTrackTypeAndIndex()[1] == track), None)
        extra = [x for x in added if x is not keep]
        if extra:
            tl.DeleteClips(extra, False)
        if keep is None:
            return None
        got = keep.GetEnd() - keep.GetStart()
        if got == want:
            return keep
        tl.DeleteClips([keep], False)
        e += 1 if got < want else -1
    return None


# ---------------------------------------------------------------- CLI

def _print_status(s: Session):
    st = s.state
    if not st:
        print(f"No edit state in {s.dir}.")
        print("Next: python3 rvs.py adopt --timeline '<timeline to start from>'")
        return
    print(f"Resolve project: {st['resolve_project']}")
    print(f"Current: v{st['current']['version']:02d}  {st['current']['timeline']}")
    print("History:")
    for h in st["history"]:
        print(f"  v{h['version']:02d}  {h['date'][:16]}  {h['step']:<10} {h['timeline']}  - {h['summary']}")
    if st.get("pending"):
        p = st["pending"]
        print(f"IN PROGRESS: step '{p['step']}' on '{p['timeline']}' since {p['date']}")
    if s.project:
        changes = s.check()
        print(f"Hand edits since last step: {len(changes)}" + (" (run: rvs.py sync)" if changes else ""))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="rvs.py", description="resolve-bridge: versioned, saved timeline steps.")
    ap.add_argument("--workdir", default=os.environ.get("RVS_WORKDIR", "."),
                    help="video project working folder; state lives in <workdir>/.edit (default: cwd)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="where are we: current version, history, pending step, hand edits")
    a = sub.add_parser("adopt", help="start the state from an existing timeline (changes nothing in Resolve)")
    a.add_argument("--timeline", help="timeline name (default: the one open in Resolve)")
    a.add_argument("--base", help="base name for new versions (default: timeline name without _vNN...)")
    a.add_argument("--archive-folder", default="Archive", help="media pool folder for old versions ('' = off)")
    sub.add_parser("check", help="list hand edits on the current timeline since the last step (exit 1 if any)")
    sy = sub.add_parser("sync", help="record hand edits as their own history entry")
    sy.add_argument("--summary", default="hand edits in Resolve")
    e = sub.add_parser("export", help="write a timeline as a plan file")
    e.add_argument("--timeline", help="default: current timeline from state, else the open one")
    e.add_argument("-o", "--out", required=True)
    b = sub.add_parser("begin", help="fork: duplicate the current timeline into a new version")
    b.add_argument("step")
    f = sub.add_parser("finish", help="archive previous version, save project, record the step")
    f.add_argument("--summary", required=True)
    sub.add_parser("abandon", help="forget the pending step (its timeline stays in Resolve)")
    bl = sub.add_parser("build", help="create a new version from a plan file (structural steps)")
    bl.add_argument("plan")
    bl.add_argument("--step", required=True)
    bl.add_argument("--summary", required=True)
    args = ap.parse_args(argv)

    try:
        s = Session(args.workdir)
        if args.cmd == "status":
            _print_status(s)
        elif args.cmd == "adopt":
            entry, plan = s.adopt(args.timeline, args.base, args.archive_folder)
            print(f"v{entry['version']:02d} adopted '{entry['timeline']}': {len(plan['items'])} items, "
                  f"{len(plan['unsupported'])} non-media. State: {s.state_path}")
        elif args.cmd == "check":
            changes = s.check()
            print("\n".join(changes) if changes else "no hand edits")
            return 1 if changes else 0
        elif args.cmd == "sync":
            entry = s.sync(args.summary)
            print(f"recorded as v{entry['version']:02d}" if entry else "no hand edits; nothing recorded")
        elif args.cmd == "export":
            tl = (find_timeline(s.project, args.timeline) if args.timeline
                  else (s.current_timeline() if s.state else s.project.GetCurrentTimeline()))
            if tl is None:
                raise RvsError("Timeline not found.")
            Path(args.out).write_text(json.dumps(export_plan(tl), indent=1, ensure_ascii=False))
            print(f"wrote {args.out}")
        elif args.cmd == "begin":
            tl = s.begin_step(args.step)
            print(f"working on '{tl.GetName()}'. When done: rvs.py finish --summary '...'")
        elif args.cmd == "finish":
            entry = s.finish_step(args.summary)
            print(f"v{entry['version']:02d} saved: {entry['timeline']}")
        elif args.cmd == "abandon":
            p = s.abandon_step()
            print(f"dropped pending step '{p['step']}'. Timeline '{p['timeline']}' is still in Resolve.")
        elif args.cmd == "build":
            plan = json.loads(Path(args.plan).read_text())
            entry = s.build(plan, args.step, args.summary)
            print(f"v{entry['version']:02d} saved: {entry['timeline']}")
    except RvsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
