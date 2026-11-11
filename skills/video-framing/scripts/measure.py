"""Measuring for video-framing: where the eyes, face, head and hands are in each on-camera item.

Vision runs in a small Swift helper (`face_probe.swift`), compiled once per project. ffmpeg extracts
frames at a low rate; `summarize` turns the per-frame detections into one robust summary per item.

Coordinates are normalised source coordinates 0..1, origin top-left. A box is (left, top, right, bottom).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import statistics
import subprocess
import tempfile
from pathlib import Path

from timeline import item_key

SWIFT_SRC = Path(__file__).resolve().parent / "face_probe.swift"
FRAME_WIDTH = 960
HDR_TRANSFERS = ("arib-std-b67", "smpte2084")
HAND_WRIST_MAX = 0.2   # a hand box centre this close to a body-pose wrist belongs to the speaker
HAND_FACE_MAX = 0.5    # without wrists: a hand this close to the face belongs to the speaker
RESTING = 0.93         # a hand box reaching this far down rests at the source's bottom edge
# Part of every cache key. Bump it whenever face_probe.swift or summarize changes what is measured.
# 2: hands resting at the source's bottom edge no longer set the extent's bottom.
# 3: the resting threshold moved from 0.98 to 0.93 of the source height.
# 4: `extent_all`, the extent with resting hands counted (for setups marked bottom_hands: gesture).
# 5: the resting filter applies to hand boxes only; head bottoms always count.
# 6: per-edge extent drops one frame (the single-frame outlier), not the 2nd/98th percentile.
MEASURE_VERSION = 6


# ---------------------------------------------------------------- summaries (pure)

def _edge(values: list[float], low: bool) -> float:
    """The second-most-extreme value (the lowest for `low`, else the highest): one frame per edge,
    the single-frame outlier, is dropped. With one value, that value."""
    s = sorted(values, reverse=not low)
    return s[1] if len(s) > 1 else s[0]


def _dist(a, b) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def _speaker_hands(f: dict) -> list:
    """The frame's hand boxes that belong to the speaker, not to someone in the background.

    A hand counts when its centre is near a body-pose wrist. When fewer than two wrists were found,
    the hand's own wrist may be the missing one, so a hand near the face counts too, and with no face
    to judge by every hand counts. Only with both wrists found is a hand far from both dropped.
    """
    joints = f.get("joints") or {}
    wrists = [joints[w] for w in ("left_wrist", "right_wrist") if joints.get(w)]
    face = f.get("face")
    face_c = ((face[0] + face[2]) / 2, (face[1] + face[3]) / 2) if face else None
    keep = []
    for h in f.get("hands") or []:
        c = ((h[0] + h[2]) / 2, (h[1] + h[3]) / 2)
        if any(_dist(c, w) <= HAND_WRIST_MAX for w in wrists):
            keep.append(h)
        elif len(wrists) < 2 and (face_c is None or _dist(c, face_c) <= HAND_FACE_MAX):
            keep.append(h)
    return keep


def summarize(frames: list[dict]) -> dict:
    """One summary over a clip's frame detections.

    `detected` is the fraction of frames with a face. `eyes`, `face_h` and `joints_y` are medians over
    the frames with a face only (the speaker may leave the shot). `extent` takes, per frame, the
    speaker's extreme value per edge over the head box and the speaker's hand boxes, then per edge the
    second-most-extreme frame: exactly one frame per edge is dropped, so a gesture seen in a single
    frame does not widen it, and a gesture held for two frames or more does, however long the item.
    Hands count from every frame, including frames where a gesture hides the face.
    A hand box whose bottom is at RESTING or lower rests at the source's bottom edge (on the knees or
    a table) and is already cut by the source: it counts for the left, right and top, not the bottom.
    `extent_all` is the same with every hand counting for the bottom too (a reach toward the lens
    also touches the bottom edge; the agent can switch a setup to it).
    With no face in any frame there is no speaker to frame, and every value is None.
    """
    seen = [f for f in frames if f.get("face")]
    detected = len(seen) / len(frames) if frames else 0.0
    if not seen:
        return {"detected": detected, "eyes": None, "face_h": None,
                "extent": None, "extent_all": None, "joints_y": {}}

    eyes_pts = [f["eyes"] for f in seen if f.get("eyes")]
    eyes = [statistics.median(p[0] for p in eyes_pts), statistics.median(p[1] for p in eyes_pts)] \
        if eyes_pts else None
    face_h = statistics.median(f["face"][3] - f["face"][1] for f in seen)

    # Per frame: the speaker's extreme value per edge over that frame's head and hands.
    lefts, tops, rights, lows, lows_all = [], [], [], [], []
    for f in frames:
        head = [f["head"]] if f.get("face") and f.get("head") else []
        hands = _speaker_hands(f)
        boxes = head + hands
        if not boxes:
            continue
        lefts.append(min(b[0] for b in boxes))
        tops.append(min(b[1] for b in boxes))
        rights.append(max(b[2] for b in boxes))
        lows_all.append(max(b[3] for b in boxes))
        low = [b[3] for b in head] + [b[3] for b in hands if b[3] < RESTING]
        if low:
            lows.append(max(low))
    extent = extent_all = None
    if lefts:
        sides = [_edge(lefts, True), _edge(tops, True), _edge(rights, False)]
        extent = sides + [_edge(lows or lows_all, False)]
        extent_all = sides + [_edge(lows_all, False)]

    ys: dict[str, list[float]] = {}
    for f in seen:
        for name, pt in (f.get("joints") or {}).items():
            ys.setdefault(name, []).append(pt[1])
    joints_y = {name: statistics.median(v) for name, v in sorted(ys.items())}

    return {"detected": detected, "eyes": eyes, "face_h": face_h, "extent": extent,
            "extent_all": extent_all, "joints_y": joints_y}


# ---------------------------------------------------------------- Vision helper

def helper_path(cache: Path) -> Path:
    """The compiled face_probe binary under `cache/bin`, compiled (or recompiled) when needed."""
    binary = Path(cache) / "bin" / "face_probe"
    if binary.exists() and binary.stat().st_mtime >= SWIFT_SRC.stat().st_mtime:
        return binary
    swiftc = shutil.which("swiftc")
    if not swiftc:
        raise RuntimeError("video-framing measures with Apple Vision and needs macOS with Xcode command line tools")
    binary.parent.mkdir(parents=True, exist_ok=True)
    tmp = binary.with_suffix(".tmp")
    # Swift 5 language mode: the helper is a synchronous top-level script and needs no strict concurrency.
    r = subprocess.run([swiftc, "-O", "-swift-version", "5", str(SWIFT_SRC), "-o", str(tmp)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"face_probe failed to compile:\n{r.stderr.strip()}")
    tmp.replace(binary)
    return binary


def probe_frames(binary: Path, images: list[Path]) -> list[dict]:
    """Run the helper over `images`; one detection dict per image, in order."""
    with tempfile.TemporaryDirectory(prefix="face_probe_") as td:
        inp, out = Path(td) / "in.json", Path(td) / "out.json"
        inp.write_text(json.dumps([str(p) for p in images]))
        r = subprocess.run([str(binary), str(inp), str(out)], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"face_probe failed (exit {r.returncode}):\n{r.stderr.strip()}")
        return json.loads(out.read_text())


# ---------------------------------------------------------------- frames

def _require_ffmpeg() -> None:
    missing = [t for t in ("ffmpeg", "ffprobe") if not shutil.which(t)]
    if missing:
        raise RuntimeError(f"video-framing needs {' and '.join(missing)} on PATH to read frames")


def _has_zscale() -> bool:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True)
    return any(line.split()[1:2] == ["zscale"] for line in r.stdout.splitlines() if line.strip())


def _color_transfer(path: str) -> str:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=color_transfer", "-of", "csv=p=0", path], capture_output=True, text=True)
    return r.stdout.strip()


def extract_frames(path, src_start_s: float, src_end_s: float, out_dir, fps: float = 4.0) -> list[Path]:
    """JPEG frames of `path` between the two source times, `fps` per second, 960 px wide.

    HDR sources (HLG or PQ) are tone-mapped to SDR when ffmpeg has zscale, so Vision sees normal contrast.
    """
    _require_ffmpeg()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("f_*.jpg"):
        old.unlink()
    scale = f"scale={FRAME_WIDTH}:-2"
    if _color_transfer(str(path)) in HDR_TRANSFERS and _has_zscale():
        vf = (f"fps={fps},zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
              f"tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p,{scale}")
    else:
        vf = f"fps={fps},{scale}"
    duration = max(src_end_s - src_start_s, 1.0 / fps)
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{src_start_s:.3f}", "-i", str(path),
                        "-t", f"{duration:.3f}", "-vf", vf, "-q:v", "3", str(out_dir / "f_%05d.jpg")],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg could not read frames from {path}:\n{r.stderr.strip()}")
    return sorted(out_dir.glob("f_*.jpg"))


# ---------------------------------------------------------------- items

def _source(item: dict) -> Path:
    """The item's source file; a clear error when the plan has no path or the file is gone."""
    path = item.get("path")
    name = f"{item_key(item)} ({item.get('clip') or 'unnamed clip'})"
    if not path:
        raise RuntimeError(f"cannot measure {name}: the plan item has no source path")
    p = Path(path)
    if not p.is_file():
        raise RuntimeError(f"cannot measure {name}: source file not found: {path}")
    return p


def _cache_name(item: dict, src: Path) -> str:
    """Changes with the algorithm version, the source range and the file itself (size, mtime)."""
    st = src.stat()
    key = f"v{MEASURE_VERSION}:{src}:{st.st_size}:{st.st_mtime_ns}:{item['src_start']}:{item['src_end']}"
    return hashlib.sha1(key.encode()).hexdigest() + ".json"


def measure_items(plan: dict, cam_items: list[dict], workdir, progress=None) -> dict[str, dict]:
    """One `summarize` result per on-camera item, keyed by `item_key`.

    The source range is src_start..src_end in the clip's own frame rate (so retimed clips measure what
    they actually show). Each summary is cached; a summary over zero extracted frames is not cached.
    `progress(n, total, item, cached)` is called before each item.
    """
    cache = Path(workdir) / ".edit" / "framing"
    mdir = cache / "measure"
    mdir.mkdir(parents=True, exist_ok=True)
    tl_fps = float(plan["timeline"]["fps"])
    out: dict[str, dict] = {}
    binary = None
    for n, it in enumerate(cam_items, 1):
        src = _source(it)
        cfile = mdir / _cache_name(it, src)
        if progress:
            progress(n, len(cam_items), it, cfile.exists())
        if cfile.exists():
            out[item_key(it)] = json.loads(cfile.read_text())
            continue
        if binary is None:
            binary = helper_path(cache)
        src_fps = float(it.get("src_fps") or tl_fps)
        t0, t1 = it["src_start"] / src_fps, it["src_end"] / src_fps
        with tempfile.TemporaryDirectory(prefix="framing_frames_") as td:
            images = extract_frames(src, t0, t1, td)
            summary = summarize(probe_frames(binary, images) if images else [])
        if images:
            cfile.write_text(json.dumps(summary, indent=1))
        out[item_key(it)] = summary
    return out
