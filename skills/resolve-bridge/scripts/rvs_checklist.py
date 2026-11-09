"""EDIT_CHECKLIST.md: the human-readable view of a video's edit state.

The state file is the source of truth. The checklist is regenerated from it after every change, with two
things read back from the file first:
  - ticked issue boxes ("- [x] **I3** ...") resolve those issues
  - everything between the notes markers is kept exactly as the user wrote it
"""
from __future__ import annotations

import re
from pathlib import Path

FILENAME = "EDIT_CHECKLIST.md"
NOTES_START = "<!-- notes:start (kept as you write it) -->"
NOTES_END = "<!-- notes:end -->"

# The full pipeline, in order: (step name used in history, skill, what it does)
PIPELINE = [
    ("ingest", "video-ingest", "Transcribe raw clips, match them to the script, catalogue b-roll"),
    ("assemble", "video-assemble", "First cut: best take per line, b-roll placed"),
    ("broll", "video-assemble", "B-roll placed over the matching lines"),
    ("tighten", "video-tighten", "Natural cut edges, shorter pauses"),
    ("framing", "video-framing", "Declutter crop, punch-ins on jump cuts, eye-trace, push-ins"),
    ("color", "video-color", "HDR to Rec.709, one grade per setup"),
    ("sound", "video-sound", "Voice isolation, fades, loudness, music, SFX"),
    ("graphics", "video-graphics", "Animations and callouts"),
    ("captions", "video-captions", "Subtitles corrected against the script"),
    ("qc", "video-qc", "Re-transcribe the render, check cuts, loudness, gaps"),
    ("deliver", "video-deliver", "Render, chapters, description"),
    ("shorts", "video-shorts", "Vertical clips for social media"),
]

_TICK = re.compile(r"^\s*-\s*\[[xX]\]\s*\*\*(I\d+)\*\*")


def read_back(path: Path) -> tuple[set[str], str | None]:
    """(issue ids ticked in the file, notes text) from an existing checklist."""
    if not path.exists():
        return set(), None
    text = path.read_text()
    ticked = {m.group(1) for line in text.splitlines() if (m := _TICK.match(line))}
    notes = None
    if NOTES_START in text and NOTES_END in text:
        notes = text.split(NOTES_START, 1)[1].split(NOTES_END, 1)[0].strip("\n")
    return ticked, notes


def next_step(state: dict) -> tuple | None:
    done = {h["step"] for h in state.get("history", [])}
    skipped = set(state.get("skipped", {}))
    return next((p for p in PIPELINE if p[0] not in done and p[0] not in skipped), None)


def render(state: dict, notes: str | None, now: str) -> str:
    cur = state.get("current", {})
    hist = state.get("history", [])
    issues = state.get("issues", [])
    skipped = state.get("skipped", {})
    last_by_step = {}
    for h in hist:
        last_by_step[h["step"]] = h
    pending = state.get("pending")
    nxt = next_step(state)
    open_issues = [i for i in issues if i["status"] == "open"]
    done_issues = [i for i in issues if i["status"] != "open"]

    L = [f"# Edit checklist: {state.get('base_name', '')}", "",
         f"Resolve project **{state.get('resolve_project', '')}** · current timeline "
         f"**v{cur.get('version', 0):02d} {cur.get('timeline', '')}** · updated {now[:16].replace('T', ' ')}",
         "", "_This file is regenerated after every step. Tick an issue's box to close it. "
         "Write anything you like in **Your notes**; it is kept._", ""]

    L.append("## Next")
    L.append("")
    if pending:
        L.append(f"- **Finish or abandon the step in progress:** `{pending['step']}` on `{pending['timeline']}` "
                 f"(started {pending['date'][:16].replace('T', ' ')}).")
    if open_issues:
        n = len(open_issues)
        L.append(f"- **{n} open issue{'s' if n != 1 else ''}** below {'need' if n != 1 else 'needs'} a look.")
    if nxt:
        L.append(f"- Next step: **{nxt[0]}** ({nxt[1]}): {nxt[2]}.")
    else:
        L.append("- All pipeline steps are done or skipped.")
    L.append("")

    L.append("## Pipeline")
    L.append("")
    for name, skill, what in PIPELINE:
        if pending and pending["step"] == name:
            L.append(f"- [ ] **{name}** — in progress on `{pending['timeline']}`")
        elif name in last_by_step:
            h = last_by_step[name]
            L.append(f"- [x] **{name}** — v{h['version']:02d} `{h['timeline']}`, {h['date'][:10]}: {h['summary']}")
        elif name in skipped:
            L.append(f"- [x] ~~{name}~~ — skipped: {skipped[name] or 'no reason given'}")
        else:
            L.append(f"- [ ] **{name}** — {what}")
    L.append("")

    L.append("## Needs attention")
    L.append("")
    if open_issues:
        for i in open_issues:
            where = f" `{i['where']}`" if i.get("where") else ""
            L.append(f"- [ ] **{i['id']}** ({i['step']}){where}: {i['text']}")
    else:
        L.append("Nothing open.")
    L.append("")
    if done_issues:
        L.append("<details><summary>Resolved issues</summary>")
        L.append("")
        for i in done_issues:
            note = f" — {i['resolution']}" if i.get("resolution") else ""
            L.append(f"- [x] {i['id']} ({i['step']}): {i['text']}{note}")
        L.append("")
        L.append("</details>")
        L.append("")

    L.append("## Your notes")
    L.append("")
    L.append(NOTES_START)
    L.append(notes if notes is not None else "Shots still to film, ideas, decisions...")
    L.append(NOTES_END)
    L.append("")

    L.append("## History")
    L.append("")
    L.append("| Version | Date | Step | Timeline | Summary |")
    L.append("|---|---|---|---|---|")
    for h in hist:
        L.append(f"| v{h['version']:02d} | {h['date'][:16].replace('T', ' ')} | {h['step']} | `{h['timeline']}` | {h['summary']} |")
    L.append("")
    return "\n".join(L)
