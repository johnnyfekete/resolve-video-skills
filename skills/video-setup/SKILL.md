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
   | Which timeline should editing start from? | `--timeline` | Only when a first cut exists. Not needed before `video-organize` |
   | Where is the script? | `--script` | Yes |
   | Is there a storyboard file (one `## S01` block per scene, see `video-organize`)? | `--storyboard PATH` | Yes. Without one, takes cannot be matched to scenes |
   | Which steps does this video not need, and why? | `--skip step=reason` (repeat) | Optional |
   | What do you say on set to keep a take? | `--approve-phrases "very nice" "that was good"` | Optional |
   | What do you say on set to reject a take? | `--reject-phrases "one more time" "again"` | Optional |
   | Music and SFX folders? | `--music`, `--sfx` | Before the sound step |
   | A saved colour look? | `--look file.drx` | Before the colour step |
   | Delivery format? | `--delivery "1080p, -14 LUFS"` | Optional |
   | How fast should the talking feel? (see the pace table below) | `--pace natural\|brisk\|snappy` | Optional. Default `natural` |

   **Pace** sets how hard `video-tighten` cuts pauses inside a take, including pauses in the middle of a sentence. Offer the examples and suggest `natural` unless the user asks for faster. Every cut pause becomes a jump cut that `video-framing` hides.

   | Pace | Pauses longer than … | are cut to … | Feels like |
   |---|---|---|---|
   | `natural` (default) | 0.75 s | 0.3 s | Only long pauses go and the sentence rhythm stays. Explainers, storytelling |
   | `brisk` | 0.5 s | 0.25 s | Thinking pauses go and breaths stay. Most YouTube talking heads |
   | `snappy` | 0.35 s | 0.18 s | Every gap is trimmed to a breath, also inside sentences. TikTok, Shorts |

   The pace can be changed later by re-running `write --pace …`, or for one tighten run with `tighten.py --pace …`.

   Steps: organize, assemble, broll, tighten, framing, color, sound, graphics, captions, qc, deliver, shorts.
4. **Write.**
   ```bash
   python3 scripts/setup.py --workdir <folder> write --timeline "<name>" --script "<where>" --storyboard storyboard.md --skip "assemble=cut already exists"
   ```
5. **Finish.** Show the user `EDIT_CHECKLIST.md`, name the next step, and tell them to start future sessions in this folder.

## What it writes

- `AGENTS.md`: the rules every agent working in this folder follows, and this video's details. Only the block between the `resolve-video-skills` markers is generated. Anything the user adds outside it is kept.
- `.edit/config.json`: the answers above.
- `EDIT_CHECKLIST.md` and `.edit/state.json`, through resolve-bridge, on every `write`. Before a timeline is tracked the state is empty (version 0).

## Behaviour

- `write` works with no timeline and with Resolve closed: it creates an empty state (version 0) so `video-organize` can record issues. Resolve is needed only when `--timeline` starts tracking.
- `check` warns when no storyboard or script is recorded.
- An older `phrases` setting is kept and shown as "verdict phrases (meaning unknown)". Ask the user which are approvals and which are rejections, then save them with the two new options.
- `write` changes nothing in Resolve. Tracking a timeline only reads it.
- A project that is already tracked keeps its history. `--timeline` is ignored. To restart from another timeline, use resolve-bridge's `adopt`.
- `check` reports timeline and colour settings but does not change them. Changing them on an existing cut can reframe every clip, so that is the user's call.
