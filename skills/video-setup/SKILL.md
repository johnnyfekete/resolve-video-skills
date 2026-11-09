---
name: video-setup
description: Prepares a video project for the resolve-video-skills. Checks tools, DaVinci Resolve Studio, timeline settings and footage formats, records the project's settings, starts tracking the first timeline, and writes an AGENTS.md with the editing rules for that video. Use once at the start of a new video, when the user asks to set up a video project, or when a project has no AGENTS.md or edit state.
---

# video-setup

Run this once per video, before any other editing skill. It is safe to re-run. Requires the **resolve-bridge** skill installed next to this one (or set `RVS_BRIDGE`).

Run scripts as `python3 <this skill's folder>/scripts/setup.py --workdir <folder> <command>`.

## Workflow

1. **Pick the working folder.** One folder per video, used by every future session. Ask the user if it is not the current directory.
2. **Check.** Read-only:
   ```bash
   python3 scripts/setup.py --workdir <folder> check
   ```
   Fix every `FAIL` with the user before continuing. Show each `WARN` and let the user decide.
3. **Ask for what the check cannot know.** Ask only what is missing, and accept "not yet" for anything optional:

   | Question | Option | Needed |
   |---|---|---|
   | Which timeline should editing start from? | `--timeline` | When a first cut exists |
   | Where is the script? | `--script` | Yes |
   | Which steps does this video not need, and why? | `--skip step=reason` (repeat) | Optional |
   | What do you say on set to judge a take? | `--phrases "one more time" "that was good"` | Optional |
   | Music and SFX folders? | `--music`, `--sfx` | Before the sound step |
   | A saved colour look? | `--look file.drx` | Before the colour step |
   | Delivery format? | `--delivery "1080p, -14 LUFS"` | Optional |

   Steps: ingest, assemble, broll, tighten, framing, color, sound, graphics, captions, qc, deliver, shorts.
4. **Write.**
   ```bash
   python3 scripts/setup.py --workdir <folder> write --timeline "<name>" --script "<where>" --skip "ingest=clips already organised"
   ```
5. **Finish.** Show the user `EDIT_CHECKLIST.md`, name the next step, and tell them to start future sessions in this folder.

## What it writes

- `AGENTS.md`: the rules every agent working in this folder follows, and this video's details. Only the block between the `resolve-video-skills` markers is generated. Anything the user adds outside it is kept.
- `.edit/config.json`: the answers above.
- `EDIT_CHECKLIST.md` and `.edit/state.json`, through resolve-bridge, once a timeline is tracked.

## Behaviour

- `write` changes nothing in Resolve. Tracking a timeline only reads it.
- A project that is already tracked keeps its history. `--timeline` is ignored. To restart from another timeline, use resolve-bridge's `adopt`.
- `check` reports timeline and colour settings but does not change them. Changing them on an existing cut can reframe every clip, so that is the user's call.
