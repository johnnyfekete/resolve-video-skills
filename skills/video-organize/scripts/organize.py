#!/usr/bin/env python3
"""video-organize CLI: footage folder + storyboard -> takes, scores, b-roll catalogue, TAKES.md and issues.

    organize.py --workdir W run --footage DIR [--storyboard FILE] [--every 2] [--yes]
    organize.py --workdir W describe PAYLOAD.json
    organize.py --workdir W answer SCENE FILE LETTER
    organize.py --workdir W pick TAKE_ID
    organize.py --workdir W report

Runs with Resolve closed. State lives in <workdir>/.edit/organize/ (takes.json, broll.json, transcripts/,
frames/); TAKES.md is written to the workdir and issues go to EDIT_CHECKLIST.md through resolve-bridge.
"""
from __future__ import annotations

import argparse
import collections
import dataclasses
import difflib
import json
import os
import string
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, os.environ.get("RVS_BRIDGE") or str(HERE.parent.parent / "resolve-bridge" / "scripts"))

import broll  # noqa: E402
import report  # noqa: E402
import rvs  # noqa: E402
import rvs_audio  # noqa: E402
import score  # noqa: E402
import store  # noqa: E402
import storyboard  # noqa: E402
import takes as takes_mod  # noqa: E402
import transcribe  # noqa: E402
from storyboard import Scene, tokens  # noqa: E402
from takes import Take  # noqa: E402

VIDEO_EXT = {".mov", ".mp4", ".m4v", ".mxf", ".avi", ".mkv", ".mts", ".m2ts"}
AUDIO_EXT = {".wav", ".m4a", ".mp3", ".aiff", ".aif", ".flac"}
GROUP_SIMILARITY = 0.6  # difflib ratio on token lists that puts two takes in one group (no storyboard)
STEP = "organize"
TAKE_FIELDS = [f.name for f in dataclasses.fields(Take)]
SCORE_FIELDS = ("score", "reasons", "verdict", "note", "broken")


class OrganizeError(Exception):
    pass


def _org(workdir: Path) -> Path:
    return workdir / rvs.STATE_DIR / "organize"


def _config(workdir: Path) -> dict:
    path = workdir / rvs.STATE_DIR / "config.json"
    return json.loads(path.read_text()) if path.exists() else {}


def _scan(footage: Path) -> list[Path]:
    """Video and audio-only files under `footage`, recursive, hidden files and folders skipped."""
    found = []
    for p in sorted(footage.rglob("*")):
        rel = p.relative_to(footage)
        if any(part.startswith(".") for part in rel.parts) or not p.is_file():
            continue
        if p.suffix.lower() in VIDEO_EXT | AUDIO_EXT:
            found.append(p)
    return found


def _is_audio(file: str) -> bool:
    return Path(file).suffix.lower() in AUDIO_EXT


def _take(d: dict) -> Take:
    return Take(**{k: d[k] for k in TAKE_FIELDS})


def _scenes(data: dict) -> list[Scene]:
    return [Scene(**s) for s in data.get("scenes", [])]


# ---------------------------------------------------------------- steps of `run`

def _transcribe(files: list[Path], org: Path) -> tuple[dict, list[dict]]:
    """{file: transcript or None} and the file list with kinds. A video with no audio stream is b-roll."""
    transcripts, infos = {}, []
    for p in files:
        f = str(p)
        audio_only = _is_audio(f)
        if not transcribe.audio_streams(f):
            if audio_only:
                print(f"warning: {f} has no audio stream; skipped")
                continue
            transcripts[f] = None
        else:
            transcripts[f] = transcribe.transcribe(f, org / "transcripts")
        t = transcripts[f]
        spoken = audio_only or (t is not None and takes_mod.is_spoken(t))
        info = {"file": f, "kind": "spoken" if spoken else "broll", "audio_only": audio_only,
                "duration": t["duration"] if t else rvs_audio.probe_duration(f)}
        if t and t.get("warning"):
            info["warning"] = t["warning"]
            print(f"warning: {f}: {t['warning']}")
        infos.append(info)
    return transcripts, infos


def _group(found: list[Take]) -> list[Scene]:
    """No storyboard: put takes with similar text into G01, G02... in order of first appearance."""
    groups: list[tuple[Scene, list[str]]] = []
    for t in found:
        toks = [tok for w in t.words for tok in tokens(w["w"])]
        ratios = [difflib.SequenceMatcher(None, first, toks, autojunk=False).ratio() for _, first in groups]
        if ratios and max(ratios) >= GROUP_SIMILARITY:
            sc = groups[ratios.index(max(ratios))][0]
        else:
            sc = Scene(f"G{len(groups) + 1:02d}", " ".join(w["w"].strip() for w in t.words))
            groups.append((sc, toks))
        t.scene, t.status = sc.id, "grouped"
    return [sc for sc, _ in groups]


def _score(t: Take, scene: Scene | None, chatter: list[dict], config: dict, mode: str) -> dict:
    if t.status == "unmatched":
        return {}
    metrics = score.audio_metrics(t.file, t.start, t.end)
    if mode == "none":
        fraction, reasons = score._audio_fraction(metrics)
        value, broken = round(100 * fraction), []
    else:
        broken = score.broken_parts(t, scene)
        value, reasons = score.score(t, scene, metrics, broken)
    out = {"score": value, "reasons": reasons, "broken": broken, "note": None,
           "verdict": score.verdict(t, chatter, config.get("approve_phrases") or [],
                                    config.get("reject_phrases") or [])}
    heard = [p for p in config.get("phrases") or [] if score.verdict(t, chatter, [p], []) == "approved"]
    if heard:
        out["note"] = "heard after the take: " + ", ".join(f'"{p}"' for p in heard) + \
                      " (verdict phrase, meaning unknown; ranking unchanged)"
    return out


def _letters(data: dict, scenes: list[Scene], found: list[Take], audio_only: set[str]) -> None:
    """Shot letter per file per scene; new doubts join the open questions that still apply."""
    def diff(a: str, ta: float, b: str, tb: float) -> float:
        return 0.0 if a in audio_only or b in audio_only else store.frame_diff(a, ta, b, tb)

    setups = data.setdefault("setups", {})
    questions = []
    for sc in scenes:
        scene_takes = [t for t in found if t.scene == sc.id]
        if not scene_takes:
            continue
        letters, qs = store.shot_letters(sc, scene_takes, diff, setups, prior=data["shots"].get(sc.id))
        data["shots"][sc.id] = letters
        questions += qs
    for q in data.get("questions", []):
        if all(f in data["shots"].get(q["scene"], {}) for f in q["files"]) and \
                not any(n["scene"] == q["scene"] and n["files"][1] == q["files"][1] for n in questions):
            questions.append(q)
    data["questions"] = questions


def _analyse(data: dict, mode: str) -> None:
    """Rank every scene's takes and suggest patches for the best one (stored as take ids)."""
    scenes = {sc.id: sc for sc in _scenes(data)}
    data["ranking"], data["patches"] = {}, {}
    by_scene = collections.defaultdict(list)
    for d in data["takes"]:
        if d.get("id"):
            by_scene[d["scene"]].append(d)
    for sid, ds in by_scene.items():
        objs = {d["id"]: _take(d) for d in ds}
        ranked = score.rank([{"take": objs[d["id"]], "score": d.get("score", 0), "verdict": d.get("verdict"),
                              "id": d["id"]} for d in ds])
        data["ranking"][sid] = [e["id"] for e in ranked]
        best = report.best_id(data, sid)
        if mode == "none" or sid not in scenes or best not in objs:
            continue
        if any(d["id"] != best and d["status"] in score.STATUS_OK for d in ds):
            continue  # prefer a different take of the whole scene
        id_of = {id(o): i for i, o in objs.items()}
        found = score.patches(objs[best], [o for i, o in objs.items() if i != best], scenes[sid])
        if found:
            data["patches"][sid] = [{"base": id_of[id(p["base"])], "from": id_of[id(p["from"])],
                                     "clauses": p["clauses"], "from_text": p["from_text"]} for p in found]


def _where(sid: str, t: dict | None) -> str:
    return f"{sid}, {Path(t['file']).name} {report.tc(t['start'])}" if t else sid


def _issues(data: dict, mode: str, broll_data: dict) -> list[tuple[str, str]]:
    by_id = {t["id"]: t for t in data["takes"] if t.get("id")}
    wanted: list[tuple[str, str]] = []
    scenes = _scenes(data)
    if mode != "none":
        broll_files = [f["file"] for f in data.get("files", []) if f["kind"] == "broll"]
        described = all(broll_data.get(f, {}).get("frames") for f in broll_files)
        matched = {m["scene"] for e in broll_data.values() for m in e.get("matches") or []}
        for sc in scenes:
            ds = [t for t in by_id.values() if t["scene"] == sc.id]
            best = by_id.get(report.best_id(data, sc.id))
            usable = [t for t in ds if t["status"] in score.STATUS_OK]
            if tokens(sc.text) and not usable:
                wanted.append((f"{sc.id}: no complete or reworded take", _where(sc.id, best)))
            covered = {c for p in data["patches"].get(sc.id, []) for c in p["clauses"]}
            unfixed = {b["clause"] for b in best.get("broken") or []} - covered if best else set()
            if unfixed and not any(t is not best for t in usable):
                wanted.append((f"{sc.id}: best take {best['id']} has a broken part no whole-clause patch can "
                               "fix; it needs a patch smaller than a clause or a new take", _where(sc.id, best)))
            if sc.broll and sc.id not in matched and described:
                wanted.append((f"{sc.id}: no b-roll found for \"{sc.broll}\"", sc.id))
    for p in store.stale_picks(data):
        wanted.append((f"{p['scene']}: your pick {p['id']} no longer matches a take", _where(p["scene"], p)))
    return wanted


def _sync_issues(workdir: Path, data: dict, mode: str, broll_data: dict) -> None:
    """Add the issues that apply now; resolve the ones this skill added earlier that no longer apply."""
    s = rvs.Session(workdir, connect_now=False)
    s.init_state()
    wanted = _issues(data, mode, broll_data)
    ours = set(data.get("issue_ids", []))
    for text, where in wanted:
        issue = s.add_issue(STEP, text, where) or next(
            i for i in s.state["issues"] if i["step"] == STEP and i["text"] == text)
        ours.add(issue["id"])
    texts = {text for text, _ in wanted}
    for i in list(s.state["issues"]):
        if i["id"] in ours and i["status"] == "open" and i["text"] not in texts:
            s.resolve_issue(i["id"], "no longer applies after video-organize re-ran")
    data["issue_ids"] = sorted(ours)


def _load_broll(org: Path) -> dict:
    path = org / "broll.json"
    return json.loads(path.read_text()) if path.exists() else {}


def _finish(workdir: Path, data: dict, issues: bool = True) -> None:
    """Write TAKES.md (and sync issues), then save the store."""
    org = _org(workdir)
    broll_data = _load_broll(org)
    mode = data.get("mode", "none")
    (workdir / "TAKES.md").write_text(report.render(_scenes(data), mode, data, broll_data))
    if issues:
        _sync_issues(workdir, data, mode, broll_data)
    store.save(org, data)


# ---------------------------------------------------------------- commands

def cmd_run(workdir: Path, args) -> int:
    org = _org(workdir)
    config = _config(workdir)
    if args.storyboard:
        sb_path = str(Path(args.storyboard).resolve())
    else:
        sb = config.get("storyboard")
        sb_path = str((workdir / sb).resolve()) if sb else None
    scenes, mode = storyboard.load(sb_path)
    footage = Path(args.footage).resolve()
    if not footage.is_dir():
        raise OrganizeError(f"No footage folder at {footage}")
    files = _scan(footage)
    if not files:
        raise OrganizeError(f"No video or audio files in {footage}")
    n_audio = sum(_is_audio(str(p)) for p in files)
    total = sum(rvs_audio.probe_duration(str(p)) for p in files)
    print(f"Footage: {footage}")
    print(f"  {len(files) - n_audio} recordings, {n_audio} audio-only files, {report.tc(total)} in all")
    print(f"  {len(scenes)} scenes in {sb_path}" if sb_path else
          "  no storyboard or script: takes will be grouped by similar text")
    if not args.yes:
        print("Check this is the right folder with the user, then run again with --yes.")
        return 0

    data = store.load(org)
    transcripts, infos = _transcribe(files, org)
    spoken = [i["file"] for i in infos if i["kind"] == "spoken"]
    langs = [transcripts[f]["language"] for f in spoken if transcripts[f] and transcripts[f].get("language")]
    language = collections.Counter(langs).most_common(1)[0][0] if langs else None

    found, chatter = [], {}
    for f in spoken:
        ts, chatter[f] = takes_mod.find_takes(transcripts[f], scenes, language)
        found += ts
    if mode == "none":
        scenes = _group(found)
    by_id = {sc.id: sc for sc in scenes}
    extras = [_score(t, by_id.get(t.scene), chatter[t.file], config, mode) for t in found]
    _letters(data, scenes, [t for t in found if t.scene], {i["file"] for i in infos if i["audio_only"]})
    data["takes"] = store.assign_ids(data["takes"], found, data["shots"], issued=data["issued"])
    for d, extra in zip(data["takes"], extras):
        d.update(extra)
    data.update({"mode": mode, "storyboard": sb_path, "footage": str(footage), "files": infos,
                 "chatter": chatter, "scenes": [dataclasses.asdict(sc) for sc in scenes]})

    old_broll, new_broll = _load_broll(org), {}
    for info in infos:
        if info["kind"] != "broll":
            continue
        f = info["file"]
        frames = broll.extract_frames(f, org / "frames", args.every)
        prev = old_broll.get(f, {})
        new_broll[f] = {"frames": prev.get("frames", {}), "matches": prev.get("matches", []),
                        "frame_dir": str(Path(frames[0]["image"]).parent) if frames else None,
                        "every": args.every, "samples": frames, "moments": broll.best_moments(frames)}
    org.mkdir(parents=True, exist_ok=True)
    (org / "broll.json").write_text(json.dumps(new_broll, indent=2, sort_keys=True))

    _analyse(data, mode)
    _finish(workdir, data)
    s = rvs.Session(workdir, connect_now=False)
    missing = sum("no complete or reworded take" in text for text, _ in
                  _issues(data, mode, new_broll)) if mode != "none" else 0
    s.record_step(STEP, f"{len(spoken) + len(new_broll)} recordings, "
                  f"{sum(1 for t in data['takes'] if t.get('id'))} takes, {missing} scenes without a take")
    _next_steps(workdir, data, new_broll)
    return 0


def _next_steps(workdir: Path, data: dict, broll_data: dict) -> None:
    with_id = [t for t in data["takes"] if t.get("id")]
    print(f"Wrote {workdir / 'TAKES.md'}: {len(with_id)} takes, "
          f"{len(data['takes']) - len(with_id)} unmatched clips, {len(broll_data)} b-roll files.")
    todo = {f: e for f, e in broll_data.items() if not e.get("frames") and e.get("frame_dir")}
    if todo:
        print("\nDescribe the b-roll frames. Each folder holds one JPEG per sample: frame N is at N x every s.")
        for f, e in todo.items():
            print(f"  - {f} (every {e['every']} s):")
            print(f"    {e['frame_dir']}")
        print('Then write {"<recording path>": {"frames": {"<seconds>": "what is on screen"}, '
              '"matches": [{"scene": "S04", "start": 2, "end": 6, "why": "..."}]}} to a JSON file and run: '
              "organize.py describe FILE")
    if data["questions"]:
        print("\nAsk the user, then run: organize.py answer SCENE FILE LETTER")
        for q in data["questions"]:
            print(f"  - {q['scene']}: is {q['files'][1]} the same camera setup as {q['files'][0]}? "
                  f"(frame difference {q['diff']})")


def cmd_describe(workdir: Path, args) -> int:
    data = store.load(_org(workdir))
    root = Path(data.get("footage") or ".")
    payload = {str(Path(f).resolve() if Path(f).is_absolute() else (root / f).resolve()): entry
               for f, entry in json.loads(Path(args.payload).read_text()).items()}
    broll.record_descriptions(_org(workdir), payload, {sc.id for sc in _scenes(data)})
    _finish(workdir, data)
    return 0


def cmd_answer(workdir: Path, args) -> int:
    data = store.load(_org(workdir))
    letter = args.letter.upper()
    if len(letter) != 1 or letter not in string.ascii_uppercase:
        raise OrganizeError(f"A shot letter is one letter A-Z, not {args.letter!r}")
    path = Path(args.file)
    if not path.is_absolute():
        path = Path(data.get("footage") or ".") / path
    file = str(path.resolve())
    shots = data["shots"].get(args.scene, {})
    if file not in shots:
        raise OrganizeError(f"{file} has no take in {args.scene}")
    data["questions"] = [q for q in data["questions"] if not (q["scene"] == args.scene and q["files"][1] == file)]
    if shots[file] != letter:
        shots[file] = letter
        keep = [d for d in data["takes"] if not (d["scene"] == args.scene and d["file"] == file)]
        renewed = store.assign_ids(keep, [_take(d) for d in data["takes"]], data["shots"], issued=data["issued"])
        moved = {}
        for new, old in zip(renewed, data["takes"]):
            new.update({k: old[k] for k in SCORE_FIELDS if k in old})
            if old.get("id") and new["id"] != old["id"]:
                moved[old["id"]] = new["id"]
        data["takes"] = renewed
        for pick in data["picks"].values():  # same file and range, new id: the pick follows it
            pick["id"] = moved.get(pick["id"], pick["id"])
    _analyse(data, data.get("mode", "none"))
    _finish(workdir, data)
    return 0


def cmd_pick(workdir: Path, args) -> int:
    data = store.load(_org(workdir))
    try:
        store.apply_pick(data, args.take_id)
    except KeyError:
        raise OrganizeError(f"No take {args.take_id}") from None
    _analyse(data, data.get("mode", "none"))
    _finish(workdir, data)
    scene = next(s for s, p in data["picks"].items() if p["id"] == args.take_id)
    print(f"Picked {args.take_id} for {scene}. TAKES.md updated.")
    return 0


def cmd_report(workdir: Path, args) -> int:
    data = store.load(_org(workdir))
    if not data["takes"] and not data.get("files"):
        raise OrganizeError("Nothing organized yet. Run: organize.py run --footage DIR")
    _finish(workdir, data, issues=False)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="organize.py", description=__doc__.split("\n")[0])
    ap.add_argument("--workdir", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="find, score and catalogue takes in a footage folder")
    p.add_argument("--footage", required=True)
    p.add_argument("--storyboard")
    p.add_argument("--every", type=float, default=2.0, help="seconds between b-roll frames")
    p.add_argument("--yes", action="store_true", help="go ahead after the counts (confirm the folder first)")
    p = sub.add_parser("describe", help="record the agent's b-roll frame descriptions")
    p.add_argument("payload")
    p = sub.add_parser("answer", help="settle a shot-letter question")
    p.add_argument("scene")
    p.add_argument("file")
    p.add_argument("letter")
    p = sub.add_parser("pick", help="record the user's pick for a scene")
    p.add_argument("take_id")
    sub.add_parser("report", help="rewrite TAKES.md")
    args = ap.parse_args(argv)
    workdir = Path(args.workdir).resolve()
    commands = {"run": cmd_run, "describe": cmd_describe, "answer": cmd_answer, "pick": cmd_pick,
                "report": cmd_report}
    try:
        return commands[args.cmd](workdir, args)
    except (OrganizeError, ValueError, OSError, rvs.RvsError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
