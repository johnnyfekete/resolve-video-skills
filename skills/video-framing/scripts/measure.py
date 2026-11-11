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
EXTENT_LOW, EXTENT_HIGH = 2.0, 98.0


# ---------------------------------------------------------------- summaries (pure)

def _percentile(values: list[float], pct: float) -> float:
    """Linear-interpolated percentile (numpy's default method)."""
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * pct / 100.0
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def summarize(frames: list[dict]) -> dict:
    """One summary over a clip's frame detections.

    Frames with no face are ignored (the speaker is out of shot). `detected` is the fraction of frames
    with a face. `eyes` and `joints_y` are medians; `extent` is the per-edge 2nd/98th percentile over
    the head and hand boxes, so a gesture seen in a single frame does not widen it.
    """
    seen = [f for f in frames if f.get("face")]
    detected = len(seen) / len(frames) if frames else 0.0
    if not seen:
        return {"detected": detected, "eyes": None, "face_h": None,
                "extent": None, "joints_y": {}}

    eyes_pts = [f["eyes"] for f in seen if f.get("eyes")]
    eyes = [statistics.median(p[0] for p in eyes_pts), statistics.median(p[1] for p in eyes_pts)] \
        if eyes_pts else None
    face_h = statistics.median(f["face"][3] - f["face"][1] for f in seen)

    boxes = []
    for f in seen:
        if f.get("head"):
            boxes.append(f["head"])
        boxes.extend(f.get("hands") or [])
    extent = None
    if boxes:
        extent = [_percentile([b[0] for b in boxes], EXTENT_LOW),
                  _percentile([b[1] for b in boxes], EXTENT_LOW),
                  _percentile([b[2] for b in boxes], EXTENT_HIGH),
                  _percentile([b[3] for b in boxes], EXTENT_HIGH)]

    ys: dict[str, list[float]] = {}
    for f in seen:
        for name, pt in (f.get("joints") or {}).items():
            ys.setdefault(name, []).append(pt[1])
    joints_y = {name: statistics.median(v) for name, v in sorted(ys.items())}

    return {"detected": detected, "eyes": eyes, "face_h": face_h, "extent": extent, "joints_y": joints_y}


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
        subprocess.run([str(binary), str(inp), str(out)], check=True, capture_output=True, text=True)
        return json.loads(out.read_text())


# ---------------------------------------------------------------- frames

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
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{src_start_s:.3f}", "-i", str(path),
                    "-t", f"{duration:.3f}", "-vf", vf, "-q:v", "3", str(out_dir / "f_%05d.jpg")],
                   check=True, capture_output=True, text=True)
    return sorted(out_dir.glob("f_*.jpg"))


# ---------------------------------------------------------------- items

def _cache_name(item: dict) -> str:
    key = f"{item['path']}:{item['src_start']}:{item['src_end']}"
    return hashlib.sha1(key.encode()).hexdigest() + ".json"


def measure_items(plan: dict, cam_items: list[dict], workdir) -> dict[str, dict]:
    """One `summarize` result per on-camera item, keyed by `item_key`. Cached per source range."""
    cache = Path(workdir) / ".edit" / "framing"
    mdir = cache / "measure"
    mdir.mkdir(parents=True, exist_ok=True)
    tl_fps = float(plan["timeline"]["fps"])
    out: dict[str, dict] = {}
    binary = None
    for it in cam_items:
        cfile = mdir / _cache_name(it)
        if cfile.exists():
            out[item_key(it)] = json.loads(cfile.read_text())
            continue
        if binary is None:
            binary = helper_path(cache)
        src_fps = float(it.get("src_fps") or tl_fps)
        t0 = it["src_start"] / src_fps
        t1 = t0 + (it["end"] - it["start"]) / tl_fps
        with tempfile.TemporaryDirectory(prefix="framing_frames_") as td:
            images = extract_frames(it["path"], t0, t1, td)
            summary = summarize(probe_frames(binary, images) if images else [])
        cfile.write_text(json.dumps(summary, indent=1))
        out[item_key(it)] = summary
    return out
