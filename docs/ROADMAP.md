# Roadmap

**Next to build:** `video-framing` (declutter crop, punch-ins, eye-trace).

## Who this is for

People who record scripted talking-head videos (YouTube explainers, essays, tutorials): several takes per line, some b-roll, a person speaking to camera. Not for vlogs without a script, multicam podcasts or narrative film.

## The pipeline

```
raw files ─► organize ─► assemble ─► tighten ─► framing ─► color ─► sound ─► graphics ─► captions ─► qc ─► deliver ─► shorts
                └───────────── every step reads and writes the plan through resolve-bridge ─────────────┘
```

Each skill reads the current plan, changes one thing, builds a **new** timeline version (`<name>_v3_framing`), saves the project and records the step. It never edits the previous version.

## State and versions

Every session starts from the latest saved state, and every step ends with a save. No step depends on what an earlier chat remembers.

1. **One state file per video project** (`<project>/.edit/state.json`): current timeline, ordered step history (step, timeline, date, summary), path to the current plan. Plans are kept per version (`plan_v3_framing.json`), never overwritten.
2. **Start of every skill:** read the state file, open its current timeline, compare the timeline with the plan. If the user edited by hand in Resolve since, read those changes back into a new plan version first. Manual edits are never overwritten.
3. **End of every skill:** build the new timeline, `project.SaveProject()`, write the new plan and state entry. The step only counts as done after the save succeeds.
4. **Old versions** move into an `Archive` media pool folder. The newest timeline stays at the top level. Nothing is deleted.
5. **"Where are we?"**: any session can print the step history and the next step from the state file.
6. **Checklist** (`<project>/EDIT_CHECKLIST.md`), regenerated after every change: next step, pipeline status (done / skipped / to do), open issues from any skill with clip + timecode, resolved issues, history, and a notes section that is never overwritten. Ticking an issue's box in the file closes it.

## The skills

### 0. `resolve-bridge` (shared library) — **built (v0.1)**

- `skills/resolve-bridge/scripts/rvs.py`: Python library + CLI over Resolve's scripting API.
- Two ways to make a version: **fork** (duplicate the timeline, change it in place; keeps grades, titles, effects) and **build** (fresh timeline from a plan file, for structural cuts).
- State file, saved project after every step, archive folder, hand-edit detection (`check`, `sync`; `begin` syncs automatically, `build` refuses until synced).
- Plan format: [skills/resolve-bridge/references/plan-format.md](../skills/resolve-bridge/references/plan-format.md).
- Tested on Resolve Studio 21.2 with a 65-item, 4K 60 fps → 1080p 24 fps timeline: export → build round trip is identical.
- Still to add (with the skills that need them): slice transcription, voice activity detection, still export.

### `video-setup`: one-time project preparation — **built (v0.1)**

- Read-only check of tools, Resolve Studio version, timeline and colour settings, and footage formats.
- Records the script location and per-video settings, starts tracking the first timeline, marks skipped steps.
- Generates `AGENTS.md` for the video: always build on the latest version, where versions and files go, how hand edits and issues work. Text outside the generated block is kept.

### 1. `video-organize`: random raw files → organised catalogue — **built**

- Local Whisper transcription of every recording, in slices cut at silences, saved per recording and not repeated when the file is unchanged.
- Takes found by aligning speech with a Markdown storyboard (one `## S01` block per scene, optional `Setup`, `Voiceover`, `B-roll`). No storyboard: takes are grouped by similar text, with a warning.
- Score 0-100 per take: complete, broken words, audio quality. On-set approve and reject phrases outrank the score. Patches from other takes swap full ideas only.
- B-roll: frames every 2 s, sharpness, exposure and shake measured, the agent describes the frames and matches them to the storyboard's `B-roll:` notes.
- Film-style ids and names (`S02A-T03`, `S02A_T03_<original>`), shot letters from `Setup` or asked of the user.
- Results: `TAKES.md`, `.edit/organize/*.json`, issues for scenes with no usable take and b-roll not filmed. A rename of files and Resolve clips is offered and applied only after the user approves the full list, with undo.
- Runs with Resolve closed. Resolve is needed only to rename clips already in a project.

### 2. `video-assemble`: script → first cut

- Per script line, pick the take: complete against the script, no stumbles, on-set verdict wins. Prefer one continuous take over jump-cut pieces.
- Place b-roll against the script's b-roll notes. Flag semantic mismatches (b-roll that contradicts the line).
- Output: plan + a "still to shoot" list.
- Script source is pluggable: a Markdown script file by default, adapters for other tools.

### 3. `video-tighten`: remove dead air, natural edges — **built (v0.1)**

- Edges from measured speech: 0.22 s before the first word, 0.38 s after the last. Edges may grow up to 0.5 s into silence of the same take to rescue clipped word endings, never into other sound.
- Pauses over 0.75 s inside a clip shortened to 0.3 s (each saves at least 0.45 s). `--skip` protects performed pauses.
- Ripple with b-roll remapped onto the new timing. Dry run + report first, `--apply` builds.
- Speech detection: `resolve-bridge/scripts/rvs_audio.py` (energy-based, whole-file thresholds, hysteresis on word edges; pure Python + ffmpeg).
- Tested on a 5:47, 65-item cut from 4K 60 fps phone footage: gave 18 edges (16 tails, 2 heads) more room where the cut was clipping or crowding a word, and flagged 2 cuts that need a different take.
- Later: J/L cuts, filler-word removal (needs transcription).

### 4. `video-framing`: declutter, zooms, eye-trace

- **Declutter base framing, per setup:** export one still per camera setup, mark distracting edges (cables, shelves, door frames, half-visible objects), propose the tightest frame that removes them with good headroom and eye line. User approves once per setup, every clip of that setup inherits it.
- Face and eye position at the first and last frame of every piece (Apple Vision helper or OpenCV).
- **Eye-trace match:** at each cut, set Pan/Tilt on the incoming clip so the eyes land within ~3% of the outgoing frame's eye position.
- **Jump-cut punch-in:** consecutive pieces from the same setup alternate between base and ~1.12× base, anchored on the eyes.
- **Emphasis push-ins:** slow ease-in-out 1.00 → 1.06 over key lines, a quick punch on jokes. Spaced out, roughly one per 30 s at most.
- **Zoom budget:** base × punch-in × push-in ≤ source/delivery ratio (2.0 for 4K source → 1080p). Lower the punch-in first, never the declutter crop.
- Timeline set to scale full frame with crop, so zoom values mean the same in every clip.

### 5. `video-color`: consistent grade

- Input transform first. Phone footage is often HDR (HLG, BT.2020): Colour Space Transform HLG/Rec.2100 → Rec.709 Gamma 2.4, or Resolve colour management. Detect it with `ffprobe` and do not guess.
- Colour groups per camera setup; same-setup clips share one grade (`CopyGrades`).
- Per group: `ApplyAutoColor` as balance, then the user's look from a `.drx` file (`ApplyGradeFromDRX`).
- Verify with numbers and eyes: export a still per clip, measure exposure and skin tone with ffmpeg `signalstats`, review a before/after contact sheet, flag clips that drift inside a group.
- Optional vignette or edge darkening for clutter that cropping cannot remove.
- Screen recordings: no look, legal-range check only.

### 6. `video-sound`: clean, even audio

- Voice Isolation on dialogue tracks at a moderate amount.
- 2-frame audio fades on every cut (`SetFades`) to avoid clicks.
- Dialogue normalised to −14 LUFS integrated, −1 dBTP (`NormalizeAudioLevel`), checked with `AnalyzeAudioLevels`.
- Music bed ducked under speech, rising in gaps and at the end.
- SFX: soft whoosh on punch-ins and graphics, ambient sound under b-roll. The user points to their own music/SFX folder; nothing is bundled.

### 7. `video-graphics`: animations where the script asks

- Reads animation notes from the script.
- Built with [Remotion](https://github.com/remotion-dev/skills), rendered as ProRes 4444 with alpha, placed on a graphics track.
- A configurable brand kit (fonts, colours, timing) so every video looks related.

### 8. `video-captions`

- Word timings from Whisper, text corrected against the script.
- `.srt` for upload; styled burned-in captions for shorts.

### 9. `video-qc`: review before delivery

- Render a proxy, re-transcribe it, diff against the script: missing lines, extra words, wrong language.
- Voice activity at every cut (clipped heads/tails), `blackdetect` for gaps, loudness report, contact sheet of frames on both sides of each cut.
- Output: a review file that starts with the next action.

### 10. `video-deliver`

- Render preset, chapters from the script's sections, description draft, thumbnail frame candidates.

### 11. `video-shorts`: vertical clips from the main cut

- Score candidate segments (20 to 50 s): self-contained meaning, strong first line, clean ending. The user picks from a shortlist.
- Per pick: duplicated timeline in 9:16, face-anchored crop (`SmartReframe` or the framing skill's face data), tighter pauses, burned captions, hook text in the first 2 s.

## Build order

Now:

1. ~~`resolve-bridge`~~ done
2. ~~`video-tighten`~~ done
3. `video-framing`
4. `video-sound`
5. `video-color`

Later: `video-qc`, `video-shorts`, `video-captions`, `video-assemble`, `video-graphics`, `video-deliver`, and a thin `video-edit` orchestrator that runs the chain.

## Configuration (per user)

Kept out of the skills, in a config file:

- On-set verdict phrases
- Script source adapter
- Edge padding and pause thresholds
- Look `.drx` path, brand kit
- Music and SFX folders
- Delivery resolution and loudness target
