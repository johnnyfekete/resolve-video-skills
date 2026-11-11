#!/usr/bin/env python3
"""video-framing: declutter base frames, punch-ins on jump cuts and push-ins, as a new Resolve version.

  python3 framing.py prepare --workdir <folder>   # measure, group setups, stills + transcript, decisions template
  python3 framing.py solve   --workdir <folder>   # dry run: solution, REPORT.md, previews; changes nothing
  python3 framing.py apply   --workdir <folder>   # solve, then fork <base>_vNN_framing, set the frames, save

Between prepare and solve the agent fills in .edit/framing/decisions.json (labels, keep-out and
keep-in boxes, push-in lines). Outputs: generated/framing/ (REPORT.md, setups/, flags/) and
.edit/framing/ (context.json, transcript.md, decisions.json, solution.json, applied.json).
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
BRIDGE = Path(os.environ.get("RVS_BRIDGE", HERE.parent.parent / "resolve-bridge" / "scripts"))
sys.path.insert(0, str(BRIDGE))
try:
    import rvs  # noqa: E402
    import rvs_transcribe  # noqa: E402
except ImportError:
    sys.exit(f"resolve-bridge not found at {BRIDGE}. Install it next to this skill or set RVS_BRIDGE.")

import apply_resolve  # noqa: E402
import decisions as dec  # noqa: E402
import preview  # noqa: E402
from geometry import Rect, rect_to_props, zoom_budget, zoom_of  # noqa: E402
from measure import measure_items  # noqa: E402
from setups import group_setups, signature  # noqa: E402
from solve import Opts, setup_info_from, solve_base, solve_cuts  # noqa: E402
from timeline import _prefixes_match, item_key, on_camera, shot_prefix, visible_cuts  # noqa: E402

STEP = "framing"
ASPECT_TOL = 0.01


class FramingError(Exception):
    """Expected failure with a message for the user; exit code 2."""


# ---------------------------------------------------------------- small helpers

def tc(sec: float) -> str:
    return f"{int(sec // 60):02d}:{sec % 60:05.2f}"


def _paths(workdir) -> tuple[Path, Path]:
    w = Path(workdir).resolve()
    return w / rvs.STATE_DIR / "framing", w / "generated" / "framing"


def _video_items(plan: dict) -> list[dict]:
    return [i for i in plan.get("items", []) if i.get("media") in ("video", "both")
            and i.get("track") is not None]


def _where(it: dict, fps: float) -> str:
    return f"{it.get('clip') or it.get('path')} at {tc(it['start'] / fps)}"


_SIZES: dict[str, tuple[int, int]] = {}


def source_size(path) -> tuple[int, int]:
    """(width, height) of the source as displayed: ffprobe, with a 90/270 degree rotation applied."""
    path = str(path)
    if path in _SIZES:
        return _SIZES[path]
    if not shutil.which("ffprobe"):
        raise FramingError("video-framing needs ffprobe on PATH to read source sizes")
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height:stream_tags=rotate:stream_side_data=rotation",
                        "-of", "json", path], capture_output=True, text=True)
    if r.returncode != 0:
        raise FramingError(f"ffprobe could not read {path}:\n{r.stderr.strip()}")
    st = (json.loads(r.stdout or "{}").get("streams") or [{}])[0]
    w, h = int(st.get("width") or 0), int(st.get("height") or 0)
    rot = st.get("tags", {}).get("rotate")
    for sd in st.get("side_data_list") or []:
        rot = sd.get("rotation", rot)
    if rot is not None and abs(int(float(rot))) % 180 == 90:
        w, h = h, w
    if not (w and h):
        raise FramingError(f"no video size found in {path}")
    _SIZES[path] = (w, h)
    return w, h


def _frame_path(fdir: Path, it: dict) -> Path:
    h = hashlib.sha1(dec.match_key(it).encode()).hexdigest()[:16]
    return fdir / "frames" / f"{h}.jpg"


def item_frame(fdir: Path, plan: dict, it: dict) -> Path:
    """A 1920 px frame from the middle of the item (cached by source path, in-point and length)."""
    out = _frame_path(fdir, it)
    if out.exists():
        return out
    fps = float(plan["timeline"]["fps"])
    sf = float(it.get("src_fps") or fps)
    t = it["src_start"] / sf + (it["end"] - it["start"]) / fps / 2
    return preview.grab_frame(it["path"], t, out)


def _session(workdir, need_resolve: bool):
    """A Session; without need_resolve it falls back to the recorded plan when Resolve is not usable."""
    if need_resolve:
        s = rvs.Session(workdir)
        s._require_state()
        return s
    try:
        s = rvs.Session(workdir)
    except rvs.RvsError as e:
        print(f"Resolve not available ({e}); using the recorded plan.")
        s = rvs.Session(workdir, connect_now=False)
    s._require_state()
    return s


def _status(s) -> None:
    cur = s.state["current"]
    if cur.get("timeline") is None:
        raise FramingError("No timeline tracked yet. Run: rvs.py adopt --timeline '<name>'")
    print(f"current: v{cur['version']:02d} {cur['timeline']}")
    if s.state.get("pending"):
        p = s.state["pending"]
        raise FramingError(f"step '{p['step']}' on '{p['timeline']}' is still in progress. "
                           "Finish or abandon it first (rvs.py finish / rvs.py abandon).")
    if s.project is not None:
        changes = s.check()
        if changes:
            entry = s.sync("hand edits before framing")
            print(f"recorded {len(changes)} hand edits as v{entry['version']:02d}")


# ---------------------------------------------------------------- transcript

def _sound_pieces(plan: dict, cam: list[dict], setup_of: dict) -> list[tuple[dict, str]]:
    """(sound piece, setup id) per on-camera item: the item itself when it carries sound, else the
    overlapping dialogue piece clipped to the picture."""
    fps = float(plan["timeline"]["fps"])
    sound = [i for i in plan.get("items", []) if i.get("media") in ("audio", "both")]
    out = []
    for it in cam:
        sid = setup_of.get(item_key(it))
        if sid is None:
            continue
        if it.get("media") == "both":
            out.append((it, sid))
            continue
        pre = shot_prefix(it.get("clip", ""))
        for snd in sound:
            if not (snd["start"] < it["end"] and it["start"] < snd["end"]):
                continue
            if not ((it.get("path") and it.get("path") == snd.get("path"))
                    or _prefixes_match(pre, shot_prefix(snd.get("clip", "")))):
                continue
            s, e = max(it["start"], snd["start"]), min(it["end"], snd["end"])
            sf = float(snd.get("src_fps") or fps)
            out.append(({**snd, "start": s, "end": e,
                         "src_start": snd["src_start"] + round((s - snd["start"]) * sf / fps)}, sid))
    return out


def transcript_lines(plan: dict, pieces: list[tuple[dict, str]], transcripts: dict) -> list[str]:
    """`MM:SS.ss-MM:SS.ss <setup> <text>` per transcript segment heard on camera, in timeline time.

    Segments are in source seconds; each is clipped to the piece's source window (100% speed)."""
    fps = float(plan["timeline"]["fps"])
    rows = []
    for it, sid in pieces:
        tr = transcripts.get(it.get("path"))
        if not tr:
            continue
        sf = float(it.get("src_fps") or fps)
        s0 = it["src_start"] / sf
        s1 = s0 + (it["end"] - it["start"]) / fps
        t0 = it["start"] / fps
        for seg in tr.get("segments", []):
            a, b = max(seg["start"], s0), min(seg["end"], s1)
            text = " ".join(seg.get("text", "").split())
            if b <= a or not text:
                continue
            rows.append((t0 + a - s0, t0 + b - s0, sid, text))
    rows.sort()
    return [f"{tc(a)}-{tc(b)} {sid} {text}" for a, b, sid, text in rows]


def write_transcript(workdir: Path, fdir: Path, plan: dict, cam: list[dict], setup_of: dict) -> Path:
    pieces = _sound_pieces(plan, cam, setup_of)
    org = workdir / rvs.STATE_DIR / "organize" / "transcripts"
    cache = org if org.is_dir() else fdir / "transcripts"
    transcripts, problems = {}, []
    for path in sorted({p["path"] for p, _ in pieces if p.get("path")}):
        try:
            transcripts[path] = rvs_transcribe.transcribe(path, cache)
        except (RuntimeError, OSError, subprocess.CalledProcessError) as e:
            problems.append(f"{Path(path).name}: {e}")
    lines = ["# On-camera speech (timeline time, setup, text)", ""]
    lines += [f"note: not transcribed: {p}" for p in problems]
    lines += transcript_lines(plan, pieces, transcripts)
    out = fdir / "transcript.md"
    out.write_text("\n".join(lines) + "\n")
    for p in problems:
        print(f"warning: not transcribed: {p}", file=sys.stderr)
    return out


# ---------------------------------------------------------------- prepare

def _with_reclassify(plan: dict, cam: list[dict], reclassify: dict) -> list[dict]:
    keys = {item_key(i) for i in cam}
    out = [i for i in cam if reclassify.get(item_key(i)) != "broll"]
    for it in _video_items(plan):
        k = item_key(it)
        if reclassify.get(k) == "on_camera" and k not in keys:
            out.append(it)
    return sorted(out, key=lambda i: (i["start"], i["track"]))


def _budget(sizes: list[tuple[int, int]], tl_h: int) -> float:
    return min(zoom_budget(h, tl_h) for _, h in sizes)


def prepare(opt) -> int:
    workdir = Path(opt.workdir).resolve()
    s = _session(workdir, need_resolve=False)
    _status(s)
    plan = s.load_plan()
    fdir, gdir = _paths(workdir)
    fdir.mkdir(parents=True, exist_ok=True)
    dpath = fdir / "decisions.json"
    old = json.loads(dpath.read_text()) if dpath.exists() else None
    fps = float(plan["timeline"]["fps"])
    tl_w, tl_h = int(plan["timeline"]["width"]), int(plan["timeline"]["height"])

    cam = _with_reclassify(plan, on_camera(plan), (old or {}).get("reclassify", {}))
    if not cam:
        raise FramingError("no on-camera items found on the current timeline")
    print(f"{len(cam)} on-camera items; measuring (cached after the first run)...")
    measures = measure_items(plan, cam, workdir)
    frames = {item_key(it): item_frame(fdir, plan, it) for it in cam}
    setups = group_setups(cam, {k: signature(f) for k, f in frames.items()})
    setup_of = {k: sid for sid, keys in setups.items() for k in keys}
    by_key = {item_key(i): i for i in cam}
    sources = {p: list(source_size(p)) for p in sorted({i["path"] for i in cam})}
    hand = dec.hand_framed(cam, dec.load_applied(fdir / "applied.json"))

    shutil.rmtree(gdir / "setups", ignore_errors=True)
    ctx_setups = {}
    for sid, keys in setups.items():
        first = by_key[keys[0]]
        still = preview.setup_still(frames[keys[0]], gdir / "setups" / f"{sid}.jpg",
                                    title=f"Setup {sid}: {first.get('clip')}")
        sizes = [tuple(sources[by_key[k]["path"]]) for k in keys]
        ctx_setups[sid] = {"items": keys, "still": str(still), "frame": str(frames[keys[0]]),
                           "budget": round(_budget(sizes, tl_h), 3),
                           "source_size": list(min(sizes, key=lambda x: x[1]))}
    items = {}
    for it in cam:
        k = item_key(it)
        items[k] = {"clip": it.get("clip"), "path": it.get("path"), "track": it["track"],
                    "start": it["start"], "end": it["end"],
                    "timecode": f"{tc(it['start'] / fps)}-{tc(it['end'] / fps)}",
                    "setup": setup_of[k], "hand_framed": k in hand, "measure": measures.get(k),
                    "frame": str(frames[k])}
    context = {"timeline": {**plan["timeline"], "version": s.state["current"]["version"]},
               "timeline_size": [tl_w, tl_h], "sources": sources,
               "budget": round(min(c["budget"] for c in ctx_setups.values()), 3),
               "setups": ctx_setups, "items": items}
    (fdir / "context.json").write_text(json.dumps(context, indent=1, ensure_ascii=False))
    transcript = write_transcript(workdir, fdir, plan, cam, setup_of)

    data = dec.merge(old, setups) if old else dec.template(setups)
    dec.save(dpath, data)

    todo = [sid for sid, d in data["setups"].items() if d.get("label") not in dec.LABELS]
    print(f"{len(setups)} setups, {len(hand)} hand-framed items (left alone).")
    for sid, c in ctx_setups.items():
        print(f"  {sid}: {len(c['items'])} items, budget {c['budget']:.2f}x, still {c['still']}")
    print(f"context:    {fdir / 'context.json'}")
    print(f"transcript: {transcript}")
    print(f"decisions:  {dpath}")
    if todo:
        print("Fill in for setups " + ", ".join(todo) + ": label (plain|dynamic), reason, keep_out and "
              "keep_in boxes [left, top, right, bottom] in the still's 0..1 grid coordinates.")
    print("Push-ins: add {start_s, end_s, text, reason} to push_ins (timeline seconds from the "
          "transcript), only on plain setups, on lines that carry the video. Then run solve.")
    return 0


# ---------------------------------------------------------------- solve

def _opts(opt) -> tuple[Opts, bool, bool]:
    o = Opts()
    if opt.punch is not None:
        o = dataclasses.replace(o, punch=opt.punch, punch_min=min(o.punch_min, opt.punch))
    if opt.push is not None:
        o = dataclasses.replace(o, push=opt.push)
    if opt.no_punch:
        o = dataclasses.replace(o, punch=1.0, punch_min=1.0)
    if opt.no_push:
        o = dataclasses.replace(o, push=1.0)
    return o, bool(opt.no_punch), bool(opt.no_push)


def _rect_json(r: Rect) -> dict:
    return {"cx": round(r.cx, 5), "cy": round(r.cy, 5), "w": round(r.w, 5), "aspect": round(r.aspect, 5)}


def _rect(d: dict) -> Rect:
    return Rect(d["cx"], d["cy"], d["w"], d["aspect"])


def _props(rect: Rect, tl_w: int, tl_h: int, src: tuple[int, int]) -> dict:
    if abs(src[0] / src[1] - tl_w / tl_h) <= ASPECT_TOL * tl_w / tl_h:
        return rect_to_props(rect, tl_w, tl_h)
    return rect_to_props(rect, tl_w, tl_h, src[0], src[1])


def run_solve(s, opt, workdir: Path) -> dict:
    """Solve from the session's current plan and the decisions; write solution, report, previews."""
    fdir, gdir = _paths(workdir)
    dpath = fdir / "decisions.json"
    if not dpath.exists():
        raise FramingError(f"no decisions at {dpath}. Run prepare first.")
    try:
        data = dec.load(dpath)
    except (ValueError, KeyError) as e:
        raise FramingError(f"decisions incomplete in {dpath}: {e}")
    plan = s.load_plan()
    fps = float(plan["timeline"]["fps"])
    tl_w, tl_h = int(plan["timeline"]["width"]), int(plan["timeline"]["height"])
    by_key = {item_key(i): i for i in _video_items(plan)}
    ctx_path = fdir / "context.json"
    ctx = json.loads(ctx_path.read_text()) if ctx_path.exists() else {}
    _SIZES.update({p: tuple(v) for p, v in (ctx.get("sources") or {}).items()})

    reclass = data.get("reclassify", {})
    setups = {sid: [k for k in d["items"] if reclass.get(k) != "broll"] for sid, d in data["setups"].items()}
    missing = sorted({k for keys in setups.values() for k in keys if k not in by_key})
    if missing:
        raise FramingError(f"decisions name items that are not on the current timeline ({', '.join(missing[:8])}). "
                           "The timeline changed since prepare: run prepare again.")
    listed = {k for keys in setups.values() for k in keys}
    new = [item_key(i) for i in _with_reclassify(plan, on_camera(plan), reclass) if item_key(i) not in listed]
    if new:
        print(f"warning: {len(new)} on-camera items are in no setup and stay as they are "
              f"({', '.join(new[:8])}); run prepare again to frame them.", file=sys.stderr)

    all_items = [by_key[k] for k in listed]
    hand = dec.hand_framed(all_items, dec.load_applied(fdir / "applied.json"))
    solve_keys = {sid: [k for k in keys if k not in hand] for sid, keys in setups.items()}
    cam = sorted((by_key[k] for keys in solve_keys.values() for k in keys),
                 key=lambda i: (i["start"], i["track"]))
    measures = measure_items(plan, cam, workdir)
    opts, no_punch, no_push = _opts(opt)

    results, src_of, budgets, aspects = {}, {}, {}, []
    for sid, keys in solve_keys.items():
        if not keys:
            continue
        sizes = [source_size(by_key[k]["path"]) for k in keys]
        src = min(sizes, key=lambda x: x[1])
        src_of[sid], budgets[sid] = src, zoom_budget(src[1], tl_h)
        aspects.extend([src[0] / src[1]] * len(keys))
        o = dataclasses.replace(opts, aspect=src[0] / src[1])
        results[sid] = solve_base(keys, measures, data["setups"][sid], budgets[sid], o)
    setup_of = {k: sid for sid, keys in solve_keys.items() for k in keys}
    item_rects = {k: r for res in results.values() for k, r in res["items"].items()}
    info = setup_info_from(results, data["setups"])
    if no_punch:
        for v in info.values():
            v["punch_ok"] = False

    flags = [{**f, "setup": sid} for sid, res in results.items() for f in res["flags"]]
    push_ins = [] if no_push else list(data.get("push_ins", []))
    kept_in = []
    for p in push_ins:
        s0, e0 = round(p["start_s"] * fps), round(p["end_s"] * fps)
        host = next((i for i in all_items if i["start"] <= s0 and e0 <= i["end"]), None)
        hk = item_key(host) if host else None
        why = None
        if hk in hand:
            why = "the item is framed by hand and left alone"
        elif host is not None:
            sf = float(host.get("src_fps") or fps)
            want = (host["end"] - host["start"]) * sf / fps
            if abs((host["src_end"] - host["src_start"]) - want) > max(2.0, 0.02 * want):
                why = "the item is retimed: push-in timing in Fusion is only verified at 100% speed"
        if why:
            sid = next((x for x, keys in setups.items() if hk in keys), None)
            flags.append({"key": hk, "setup": sid,
                          "text": f"push-in dropped at {p['start_s']:.1f}-{p['end_s']:.1f} s "
                                  f"({p.get('text', '')!r}): {why}"})
        else:
            kept_in.append(p)

    rects, pushes, cflags = {}, [], []
    if cam:
        budget = min(budgets.values())
        aspect = Counter(round(a, 4) for a in aspects).most_common(1)[0][0]
        cuts = visible_cuts(plan, cam, setup_of)
        rects, pushes, cflags = solve_cuts(plan, cam, cuts, item_rects, measures, setup_of, info,
                                           kept_in, budget, dataclasses.replace(opts, aspect=aspect))
    if no_punch:
        cflags = [f for f in cflags if not f["text"].startswith("no punch-ins on these jump cuts")]
    flags += [{**f, "setup": setup_of.get(f["key"])} for f in cflags]

    for sid, src in src_of.items():
        if abs(src[0] / src[1] - tl_w / tl_h) > ASPECT_TOL * tl_w / tl_h:
            flags.append({"key": None, "setup": sid,
                          "text": f"source {src[0]}x{src[1]} has another aspect than the "
                                  f"{tl_w}x{tl_h} timeline: Zoom/Pan/Tilt use the derived, unmeasured "
                                  f"formula; check the frame in Resolve"})

    solution = _solution(plan, s, data, setups, solve_keys, results, rects, pushes, flags, hand,
                         by_key, measures, src_of, budgets, push_ins, opts)
    (fdir / "solution.json").write_text(json.dumps(solution, indent=1, ensure_ascii=False))
    previews = write_previews(workdir, plan, solution, data)
    write_report(solution, data, gdir / "REPORT.md", previews)
    return solution


def _flag_where(f: dict, by_key: dict, setups: dict, fps: float, video: list[dict]) -> str:
    if f.get("key") in by_key:
        return _where(by_key[f["key"]], fps)
    m = re.search(r"at (\d+(?:\.\d+)?)-", f["text"])
    if m and f["text"].startswith("push-in"):
        fr = round(float(m.group(1)) * fps)
        it = max((i for i in video if i["start"] <= fr < i["end"]), key=lambda i: i["track"], default=None)
        if it:
            return f"{it.get('clip')} at {tc(fr / fps)}"
        return f"timeline at {tc(fr / fps)}"
    sid = f.get("setup")
    keys = setups.get(sid) or []
    if keys:
        return f"setup {sid}: {_where(by_key[keys[0]], fps)}"
    return "timeline"


def _solution(plan, s, data, setups, solve_keys, results, rects, pushes, flags, hand, by_key,
              measures, src_of, budgets, push_ins, opts) -> dict:
    fps = float(plan["timeline"]["fps"])
    tl_w, tl_h = int(plan["timeline"]["width"]), int(plan["timeline"]["height"])
    items = {}
    for sid, keys in solve_keys.items():
        res = results.get(sid)
        for k in keys:
            it, r = by_key[k], rects[k]
            base = res["rect"]
            if k in res["outliers"] or abs(r.w - base.w) > 1e-6 and zoom_of(r) < zoom_of(base):
                kind = "own frame"
            elif zoom_of(r) > zoom_of(base) * 1.005:
                kind = "punch"
            else:
                kind = "base"
            items[k] = {"clip": it.get("clip"), "path": it.get("path"), "track": it["track"],
                        "start": it["start"], "end": it["end"], "match": dec.match_key(it),
                        "setup": sid, "kind": kind, "rect": _rect_json(r), "zoom": round(zoom_of(r), 4),
                        "punch": round(zoom_of(r) / zoom_of(base), 4),
                        "props": _props(r, tl_w, tl_h, src_of[sid]),
                        "timecode": tc(it["start"] / fps)}
    out_pushes = []
    for p in pushes:
        it = by_key[p["key"]]
        sf = float(it.get("src_fps") or fps)
        out_pushes.append({"key": p["key"], "clip": it.get("clip"), "setup": items[p["key"]]["setup"],
                           "start_s": round((it["start"] + p["start_frame"]) / fps, 3),
                           "end_s": round((it["start"] + p["end_frame"]) / fps, 3),
                           "start_frame": p["start_frame"], "end_frame": p["end_frame"],
                           "comp_start": round(p["start_frame"] * sf / fps, 3),
                           "comp_end": round(p["end_frame"] * sf / fps, 3),
                           "size": round(p["from"].w / p["to"].w, 4),
                           "pivot": [round(v, 5) for v in measures[p["key"]]["eyes"]],
                           "from": _rect_json(p["from"]), "to": _rect_json(p["to"]),
                           "text": p.get("text", ""), "reason": p.get("reason", "")})
    video = _video_items(plan)
    out_flags = [{"key": f.get("key"), "setup": f.get("setup"), "text": f["text"],
                  "where": _flag_where(f, by_key, setups, fps, video)} for f in flags]
    out_setups = {}
    for sid, d in data["setups"].items():
        res = results.get(sid)
        out_setups[sid] = {"label": d.get("label"), "reason": d.get("reason", ""),
                           "items": setups.get(sid, []), "keep_out": d.get("keep_out", []),
                           "keep_in": d.get("keep_in", []),
                           "rect": _rect_json(res["rect"]) if res else None,
                           "zoom": round(zoom_of(res["rect"]), 4) if res else None,
                           "budget": round(budgets[sid], 3) if sid in budgets else None,
                           "punch_ok": bool(res["punch_ok"]) if res else False}
    left = []
    for k in sorted(hand, key=lambda k: by_key[k]["start"]):
        it = by_key[k]
        sid = next((x for x, keys in setups.items() if k in keys), None)
        left.append({"key": k, "clip": it.get("clip"), "setup": sid, "timecode": tc(it["start"] / fps),
                     "props": it.get("props") or {}, "fusion_comps": int(it.get("fusion_comps") or 0)})
    return {"timeline": {**plan["timeline"], "version": s.state["current"]["version"]},
            "options": dataclasses.asdict(opts), "setups": out_setups, "items": items,
            "pushes": out_pushes, "proposed_push_ins": push_ins, "hand_framed": left,
            "flags": out_flags}


# ---------------------------------------------------------------- previews and report

def write_previews(workdir: Path, plan: dict, solution: dict, data: dict) -> dict:
    """setups/<id>_before_after.jpg per setup and flags/<n>.jpg per flag. {"setups"|"flags": {id: path}}."""
    fdir, gdir = _paths(workdir)
    out = {"setups": {}, "flags": {}}
    if not preview.available():
        print("warning: ffmpeg not found; previews skipped.", file=sys.stderr)
        return out
    by_key = {item_key(i): i for i in _video_items(plan)}
    shutil.rmtree(gdir / "flags", ignore_errors=True)
    for old in (gdir / "setups").glob("*_before_after.jpg"):
        old.unlink()
    pushes = {p["key"]: p for p in solution["pushes"]}
    try:
        for sid, st in solution["setups"].items():
            if not st["rect"] or not st["items"]:
                continue
            first = next((k for k in st["items"] if k in solution["items"]), st["items"][0])
            frame = item_frame(fdir, plan, by_key[first])
            out["setups"][sid] = str(preview.before_after(
                frame, _rect(st["rect"]), gdir / "setups" / f"{sid}_before_after.jpg",
                st["keep_out"], st["keep_in"], title=f"{sid} {st['label']} {st['zoom']:.2f}x"))
        for n, f in enumerate(solution["flags"], 1):
            key = f.get("key") if f.get("key") in solution["items"] else None
            sid = f.get("setup")
            st = solution["setups"].get(sid) if sid else None
            if key:
                e = solution["items"][key]
                rect, it = _rect(e["rect"]), by_key[key]
                inner = _rect(pushes[key]["to"]) if key in pushes else None
            elif st and st["rect"] and st["items"]:
                rect, it, inner = _rect(st["rect"]), by_key[st["items"][0]], None
            else:
                continue
            st = solution["setups"].get(sid or "") or {"keep_out": [], "keep_in": []}
            out["flags"][n] = str(preview.before_after(
                item_frame(fdir, plan, it), rect, gdir / "flags" / f"{n}.jpg", st["keep_out"],
                st["keep_in"], inner=inner, title=f"flag {n}: {f['where']}"))
    except RuntimeError as e:
        print(f"warning: previews incomplete: {e}", file=sys.stderr)
    return out


def write_report(sol: dict, data: dict, path: Path, previews: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    t = sol["timeline"]
    fps = float(t["fps"])
    o = sol["options"]
    L = ["# Framing report", "",
         f"Timeline v{t['version']:02d} `{t['name']}`, {t['width']}x{t['height']}. "
         f"Punch {o['punch']:.2f}x (min {o['punch_min']:.2f}x), push {o['push']:.2f}x. "
         f"{len(sol['items'])} items framed, {sum(e['kind'] == 'punch' for e in sol['items'].values())} "
         f"punch-ins, {len(sol['pushes'])} push-ins, {len(sol['hand_framed'])} hand-framed left alone, "
         f"{len(sol['flags'])} flags.", "", "## Setups", ""]
    for sid, st in sol["setups"].items():
        zoom = f"{st['zoom']:.2f}x" if st["zoom"] else "not framed"
        L.append(f"### {sid}: {st['label']}, base zoom {zoom}")
        L.append("")
        L.append(f"- Reason: {st['reason'] or '(none given)'}")
        L.append(f"- Items: {len(st['items'])}" + (f", budget {st['budget']:.2f}x" if st["budget"] else "")
                 + ("" if st["punch_ok"] else ", no room for punch-ins"))
        if st["keep_out"]:
            L.append(f"- Keep out: {st['keep_out']}")
        if st["keep_in"]:
            L.append(f"- Keep in: {st['keep_in']}")
        if sid in previews.get("setups", {}):
            L.append(f"- Preview: setups/{sid}_before_after.jpg")
        L.append("")
    punches = [(k, e) for k, e in sol["items"].items() if e["kind"] == "punch"]
    L += ["## Punch-ins", ""]
    if punches:
        L += ["| Clip | Timeline | Setup | Zoom | Punch |", "|---|---|---|---|---|"]
        L += [f"| {e['clip']} | {e['timecode']} | {e['setup']} | {e['zoom']:.2f}x | {e['punch']:.2f}x |"
              for k, e in sorted(punches, key=lambda x: x[1]["start"])]
    else:
        L.append("None.")
    L += ["", "## Push-ins", ""]
    kept = {(round(p["start_s"], 2), p["text"]): p for p in sol["pushes"]}
    proposed = sol.get("proposed_push_ins") or []
    if not proposed:
        L.append("None proposed.")
    for p in sorted(proposed, key=lambda p: p["start_s"]):
        k = next((v for (s, txt), v in kept.items()
                  if txt == p.get("text", "") and abs(s - p["start_s"]) < 1 / fps + 1e-6), None)
        L.append(f"- **{tc(p['start_s'])}-{tc(p['end_s'])}** \"{p.get('text', '')}\"")
        L.append(f"  - Why: {p.get('reason', '')}")
        if k:
            L.append(f"  - Kept: {k['clip']}, {k['size']:.2f}x on the eyes")
        else:
            why = next((f["text"].split("): ", 1)[-1] for f in sol["flags"]
                        if f["text"].startswith("push-in dropped") and repr(p.get("text", "")) in f["text"]),
                       "dropped")
            L.append(f"  - Dropped: {why}")
    L += ["", "## Left alone (hand-framed)", ""]
    if sol["hand_framed"]:
        for h in sol["hand_framed"]:
            props = ", ".join(f"{k} {v}" for k, v in h["props"].items()) or "default values"
            comps = f", {h['fusion_comps']} Fusion comp(s)" if h["fusion_comps"] else ""
            L.append(f"- {h['key']} {h['clip']} at {h['timecode']} (setup {h['setup']}): {props}{comps}")
    else:
        L.append("None.")
    L += ["", "## Flags", ""]
    if sol["flags"]:
        for n, f in enumerate(sol["flags"], 1):
            pv = f" (preview: flags/{n}.jpg)" if n in previews.get("flags", {}) else ""
            L.append(f"{n}. **{f['where']}**: {f['text']}{pv}")
    else:
        L.append("None.")
    path.write_text("\n".join(L) + "\n")


def solve(opt) -> int:
    workdir = Path(opt.workdir).resolve()
    s = rvs.Session(workdir, connect_now=False)
    s._require_state()
    sol = run_solve(s, opt, workdir)
    _print_summary(sol, workdir)
    print("dry run: nothing changed in Resolve. Run apply to build.")
    return 0


def _print_summary(sol: dict, workdir: Path) -> None:
    _, gdir = _paths(workdir)
    zooms = [st["zoom"] for st in sol["setups"].values() if st["zoom"]]
    print(f"{len(zooms)} setups (zoom {min(zooms, default=0):.2f}-{max(zooms, default=0):.2f}), "
          f"{sum(e['kind'] == 'punch' for e in sol['items'].values())} punch-ins, "
          f"{len(sol['pushes'])} push-ins, {len(sol['hand_framed'])} hand-framed left alone, "
          f"{len(sol['flags'])} flags. Report: {gdir / 'REPORT.md'}")


# ---------------------------------------------------------------- apply

def apply(opt) -> int:
    workdir = Path(opt.workdir).resolve()
    s = _session(workdir, need_resolve=True)
    apply_resolve.check_scaling(s.current_timeline(), s.project)
    _status(s)
    sol = run_solve(s, opt, workdir)
    _print_summary(sol, workdir)
    entry, issues = apply_resolve.apply(s, sol)
    print(f"v{entry['version']:02d} saved: {entry['timeline']}")
    if issues:
        print(f"{len(issues)} issues added to {s.checklist_path.name}: " + ", ".join(i["id"] for i in issues))
    return 0


# ---------------------------------------------------------------- CLI

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("prepare", "solve", "apply"):
        p = sub.add_parser(name)
        p.add_argument("--workdir", default=os.environ.get("RVS_WORKDIR", "."))
        if name != "prepare":
            p.add_argument("--no-punch", action="store_true", help="no punch-ins on jump cuts")
            p.add_argument("--no-push", action="store_true", help="no push-ins")
            p.add_argument("--punch", type=float, help="punch-in factor (default 1.15)")
            p.add_argument("--push", type=float, help="push-in factor (default 1.06)")
    opt = ap.parse_args(argv)
    try:
        return {"prepare": prepare, "solve": solve, "apply": apply}[opt.cmd](opt)
    except (FramingError, rvs.RvsError, apply_resolve.ApplyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
