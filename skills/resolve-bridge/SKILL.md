---
name: resolve-bridge
description: Shared base for the resolve-video-skills. Connects to DaVinci Resolve Studio, versions every edit step as a new timeline, saves the project after each step, archives old versions and detects hand edits made in Resolve between sessions. Use before and after any step that changes a Resolve timeline, when the user asks "where are we" on a video edit, or to start tracking an existing timeline.
---

# resolve-bridge

Every timeline change in this skill set goes through `scripts/rvs.py`. It guarantees:

1. **No step edits the previous version.** Each step works on a new timeline `<base>_vNN_<step>`.
2. **Every step ends saved.** The Resolve project is saved and the step is recorded in `<workdir>/.edit/state.json`. A step is not done until both have happened.
3. **Hand edits survive.** Changes the user made in Resolve since the last step are detected and recorded before anything new happens.
4. **Old versions are kept.** The previous timeline moves to the `Archive` media pool folder. Nothing is deleted.
5. **There is always a checklist.** `<workdir>/EDIT_CHECKLIST.md` is regenerated after every change: next step, pipeline status, open issues, history, and a notes section the user owns.

## Before you start

- Resolve Studio is open with the right project loaded, and Preferences > System > General > External scripting using is set to **Local**.
- The working folder (`--workdir`, default: current directory) is the user's folder for this video. State lives in `<workdir>/.edit/`. Ask the user which folder to use if it is not obvious, and use the same one every session.

Run the CLI as `python3 <this skill's folder>/scripts/rvs.py --workdir <folder> <command>`.

## Start of every session

```bash
python3 scripts/rvs.py --workdir <folder> status
```

- **"No edit state"**: ask the user which timeline to start from, then `adopt --timeline "<name>"`. Adopting changes nothing in Resolve.
- **"IN PROGRESS"**: a previous session stopped mid-step. Tell the user, then either `finish --summary "..."` (if the work on that timeline is complete) or `abandon`.
- **"Hand edits since last step: N"**: the user changed the timeline in Resolve. `begin` records them automatically; before a `build`, run `sync` first.

## The checklist

`EDIT_CHECKLIST.md` in the working folder is what the user reads. Point them to it at the end of every step.

- **Pipeline**: ingest, assemble, broll, tighten, framing, color, sound, graphics, captions, qc, deliver, shorts. A step is done when the history has an entry with that step name. Steps the video does not need: `skip <step> --reason "..."` (undo with `unskip`).
- **Needs attention (issues)**: things a person has to check, decide, film or fix. Skills add them with `s.add_issue(step, text, where)`; you can too (`issue add "text" --step framing --where "S08 at 00:57"`). The user closes one by ticking its box in the file (read back on the next run) or with `issue resolve I3 --note "..."`.
- **Your notes**: free text between the notes markers. Never overwritten.

At the start of a session, read the open issues out to the user before starting new work. When your step finds something a person must look at, add an issue for it rather than only mentioning it in chat, so it survives the session. Write `where` as clip name + timecode in the *new* timeline.

## Making a change: pick one mode

**Fork (default).** Duplicates the current timeline, so grades, titles, effects and anything else on it carry over. Use for framing, colour, sound, captions, graphics: any step that changes items in place.

```python
import sys; sys.path.insert(0, "<this skill's folder>/scripts"); import rvs
s = rvs.Session("<folder>")
tl = s.begin_step("framing")        # new timeline, now open in Resolve
for item in tl.GetItemListInTrack("video", 1):
    item.SetProperties({"ZoomX": 1.12, "ZoomY": 1.12})
s.finish_step("punch-ins on 12 jump cuts")   # archives previous, saves, records
```

CLI equivalent: `begin <step>`, change the timeline, then `finish --summary "..."`.

**Build.** Creates a fresh timeline from a plan file. Use only for structural edits (choosing takes, cutting, reordering), which are easier to express as a list of clips than as in-place changes.

1. Start from the current plan: `s.load_plan()` (or `export -o plan.json`).
2. Change its `items` (see [references/plan-format.md](references/plan-format.md)).
3. `build plan.json --step tighten --summary "..."`.

Build refuses to run if there are unrecorded hand edits, so a plan made from an old version can never overwrite the user's changes. Titles, generators and other non-media items are not rebuilt; the CLI warns when the current timeline has any.

## Commands

| Command | What it does |
|---|---|
| `status` | Current version, history, pending step, count of hand edits |
| `adopt [--timeline N] [--base B] [--archive-folder F]` | Start tracking an existing timeline as v01 (`--archive-folder ""` turns archiving off) |
| `check` | List hand edits since the last step (exit 1 if any) |
| `sync [--summary S]` | Record hand edits as their own history entry |
| `export -o FILE [--timeline N]` | Write a timeline as a plan file |
| `begin STEP` / `finish --summary S` / `abandon` | Fork workflow |
| `build PLAN --step STEP --summary S` | Build workflow |
| `checklist` | Regenerate `EDIT_CHECKLIST.md` |
| `issue add TEXT [--step S] [--where W]` / `issue resolve ID [--note N]` / `issue list` | Things a person must check |
| `skip STEP [--reason R]` / `unskip STEP` | Mark a pipeline step as not needed |

## Rules for skills that use this

- Never call `CreateEmptyTimeline`, `DuplicateTimeline` or `SaveProject` yourself. Use `begin_step` / `build` / `finish_step`.
- Never modify the timeline named in `state.current` directly. Only the one returned by `begin_step`.
- Always end with `finish_step` (or `abandon_step` on failure), even when the change turned out to be empty, so the next session does not find a pending step.
- Report the new timeline name and version to the user at the end, and point them to `EDIT_CHECKLIST.md`.
- Anything a person must check goes in as an issue (`add_issue`), not only into chat.

## Known Resolve behaviour handled here

- `AppendToTimeline` `endFrame` is exclusive. Lengths are exact when the plan's `src_end` is used.
- Multi-channel clips (iPhone records 6 channels) spread across several audio tracks when appended. The bridge keeps one audio item per plan item and restores its recorded channel mapping.
- With high-fps sources Resolve rounds the source end by up to one source frame. Plans are compared on source start plus timeline length.
