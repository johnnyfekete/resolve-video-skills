---
name: video-tighten
description: Tightens a talking-head cut in DaVinci Resolve Studio. Measures speech in every clip, sets natural cut edges (air before the first word and after the last), rescues clipped word endings, shortens long pauses inside takes and ripples b-roll along. Produces a new timeline version through resolve-bridge. Use when the user says the cut feels too sudden or too slow, words are cut off, there is dead air, or asks to remove silences or tighten the edit.
---

# video-tighten

Requires the **resolve-bridge** skill installed next to this one (it finds it at `../resolve-bridge`, or set `RVS_BRIDGE`), plus `ffmpeg`.

## Workflow

1. **Where are we.** `python3 <resolve-bridge>/scripts/rvs.py --workdir <folder> status`. If there is no state yet, adopt the timeline the user wants to tighten (see resolve-bridge).
2. **Dry run.** Changes nothing in Resolve:
   ```bash
   python3 scripts/tighten.py --workdir <folder>
   ```
   Writes `<folder>/.edit/tighten/REPORT.md`, `report.json` and `plan.json`.
3. **Read the report with the user.** Show the runtime change, the number of shortened pauses and every flag. Flags mark cuts the script could not fix (a word starting before the in-point with other speech right before it), so someone has to check them by ear, or pick a different take.
4. **Protect performed pauses.** Silence that is acting (a look, a beat before a punchline, typing on camera) must not be cut. Ask the user, then re-run with `--skip <clip name or timeline start frame> ...`.
5. **Apply.**
   ```bash
   python3 scripts/tighten.py --workdir <folder> --skip ... --apply
   ```
   Builds `<base>_vNN_tighten`, archives the previous version, saves the project. Every flag becomes an issue in `EDIT_CHECKLIST.md`, located by clip name and timecode in the new timeline. Tell the user the new timeline name and the issue ids.

## What it does

| | Default | Option |
|---|---|---|
| Air before the first word | 0.22 s | `--pre` |
| Air after the last word | 0.38 s | `--post` |
| Edge may grow into silence of the same take (rescues clipped words) | up to 0.5 s | `--max-extend` |
| Never closer than this to other sound (breath, mumble, next take) | 0.08 s | `--guard` |
| Pause preset | the project's pace, else `natural` | `--pace natural\|brisk\|snappy` |
| Pauses inside a clip longer than … | from the pace (natural: 0.75 s) | `--pause-max` |
| … are shortened to | from the pace (natural: 0.3 s) | `--pause-keep` |
| Never create a piece shorter than | 0.5 s | `--min-piece` |
| Only fix edges, keep all pauses | off | `--no-pauses` |
| Audio tracks that are not dialogue (music) | none | `--ignore-audio-tracks 3 4` |

- **Speech detection** is energy-based (`resolve-bridge/scripts/rvs_audio.py`): thresholds come from the whole source file, words are found with a strong threshold and their edges grow through soft endings and attached breaths. It suits close-mic recordings. In noisy locations, check the flags more carefully.
- **Pace** is saved per video by `video-setup --pace` and picks the two pause values: `natural` (over 0.75 s → 0.3 s), `brisk` (over 0.5 s → 0.25 s) or `snappy` (over 0.35 s → 0.18 s, also cuts gaps inside sentences). `--pause-max` and `--pause-keep` override it for one run. If the user says the cut still feels slow, suggest one pace faster as a dry run first, and protect performed pauses with `--skip`.
- **Every shortened pause becomes a jump cut** (the clip is split). Run **video-framing** afterwards to hide them with punch-ins, or cover them with b-roll.
- **Ripple:** later clips move up. Video-only items (b-roll, overlays) are remapped onto the new timing and shortened where their section got shorter. B-roll that would end up under 0.5 s is dropped and listed.
- **Mode:** this is a structural step, so it uses resolve-bridge's *build*. Titles and generators on the current timeline are not carried over (the bridge warns). Run tighten before adding graphics.

## Not handled yet

- Overlapping dialogue (J/L cuts already in the timeline): those clips are left as they are and flagged.
- Filler words ("um", "you know") need a transcript; planned with transcription in a later skill.
