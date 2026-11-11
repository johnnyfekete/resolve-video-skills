"""Find takes in a transcript and match them to storyboard scenes.

Words are split into utterances at pauses. For every scene, runs of consecutive
utterances are aligned against the scene's tokens in order; the best
non-overlapping runs become takes. Everything else is chatter (slates, counting,
"one more time") or, when it is long and in the script language, an unmatched take.
"""
from __future__ import annotations

import collections
import difflib
from dataclasses import dataclass, field

from storyboard import Scene, tokens

# Tunable on real footage.
UTTERANCE_GAP = 0.7       # s of silence between words that ends an utterance
MIN_COVERAGE = 0.5        # share of the scene's tokens a run must align to be a candidate
MIN_PRECISION = 0.6       # share of the run's own tokens that must align
MIN_MATCHED_TOKENS = 3    # aligned tokens a candidate needs (fewer if the scene is shorter)
COMPLETE_COVERAGE = 0.85  # coverage for "complete"; also requires no substituted words
REWORDED_COVERAGE = 0.6   # coverage for "reworded"
MISSING_RUN = 4           # this many consecutive missing script tokens make a take incomplete
UNMATCHED_MIN = 3.0       # s of off-script speech that becomes an unmatched take

OTHER_LANGUAGE = "other language"
SHORT = "short"


@dataclass
class Take:
    file: str
    scene: str | None
    start: float
    end: float
    coverage: float
    status: str  # "complete" | "reworded" | "incomplete" | "unmatched"
    missing: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    words: list[dict] = field(default_factory=list)
    language: str | None = None


@dataclass
class _Utterance:
    words: list[dict]
    language: str | None
    tokens: list[str]

    @property
    def start(self) -> float:
        return self.words[0]["start"]

    @property
    def end(self) -> float:
        return self.words[-1]["end"]

    @property
    def text(self) -> str:
        return " ".join(w["w"].strip() for w in self.words).strip()


def _word_language(word: dict, segments: list[dict], default: str | None) -> str | None:
    """Language of the segment containing the word's midpoint, else the nearest segment."""
    if not segments:
        return default
    mid = (word["start"] + word["end"]) / 2
    best, best_dist = None, None
    for seg in segments:
        dist = 0.0 if seg["start"] <= mid <= seg["end"] else min(abs(mid - seg["start"]), abs(mid - seg["end"]))
        if best_dist is None or dist < best_dist:
            best, best_dist = seg, dist
            if dist == 0.0:
                break
    return best.get("language") or default


def _utterances(transcript: dict) -> list[_Utterance]:
    segments = transcript.get("segments") or []
    default = transcript.get("language")
    out: list[_Utterance] = []
    prev_end = prev_lang = None
    for word in sorted(transcript.get("words") or [], key=lambda w: w["start"]):
        lang = _word_language(word, segments, default)
        if out and word["start"] - prev_end <= UTTERANCE_GAP and lang == prev_lang:
            out[-1].words.append(word)
            out[-1].tokens.extend(tokens(word["w"]))
        else:
            out.append(_Utterance([word], lang, tokens(word["w"])))
        prev_end, prev_lang = word["end"], lang
    return out


@dataclass
class _Alignment:
    coverage: float
    precision: float
    matched: int
    missing: list[str]
    extra: list[str]
    longest_missing: int
    substituted: bool


def _align(script: list[str], spoken: list[str]) -> _Alignment:
    sm = difflib.SequenceMatcher(None, script, spoken, autojunk=False)
    matched = sum(b.size for b in sm.get_matching_blocks())
    missing: list[str] = []
    extra: list[str] = []
    longest = 0
    substituted = False
    for op, a1, a2, b1, b2 in sm.get_opcodes():
        if op in ("delete", "replace"):
            missing.extend(script[a1:a2])
            longest = max(longest, a2 - a1)
        if op in ("insert", "replace"):
            extra.extend(spoken[b1:b2])
        if op == "replace":
            substituted = True
    return _Alignment(matched / len(script), matched / len(spoken) if spoken else 0.0,
                      matched, missing, extra, longest, substituted)


def _status(al: _Alignment) -> str:
    if al.longest_missing >= MISSING_RUN:
        return "incomplete"
    if al.coverage >= COMPLETE_COVERAGE and not al.substituted:
        return "complete"
    if al.coverage >= REWORDED_COVERAGE:
        return "reworded"
    return "incomplete"


def _language(utts: list[_Utterance]) -> str | None:
    langs = [u.language for u in utts for _ in u.words if u.language]
    return collections.Counter(langs).most_common(1)[0][0] if langs else None


def _candidates(utts: list[_Utterance], usable: list[bool], sc: Scene) -> list[tuple]:
    script = tokens(sc.text)
    if not script:
        return []
    need = min(MIN_MATCHED_TOKENS, len(script))
    max_run = len(script) / MIN_PRECISION  # a longer run cannot reach MIN_PRECISION
    found = []
    for i in range(len(utts)):
        spoken: list[str] = []
        for j in range(i, len(utts)):
            if not usable[j]:
                break
            spoken = spoken + utts[j].tokens
            if len(spoken) > max_run:
                break
            if not utts[j].tokens:
                continue
            al = _align(script, spoken)
            if al.coverage >= MIN_COVERAGE and al.precision >= MIN_PRECISION and al.matched >= need:
                found.append((al, i, j, sc))
    return found


def find_takes(transcript: dict, scenes: list[Scene],
               script_language: str | None) -> tuple[list[Take], list[dict]]:
    """Takes (in time order) and chatter segments {"start", "end", "text", "reason"}."""
    file = transcript.get("file")
    utts = _utterances(transcript)
    other = [bool(script_language and u.language and u.language != script_language) for u in utts]
    usable = [not o for o in other]

    candidates = [c for sc in scenes for c in _candidates(utts, usable, sc)]
    candidates.sort(key=lambda c: (-c[0].coverage, -c[0].precision, c[1], c[2] - c[1]))
    used = [False] * len(utts)
    result: list[Take] = []
    for al, i, j, sc in candidates:
        if any(used[i:j + 1]):
            continue
        for k in range(i, j + 1):
            used[k] = True
        run = utts[i:j + 1]
        words = [w for u in run for w in u.words]
        result.append(Take(file, sc.id, run[0].start, run[-1].end, round(al.coverage, 4), _status(al),
                           al.missing, al.extra, words, _language(run)))

    chatter: list[dict] = []
    for k, u in enumerate(utts):
        if used[k]:
            continue
        if other[k]:
            reason = OTHER_LANGUAGE
        elif u.end - u.start >= UNMATCHED_MIN and u.tokens:
            result.append(Take(file, None, u.start, u.end, 0.0, "unmatched", [], list(u.tokens),
                               list(u.words), u.language))
            continue
        else:
            reason = SHORT
        chatter.append({"start": u.start, "end": u.end, "text": u.text, "reason": reason})

    result.sort(key=lambda t: t.start)
    return result, chatter


def is_spoken(transcript: dict, min_speech: float = 3.0) -> bool:
    """True when some stretch of continuous speech lasts at least min_speech seconds."""
    return any(u.end - u.start >= min_speech and u.tokens for u in _utterances(transcript))
