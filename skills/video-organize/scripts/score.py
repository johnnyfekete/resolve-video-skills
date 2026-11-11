"""Score takes, read on-set verdicts, rank takes per scene and suggest patches.

Clause numbers are 0-based indexes into `Scene.clauses`.
"""
from __future__ import annotations

import difflib
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "resolve-bridge" / "scripts"))

import rvs_audio  # noqa: E402
from storyboard import Scene, tokens  # noqa: E402
from takes import Take  # noqa: E402

# Tunable on real footage.
CLIP_PEAK_DB = -0.1        # peak at or above this is clipped
SAFE_PEAK_DB = -1.0        # full marks need a peak below this
LUFS_RANGE = (-23.0, -14.0)
LUFS_FALLOFF = 12.0        # LU outside the range that costs all loudness points
NOISE_MARGIN_DB = 40.0     # full marks need the noise floor this far below the speech level
NOISE_MARGIN_FLOOR = 20.0  # at or below this margin the noise points are gone
BROKEN_COST = 0.34         # share of the "clean" points each broken part costs
SILENT_LUFS = -69.0
REWORDED_PENALTY = 5
INCOMPLETE_SHARE = 0.5     # an incomplete take earns at most this share of the completeness points
MIN_PATCH_PAUSE = 0.15     # s of silence needed to cut a patch in or out

# (completeness, clean, audio) points
WEIGHTS_CAMERA = (45, 30, 25)
WEIGHTS_VOICEOVER = (30, 20, 50)
STATUS_OK = ("complete", "reworded")


def _ffmpeg_report(path: str, start: float, end: float, stream: int) -> str:
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{max(start, 0):.3f}", "-t", f"{max(end - start, 0.01):.3f}",
           "-i", path, "-map", f"0:a:{stream}", "-af", "astats=measure_perchannel=none,ebur128=peak=true",
           "-f", "null", "-"]
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stderr


def _number(text: str) -> float | None:
    try:
        v = float(text)
    except ValueError:
        return None
    return v if v > -120 else None  # -inf and silence floors read as "no signal"


def audio_metrics(path: str, start: float, end: float, stream: int = 0) -> dict:
    """{"lufs", "peak_db", "noise_db", "clipped"} for [start, end) of an audio stream.

    lufs is integrated loudness, peak_db the sample peak in dBFS, noise_db the file's noise floor in dBFS
    (rvs_audio.file_levels, so every take from one recording shares it). Either level is None for silence."""
    report = _ffmpeg_report(path, start, end, stream)
    lufs = peak = None
    summary = report.rsplit("Summary:", 1)[-1]
    m = re.search(r"\bI:\s+(-?[\d.]+|-inf)\s+LUFS", summary)
    if m:
        lufs = _number(m.group(1))
        if lufs is not None and lufs <= SILENT_LUFS:
            lufs = None  # ebur128 reports about -70 LUFS for silence
    overall = report.split("Overall", 1)[-1]
    m = re.search(r"Peak level dB:\s+(-?[\d.]+|-inf)", overall)
    if m:
        peak = _number(m.group(1))
    noise = rvs_audio.file_levels(path, stream)[0]
    return {"lufs": lufs, "peak_db": peak, "noise_db": noise,
            "clipped": peak is not None and peak >= CLIP_PEAK_DB}


# ---------------------------------------------------------------- alignment to clauses

def _script(scene: Scene) -> tuple[list[str], list[int]]:
    toks: list[str] = []
    clause_of: list[int] = []
    for i, clause in enumerate(scene.clauses):
        for t in tokens(clause):
            toks.append(t)
            clause_of.append(i)
    return toks, clause_of


class _Aligned:
    """A take's spoken tokens placed on the scene's clauses."""

    def __init__(self, take: Take, scene: Scene):
        self.script, self.script_clause = _script(scene)
        self.spoken: list[str] = []
        self.word_of: list[int] = []  # spoken token -> index in take.words
        for k, w in enumerate(take.words):
            for t in tokens(w["w"]):
                self.spoken.append(t)
                self.word_of.append(k)
        self.sm = difflib.SequenceMatcher(None, self.script, self.spoken, autojunk=False)
        self.clause_of_spoken: list[int] = [0] * len(self.spoken)
        self.matched = [False] * len(self.script)  # script token said exactly
        self.matched_spoken = [False] * len(self.spoken)
        self.inserted = [False] * len(self.spoken)  # spoken tokens with no counterpart in the script
        last = 0
        for op, a1, a2, b1, b2 in self.sm.get_opcodes():
            for k in range(b1, b2):
                if op == "equal":
                    self.matched[a1 + k - b1] = True
                    self.matched_spoken[k] = True
                    last = self.script_clause[a1 + k - b1]
                elif op == "replace":
                    last = self.script_clause[min(a1 + k - b1, a2 - 1)]
                else:  # insert: inside a clause it belongs there, between clauses to the one just said
                    self.inserted[k] = True
                    if 0 < a1 < len(self.script) and self.script_clause[a1 - 1] == self.script_clause[a1]:
                        last = self.script_clause[a1]
                self.clause_of_spoken[k] = last

    def clause_matched(self, clause: int) -> bool:
        idx = [i for i, c in enumerate(self.script_clause) if c == clause]
        return bool(idx) and all(self.matched[i] for i in idx)


def broken_parts(take: Take, scene: Scene) -> list[dict]:
    """{"clause", "kind", "at"} for repeated words (restart), words said that are not in the script
    (stumble) and a word cut off at the take's first or last edge (cut). `at` is a time in the file."""
    al = _Aligned(take, scene)
    out: list[dict] = []
    in_repeat = [False] * len(al.spoken)

    def clause(k: int) -> int:
        return al.clause_of_spoken[k]

    def at(k: int) -> float:
        return take.words[al.word_of[k]]["start"]

    # restart: a run of 1-3 tokens said twice in a row where the script does not repeat it
    for n in (3, 2, 1):
        for k in range(len(al.spoken) - 2 * n + 1):
            seg = al.spoken[k:k + n]
            if seg != al.spoken[k + n:k + 2 * n] or any(in_repeat[k:k + 2 * n]):
                continue
            if any(al.script[i:i + 2 * n] == seg + seg for i in range(len(al.script))):
                continue
            for j in range(k, k + 2 * n):
                in_repeat[j] = True
            out.append({"clause": clause(k + n), "kind": "restart", "at": at(k + n)})

    # stumble: words said inside a clause that the script does not have
    k = 0
    while k < len(al.spoken):
        if not al.inserted[k] or in_repeat[k] or not _between_matched(al, k):
            k += 1
            continue
        out.append({"clause": clause(k), "kind": "stumble", "at": at(k)})
        while k < len(al.spoken) and al.inserted[k] and not in_repeat[k]:
            k += 1

    # cut: a truncated first or last word
    for k in _cut_tokens(al):
        out.append({"clause": clause(k), "kind": "cut", "at": at(k)})
    out.sort(key=lambda b: (b["at"], b["kind"]))
    return out


def _between_matched(al: _Aligned, k: int) -> bool:
    """True when spoken token k sits between two exactly-said tokens of one clause."""
    before = next((i for i in range(k - 1, -1, -1) if al.matched_spoken[i]), None)
    after = next((i for i in range(k + 1, len(al.spoken)) if al.matched_spoken[i]), None)
    return before is not None and after is not None and al.clause_of_spoken[before] == al.clause_of_spoken[after]


def _expected(al: _Aligned, k: int) -> list[str]:
    """Script tokens that the spoken token k replaced."""
    for op, a1, a2, b1, b2 in al.sm.get_opcodes():
        if op == "replace" and b1 <= k < b2:
            return al.script[a1:a2]
    return []


def _cut_tokens(al: _Aligned) -> list[int]:
    """Spoken token indexes that are a truncated first (ending of a word) or last (start of a word) word."""
    found = []
    if not al.spoken:
        return found
    for k, head in ((0, True), (len(al.spoken) - 1, False)):
        t = al.spoken[k]
        if any(len(t) < len(e) and (e.endswith(t) if head else e.startswith(t)) for e in _expected(al, k)):
            found.append(k)
    return sorted(set(found))


# ---------------------------------------------------------------- verdicts

def _contains(haystack: list[str], phrase: str) -> bool:
    needle = tokens(phrase)
    return bool(needle) and any(haystack[i:i + len(needle)] == needle
                                for i in range(len(haystack) - len(needle) + 1))


def verdict(take: Take, chatter: list[dict], approve: list[str], reject: list[str],
            window: float = 8.0) -> str | None:
    """"approved" / "rejected" from chatter that starts within `window` s after the take ends.
    Rejection wins when both match."""
    heard = [tokens(c["text"]) for c in chatter if take.end <= c["start"] <= take.end + window]
    if any(_contains(h, p) for h in heard for p in reject):
        return "rejected"
    if any(_contains(h, p) for h in heard for p in approve):
        return "approved"
    return None


# ---------------------------------------------------------------- score

def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _audio_fraction(m: dict) -> tuple[float, list[str]]:
    if m.get("clipped"):
        return 0.0, ["clipped audio"]
    lufs, peak, noise = m.get("lufs"), m.get("peak_db"), m.get("noise_db")
    if lufs is None:
        return 0.0, ["no audio level"]
    lo, hi = LUFS_RANGE
    off = max(lo - lufs, lufs - hi, 0.0)
    loud = 1 - _clamp(off / LUFS_FALLOFF)
    safe = 1.0 if peak is None else 1 - _clamp(peak - SAFE_PEAK_DB)
    if noise is None:
        quiet = 1.0
    else:
        quiet = _clamp((lufs - noise - NOISE_MARGIN_FLOOR) / (NOISE_MARGIN_DB - NOISE_MARGIN_FLOOR))
    reasons = []
    if off:
        reasons.append(f"{'quiet' if lufs < lo else 'loud'} audio ({lufs:.1f} LUFS)")
    if safe < 1:
        reasons.append(f"hot peak ({peak:.1f} dBFS)")
    if quiet < 1:
        reasons.append(f"noisy ({lufs - noise:.0f} dB above the noise floor)")
    return 0.5 * loud + 0.2 * safe + 0.3 * quiet, reasons


def score(take: Take, scene: Scene, metrics: dict, broken: list[dict]) -> tuple[int, list[str]]:
    """0-100 and the reasons points were lost. Weights (completeness, clean, audio): on camera 45/30/25,
    voiceover 30/20/50."""
    w_complete, w_clean, w_audio = WEIGHTS_VOICEOVER if scene.voiceover else WEIGHTS_CAMERA
    reasons: list[str] = []
    if take.status in STATUS_OK:
        complete = 1.0
    else:
        n = len(tokens(scene.text)) or 1
        complete = INCOMPLETE_SHARE * _clamp(1 - len(take.missing) / n)
        reasons.append("incomplete" + (f", missing: {' '.join(take.missing)}" if take.missing else ""))
    clean = _clamp(1 - BROKEN_COST * len(broken))
    for b in broken:
        reasons.append(f"{b['kind']} in clause {b['clause']} at {b['at']:.1f}s")
    audio, audio_reasons = _audio_fraction(metrics)
    reasons += audio_reasons
    total = w_complete * complete + w_clean * clean + w_audio * audio
    if take.status == "reworded":
        total -= REWORDED_PENALTY
        reasons.append("reworded")
    return max(0, min(100, round(total))), reasons


# ---------------------------------------------------------------- ranking

def rank(entries: list[dict]) -> list[dict]:
    """Entries {"take", "score", "verdict"} of one scene, best first: approved, then (a rejected take that
    is the scene's only complete or reworded one), then the rest by score, then rejected. Ties keep
    filming order. Unmatched takes are left out."""
    kept = [e for e in entries if e["take"].status != "unmatched"]
    usable = [e for e in kept if e["take"].status in STATUS_OK]

    def key(e: dict):
        group = 2
        if e["verdict"] == "approved":
            group = 0
        elif e["verdict"] == "rejected":
            group = 1 if usable == [e] else 3
        t = e["take"]
        return group, -e["score"], t.file, t.start

    return sorted(kept, key=key)


# ---------------------------------------------------------------- patches

def _clause_words(al: _Aligned, take: Take, clause: int) -> list[int]:
    """Indexes in take.words of the words placed in the clause."""
    return sorted({al.word_of[k] for k, c in enumerate(al.clause_of_spoken) if c == clause})


def _runs(clauses: list[int]) -> list[list[int]]:
    runs: list[list[int]] = []
    for c in clauses:
        if runs and c == runs[-1][-1] + 1:
            runs[-1].append(c)
        else:
            runs.append([c])
    return runs


def _clean_spans(take: Take, scene: Scene) -> dict[int, tuple[int, int]]:
    """clause -> (first, last) word index, for the clauses said exactly with no broken part."""
    al = _Aligned(take, scene)
    dirty = {b["clause"] for b in broken_parts(take, scene)}
    spans = {}
    for c in range(len(scene.clauses)):
        idx = _clause_words(al, take, c)
        if c not in dirty and idx and al.clause_matched(c):
            spans[c] = (idx[0], idx[-1])
    return spans


def patches(best: Take, others: list[Take], scene: Scene) -> list[dict]:
    """Fixes for the broken clauses of `best`: {"base": Take, "from": Take, "clauses": [int], "from_text": str}.

    One entry per (source take, contiguous run of broken clauses): `clauses` is that run and `from_text` the
    source's words for exactly that run. A source qualifies for a run when it is clean in every clause of it and
    both takes can be cut at pauses of at least 0.15 s; whole clauses only. Empty when `best` has nothing to fix."""
    if best.status == "unmatched" or not scene.clauses:
        return []
    best_spans = _clean_spans(best, scene)
    broken = [c for c in range(len(scene.clauses)) if c not in best_spans]
    if not broken:
        return []
    out = []
    for run in _runs(broken):
        for other in others:
            if other is best or other.status == "unmatched":
                continue
            spans = _clean_spans(other, scene)
            if not all(c in spans for c in run) or not _runs_cuttable(best, other, run, best_spans, spans):
                continue
            first, last = spans[run[0]][0], spans[run[-1]][1]
            text = " ".join(w["w"].strip() for w in other.words[first:last + 1])
            out.append({"base": best, "from": other, "clauses": run, "from_text": text})
    return out


def _runs_cuttable(best: Take, other: Take, run: list[int], best_spans: dict, spans: dict) -> bool:
    """Both takes need a pause at the run's edges. In `best` the run is located by the clean clauses
    around it, since a broken or missing clause has no clean span of its own."""
    first, last = spans[run[0]][0], spans[run[-1]][1]
    if not (_pause_at(other.words, first) and _pause_at(other.words, last + 1)):
        return False
    before = best_spans.get(run[0] - 1)
    after = best_spans.get(run[-1] + 1)
    cut_in = before[1] + 1 if before else 0
    cut_out = after[0] if after else len(best.words)
    return all(_pause_at(best.words, b) for b in (cut_in, cut_out))


def _pause_at(words: list[dict], boundary: int) -> bool:
    """True when the cut before words[boundary] lands in a pause (or at a take edge)."""
    if boundary <= 0 or boundary >= len(words):
        return True
    return words[boundary]["start"] - words[boundary - 1]["end"] >= MIN_PATCH_PAUSE
