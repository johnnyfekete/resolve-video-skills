"""Audio helpers shared by the resolve-video-skills: energy-based voice activity detection.

Pure Python + ffmpeg (no numpy). Good enough for close-mic talking-head recordings, where speech is far
above the room noise. Not a replacement for a neural VAD on noisy location audio.
"""
from __future__ import annotations

import array
import math
import subprocess

SR = 8000          # analysis sample rate
HOP = 0.01         # 10 ms analysis frames


def read_mono(path: str, start: float, duration: float, stream: int = 0) -> array.array:
    """Decode [start, start+duration) seconds of an audio stream to mono 16-bit samples at SR."""
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{max(start, 0):.3f}", "-t", f"{duration:.3f}", "-i", path,
           "-map", f"0:a:{stream}", "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    a = array.array("h")
    a.frombytes(raw[: len(raw) // 2 * 2])
    return a


def frame_db(samples: array.array) -> list[float]:
    """RMS level in dBFS for each 10 ms frame."""
    n = int(SR * HOP)
    out = []
    for i in range(0, len(samples) - n + 1, n):
        chunk = samples[i:i + n]
        ms = sum(x * x for x in chunk) / n
        out.append(10 * math.log10(ms / (32768.0 ** 2)) if ms > 0 else -120.0)
    return out


def _percentile(values: list[float], p: float) -> float:
    s = sorted(values)
    if not s:
        return -120.0
    k = min(len(s) - 1, max(0, int(round(p / 100 * (len(s) - 1)))))
    return s[k]


_LEVELS: dict = {}


def file_levels(path: str, stream: int = 0) -> tuple[float, float]:
    """(noise floor, speech level) in dBFS for a whole file: 15th and 90th percentile of 10 ms frames.
    Using whole-file levels keeps thresholds identical for every cut taken from the same recording."""
    key = (path, stream)
    if key not in _LEVELS:
        db = frame_db(read_mono(path, 0.0, 24 * 3600, stream))
        _LEVELS[key] = (_percentile(db, 15), _percentile(db, 90))
    return _LEVELS[key]


def _runs(db: list[float], thr: float, start: float, min_gap: float, min_len: float) -> list[tuple]:
    segs, cur = [], None
    for i, v in enumerate(db):
        if v >= thr:
            if cur is None:
                cur = [i, i + 1]
            elif (i - cur[1]) * HOP <= min_gap:
                cur[1] = i + 1
            else:
                segs.append(cur)
                cur = [i, i + 1]
    if cur is not None:
        segs.append(cur)
    return [(round(start + s * HOP, 3), round(start + e * HOP, 3)) for s, e in segs if (e - s) * HOP >= min_len]


def speech_segments(path: str, start: float, duration: float, *, stream: int = 0,
                    min_gap: float = 0.15, min_speech: float = 0.08,
                    head_grow: float = 0.3, tail_grow: float = 0.4) -> dict:
    """Find speech, and any other audible sound, in a source range.

    Thresholds come from the whole file (see file_levels):
      speech:   noise + max(6 dB, 40% of the way to the speech level). Gaps under min_gap (inside words)
                are merged; blips under min_speech (clicks) dropped.
      activity: noise + 10 dB for at least 80 ms. Breaths, mumbles, clatter, the next take. Use it to keep
                cut extensions away from sounds that are not words. (Word edges themselves grow down to
                noise + 6 dB, see below.)
    Times are absolute source seconds.
    """
    start = max(0.0, start)
    noise, speech = file_levels(path, stream)
    db = frame_db(read_mono(path, start, duration, stream))
    thr = noise + max(6.0, 0.4 * (speech - noise))
    act_thr = noise + 6.0
    # Hysteresis: words are found with the strong threshold, then their edges grow through quieter sound
    # (soft consonants, decaying vowels, an attached breath) down to the activity threshold.
    segs = []
    for a, b in _runs(db, thr, 0.0, min_gap, min_speech):
        i, j = int(round(a / HOP)), int(round(b / HOP))
        lo, hi = max(0, i - int(head_grow / HOP)), min(len(db), j + int(tail_grow / HOP))
        while i > lo and db[i - 1] >= act_thr:
            i -= 1
        while j < hi and db[j] >= act_thr:
            j += 1
        if segs and i * HOP - segs[-1][1] <= min_gap:
            segs[-1][1] = j * HOP
        else:
            segs.append([i * HOP, j * HOP])
    return {"segments": [(round(start + a, 3), round(start + b, 3)) for a, b in segs],
            "activity": _runs(db, noise + 10.0, start, 0.05, 0.08),
            "threshold": round(thr, 1), "activity_threshold": round(act_thr, 1),
            "noise": round(noise, 1), "speech": round(speech, 1)}


def probe_duration(path: str) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip() or 0)
