---
name: video-organize
description: Organises raw footage for a scripted video. Transcribes every recording locally, finds each take and matches it to a storyboard scene, scores completeness, broken words and audio, reads on-set verdicts, suggests the best take or a patch from two takes, catalogues b-roll against the storyboard's b-roll notes, writes TAKES.md and offers a rename with film-style names (S02A_T03_original). Use when new footage arrives, when the user wants takes organised, compared or picked, wants to know which scenes have no usable take, or asks to rename clips.
---

# video-organize

Run this after `video-setup` and before `video-assemble`. It needs no timeline and works with Resolve closed. Requires the **resolve-bridge** skill installed next to this one (or set `RVS_BRIDGE`), `ffmpeg`, and either `uv` (for local Whisper) or a transcriber command in `RVS_TRANSCRIBE_CMD`.

Run scripts as `python3 <this skill's folder>/scripts/organize.py --workdir <folder> <command>`.

## Storyboard format

Plain Markdown, one block per scene. The scene id comes from the heading.

```markdown
## S01
Setup: counter top

Let me start with a confession: AI helped me plan this video.

## S03
Voiceover: yes
B-roll: showing AI researching something funny
```

`Setup`, `Voiceover` and `B-roll` are optional. Everything else in a block is the spoken text. A block with only `B-roll:` is a b-roll-only scene. A plain script without headings counts each paragraph as a scene. With no storyboard at all, takes are grouped by similar text (`G01`, `G02`), with a warning at the top of `TAKES.md` and no scoring against a script or rename offer.

## Workflow

1. **Setup check.** Run `video-setup`'s `check`. It warns if no storyboard is recorded, and `.edit/config.json` should hold `storyboard`, `approve_phrases` and `reject_phrases` (an older `phrases` list only shows as a note: ask which phrases approve and which reject, and save them with `setup.py write`).
2. **Count first.** Ask for the footage folder, then:
   ```bash
   python3 scripts/organize.py --workdir <folder> run --footage <footage folder> [--storyboard FILE] [--every 2]
   ```
   Without `--yes` this prints the counts (recordings, duration, scenes) and stops. Confirm with the user that the folder looks right.
3. **Run.**
   ```bash
   python3 scripts/organize.py --workdir <folder> run --footage <footage folder> --yes
   ```
   Transcribes (a recording already transcribed and unchanged is skipped), finds and scores takes, extracts b-roll frames, writes `TAKES.md` and records issues (scenes with no usable take, b-roll not filmed, broken clauses nobody can patch).
4. **Your part: describe the b-roll.** The run lists one frame folder per b-roll recording (one JPEG per sample, frame N is at N x `every` seconds). For each folder:
   - open the frames and describe what is on screen, per timestamp;
   - match the descriptions to the storyboard's `B-roll:` notes: scene, start, end, and why;
   - write the payload, then record it:
     ```json
     {"<recording path>": {"frames": {"0": "a person typing", "2": "close-up of a screen"},
                           "matches": [{"scene": "S04", "start": 2, "end": 6, "why": "screen close-up"}]}}
     ```
     ```bash
     python3 scripts/organize.py --workdir <folder> describe payload.json
     ```
   Describe only what you can see. Do not invent matches.
5. **Your part: shot-letter questions.** When the run cannot tell whether two files of a scene are the same camera setup, it lists a question with a frame difference. Ask the user, then record each answer:
   ```bash
   python3 scripts/organize.py --workdir <folder> answer S02 <file> B
   ```
6. **Show `TAKES.md`.** One section per scene with the suggested take first, its score and reasons, patches (marked with both take ids, always a recommendation), unmatched clips and scenes with no take. Read the issues out to the user.
7. **Corrections.** When the user prefers another take ("use S02A-T03"):
   ```bash
   python3 scripts/organize.py --workdir <folder> pick S02A-T03
   ```
   A user's pick survives re-runs. `report` rewrites `TAKES.md` without changing anything else.
8. **Rename.** Offered by `organize.py rename` (proposal first, `--apply` only after the user approves the full list, `--undo` to revert). Show the complete old-to-new list and apply only on a clear yes. Never rename any other way.
9. **Record the step** through resolve-bridge, then point to `video-assemble` (not built yet) as the next step.

## Names

| Case | Name |
|---|---|
| Take id in `TAKES.md` | `S02A-T03` |
| File with one take | `S02A_T03_IMG_5419.MOV` |
| File with several takes of one shot | `S02A_T01-T03_IMG_5420.MOV` |
| File with several scenes | `S03A_S05A_S06A_IMG_5422.MOV` |
| Second camera setup of a scene | `S02B_T01_IMG_5430.MOV` |

Every shot has a letter. A different letter means a different camera setup, not a retake. B-roll and unmatched files keep their names.

## Rules

- Source footage is never moved or overwritten. It is renamed only by the approved `rename`.
- Scoring is 0 to 100 and always shown with reasons. An on-set rejection outranks the score, an approval ranks first. Voiceover scenes are judged mainly on sound.
- Patches swap at least one full idea cut at a natural pause, never a single word.
- Working files live in `<folder>/.edit/organize/` (do not edit by hand). `video-assemble` reads only those.
