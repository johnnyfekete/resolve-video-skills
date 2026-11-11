"""B-roll: sample frames, measure them, validate and store the agent's descriptions.

Standard library only. The descriptions are written by the agent after it looks at the extracted frames;
this module extracts and measures the frames and validates/stores what the agent reports in broll.json.
"""
from __future__ import annotations

import json
import re
import statistics
import subprocess
import tempfile
from pathlib import Path

LUMA_MIN, LUMA_MAX = 16.0, 235.0
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


def extract_frames(path: str, out_dir: Path, every: float = 2.0) -> list[dict]:
    """One ffmpeg pass: a 640 px JPEG every `every` s plus luma, sharpness (mean edge strength) and motion."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9_.-]", "_", Path(path).stem)
    with tempfile.TemporaryDirectory() as td:
        luma_f, edge_f, motion_f = (Path(td) / n for n in ("l.txt", "e.txt", "m.txt"))
        key = "metadata=print:key=lavfi.signalstats.YAVG:file="
        graph = (f"[0:v]fps=1/{every},scale=640:-2,split=3[a][b][c];"
                 f"[b]edgedetect,signalstats,{key}{edge_f},nullsink;"
                 f"[c]tblend=all_mode=difference,signalstats,{key}{motion_f},nullsink;"
                 f"[a]signalstats,{key}{luma_f}[o]")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-filter_complex", graph, "-map", "[o]",
                        "-an", "-start_number", "0", "-q:v", "3", str(out_dir / f"{stem}_%04d.jpg")],
                       check=True, capture_output=True, text=True)
        luma, edge, motion = (_read_yavg(f, every) for f in (luma_f, edge_f, motion_f))
    frames = []
    for i in range(10**6):
        img = out_dir / f"{stem}_{i:04d}.jpg"
        if not img.exists():
            break
        t = i * every
        frames.append({"t": int(t) if float(t).is_integer() else t, "image": str(img),
                       "sharpness": round(edge.get(i, 0.0), 3), "luma": round(luma.get(i, 0.0), 3),
                       "motion": round(motion.get(i, 0.0), 3)})
    return frames


def best_moments(frames: list[dict], min_len: float = 3.0) -> list[dict]:
    """Runs of well-exposed, sharper-than-median frames, longest and best first."""
    if not frames:
        return []
    median = statistics.median(f["sharpness"] for f in frames)
    top = max(f["sharpness"] for f in frames) or 1.0
    step = frames[1]["t"] - frames[0]["t"] if len(frames) > 1 else 2.0
    good = [LUMA_MIN <= f["luma"] <= LUMA_MAX and f["sharpness"] >= median and f["sharpness"] > 0
            for f in frames]
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
            runs.append({"start": start, "end": end, "score": round(100 * (0.6 * sharp + 0.4 * expo), 1)})
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
