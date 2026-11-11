#!/usr/bin/env python3
"""Resolve spike for video-framing: calibration, Dynamic Zoom, Fusion push-in.

Runs only in a scratch Resolve project (name starts with "rvs_scratch"), never in a real edit.
Re-run it after a Resolve update to check that the notes in references/resolve-notes.md still hold.

    python3 spike_resolve.py enter                 # remember the open project, load/create rvs_scratch_framing
    python3 spike_resolve.py calibrate --source CLIP.MOV
    python3 spike_resolve.py dynzoom
    python3 spike_resolve.py fusion --source CLIP.MOV
    python3 spike_resolve.py leave                 # reload the project that was open before `enter`

Every command except enter/leave refuses to run unless the open project is a scratch project.
Outputs (test chart, stills, results.json) go to --workdir (default: a folder in the system temp dir).
The source clip is only read and imported into the scratch media pool.

Stdlib plus the Resolve scripting API. ffmpeg/ffprobe (on PATH) make the test chart and read pixels.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "resolve-bridge" / "scripts"))
import rvs  # noqa: E402

SCRATCH_PREFIX = "rvs_scratch"
SCRATCH_PROJECT = "rvs_scratch_framing"
TL_W, TL_H, TL_FPS = 1920, 1080, "24"
SRC_W, SRC_H = 3840, 2160
GRID = 10                      # chart dots at i/GRID, i = 1..GRID-1, both axes
DOT = 12                       # dot size in chart pixels (centre exactly at i/GRID in normalised coords)


# ---------------------------------------------------------------- helpers

def workdir(args) -> Path:
    d = Path(args.workdir).resolve() if args.workdir else Path(tempfile.gettempdir()) / "rvs_spike_framing"
    d.mkdir(parents=True, exist_ok=True)
    return d


def scratch():
    resolve, project = rvs.connect()
    if not project.GetName().startswith(SCRATCH_PREFIX):
        sys.exit(f"refusing to run: open project is {project.GetName()!r}, not a {SCRATCH_PREFIX}* project. "
                 f"Run `{Path(__file__).name} enter` first.")
    return resolve, project


def run(cmd):
    return subprocess.run(cmd, check=True, capture_output=True).stdout


def read_gray(path: Path):
    """Return (w, h, bytes) of an image as 8-bit gray."""
    info = json.loads(run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                           "stream=width,height", "-of", "json", str(path)]))["streams"][0]
    raw = run(["ffmpeg", "-v", "error", "-i", str(path), "-frames:v", "1", "-f", "rawvideo",
               "-pix_fmt", "gray", "-"])
    return info["width"], info["height"], raw


def blobs(w, h, px, thresh=128):
    """Centroids (x, y in pixel-centre coords) of bright connected blobs."""
    seen = bytearray(w * h)
    out = []
    for i in range(w * h):
        if px[i] < thresh or seen[i]:
            continue
        stack = [i]; seen[i] = 1
        sx = sy = sm = 0.0
        while stack:
            j = stack.pop()
            v = px[j]; x, y = j % w, j // w
            sx += x * v; sy += y * v; sm += v
            for k in (j - 1, j + 1, j - w, j + w):
                if 0 <= k < w * h and not seen[k] and px[k] >= thresh and abs(k % w - x) <= 1:
                    seen[k] = 1; stack.append(k)
        out.append((sx / sm + 0.5, sy / sm + 0.5, sm))
    return out


def fit_line(xs, ys):
    n = len(xs); mx = sum(xs) / n; my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    a = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return a, my - a * mx


def export_still(project, timeline, frame, path: Path):
    """Export the timeline frame `frame` (offset from timeline start) as a PNG still."""
    if path.exists():
        path.unlink()
    fps = float(timeline.GetSetting("timelineFrameRate"))
    tc = frames_to_tc(timeline.GetStartFrame() + frame, round(fps))
    if not timeline.SetCurrentTimecode(tc):
        raise RuntimeError(f"SetCurrentTimecode({tc}) failed")
    if not project.ExportCurrentFrameAsStill(str(path)):
        raise RuntimeError("ExportCurrentFrameAsStill failed")
    for _ in range(50):
        if path.exists() and path.stat().st_size > 0:
            time.sleep(0.2)
            return path
        time.sleep(0.1)
    raise RuntimeError(f"still not written: {path}")


def frames_to_tc(f, fps):
    return f"{f // (3600 * fps):02d}:{f // (60 * fps) % 60:02d}:{f // fps % 60:02d}:{f % fps:02d}"


def make_chart(d: Path) -> Path:
    """A 3840x2160 black chart with white DOTxDOT dots centred exactly on (i/GRID, j/GRID)."""
    chart = d / "rvs_chart_4k.mp4"
    if chart.exists():
        return chart
    boxes = [f"drawbox=x={SRC_W * i // GRID - DOT // 2}:y={SRC_H * j // GRID - DOT // 2}:w={DOT}:h={DOT}"
             f":color=white:t=fill" for i in range(1, GRID) for j in range(1, GRID)]
    (d / "chart_filter.txt").write_text(",".join(boxes))
    run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=black:s={SRC_W}x{SRC_H}:r=60:d=4",
         "-filter_script:v", str(d / "chart_filter.txt"), "-c:v", "libx264", "-preset", "ultrafast", "-crf", "4",
         "-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
         str(chart)])
    return chart


def import_clip(project, path: Path):
    mp = project.GetMediaPool()
    for clip in mp.GetRootFolder().GetClipList() or []:
        if clip.GetClipProperty("File Path") == str(path):
            return clip
    items = mp.ImportMedia([str(path)])
    if not items:
        raise RuntimeError(f"import failed: {path}")
    return items[0]


def new_timeline(project, name, clip, src_frames, width=TL_W, height=TL_H, src_start=0):
    """Fresh timeline `name` (replacing an old one of that name in the scratch project) with `clip` on V1."""
    mp = project.GetMediaPool()
    old = rvs.find_timeline(project, name)
    if old:
        mp.DeleteTimelines([old])
    tl = mp.CreateEmptyTimeline(name)
    project.SetCurrentTimeline(tl)
    tl.SetSetting("useCustomSettings", "1")
    for k, v in (("timelineFrameRate", TL_FPS), ("timelineResolutionWidth", str(width)),
                 ("timelineResolutionHeight", str(height)), ("timelineInputResMismatchBehavior", "scaleToCrop")):
        if not tl.SetSetting(k, v):
            print(f"warning: timeline setting {k}={v} refused", file=sys.stderr)
    mp.AppendToTimeline([{"mediaPoolItem": clip, "startFrame": src_start, "endFrame": src_frames - 1}])
    item = tl.GetItemListInTrack("video", 1)[0]
    return tl, item


def set_props(item, props):
    full = {"ZoomGang": False, "ZoomX": 1.0, "ZoomY": 1.0, "Pan": 0.0, "Tilt": 0.0}
    full.update(props)
    ok = item.SetProperties(full) if hasattr(item, "SetProperties") else item.SetProperty(full)
    if not ok:
        for k, v in full.items():
            item.SetProperty(k, v)
    got = {k: item.GetProperty(k) for k in full}
    return got


# ---------------------------------------------------------------- formula (filled in from the fit)

def rect_to_props(cx, cy, w, tl_w=TL_W, tl_h=TL_H, src_w=SRC_W, src_h=SRC_H):
    """Crop rect in normalised source coords (centre cx, cy; width w; origin top-left; source aspect)
    -> item properties under 'scaleToCrop'. See references/resolve-notes.md."""
    zoom = 1.0 / w
    fit = max(tl_w / src_w, tl_h / src_h)            # scale full frame with crop
    pan = -(cx - 0.5) * src_w * fit * zoom           # timeline pixels, +x moves the image right
    tilt = (cy - 0.5) * src_h * fit * zoom           # timeline pixels, +y moves the image up
    return {"ZoomX": zoom, "ZoomY": zoom, "Pan": pan, "Tilt": tilt}


def predict(u, v, p, tl_w=TL_W, tl_h=TL_H, src_w=SRC_W, src_h=SRC_H):
    """Where source point (u, v) (normalised, top-left origin) lands in the timeline frame, in pixels."""
    fit = max(tl_w / src_w, tl_h / src_h)
    x = tl_w / 2 + (u - 0.5) * src_w * fit * p["ZoomX"] + p["Pan"]
    y = tl_h / 2 + (v - 0.5) * src_h * fit * p["ZoomY"] - p["Tilt"]
    return x, y


# ---------------------------------------------------------------- calibrate

FIT_CASES = [
    {"ZoomX": 1.0, "ZoomY": 1.0, "Pan": 0.0, "Tilt": 0.0},
    {"ZoomX": 1.5, "ZoomY": 1.5, "Pan": 0.0, "Tilt": 0.0},
    {"ZoomX": 1.0, "ZoomY": 1.0, "Pan": 100.0, "Tilt": 0.0},
    {"ZoomX": 1.0, "ZoomY": 1.0, "Pan": 0.0, "Tilt": 100.0},
    {"ZoomX": 2.0, "ZoomY": 2.0, "Pan": -150.0, "Tilt": 80.0},
    {"ZoomX": 1.3, "ZoomY": 1.1, "Pan": 37.0, "Tilt": -61.0},
]
VERIFY_RECTS = [(0.40, 0.45, 0.60), (0.62, 0.38, 0.75), (0.30, 0.65, 0.50)]


def measure(still: Path, props, tl_w=TL_W, tl_h=TL_H):
    """Match chart dots in `still` to source dots; return list of (u, v, x, y)."""
    w, h, px = read_gray(still)
    found = blobs(w, h, px)
    pairs = []
    for i in range(1, GRID):
        for j in range(1, GRID):
            u, v = i / GRID, j / GRID
            px_, py_ = predict(u, v, props, tl_w, tl_h)
            if not (4 < px_ < w - 4 and 4 < py_ < h - 4):
                continue
            best = min(found, key=lambda b: (b[0] - px_) ** 2 + (b[1] - py_) ** 2, default=None)
            if best and math.hypot(best[0] - px_, best[1] - py_) < 40:
                pairs.append((u, v, best[0], best[1]))
    return pairs


def grid_scale(still: Path, near=None):
    """Effective zoom of the chart in `still`: median horizontal dot spacing / spacing at zoom 1.
    With `near` (x, y), also return the blob closest to that pixel."""
    w, h, px = read_gray(still)
    found = blobs(w, h, px)
    gaps = []
    for b in found:
        right = [o[0] - b[0] for o in found if o[0] > b[0] + 2 and abs(o[1] - b[1]) < 3]
        if right:
            gaps.append(min(right))
    gaps.sort()
    scale = gaps[len(gaps) // 2] / (w / GRID) if gaps else None
    out = {"zoom": scale, "dots": len(found)}
    if near and found:
        b = min(found, key=lambda o: (o[0] - near[0]) ** 2 + (o[1] - near[1]) ** 2)
        out["blob_near"] = [round(b[0], 3), round(b[1], 3)]
    return out


def fit_case(pairs):
    ax, bx = fit_line([p[0] for p in pairs], [p[2] for p in pairs])
    ay, by = fit_line([p[1] for p in pairs], [p[3] for p in pairs])
    return {"x_per_u": ax, "x_at_u0": bx, "y_per_v": ay, "y_at_v0": by, "n": len(pairs)}


def cmd_calibrate(args):
    resolve, project = scratch()
    d = workdir(args)
    results = {"resolve": resolve.GetVersionString(), "fit": [], "verify": [], "res": []}
    chart = import_clip(project, make_chart(d))
    tl, item = new_timeline(project, "spike_cal", chart, 120)
    results["timeline_settings"] = {k: tl.GetSetting(k) for k in (
        "timelineResolutionWidth", "timelineResolutionHeight", "timelineFrameRate",
        "timelineInputResMismatchBehavior", "useCustomSettings")}
    print("timeline:", results["timeline_settings"])

    def one(tag, props, tl_w=TL_W, tl_h=TL_H):
        got = set_props(item, props)
        still = export_still(project, tl, 5, d / f"cal_{tag}.png")
        pairs = measure(still, props, tl_w, tl_h)
        f = fit_case(pairs)
        errs = [math.hypot(x - predict(u, v, props, tl_w, tl_h)[0], y - predict(u, v, props, tl_w, tl_h)[1])
                for u, v, x, y in pairs]
        row = {"tag": tag, "set": props, "got": got, "fit": f,
               "max_err_px": max(errs), "mean_err_px": sum(errs) / len(errs)}
        print(json.dumps(row))
        return row

    for n, props in enumerate(FIT_CASES):
        results["fit"].append(one(f"fit{n}", props))
    for n, (cx, cy, w) in enumerate(VERIFY_RECTS):
        props = rect_to_props(cx, cy, w)
        row = one(f"rect{n}", props)
        # where did the rect centre land, and how wide is the rect on screen?
        row["rect"] = [cx, cy, w]
        f = row["fit"]
        row["rect_centre_px"] = [f["x_per_u"] * cx + f["x_at_u0"], f["y_per_v"] * cy + f["y_at_v0"]]
        row["rect_width_px"] = f["x_per_u"] * w
        results["verify"].append(row)

    # resolution dependence: same props on a 1280x720 timeline
    tl2, item2 = new_timeline(project, "spike_cal_720", chart, 120, 1280, 720)
    for n, props in enumerate([FIT_CASES[4], rect_to_props(0.62, 0.38, 0.75, 1280, 720)]):
        set_props(item2, props)
        still = export_still(project, tl2, 5, d / f"cal720_{n}.png")
        pairs = measure(still, props, 1280, 720)
        errs = [math.hypot(x - predict(u, v, props, 1280, 720)[0], y - predict(u, v, props, 1280, 720)[1])
                for u, v, x, y in pairs]
        row = {"tag": f"720_{n}", "set": props, "fit": fit_case(pairs), "max_err_px": max(errs)}
        print(json.dumps(row))
        results["res"].append(row)
    project.SetCurrentTimeline(tl)

    if args.source:
        results["footage"] = verify_footage(project, d, Path(args.source))
    (d / "calibration.json").write_text(json.dumps(results, indent=1))
    print("wrote", d / "calibration.json")


def zstats(a):
    m = sum(a) / len(a); s = math.sqrt(sum((x - m) ** 2 for x in a) / len(a)) or 1.0
    return [(x - m) / s for x in a]


def best_shift(ref, img, w, h, r=6, step=2):
    """Shift (dx, dy) of img against ref with the highest correlation, refined to sub-pixel with a parabola.
    Returns (corr, dx, dy)."""
    c = {}
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            s = n = 0
            for y in range(r + 20, h - r - 20, step):
                row, row2 = y * w, (y + dy) * w + dx
                for x in range(r + 20, w - r - 20, step):
                    s += ref[row + x] * img[row2 + x]
                    n += 1
            c[dx, dy] = s / n
    (bx, by), best = max(c.items(), key=lambda kv: kv[1])

    def sub(m, z, p):
        den = m - 2 * z + p
        return 0.0 if den == 0 else 0.5 * (m - p) / den
    fx = bx + (sub(c[bx - 1, by], best, c[bx + 1, by]) if abs(bx) < r else 0.0)
    fy = by + (sub(c[bx, by - 1], best, c[bx, by + 1]) if abs(by) < r else 0.0)
    return best, fx, fy


def verify_footage(project, d: Path, src: Path):
    """Real 4K footage: compare Resolve stills against an ffmpeg crop of source frame 0 per the formula."""
    clip = import_clip(project, src)
    tl, item = new_timeline(project, "spike_cal_footage", clip, 120)
    frame0 = d / "footage_frame0.png"
    run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-frames:v", "1", "-update", "1", str(frame0)])
    out = []
    for n, (cx, cy, w) in enumerate(VERIFY_RECTS[:2] + [(0.5, 0.5, 1.0)]):
        props = rect_to_props(cx, cy, w)
        set_props(item, props)
        still = export_still(project, tl, 0, d / f"footage_{n}.png")
        cw, ch = SRC_W * w, SRC_H * w
        ref = d / f"footage_{n}_ref.png"
        run(["ffmpeg", "-v", "error", "-y", "-i", str(frame0), "-vf",
             f"crop={cw:.3f}:{ch:.3f}:{cx * SRC_W - cw / 2:.3f}:{cy * SRC_H - ch / 2:.3f}:exact=1,"
             f"scale={TL_W}:{TL_H}:flags=bicubic", "-update", "1", str(ref)])
        # compare at half size to keep pure-Python correlation fast; result is doubled back
        small = []
        for p in (ref, still):
            s = p.with_name(p.stem + "_half.png")
            run(["ffmpeg", "-v", "error", "-y", "-i", str(p), "-vf", "scale=960:540:flags=area,sobel",
                 "-update", "1", str(s)])
            small.append(read_gray(s))
        (w2, h2, a), (_, _, b) = small
        corr, dx, dy = best_shift(zstats(a), zstats(b), w2, h2)
        row = {"rect": [cx, cy, w], "props": props, "shift_px_1080": [round(2 * dx, 2), round(2 * dy, 2)], "corr": corr}
        print(json.dumps(row))
        out.append(row)
    return out


# ---------------------------------------------------------------- dynamic zoom

DZ_KEYS = ["DynamicZoomStart", "DynamicZoomEnd", "DynamicZoomStartRect", "DynamicZoomEndRect",
           "DynamicZoomStartZoom", "DynamicZoomEndZoom", "DynamicZoomStartPan", "DynamicZoomEndPan",
           "DynamicZoomStartPosition", "DynamicZoomEndPosition", "DynamicZoomStartX", "DynamicZoomStartY",
           "DynamicZoomStartScale", "DynamicZoomEndScale", "DynamicZoomSwap", "DynamicZoomMode"]


def cmd_dynzoom(args):
    resolve, project = scratch()
    d = workdir(args)
    chart = import_clip(project, make_chart(d))
    tl, item = new_timeline(project, "spike_dynzoom", chart, 240)
    res = {"resolve": resolve.GetVersionString()}
    props = item.GetProperty()
    res["dz_keys_in_GetProperty"] = sorted(k for k in props if "dynamic" in k.lower())
    res["enable"] = item.SetProperty("DynamicZoomEnabled", True)
    res["ease_set"] = item.SetProperty("DynamicZoomEase", 3)
    res["after"] = {k: item.GetProperty(k) for k in ("DynamicZoomEnabled", "DynamicZoomEase")}
    tries = {}
    for k in DZ_KEYS:
        for v in (1.2, [0.1, 0.1, 0.9, 0.9], {"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8}):
            try:
                ok = item.SetProperty(k, v)
            except Exception as e:  # noqa: BLE001
                ok = f"error {e}"
            tries.setdefault(k, []).append(ok)
            try:
                ok2 = item.SetProperties({k: v})
            except Exception as e:  # noqa: BLE001
                ok2 = f"error {e}"
            tries[k].append(ok2)
    res["set_attempts"] = tries
    res["after_props_dz"] = {k: v for k, v in item.GetProperty().items() if "dynamic" in k.lower()}
    # does Dynamic Zoom with default rects change the picture over time?
    a = export_still(project, tl, 2, d / "dz_start.png")
    b = export_still(project, tl, int(tl.GetEndFrame() - tl.GetStartFrame()) - 3, d / "dz_end.png")
    res["start_still"], res["end_still"] = grid_scale(a), grid_scale(b)
    # and with Dynamic Zoom disabled again
    item.SetProperty("DynamicZoomEnabled", False)
    res["disabled_end_still"] = grid_scale(export_still(project, tl, int(tl.GetEndFrame() - tl.GetStartFrame()) - 3,
                                                        d / "dz_end_off.png"))
    print(json.dumps(res, indent=1))
    (d / "dynzoom.json").write_text(json.dumps(res, indent=1))


# ---------------------------------------------------------------- fusion push-in

def item_to_comp_time(item_frame, src_fps, tl_fps):
    """Comp time of an item-relative timeline frame. Comp time 0 is the item's first frame and
    counts SOURCE frames (a 60 fps clip on a 24 fps timeline runs 2.5 comp frames per timeline frame)."""
    return item_frame * src_fps / tl_fps


def add_push_in(item, t0, t1, z0, z1, eye_u, eye_v, name="rvs_pushin"):
    """Fusion Transform on `item`: Size z0 -> z1 between comp times t0..t1 (see item_to_comp_time),
    ease in and out, scaled about source point (eye_u, eye_v) (normalised, top-left origin).
    Only these call forms are known to reach the Edit page render (see resolve-notes.md)."""
    comp = item.AddFusionComp()
    if comp is None:
        raise RuntimeError("AddFusionComp returned None")
    mi, mo = comp.FindTool("MediaIn1"), comp.FindTool("MediaOut1")
    tr = comp.AddTool("Transform", -32768, -32768)
    tr.SetAttrs({"TOOLS_Name": name})
    tr.ConnectInput("Input", mi)
    mo.ConnectInput("Input", tr)
    tr.SetInput("Pivot", {1: eye_u, 2: 1.0 - eye_v})        # Fusion y runs bottom-up; Center stays 0.5, 0.5
    tr.AddModifier("Size", "BezierSpline")
    tr.SetInput("Size", z0, t0)
    tr.SetInput("Size", z1, t1)
    spline = tr.Size.GetConnectedOutput().GetTool()
    dt = (t1 - t0) / 3.0                                     # handles are relative (dt, dvalue); flat = eased
    spline.SetKeyFrames({t0: {1: z0, "RH": {1: dt, 2: 0.0}},
                         t1: {1: z1, "LH": {1: -dt, 2: 0.0}}}, True)
    return comp, tr, spline


def cmd_fusion(args):
    resolve, project = scratch()
    d = workdir(args)
    chart = import_clip(project, make_chart(d))
    res = {"resolve": resolve.GetVersionString()}
    src_fps, tl_fps = 60.0, float(TL_FPS)
    eye = (0.6, 0.3)                                        # a chart dot: pixel (1152, 324) at zoom 1
    eye_px = (TL_W * eye[0], TL_H * eye[1])

    # 1. eased push-in 1.0 -> 2.0 (large so the measurement is clear)
    tl, item = new_timeline(project, "spike_fusion", chart, 240)
    res["comp_count_before"] = item.GetFusionCompCount()
    t0, t1 = 0.0, item_to_comp_time(48, src_fps, tl_fps)      # timeline frames 0..48 of the item
    comp, tr, spline = add_push_in(item, t0, t1, 1.0, 2.0, *eye)
    res["comp_count_after"] = item.GetFusionCompCount()
    res["comp_names"] = item.GetFusionCompNameList()
    res["tool_found_by_name"] = bool(comp.FindTool("rvs_pushin"))
    res["keys"] = str(spline.GetKeyFrames())
    samples = []
    for f in (0, 6, 12, 24, 36, 48, 60):
        t = item_to_comp_time(f, src_fps, tl_fps)
        g = grid_scale(export_still(project, tl, f, d / f"fus_{f}.png"), eye_px)
        u = min(1.0, max(0.0, t / t1))
        # expected: cubic Bezier with flat handles at 1/3 of the span; solve x(s) = u, then y(s)
        lo, hi = 0.0, 1.0
        for _ in range(60):
            m = (lo + hi) / 2
            x = 3 * m * (1 - m) ** 2 * (1 / 3) + 3 * m * m * (1 - m) * (2 / 3) + m ** 3
            lo, hi = (m, hi) if x < u else (lo, m)
        y = 3 * lo * lo * (1 - lo) + lo ** 3
        samples.append({"tl_frame": f, "comp_time": t, "spline": tr.GetInput("Size", t),
                        "expected_ease": 1.0 + y, "measured_zoom": g["zoom"], "eye_px": g.get("blob_near")})
    res["push_in"] = samples

    # 2. comp time origin: item trimmed to start at source frame 60
    tl2, item2 = new_timeline(project, "spike_fusion_trim", chart, 240, src_start=60)
    comp2, tr2, _ = add_push_in(item2, 0.0, 100.0, 1.0, 2.0, 0.5, 0.5)
    mi = comp2.FindTool("MediaIn1").GetAttrs()
    res["trim_test"] = {"item_source_start": item2.GetSourceStartFrame(),
                        "mediain_trim_in": mi.get("TOOLI_Clip_TrimIn"),
                        "comp_render_range": [comp2.GetAttrs()["COMPN_RenderStart"], comp2.GetAttrs()["COMPN_RenderEnd"]],
                        "zoom_at_tl_frame_0": grid_scale(export_still(project, tl2, 0, d / "fus_trim_0.png"))["zoom"],
                        "zoom_at_tl_frame_20": grid_scale(export_still(project, tl2, 20, d / "fus_trim_20.png"))["zoom"]}

    # 3. order of operations: constant Fusion Size 2 about the eye + Inspector zoom/pan/tilt
    tl3, item3 = new_timeline(project, "spike_fusion_order", chart, 240)
    insp = {"ZoomX": 1.5, "ZoomY": 1.5, "Pan": 120.0, "Tilt": -60.0}
    set_props(item3, insp)
    comp3 = item3.AddFusionComp()
    tr3 = comp3.AddTool("Transform", -32768, -32768)
    tr3.ConnectInput("Input", comp3.FindTool("MediaIn1")); comp3.FindTool("MediaOut1").ConnectInput("Input", tr3)
    tr3.SetInput("Pivot", {1: eye[0], 2: 1 - eye[1]}); tr3.SetInput("Size", 2.0)
    still = export_still(project, tl3, 10, d / "fus_order.png")
    # Fusion first (source space): source (0.5, 0.4) -> (0.4, 0.5) after Size 2 about (0.6, 0.3)
    exp_eye = predict(eye[0], eye[1], insp)
    exp_other = predict(0.4, 0.5, insp)
    res["order_test"] = {"inspector": insp, "fusion_size": 2.0, "pivot_src": list(eye),
                         "expected_if_fusion_first": {"eye": exp_eye, "src(0.5,0.4)": exp_other},
                         "measured": {"eye": grid_scale(still, exp_eye), "src(0.5,0.4)": grid_scale(still, exp_other)}}

    # 4. Center keyframes via XYPath (for a moving anchor)
    tl4, item4 = new_timeline(project, "spike_fusion_center", chart, 240)
    comp4 = item4.AddFusionComp()
    tr4 = comp4.AddTool("Transform", -32768, -32768)
    tr4.ConnectInput("Input", comp4.FindTool("MediaIn1")); comp4.FindTool("MediaOut1").ConnectInput("Input", tr4)
    res["center_xypath"] = {"modifier": tr4.AddModifier("Center", "XYPath"),
                            "set0": tr4.SetInput("Center", {1: 0.5, 2: 0.5}, 0),
                            "set1": tr4.SetInput("Center", {1: 0.6, 2: 0.5}, 120)}
    # timeline frame 12 = comp time 30 -> Center x 0.525 -> image moves +0.025*1920 = +48 px
    res["center_xypath"]["value_at_30"] = tr4.GetInput("Center", 30)
    res["center_xypath"]["dot_near_(1008,540)_at_tl12"] = grid_scale(
        export_still(project, tl4, 12, d / "fus_center_12.png"), (1008, 540)).get("blob_near")
    project.SetCurrentTimeline(tl)
    (d / "fusion.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps(res, indent=1, default=str))


# ---------------------------------------------------------------- enter / leave

def cmd_enter(args):
    resolve, project = rvs.connect()
    d = workdir(args)
    prev = project.GetName()
    pm = resolve.GetProjectManager()
    if not prev.startswith(SCRATCH_PREFIX):
        (d / "previous_project.txt").write_text(prev)
    print("previous project:", prev)
    p = pm.LoadProject(SCRATCH_PROJECT) or pm.CreateProject(SCRATCH_PROJECT)
    if not p:
        sys.exit("could not load or create " + SCRATCH_PROJECT)
    p.SetSetting("timelineInputResMismatchBehavior", "scaleToCrop")
    print("now in:", pm.GetCurrentProject().GetName())


def cmd_leave(args):
    resolve, project = scratch()
    d = workdir(args)
    prev = (d / "previous_project.txt").read_text().strip()
    resolve.GetProjectManager().SaveProject()
    ok = resolve.GetProjectManager().LoadProject(prev)
    print("reloaded", prev, "->", bool(ok), resolve.GetProjectManager().GetCurrentProject().GetName())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workdir")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("enter", "leave", "dynzoom"):
        sub.add_parser(name)
    for name in ("calibrate", "fusion"):
        sp = sub.add_parser(name)
        sp.add_argument("--source", help="4K clip, read only (calibrate: real-footage check)")
    args = ap.parse_args()
    {"enter": cmd_enter, "leave": cmd_leave, "calibrate": cmd_calibrate,
     "dynzoom": cmd_dynzoom, "fusion": cmd_fusion}[args.cmd](args)


if __name__ == "__main__":
    main()
