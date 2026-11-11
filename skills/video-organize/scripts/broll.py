"""B-roll: sample frames, measure them, validate and store the agent's descriptions.

Standard library only. The descriptions are written by the agent after it looks at the extracted frames;
this module extracts and measures the frames and validates/stores what the agent reports in broll.json.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import re
import shutil
import statistics
import subprocess
import tempfile
from pathlib import Path

LUMA_MIN, LUMA_MAX = 16.0, 235.0
# Frames whose camera shake (mean abs difference to the next native frame, 0-255) is above this are
# excluded from best moments. Measured: still 0.0, testsrc ~1, rotating/handheld-like ~30.
MAX_MOTION = 10.0
_PTS = re.compile(r"pts_time:([0-9.]+)")
_YAVG = re.compile(r"lavfi\.signalstats\.YAVG=([0-9.]+)")


def _read_yavg(path: Path, every: float) -> dict[int, float]:
    """Parse `metadata=print` output into {frame index: YAVG}, keyed by pts_time / every."""
    out: dict[int, float] = {}
    idx = None
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        m = _PTS.search(line)
        if m:
            idx = round(float(m.group(1)) / every)
            continue
        m = _YAVG.search(line)
        if m and idx is not None:
            out[idx] = float(m.group(1))
    return out


def _read_pts_yavg(path: Path) -> list[tuple[float, float]]:
    """Parse `metadata=print` output into [(pts_time, YAVG)] in stream order."""
    out, pts = [], None
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        m = _PTS.search(line)
        if m:
            pts = float(m.group(1))
            continue
        m = _YAVG.search(line)
        if m and pts is not None:
            out.append((pts, float(m.group(1))))
            pts = None
    return out


def _shake(path: str, times: list[float], td: Path) -> list[float]:
    """Camera shake at each sample time: mean absolute difference (0-255) between the sample frame and the
    very next native frame. Needs a second, low-resolution pass over the native frames."""
    f = td / "native.txt"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-vf",
                    "scale=160:-2,tblend=all_mode=difference,signalstats,"
                    f"metadata=print:key=lavfi.signalstats.YAVG:file={f}", "-an", "-f", "null", "-"],
                   check=True, capture_output=True, text=True)
    native = _read_pts_yavg(f)  # entry j = |frame j - frame j-1| at frame j's pts (first frame has none)
    if not native:
        return [0.0] * len(times)
    stamps = [p for p, _ in native]
    out = []
    for t in times:
        j = min(bisect.bisect_left(stamps, t), len(stamps) - 1)  # first diff entry at or after the sample
        if abs(stamps[j] - t) > 1e-6 and j > 0 and abs(stamps[j - 1] - t) < abs(stamps[j] - t):
            j -= 1
        # `native[j]` is the frame nearest the sample (diff against its predecessor); the next one is j+1
        out.append(native[j + 1][1] if j + 1 < len(native) else 0.0)
    return out


def extract_frames(path: str, out_dir: Path, every: float = 2.0) -> list[dict]:
    """A 640 px JPEG every `every` s with luma, sharpness (mean edge strength) and motion (camera shake).

    Frames go to their own subdirectory of `out_dir` (named from a hash of the file's full path), which is
    cleared first, so other files and earlier runs never leak in."""
    key_dir = hashlib.sha1(str(Path(path).resolve()).encode()).hexdigest()[:8]
    stem = re.sub(r"[^A-Za-z0-9_.-]", "_", Path(path).stem)
    frame_dir = Path(out_dir) / f"{stem}_{key_dir}"
    if frame_dir.exists():
        shutil.rmtree(frame_dir)
    frame_dir.mkdir(parents=True)
    with tempfile.TemporaryDirectory() as td:
        luma_f, edge_f = (Path(td) / n for n in ("l.txt", "e.txt"))
        key = "metadata=print:key=lavfi.signalstats.YAVG:file="
        graph = (f"[0:v]fps=1/{every},scale=640:-2,split=2[a][b];"
                 f"[b]edgedetect,signalstats,{key}{edge_f},nullsink;"
                 f"[a]signalstats,{key}{luma_f}[o]")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-filter_complex", graph, "-map", "[o]",
                        "-an", "-start_number", "0", "-q:v", "3", str(frame_dir / "%04d.jpg")],
                       check=True, capture_output=True, text=True)
        luma, edge = (_read_yavg(f, every) for f in (luma_f, edge_f))
        emitted = sorted(int(p.stem) for p in frame_dir.glob("*.jpg"))
        motion = _shake(path, [i * every for i in emitted], Path(td))
    frames = []
    for i, mo in zip(emitted, motion):
        t = i * every
        frames.append({"t": int(t) if float(t).is_integer() else t, "image": str(frame_dir / f"{i:04d}.jpg"),
                       "sharpness": round(edge.get(i, 0.0), 3), "luma": round(luma.get(i, 0.0), 3),
                       "motion": round(mo, 3)})
    return frames


def best_moments(frames: list[dict], min_len: float = 3.0) -> list[dict]:
    """Runs of well-exposed, steady, sharper-than-median frames, longest and best first."""
    if not frames:
        return []
    median = statistics.median(f["sharpness"] for f in frames)
    top = max(f["sharpness"] for f in frames) or 1.0
    step = frames[1]["t"] - frames[0]["t"] if len(frames) > 1 else 2.0
    good = [LUMA_MIN <= f["luma"] <= LUMA_MAX and f["sharpness"] >= median and f["sharpness"] > 0
            and f["motion"] <= MAX_MOTION for f in frames]
    runs, i = [], 0
    while i < len(frames):
        if not good[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(frames) and good[j + 1]:
            j += 1
        run = frames[i:j + 1]
        start, end = run[0]["t"], run[-1]["t"] + step
        if end - start >= min_len:
            sharp = sum(f["sharpness"] for f in run) / len(run) / top
            expo = sum(1 - abs(f["luma"] - 125.5) / 109.5 for f in run) / len(run)
            steady = 1 - sum(f["motion"] for f in run) / len(run) / MAX_MOTION
            runs.append({"start": start, "end": end,
                         "score": round(100 * (0.6 * sharp + 0.4 * expo) * steady, 1)})
        i = j + 1
    return sorted(runs, key=lambda r: (-(r["end"] - r["start"]), -r["score"]))


def _duration(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                        str(path)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        raise ValueError(f"cannot read duration of {path}") from None


def record_descriptions(dir: Path, payload: dict, scene_ids: set[str]) -> dict:
    """Validate the agent's per-file descriptions and merge them into dir/broll.json (nothing is written on error)."""
    dir = Path(dir)
    clean: dict[str, dict] = {}
    for file, entry in payload.items():
        p = Path(file) if Path(file).is_absolute() else dir / file
        dur = _duration(p)
        frames = {}
        for t, desc in (entry.get("frames") or {}).items():
            try:
                tv = float(t)
            except (TypeError, ValueError):
                raise ValueError(f"{file}: frame time {t!r} is not a number") from None
            if not 0 <= tv <= dur:
                raise ValueError(f"{file}: frame time {t} is outside the file (0-{dur:.2f})")
            if not isinstance(desc, str) or not desc.strip():
                raise ValueError(f"{file}: frame {t} has no description")
            frames[str(t)] = desc
        matches = []
        for m in entry.get("matches") or []:
            if m.get("scene") not in scene_ids:
                raise ValueError(f"{file}: unknown scene {m.get('scene')!r}")
            start, end = m.get("start"), m.get("end")
            if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) \
                    or not 0 <= start < end <= dur:
                raise ValueError(f"{file}: match {start}-{end} is not inside the file (0-{dur:.2f})")
            matches.append({"scene": m["scene"], "start": start, "end": end, "why": m.get("why", "")})
        clean[file] = {"frames": frames, "matches": matches}
    store = dir / "broll.json"
    data = json.loads(store.read_text()) if store.exists() else {}
    for file, entry in clean.items():
        cur = data.setdefault(file, {"frames": {}, "matches": []})
        cur["frames"].update(entry["frames"])
        for m in entry["matches"]:
            if m not in cur["matches"]:
                cur["matches"].append(m)
    store.write_text(json.dumps(data, indent=2, sort_keys=True))
    return data
