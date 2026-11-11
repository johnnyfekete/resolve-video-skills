# video-organize: design

Status: draft for approval. Replaces the roadmap's `video-ingest`.

## Why

The repo currently only helps once a first cut exists. Someone who has just finished filming has a folder of files in random order, often several takes per line and sometimes several scenes in one recording. `video-organize` turns that into a reviewed list of what exists, which take is best, and what is missing. It is the first step of the pipeline and has to work on footage it has never seen.

The user reviews its result before anything goes into a timeline. `video-assemble`, a later and separate skill, builds the first timeline from that result. They are separate because transcription is slow and take picking is revisited: a bad pick should not cost a new transcription, and wrong matches should be caught before a cut exists.

## Goals

1. Transcribe every recording locally and keep the transcripts for re-runs.
2. Find each take inside each recording and match it to a storyboard scene.
3. Score every spoken take 0-100 with visible reasons, and suggest the best per scene.
4. Find broken words and point to cleaner takes of the same part.
5. Find the best moments in b-roll clips and match them to the storyboard's b-roll notes.
6. Report what is missing: scenes without a usable take, b-roll not filmed.
7. Offer to rename footage to the industry naming scheme, only after asking.

## Non-goals

- Building a timeline or importing clips to the media pool (`video-assemble`).
- Choosing final cut points (`video-tighten`).
- Removing filler words.
- Any integration with a specific script or planning tool. The repo is generic. Users export their storyboard to the plain format below themselves.

## Anti-goals: a failure even if it technically works

- Renaming anything without the user's approval of the full list.
- Overwriting a take the user picked when the skill re-runs.
- Confidently matching a take to the wrong scene. Low-confidence matches are listed as unmatched.
- Treating on-set chatter between takes as a take.
- Trusting long-file transcription. Whisper on long files drops repeated takes and stretches word timestamps.

## Input

The working folder (the same `--workdir` as the other skills), a footage folder, and a storyboard file.

### Storyboard format

Plain Markdown, one block per scene. The scene number comes from the heading.

```markdown
## S01
Setup: counter top

Let me start with a confession: AI helped me plan this video.

## S03
Voiceover: yes
B-roll: showing AI researching something funny

It writes my code, and I use it for research and brainstorming.
```

`Setup`, `Voiceover` and `B-roll` are optional. Everything else in the block is the spoken text. `B-roll:` without spoken text is a b-roll-only scene.

### No storyboard or script

Still transcribe and find takes. Group takes by similar text into numbered groups (`G01`, `G02`) and put a clear warning at the top of `TAKES.md`. No completeness scoring, no missing-scene report, no rename offer, because there are no scenes to name files after. `video-setup` must also ask for a storyboard or script and warn if there is none.

A plain script without scene headings: treat each paragraph as a scene, numbered in order.

## What it does

1. **Read the storyboard, list the footage.** Report counts, ask if the folder looks wrong.
2. **Transcribe.** Local Whisper, language auto-detected, in slices cut at silences (found with `resolve-bridge/scripts/rvs_audio.py`) so no slice splits a word. Saved per recording. A recording already transcribed and unchanged is not transcribed again.
3. **Find takes.** Align transcribed speech against the storyboard. A take is a continuous run of speech matching a scene's text, with source in and out times. Partial attempts count as incomplete takes. Speech that matches nothing is on-set chatter, kept in the transcript but not treated as a take.
4. **Read on-set verdicts.** Configured phrases said just after a take (the `phrases` setting from `video-setup`) become that take's verdict.
5. **Measure and score** (below).
6. **Catalogue b-roll.** Recordings with no speech matching the script are b-roll.
7. **Write results** (below). Scenes with no usable take and b-roll not filmed go into `EDIT_CHECKLIST.md` as issues.
8. **Offer the rename.**

## Scoring spoken takes

0-100, stored with its parts and shown with reasons:

- **Complete:** every word of the scene's text is present.
- **Broken words:** stumbles, restarts, cut-off words. Found from the transcript compared with the script, and confirmed against the audio.
- **Audio quality:** level, clipping and background noise, measured with `ffmpeg`.
- **On-set verdict:** outweighs the score when ranking. A take rejected on set ("one more time") is not suggested if another complete take exists, and an approved take ranks first.

**Voiceover scenes** are scored mainly on sound, because the picture will not be seen. Volume and clarity count most.

### Broken words and patches

When the best take has a broken part, look for that part in other takes.

1. Prefer a different take of the whole scene.
2. Otherwise suggest a patch that swaps at least one full idea (a sentence or clause), cut at a natural pause. Never a single word.
3. Mark it in `TAKES.md` with both take IDs, for example `S12A-T02 + S12A-T04 from "Remove em dashes"`. It is a recommendation for the user to decide.
4. A scene only a word-level patch could fix becomes an issue instead.

## B-roll

- A screenshot every 2 seconds (adjustable).
- The agent running the skill reads the screenshots itself and describes them, so no extra service or key is needed. The skill's job is to prepare frames and record what the agent reports.
- Match descriptions to the storyboard's `B-roll:` notes.
- Measure sharpness and exposure with `ffmpeg`. Blurry or badly exposed moments score lower, shaky ones too where motion can be measured.
- Result: the best moments per clip, with in and out points, a score, and what is on screen.

## Results

```
<workdir>/TAKES.md                     read by the user, regenerated, never edited by hand
<workdir>/.edit/organize/transcripts/  one per recording
<workdir>/.edit/organize/takes.json    every take: file, in/out, scene, shot, score, reasons, verdict, user pick
<workdir>/.edit/organize/broll.json    b-roll moments
<workdir>/.edit/organize/renames.json  old and new paths, so a rename can be undone
```

`TAKES.md`: one section per scene, takes listed with the suggested best first, then unmatched clips and scenes with no take. `video-assemble` reads only the `.edit/organize/` files, so it works in a new session.

### Corrections

In chat ("use S02A-T03"): the working file records the pick as the user's and `TAKES.md` is regenerated. After a cut exists, swapping a clip by hand in Resolve is recorded by `resolve-bridge` as a hand edit. A user's pick always survives a re-run.

## Naming: film-industry scene, shot, take

Every shot has a letter, including the first. Takes restart at 1 for each shot and are numbered in filming order across files. A letter means a different camera setup, not a retake.

| Case | Name |
|---|---|
| Take ID in `TAKES.md`, always | `S02A-T03` |
| File with one take | `S02A_T03_IMG_5419.MOV` |
| File with several takes of one shot | `S02A_T01-T03_IMG_5420.MOV` |
| File with several scenes | `S03A_S05A_S06A_IMG_5422.MOV` |
| Second camera setup of a scene | `S02B_T01_IMG_5430.MOV` |

The storyboard's `Setup` decides the letter when present. Otherwise compare frames between clips, and ask the user when unsure. The original camera number stays at the end.

## Renaming

Asked every time, never assumed. The user sees the complete old-to-new list and approves it.

1. Rename the files on disk.
2. If the clips are already in the open Resolve project, point each to its new file and update its clip name.
3. Write `renames.json` so it can be undone.
4. Update paths in the organize files and any saved plans.

**Must be tested before relying on it:** Resolve's `MediaPoolItem.ReplaceClip(filePath)` replaces a clip's underlying file, but its docs do not say whether cuts and grades on existing timelines survive. Test on a throwaway project. If they do not survive, offer renaming only for clips not yet used on a timeline.

Update the generated `AGENTS.md` rule to "only rename source footage through `video-organize`, after asking".

## Changes needed elsewhere

- `resolve-bridge`: rename pipeline step `ingest` to `organize`. Projects whose state already says `ingest` was skipped must keep working.
- `resolve-bridge`: issues currently need a tracked timeline, but organize runs before one exists. Issues and the checklist must work with no timeline yet.
- `video-setup`: ask for a storyboard or script and warn when there is none. Its check should work on a project with no timeline, which is the normal case now.
- Docs: replace `video-ingest` with `video-organize` in the README and roadmap.

## Constraints

- Local only: no paid API or key.
- macOS on Apple Silicon first, matching the README.
- Resolve is needed only for the rename step. Everything else runs without it.
- Repo text stays agent-neutral and contains nothing specific to one user's video or tools.

## Decided by the user

Two skills rather than one; the name `video-organize`; asks before renaming and renames both files and Resolve clips; industry naming; corrections in chat or by hand in Resolve; a 0-100 score; voiceover takes judged on sound; patches of full ideas only; screenshots every few seconds for b-roll; working files in the video's folder; a warning, with transcription still running, when there is no storyboard.

After review, also decided by the user:

- **Reworded takes** count as complete, with a small score penalty, and are labelled "reworded" in `TAKES.md`. Matching is therefore by meaning and order of the scene's text, not word for word. Only missing parts make a take incomplete.
- **Audio-only voiceover files** (WAV, M4A and similar) are in scope: transcribed, matched and scored like spoken takes, with no frame steps. Syncing a separate recorder or lav mic to camera files is out of scope for this version.

## Details settled during review (my calls, open to change)

- **Spoken or b-roll** is decided by sustained speech, not by script match. Speech that matches nothing confidently is an unmatched spoken clip, never b-roll. Classification is per section, so one recording can hold both takes and b-roll moments. The same test works with no storyboard.
- **Audio stream:** the first stream by default. The skill warns when a file has several usable ones.
- **Shot letters:** assigned after takes are found, within each scene. A scene starts as `A`; a new letter needs clearly different frames, with `Setup` as a hint when scenes share a setup. Doubts are asked in one batch before `TAKES.md` is written. "Best per scene" ranks across all shots of the scene.
- **Names not in the table:** b-roll keeps its name unless the user asks otherwise. Unmatched files are left out of the rename. Several shots of one scene: `S02A_S02B_IMG_5420.MOV`. A continuous run through several scenes is one take per scene.
- **Take IDs are frozen once issued.** New takes get the next free number, even if filmed earlier. A pick is stored with its file and times as well as its ID. A pick whose take no longer exists becomes an issue. Filming order is file creation time, then camera number.
- **Rename and Resolve:** rename needs the matching project open in Resolve, unless the user confirms the clips were never imported. Only the current plan's paths are updated. Versioned plan history stays as it was. Undo is a built-in command using `renames.json`.
- **On-set phrases** are two lists, approving and rejecting (`--approve-phrases`, `--reject-phrases` in `video-setup`). An existing flat `phrases` list counts as unknown and is shown without changing the ranking.
- **Hand swaps in Resolve** are not fed back as picks in this version. A re-run of organize never touches a timeline. `video-assemble` must decide how to respect them.

## Left to the builder (my calls so far, open to change)

- `TAKES.md` as the review file and the split of files under `.edit/organize/`.
- The storyboard file format above.
- On-set verdict outranking the score.
- The 2-second screenshot default.
- Low-confidence matches listed as unmatched.
- Grouping as `G01` when there is no storyboard, and no rename offer in that case.
- Whisper local via `mlx-whisper`. Score weights and thresholds, tuned on real footage.
- Importing to the media pool belongs to `video-assemble`.
