#!/usr/bin/env python3
"""video-tighten: natural cut edges and shorter pauses, as a new Resolve timeline version.

Reads the current plan from resolve-bridge, measures speech in every item that has audio, then:
  edges   in-point = first word - PRE, out-point = last word + POST. May extend an edge by up to
          MAX_EXTEND into silence of the same take (fixes clipped words), never into other speech.
  pauses  silences inside an item longer than PAUSE_MAX are shortened to PAUSE_KEEP by splitting
          the item (creates a jump cut; video-framing hides those).
  ripple  later items move up. Video-only items (b-roll, overlays) are remapped onto the new timing.

  python3 tighten.py --workdir <folder>              # dry run: writes report + plan, changes nothing
  python3 tighten.py --workdir <folder> --apply      # builds <base>_vNN_tighten and saves
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BRIDGE = Path(os.environ.get("RVS_BRIDGE", HERE.parent.parent / "resolve-bridge" / "scripts"))
sys.path.insert(0, str(BRIDGE))
try:
    import rvs  # noqa: E402
    import rvs_audio  # noqa: E402
except ImportError:
    sys.exit(f"resolve-bridge not found at {BRIDGE}. Install it next to this skill or set RVS_BRIDGE.")


def resolve_pace(opt, workdir) -> str:
    """Fill opt.pause_max / opt.pause_keep from the pace. Order: an explicit --pause-max / --pause-keep,
    then --pace, then the pace video-setup saved in .edit/config.json, then the default. Returns where
    the pace came from."""
    source = "option"
    if not opt.pace:
        cfg_path = Path(workdir) / rvs.STATE_DIR / "config.json"
        try:
            saved = json.loads(cfg_path.read_text()).get("pace") if cfg_path.exists() else None
        except ValueError:
            saved = None
        if saved in rvs.PACES:
            opt.pace, source = saved, "project setting"
        else:
            if saved:
                print(f"unknown pace '{saved}' in {cfg_path}; using {rvs.DEFAULT_PACE}", file=sys.stderr)
            opt.pace, source = rvs.DEFAULT_PACE, "default"
    preset = rvs.PACES[opt.pace]
    if opt.pause_max is None:
        opt.pause_max = preset["pause_max"]
    if opt.pause_keep is None:
        opt.pause_keep = preset["pause_keep"]
    return source


def plan_tighten(plan: dict, opt) -> tuple[dict, dict]:
    tl_fps = plan["timeline"]["fps"]
    items = plan["items"]
    ignore = set(opt.ignore_audio_tracks or [])
    speech = [d for d in items if d["media"] in ("both", "audio") and d.get("audio_track") not in ignore
              and d.get("enabled", True)]
    speech.sort(key=lambda d: (d["start"], d.get("track") or 0))
    others = [d for d in items if d not in speech]
    report = {"settings": vars(opt).copy(), "items": [], "flags": []}
    flagged = []

    def flag(d, text):
        flagged.append((d, text))
    report["settings"].pop("func", None)

    # 1. measure + decide new source ranges per speech item
    durations = {}
    prev_end_old = None
    for d in speech:
        sf = d.get("src_fps") or tl_fps
        s0, e0 = d["src_start"] / sf, d["src_start"] / sf + (d["end"] - d["start"]) / tl_fps
        entry = {"id": d["id"], "clip": d["clip"], "old": [d["start"], d["end"]], "src": [round(s0, 3), round(e0, 3)]}
        overlap = prev_end_old is not None and d["start"] < prev_end_old
        prev_end_old = max(prev_end_old or 0, d["end"])
        skipped = d["clip"] in opt.skip or str(d["start"]) in opt.skip
        if overlap or skipped or not d.get("path"):
            entry["note"] = ("skipped on request; left as is" if skipped else
                             "overlaps the previous speech item; left as is" if overlap else "no file path; left as is")
            if not skipped:
                flag(d, entry["note"])
            d["_pieces"] = [(s0, e0)]
            report["items"].append(entry)
            continue
        if d["path"] not in durations:
            durations[d["path"]] = rvs_audio.probe_duration(d["path"])
        dur = durations[d["path"]]
        w0, w1 = max(0.0, s0 - opt.window), min(dur, e0 + opt.window)
        vad = rvs_audio.speech_segments(d["path"], w0, w1 - w0)
        segs = vad["segments"]
        inside = [s for s in segs if s[1] > s0 and s[0] < e0]
        if not inside:
            entry["note"] = "no speech found; left as is"
            flag(d, "no speech detected")
            d["_pieces"] = [(s0, e0)]
            report["items"].append(entry)
            continue
        first, last = inside[0], inside[-1]
        # Bounds come from *any* audible sound (breaths, mumbles, the next take), not only words.
        act = vad["activity"]
        before = [s for s in segs if s[1] <= first[0]] + [a for a in act if a[1] < first[0] - 0.05]
        after = [s for s in segs if s[0] >= last[1]] + [a for a in act if a[0] > last[1] + 0.05]
        before.sort(key=lambda x: x[1])
        after.sort(key=lambda x: x[0])
        lo = max(0.0, s0 - opt.max_extend, (before[-1][1] + opt.guard) if before else 0.0)
        hi = min(dur, e0 + opt.max_extend, (after[0][0] - opt.guard) if after else dur)
        new_in = max(lo, first[0] - opt.pre)
        new_out = min(hi, last[1] + opt.post)
        if first[0] < lo:
            flag(d, f"first word starts {s0 - first[0]:.2f}s before the in-point and other sound is right before "
                    "it, so the edge cannot move. Listen to this cut; it may need a different take.")
        if last[1] > hi:
            flag(d, f"last word runs {last[1] - e0:.2f}s past the out-point and other sound follows it, so the "
                    "edge cannot move. Listen to this cut; it may need a different take.")
        # pauses
        cuts = []
        if not opt.no_pauses:
            words = [s for s in segs if s[1] > new_in and s[0] < new_out]
            for a, b in zip(words, words[1:]):
                gap = b[0] - a[1]
                if gap > opt.pause_max:
                    cuts.append((a[1] + opt.pause_keep * 0.6, b[0] - opt.pause_keep * 0.4))
        pieces, cur = [], new_in
        for c0, c1 in cuts:
            pieces.append((cur, c0))
            cur = c1
        pieces.append((cur, new_out))
        # never leave slivers: merge pieces shorter than min_piece back (undo that cut)
        merged = [pieces[0]]
        for p in pieces[1:]:
            if p[1] - p[0] < opt.min_piece or merged[-1][1] - merged[-1][0] < opt.min_piece:
                merged[-1] = (merged[-1][0], p[1])
            else:
                merged.append(p)
        d["_pieces"] = merged
        entry.update({"speech": [first[0], last[1]], "new_src": [[round(a, 3), round(b, 3)] for a, b in merged],
                      "head": round(new_in - s0, 2), "tail": round(new_out - e0, 2),
                      "pauses_cut": len(merged) - 1,
                      "removed": round((e0 - s0) - sum(b - a for a, b in merged), 2)})
        report["items"].append(entry)

    # 2. lay out speech pieces, keeping original gaps between speech items
    new_items, mapping = [], []   # mapping: (old_s, old_e, item, [(src_a, src_b, new_start), ...])
    cursor_old, cursor_new = None, None
    for d in speech:
        sf = d.get("src_fps") or tl_fps
        if cursor_old is None:
            new_s = d["start"]
        elif d["start"] >= cursor_old:
            new_s = cursor_new + (d["start"] - cursor_old)
        else:
            new_s = cursor_new - (cursor_old - d["start"])
        pos, pmap = new_s, []
        for a, b in d["_pieces"]:
            n = max(1, round((b - a) * tl_fps))
            src_s = round(a * sf)
            piece = {k: v for k, v in d.items() if not k.startswith("_") and k != "id"}
            piece.update({"start": pos, "end": pos + n, "src_start": src_s,
                          "src_end": src_s + math.ceil(n * sf / tl_fps)})
            new_items.append(piece)
            pmap.append((a, b, pos))
            d.setdefault("_new_start", pos)
            pos += n
        mapping.append((d["start"], d["end"], d, pmap))
        if cursor_old is None or d["end"] >= cursor_old:
            cursor_old, cursor_new = d["end"], pos

    def remap(t: int) -> int:
        """Old timeline frame -> new timeline frame."""
        prev = None
        for old_s, old_e, d, pmap in mapping:
            if t < old_s:
                if prev is None:
                    return t
                p_old_e, p_new_e = prev
                return p_new_e + (t - p_old_e) if t >= p_old_e else p_new_e
            if old_s <= t < old_e:
                sf = d.get("src_fps") or tl_fps
                st = d["src_start"] / sf + (t - old_s) / tl_fps
                for a, b, ns in pmap:
                    if st < a:
                        return ns
                    if st < b:
                        return ns + round((st - a) * tl_fps)
                a, b, ns = pmap[-1]
                return ns + round((b - a) * tl_fps)
            a, b, ns = pmap[-1]
            prev = (old_e, ns + round((b - a) * tl_fps))
        p_old_e, p_new_e = prev
        return p_new_e + (t - p_old_e)

    def tc(frame):
        sec = frame / tl_fps
        return f"{int(sec // 60):02d}:{sec % 60:05.2f}"

    for d, text in flagged:
        report["flags"].append({"clip": d["clip"], "where": f"{d['clip']} at {tc(d.get('_new_start', d['start']))}",
                                "text": text})

    # 3. remap everything else (b-roll, overlays, music)
    for d in others:
        ns, ne = remap(d["start"]), remap(d["end"])
        if ne - ns < opt.min_overlay * tl_fps:
            report["flags"].append({"clip": d["clip"], "where": f"{d['clip']} near {tc(ns)}",
                                    "text": f"b-roll dropped: it would be under {opt.min_overlay}s after tightening. "
                                            "Re-place it if the section needs cover."})
            continue
        sf = d.get("src_fps") or tl_fps
        n = d.copy()
        n.pop("id", None)
        n.update({"start": ns, "end": ne, "src_end": d["src_start"] + math.ceil((ne - ns) * sf / tl_fps)})
        new_items.append(n)
        if (ne - ns) != (d["end"] - d["start"]):
            report["items"].append({"clip": d["clip"], "old": [d["start"], d["end"]], "remapped": [ns, ne]})

    new_items.sort(key=lambda d: (d.get("track") or 99, d.get("audio_track") or 0, d["start"]))
    for i, d in enumerate(new_items, 1):
        d["id"] = f"i{i:03d}"
    new_plan = {**plan, "items": new_items, "unsupported": []}
    old_len = max(d["end"] for d in items)
    new_len = max(d["end"] for d in new_items)
    report["runtime"] = {"before_s": round(old_len / tl_fps, 2), "after_s": round(new_len / tl_fps, 2)}
    report["counts"] = {"speech_items": len(speech), "pauses_cut": sum(e.get("pauses_cut", 0) for e in report["items"]),
                        "pieces_after": sum(1 for d in new_items if d["media"] in ("both", "audio"))}
    for d in items:
        d.pop("_pieces", None)
        d.pop("_new_start", None)
    return new_plan, report


def write_report(report: dict, path: Path):
    r = report
    lines = [f"# Tighten report\n",
             f"Runtime: {r['runtime']['before_s']}s -> {r['runtime']['after_s']}s "
             f"({r['runtime']['after_s'] - r['runtime']['before_s']:+.2f}s). "
             f"{r['counts']['speech_items']} speech items, {r['counts']['pauses_cut']} pauses shortened "
             f"(pace {r['settings'].get('pace')}: over {r['settings'].get('pause_max')}s -> "
             f"{r['settings'].get('pause_keep')}s).\n"]
    if r["flags"]:
        lines.append("## Check these by eye/ear\n")
        lines += [f"- **{f['where']}**: {f['text']}" for f in r["flags"]]
        lines.append("")
    lines.append("## Per item (seconds; head/tail: + = later/longer, - = earlier/shorter)\n")
    lines.append("| Clip | Timeline in | Head | Tail | Pauses cut | Removed |")
    lines.append("|---|---|---|---|---|---|")
    for e in r["items"]:
        if "head" in e:
            lines.append(f"| {e['clip']} | {e['old'][0]} | {e['head']:+.2f} | {e['tail']:+.2f} | {e['pauses_cut']} | {e['removed']:.2f} |")
    path.write_text("\n".join(lines) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workdir", default=os.environ.get("RVS_WORKDIR", "."))
    ap.add_argument("--apply", action="store_true", help="build the new timeline (default: dry run)")
    ap.add_argument("--plan", help="input plan file (default: current plan from the state)")
    ap.add_argument("--pre", type=float, default=0.22, help="air before the first word (s)")
    ap.add_argument("--post", type=float, default=0.38, help="air after the last word (s)")
    ap.add_argument("--max-extend", type=float, default=0.5, help="max an edge may grow into silence (s)")
    ap.add_argument("--guard", type=float, default=0.08, help="min distance to neighbouring speech (s)")
    ap.add_argument("--pace", choices=list(rvs.PACES),
                    help="pause preset (default: the pace saved by video-setup, else natural). " +
                         "; ".join(f"{k}: >{v['pause_max']}s -> {v['pause_keep']}s" for k, v in rvs.PACES.items()))
    ap.add_argument("--pause-max", type=float, help="pauses longer than this get shortened (s); overrides --pace")
    ap.add_argument("--pause-keep", type=float, help="what is left of a shortened pause (s); overrides --pace")
    ap.add_argument("--min-piece", type=float, default=0.5, help="never create a piece shorter than this (s)")
    ap.add_argument("--min-overlay", type=float, default=0.5, help="drop b-roll shorter than this after remap (s)")
    ap.add_argument("--window", type=float, default=1.0, help="audio analysed around each item (s)")
    ap.add_argument("--no-pauses", action="store_true", help="only fix edges")
    ap.add_argument("--skip", nargs="*", default=[],
                    help="leave these items untouched (except ripple): clip name, or timeline start frame")
    ap.add_argument("--ignore-audio-tracks", type=int, nargs="*", help="audio tracks that are not dialogue (music)")
    opt = ap.parse_args(argv)

    source = resolve_pace(opt, opt.workdir)
    print(f"pace {opt.pace} ({source}): pauses over {opt.pause_max}s -> {opt.pause_keep}s")
    try:
        s = rvs.Session(opt.workdir)
        s._require_state()
        if not opt.plan:
            changes = s.check()
            if changes:
                entry = s.sync("hand edits before tighten")
                print(f"recorded {len(changes)} hand edits as v{entry['version']:02d}")
        plan = json.loads(Path(opt.plan).read_text()) if opt.plan else s.load_plan()
        new_plan, report = plan_tighten(plan, opt)
        out = s.dir / "tighten"
        out.mkdir(parents=True, exist_ok=True)
        (out / "plan.json").write_text(json.dumps(new_plan, indent=1, ensure_ascii=False))
        (out / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
        write_report(report, out / "REPORT.md")
        rt = report["runtime"]
        print(f"runtime {rt['before_s']}s -> {rt['after_s']}s, {report['counts']['pauses_cut']} pauses shortened, "
              f"{len(report['flags'])} flags. Report: {out / 'REPORT.md'}")
        if opt.apply:
            summary = (f"pace {opt.pace}, edges {opt.pre}/{opt.post}s, "
                       f"{report['counts']['pauses_cut']} pauses -> {opt.pause_keep}s, "
                       f"runtime {rt['before_s']}s -> {rt['after_s']}s")
            entry = s.build(new_plan, "tighten", summary)
            print(f"v{entry['version']:02d} saved: {entry['timeline']}")
            added = [s.add_issue("tighten", f["text"], f["where"]) for f in report["flags"]]
            skipped = sum(1 for i in added if not i)
            added = [i for i in added if i]
            if added or skipped:
                line = f"{len(added)} issues added to {s.checklist_path.name}"
                if added:
                    line += " (" + ", ".join(i["id"] for i in added) + ")"
                print(line + (f", {skipped} already open" if skipped else ""))
        else:
            print("dry run: nothing changed in Resolve. Re-run with --apply to build.")
    except rvs.RvsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
