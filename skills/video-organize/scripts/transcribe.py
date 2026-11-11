"""Local transcription: slice at silences, run Whisper per slice, cache the result.

Standard library only. Whisper runs in a subprocess (`uv run --with mlx-whisper`) or in the command named
by env RVS_TRANSCRIBE_CMD, which takes `<in.json> <out.json>` (see whisper_worker.py for the formats).
"""
from __future__ import annotations

import collections
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "resolve-bridge" / "scripts"))

import rvs_audio  # noqa: E402

WORKER = Path(__file__).resolve().parent / "whisper_worker.py"


def audio_streams(path: str) -> list[dict]:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                        "stream=index,codec_name,channels", "-of", "json", path],
                       capture_output=True, text=True, check=True)
    return [{"index": s["index"], "codec": s.get("codec_name"), "channels": s.get("channels", 0)}
            for s in json.loads(r.stdout or "{}").get("streams", [])]


def _live_streams(path: str, streams: list[dict]) -> int:
    n = 0
    for i, s in enumerate(streams):
        if s["channels"] >= 2 or rvs_audio.file_levels(path, i)[1] > -50.0:
            n += 1
    return n


def default_runner(slices: list[dict]) -> list[dict]:
    cmd_env = os.environ.get("RVS_TRANSCRIBE_CMD")
    if cmd_env:
        base = shlex.split(cmd_env)
    else:
        if not shutil.which("uv"):
            raise RuntimeError("Transcription needs uv (https://docs.astral.sh/uv/) or RVS_TRANSCRIBE_CMD.")
        base = ["uv", "run", "--quiet", "--no-project", "--with", "mlx-whisper", "python", "-I", str(WORKER)]
    with tempfile.TemporaryDirectory() as d:
        src, dst = os.path.join(d, "in.json"), os.path.join(d, "out.json")
        Path(src).write_text(json.dumps(slices))
        subprocess.run(base + [src, dst], check=True, cwd=d)
        return json.loads(Path(dst).read_text())


def _extract(path: str, start: float, end: float, stream: int, out: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", path,
                    "-map", f"0:a:{stream}", "-ac", "1", "-ar", "16000", out], check=True)


def transcribe(path: str, cache_dir: Path, *, stream: int = 0,
               runner: Callable[[list[dict]], list[dict]] | None = None) -> dict:
    path = str(path)
    st = os.stat(path)
    cache_dir = Path(cache_dir)
    cache = cache_dir / (hashlib.sha1(path.encode()).hexdigest() + ".json")
    if cache.exists():
        try:
            cached = json.loads(cache.read_text())
            if cached.get("size") == st.st_size and cached.get("mtime") == st.st_mtime \
                    and cached.get("stream") == stream:
                return cached
        except (ValueError, OSError):
            pass
    streams = audio_streams(path)
    if not streams:
        raise RuntimeError(f"No audio stream in {path}")
    duration = rvs_audio.probe_duration(path)
    slices = rvs_audio.silence_cuts(path, stream=stream)
    with tempfile.TemporaryDirectory() as d:
        jobs = []
        for i, (a, b) in enumerate(slices):
            wav = os.path.join(d, f"slice{i:03d}.wav")
            _extract(path, a, b, stream, wav)
            jobs.append({"index": i, "wav": wav, "start": a, "end": b})
        results = (runner or default_runner)(jobs)
    by_index = {r["index"]: r for r in results}
    missing = [j["index"] for j in jobs if j["index"] not in by_index]
    if missing:
        raise RuntimeError(f"Transcription runner returned no result for slice(s) {missing}")
    words, segments, langs = [], [], []
    for job in jobs:
        res = by_index[job["index"]]
        off = job["start"]
        lang = res.get("language")
        langs.append(lang)
        for w in res.get("words", []):
            words.append({"w": w["w"], "start": round(off + w["start"], 3), "end": round(off + w["end"], 3),
                          "p": w.get("p", 0.0)})
        for s in res.get("segments", []):
            segments.append({"text": s["text"], "start": round(off + s["start"], 3),
                             "end": round(off + s["end"], 3), "language": lang})
    known = [x for x in langs if x]
    out = {"file": path, "size": st.st_size, "mtime": st.st_mtime, "stream": stream, "duration": duration,
           "language": collections.Counter(known).most_common(1)[0][0] if known else None,
           "words": words, "segments": segments}
    if len(streams) > 1 and _live_streams(path, streams) > 1:
        out["warning"] = f"{len(streams)} audio streams; using the first"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out))
    return out


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: transcribe.py <file> [cache_dir]", file=sys.stderr)
        return 2
    cache = Path(argv[2]) if len(argv) > 2 else Path(tempfile.gettempdir()) / "rvs-transcripts"
    t = transcribe(argv[1], cache)
    print("language:", t["language"])
    if t.get("warning"):
        print("warning:", t["warning"])
    print(" ".join(w["w"] for w in t["words"][:20]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
