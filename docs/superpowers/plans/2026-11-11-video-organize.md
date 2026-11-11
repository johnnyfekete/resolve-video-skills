# video-organize Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `video-organize` skill that turns a folder of raw recordings plus a storyboard into scored, matched takes, a b-roll catalogue, a `TAKES.md` review file and checklist issues, with an optional approved rename.

**Architecture:** Small stdlib-only Python modules under `skills/video-organize/scripts/`, one per responsibility, driven by one CLI (`organize.py`). Whisper runs out of process through `uv` so the skill itself needs no installed packages. The agent running the skill does the two judgement jobs a script cannot (describing screenshots, answering shot-letter doubts) through CLI commands that record its answers. State lives in `<workdir>/.edit/organize/`; issues go through `resolve-bridge`.

**Tech Stack:** Python 3.11+ stdlib, `ffmpeg`/`ffprobe`, `uv` + `mlx-whisper` (model `mlx-community/whisper-large-v3-turbo`), DaVinci Resolve Studio scripting API (rename step only), `unittest`.

**Spec:** `docs/superpowers/specs/2026-11-09-video-organize-design.md`

## Global Constraints

- Python 3.11+, standard library only inside the skill. Whisper only via a subprocess command.
- Local only: no paid API, no key.
- Resolve is required only for renaming clips already in a project. Every other command runs with Resolve closed (`rvs.Session(workdir, connect_now=False)`).
- Repo text is agent-neutral and contains nothing specific to one user's video or tools.
- Commits are authored by the repo owner only: no `Co-Authored-By` or "Generated with" lines.
- Never rename, move or overwrite footage except through the approved rename command.
- Take IDs: `S02A-T03`. File names: `S02A_T03_<orig>`, `S02A_T01-T03_<orig>`, `S03A_S05A_S06A_<orig>`, `S02A_S02B_<orig>`, `S02B_T01_<orig>`; b-roll and unmatched files keep their names.
- Patches swap at least one full idea (sentence or clause) cut at a natural pause; never a single word.
- B-roll screenshot interval default: 2 s.
- Work on branch `video-organize`; merge to `main` only when the user approves.

## Review Focus

1. **Chatter and slates between takes** ("ok, again", counting, swearing at a mistake) must not become takes or patches; expect them in the transcript only. Test in Task 4.
2. **The same line recorded many times in one file**: every attempt must become its own take; long-file Whisper drops repeats. Test in Task 3 (slicing) and Task 4 (detection).
3. **Re-running after adding footage or changing thresholds** must keep existing take IDs and the user's picks. Test in Task 6.
4. **Speech in another language than the script** (asides, a phone call) must be flagged, not matched. Test in Task 4.
5. **Rename collisions and odd paths**: target already exists, names with spaces or accents, a file listed twice. Rename must refuse the whole batch before touching anything. Test in Task 9.

---

### Task 1: Bridge support for projects without a timeline, and the `organize` step

**Files:**
- Modify: `skills/resolve-bridge/scripts/rvs.py` (Session `__init__`, `_require_state`, new `init_state`, `skip_step`)
- Modify: `skills/resolve-bridge/scripts/rvs_checklist.py` (PIPELINE, `render`, `next_step`)
- Modify: `skills/resolve-bridge/SKILL.md` (pipeline list)
- Test: `tests/test_bridge_state.py`

**Interfaces:**
- Produces: `Session.init_state(base_name: str | None = None) -> dict` creates `.edit/state.json` with `{"schema", "resolve_project": None, "base_name", "archive_folder": "Archive", "history": [], "current": {"version": 0, "timeline": None, "plan": None}, "issues": [], "skipped": {}}` if none exists, otherwise returns the existing state unchanged. `add_issue`, `resolve_issue`, `skip_step`, `write_checklist` work on such a state. `adopt` on it fills `resolve_project` and records v01.
- Produces: pipeline step `organize` (skill `video-organize`) replaces `ingest`. `rvs_checklist.ALIASES = {"ingest": "organize"}` is applied to history steps and skipped keys when rendering and in `next_step`; `skip_step("ingest")` stores `organize`.

- [ ] **Step 1: Write failing tests** in `tests/test_bridge_state.py` (stdlib `unittest`, temp dirs, `Session(tmp, connect_now=False)`):
  - `test_init_state_without_timeline`: `init_state("Demo")` writes state with `current["version"] == 0`; `add_issue("organize", "S04 has no take")` returns `{"id": "I1", ...}`; `EDIT_CHECKLIST.md` exists and contains `S04 has no take` and `Next step: **organize**`.
  - `test_init_state_keeps_existing`: second `init_state` call returns the same history and issues.
  - `test_legacy_ingest_counts_as_organize`: a state with `skipped == {"ingest": "done by hand"}` renders `~~organize~~` and `next_step` is not `organize`.
  - `test_skip_ingest_alias`: `skip_step("ingest", "x")` stores key `organize`.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_bridge_state -v` — expect failures (`init_state` missing).
- [ ] **Step 3: Implement.** `Session.__init__` skips the project-name check when `state["resolve_project"]` is `None`; `_print_status` and `render` show "no timeline tracked yet" when `current["timeline"]` is `None`; `check()` raises `RvsError("No timeline tracked yet.")` in that case. Rename the PIPELINE entry to `("organize", "video-organize", "Transcribe recordings, find and score takes, catalogue b-roll")`.
- [ ] **Step 4: Run** the new tests and `python3 -m unittest discover -s tests -v` — all pass.
- [ ] **Step 5: Commit** `git commit -m "Bridge: state before a timeline exists; ingest step becomes organize"`.

---

### Task 2: Storyboard and script parsing

**Files:**
- Create: `skills/video-organize/scripts/storyboard.py`
- Test: `tests/test_storyboard.py`, fixtures `tests/fixtures/storyboard.md`, `tests/fixtures/script.md`

**Interfaces:**
- Produces: `@dataclass Scene(id: str, text: str, setup: str | None, voiceover: bool, broll: str | None, clauses: list[str])`; `load(path: str | None) -> tuple[list[Scene], str]` where the second value is the mode `"storyboard" | "script" | "none"`; `tokens(text: str) -> list[str]` (lowercase, accents folded, punctuation and apostrophes removed, numbers kept as written).

- [ ] **Step 1: Write failing tests:**
  - `test_storyboard_blocks`: the fixture with `## S01` (Setup: counter top), `## S03` (Voiceover: yes, B-roll: note) and `## S05` (B-roll only) gives 3 scenes; `S03.voiceover is True`; `S05.text == ""` and `S05.broll` set; scene ids keep the heading (`"S01"`, `"S03"`).
  - `test_clauses`: `"Remove em dashes (even though I love them), and make it sound human."` splits into clauses at `. , ; : — ( )` boundaries, dropping empty ones, and never returns a clause of one word (merged into its neighbour).
  - `test_plain_script`: three paragraphs without headings → scenes `S01`, `S02`, `S03`, mode `"script"`.
  - `test_no_file`: `load(None)` → `([], "none")`.
  - `test_tokens`: `tokens("I’m 21st-century, café!") == ["im", "21st", "century", "cafe"]`.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_storyboard -v` — fail.
- [ ] **Step 3: Implement** in `storyboard.py`. Headings matching `^##\s+(S?\d+[A-Za-z]?)\b` start a scene; `Setup:`, `Voiceover:` (yes/true/1), `B-roll:` lines are fields; the rest is text.
- [ ] **Step 4: Run** tests — pass.
- [ ] **Step 5: Commit** `"video-organize: storyboard and script parsing"`.

---

### Task 3: Transcription with silence-cut slices and a cache

**Files:**
- Create: `skills/video-organize/scripts/transcribe.py`, `skills/video-organize/scripts/whisper_worker.py`
- Modify: `skills/resolve-bridge/scripts/rvs_audio.py` (add `silence_cuts`)
- Test: `tests/test_transcribe.py`

**Interfaces:**
- Consumes: `rvs_audio.speech_segments`, `rvs_audio.probe_duration`.
- Produces: `rvs_audio.silence_cuts(path: str, target: float = 30.0, max_len: float = 45.0, stream: int = 0) -> list[tuple[float, float]]` — slices covering the file, each cut in the middle of a silence nearest `target`, never longer than `max_len` (hard cut only if no silence exists).
- Produces: `transcribe.transcribe(path: str, cache_dir: Path, *, stream: int = 0, runner: Callable[[list[dict]], list[dict]] | None = None) -> dict` returning `{"file", "size", "mtime", "duration", "language", "words": [{"w", "start", "end", "p"}], "segments": [{"text", "start", "end", "language"}]}` with absolute file times. Cached as `cache_dir/<sha1(path)>.json`; reused when size and mtime match.
- Produces: default runner = subprocess `uv run --quiet --with mlx-whisper python -I whisper_worker.py <in.json> <out.json>`, overridable with env `RVS_TRANSCRIBE_CMD` (a command taking the same two arguments). `whisper_worker.py` uses `mlx_whisper.transcribe(wav, path_or_hf_repo="mlx-community/whisper-large-v3-turbo", word_timestamps=True)` per slice, language auto-detected per slice.
- Produces: audio-only files (wav, m4a, mp3, aiff, flac) are accepted; slices are extracted to 16 kHz mono WAV with ffmpeg.
- Produces: `audio_streams(path: str) -> list[dict]` → `{"index", "codec", "channels"}` from ffprobe. The first stream is used; `transcribe()` adds `"warning": "N audio streams; using the first"` to its result when more than one stream has 2+ channels or a non-trivial level.

- [ ] **Step 1: Write failing tests** (generate audio with `ffmpeg -f lavfi` sine bursts and `anullsrc` silence into a temp dir):
  - `test_silence_cuts_land_in_silence`: a 100 s file of 8 s tone / 2 s silence repeating → every cut point lies inside a silent gap; slices cover 0..duration without gaps or overlap.
  - `test_silence_cuts_hard_cut`: 60 s continuous tone with `max_len=45` → a cut at ≤45 s.
  - `test_cache_reused`: with a fake `runner` that counts calls, the second `transcribe()` call makes 0 runner calls; touching the file (new mtime) makes it call again.
  - `test_times_are_absolute`: fake runner returns words at 1.0 s inside slice 2 → result word start == slice2_start + 1.0.
  - `test_repeats_survive` (Review Focus 2): fake runner echoes one word per slice; a file with 6 slices yields 6 words.
  - `test_multiple_streams_warn`: a file muxed with two stereo audio streams → result has a `warning` mentioning 2 streams.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_transcribe -v` — fail.
- [ ] **Step 3: Implement** `silence_cuts` with `speech_segments` gaps; `transcribe()` with the cache and runner. Missing `uv` → `RuntimeError("Transcription needs uv (https://docs.astral.sh/uv/) or RVS_TRANSCRIBE_CMD.")`.
- [ ] **Step 4: Run** tests — pass. Then one manual check on a real recording: `python3 skills/video-organize/scripts/transcribe.py <file>` prints language and the first 20 words.
- [ ] **Step 5: Commit** `"video-organize: sliced local transcription with cache"`.

---

### Task 4: Finding takes and matching them to scenes

**Files:**
- Create: `skills/video-organize/scripts/takes.py`
- Test: `tests/test_takes.py` (hand-written transcript fixtures as dicts)

**Interfaces:**
- Consumes: `storyboard.Scene`, `storyboard.tokens`, transcript dicts from Task 3.
- Produces: `@dataclass Take(file: str, scene: str | None, start: float, end: float, coverage: float, status: str, missing: list[str], extra: list[str], words: list[dict], language: str)` with `status` one of `"complete" | "reworded" | "incomplete" | "unmatched"`.
- Produces: `find_takes(transcript: dict, scenes: list[Scene], script_language: str | None) -> tuple[list[Take], list[dict]]` returning takes and chatter segments `{"start", "end", "text", "reason"}`.
- Produces: `is_spoken(transcript: dict, min_speech: float = 3.0) -> bool` — sustained speech, independent of script match.

Approach: split words into utterances at gaps > 0.7 s; for each scene, grow runs of consecutive utterances and score them with ordered token alignment (`difflib.SequenceMatcher` on token lists). A run is a candidate when its aligned coverage of the scene's tokens is ≥ 0.5 and ≥ 0.6 of its own tokens align. Pick non-overlapping candidates greedily by coverage. Status: `complete` when no run of ≥ 4 consecutive script tokens is missing and coverage ≥ 0.85; `reworded` when coverage ≥ 0.6 and no run of ≥ 4 consecutive script tokens is missing; otherwise `incomplete`. Utterances matching no scene are chatter, or `unmatched` takes when they are ≥ 3 s and in the script language. Segments whose language differs from `script_language` are chatter with reason `"other language"`. Thresholds are module constants so they can be tuned on real footage.

- [ ] **Step 1: Write failing tests:**
  - `test_three_attempts_three_takes`: transcript with "let me start with a confession" said 3 times separated by 3 s gaps → 3 takes for S01 in order.
  - `test_reworded`: "Let me begin with a confession: AI helped me plan this video" vs script "Let me start with a confession: AI helped me plan this video" → `reworded`.
  - `test_missing_ending_is_incomplete`: the last 6 script words absent → `incomplete`, `missing` lists them.
  - `test_chatter_not_take` (Review Focus 1): "ok one more time" and "three two one" between takes → chatter, no take.
  - `test_other_language` (Review Focus 4): a segment with `language="hu"` while the script is English → chatter with reason `"other language"`.
  - `test_multi_scene_recording`: one transcript containing S03 then S05 text → one take each, correct scene ids.
  - `test_is_spoken`: 1 s of words → False; 20 s → True.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_takes -v` — fail.
- [ ] **Step 3: Implement** `takes.py`.
- [ ] **Step 4: Run** tests — pass.
- [ ] **Step 5: Commit** `"video-organize: take detection and scene matching"`.

---

### Task 5: Scoring, on-set verdicts and patch suggestions

**Files:**
- Create: `skills/video-organize/scripts/score.py`
- Test: `tests/test_score.py`

**Interfaces:**
- Consumes: `Take`, `Scene`, `rvs_audio.file_levels`.
- Produces: `audio_metrics(path: str, start: float, end: float, stream: int = 0) -> dict` → `{"lufs", "peak_db", "noise_db", "clipped": bool}` (ffmpeg `ebur128` + `astats`; clipped when peak ≥ −0.1 dBFS).
- Produces: `broken_parts(take: Take, scene: Scene) -> list[dict]` → `{"clause": int, "kind": "restart" | "stumble" | "cut", "at": float}`: repeated tokens or a restart inside a clause, or a word cut off at the take edge.
- Produces: `verdict(take: Take, chatter: list[dict], approve: list[str], reject: list[str], window: float = 8.0) -> str | None` → `"approved" | "rejected" | None` from chatter starting within `window` s after the take ends; rejection wins if both match.
- Produces: `score(take: Take, scene: Scene, metrics: dict, broken: list[dict]) -> tuple[int, list[str]]` → 0–100 plus reasons. Weights: on-camera completeness 45, clean (no broken parts) 30, audio 25; voiceover completeness 30, clean 20, audio 50. `reworded` −5. Audio part: full marks at −23 to −14 LUFS integrated with peak < −1 dBFS and noise ≥ 40 dB below the speech level; clipped takes lose all audio points.
- Produces: `rank(entries: list[dict]) -> list[dict]`, where each entry is `{"take": Take, "score": int, "verdict": str | None}` — approved first, rejected last (unless it is the only complete or reworded take of the scene), then by score, then filming order.
- Produces: `patches(best: Take, others: list[Take], scene: Scene) -> list[dict]` → `{"base": take_id, "from": take_id, "clauses": [int], "from_text": str}` only when every broken clause of `best` is clean in one other take; at least one whole clause; cut points at pauses ≥ 0.15 s. Scenes whose only fix would be smaller than one clause return `[]` and the caller files an issue.

- [ ] **Step 1: Write failing tests:**
  - `test_rejected_ranks_last`: two complete takes, the higher-scored one followed by "one more time" → the other ranks first.
  - `test_only_complete_take_kept_even_if_rejected`: one complete rejected take, one incomplete → rejected complete ranks first.
  - `test_voiceover_weights_audio`: same take, quiet audio (−32 LUFS) loses more points as voiceover than on camera.
  - `test_patch_needs_whole_clause`: best take broken in clause 2; another take clean in clause 2 → patch with `clauses == [2]`; if no take has clause 2 clean → `[]`.
  - `test_restart_detected`: "make it it sound" → one broken part of kind `restart`.
  - `test_audio_metrics_on_tone` (needs ffmpeg): a −20 dBFS tone gives `peak_db` within 1 dB of −20 and `clipped is False`; a full-scale square wave is `clipped`.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_score -v` — fail.
- [ ] **Step 3: Implement** `score.py`.
- [ ] **Step 4: Run** tests — pass.
- [ ] **Step 5: Commit** `"video-organize: take scoring, verdicts and patch suggestions"`.

---

### Task 6: Take IDs, shot letters and the takes store

**Files:**
- Create: `skills/video-organize/scripts/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Produces: `load(dir: Path) -> dict` / `save(dir: Path, data: dict)` for `takes.json` = `{"schema": "rvs.takes/1", "takes": [...], "picks": {scene: {"id", "file", "start", "end", "by": "user"}}, "shots": {scene: {file: letter}}, "questions": [...]}`.
- Produces: `assign_ids(old: list[dict], new: list[Take], shots: dict) -> list[dict]` — a new take inherits the id of an old take in the same file whose time range overlaps ≥ 50 %; others get the next free number for their scene+shot, in filming order (file creation time from `ffprobe` `creation_time`, then camera number in the name, then start time). IDs never change once issued.
- Produces: `shot_letters(scene: Scene, takes: list[Take], frame_diff: Callable[[str, float, str, float], float], setup_letters: dict) -> tuple[dict, list[dict]]` → `{file: letter}` and questions `{"scene", "files", "diff"}`. Start at `A`; new letter only when the middle-frame difference is ≥ 0.35 (clear) ; 0.2–0.35 is a question; `Setup` text shared by scenes is a hint that keeps the same letter.
- Produces: `frame_diff(file_a, t_a, file_b, t_b) -> float` — mean absolute difference of 64×36 grayscale frames from ffmpeg, 0–1.
- Produces: `apply_pick(data, take_id) -> dict` and `stale_picks(data) -> list[dict]` (picks whose id, file and range no longer match a take).

- [ ] **Step 1: Write failing tests** (Review Focus 3):
  - `test_ids_stable_after_new_footage`: old takes S02A-T01..T03; a new file filmed earlier adds one S02 take → old three keep their ids, the new one is S02A-T04.
  - `test_ids_stable_after_threshold_change`: same takes with edges moved by 0.4 s keep ids.
  - `test_pick_survives_rerun`: pick S02A-T02, re-run assign → pick still points to the same file and range.
  - `test_stale_pick_reported`: the picked take's file removed → `stale_picks` returns it.
  - `test_shot_letters`: fake `frame_diff` returning 0.05 / 0.5 / 0.25 → letters A, B and one question.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_store -v` — fail.
- [ ] **Step 3: Implement** `store.py`.
- [ ] **Step 4: Run** tests — pass.
- [ ] **Step 5: Commit** `"video-organize: stable take ids, shot letters, picks"`.

---

### Task 7: B-roll frames, measurements and recorded descriptions

**Files:**
- Create: `skills/video-organize/scripts/broll.py`
- Test: `tests/test_broll.py`

**Interfaces:**
- Produces: `extract_frames(path: str, out_dir: Path, every: float = 2.0) -> list[dict]` → `{"t", "image", "sharpness", "luma", "motion"}` using one ffmpeg pass (`fps=1/every`, `scale=640:-2`) plus `signalstats` YAVG for luma, Laplacian-variance proxy via `edgedetect` mean for sharpness, and frame-to-frame difference for motion.
- Produces: `record_descriptions(dir: Path, payload: dict)` — payload written by the agent: `{"file": {"frames": {"t": "description"}, "matches": [{"scene": "S03", "start": 12.0, "end": 18.0, "why": "..."}]}}`; validated (times inside the file, scene ids exist) and merged into `broll.json`.
- Produces: `best_moments(frames: list[dict], min_len: float = 3.0) -> list[dict]` → `{"start", "end", "score"}`: contiguous runs with luma between 16 and 235 and sharpness above the clip's median, scored 0–100, longest high-quality runs first.

- [ ] **Step 1: Write failing tests:**
  - `test_frames_every_two_seconds` (ffmpeg `testsrc` 10 s) → 5 frames at t = 0, 2, 4, 6, 8, each image exists.
  - `test_dark_frames_score_low`: frames 0–4 black, 4–10 `testsrc` → best moment starts ≥ 4.
  - `test_record_rejects_bad_scene`: payload with scene `S99` → `ValueError`.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_broll -v` — fail.
- [ ] **Step 3: Implement** `broll.py`.
- [ ] **Step 4: Run** tests — pass.
- [ ] **Step 5: Commit** `"video-organize: b-roll frames, quality measures, descriptions"`.

---

### Task 8: The CLI, TAKES.md and issues

**Files:**
- Create: `skills/video-organize/scripts/organize.py`, `skills/video-organize/scripts/report.py`
- Test: `tests/test_organize_cli.py`

**Interfaces:**
- Consumes: Tasks 1–7.
- Produces CLI `organize.py --workdir W <cmd>`:
  - `run --footage DIR [--storyboard FILE] [--every 2] [--yes]` — first prints counts (recordings, audio-only files, total duration, scenes in the storyboard) and stops unless `--yes` is given, so the agent can confirm the folder with the user; then scan (video and audio-only extensions, recursive, skip hidden), transcribe, find takes, score, assign ids and letters, extract b-roll frames for non-spoken files, write `takes.json`, `broll.json`, `TAKES.md`, add issues. Storyboard defaults to the `storyboard` value in `.edit/config.json`. Prints what the agent must do next (describe frames: list of frame folders; questions to ask).
  - `describe PAYLOAD.json` — `broll.record_descriptions`, then regenerate `TAKES.md`.
  - `answer SCENE FILE LETTER` — resolve a shot question, regenerate.
  - `pick TAKE_ID` — user pick, regenerate.
  - `report` — regenerate `TAKES.md` only.
- Produces: `report.render(scenes, mode, data, broll) -> str`. `TAKES.md` sections in order: warning (mode `none`: "No storyboard or script: takes are grouped by similar text and not scored for completeness"), questions waiting, one section per scene (best first, with id, file, timecodes, score, status label `reworded`/`incomplete`, verdict, patch line `S12A-T02 + S12A-T04 from "<clause text>"`, `picked by you`), unmatched spoken clips, chatter count per file, b-roll (best moments with matched scenes), scenes with no usable take.
- Issues via `rvs.Session(workdir, connect_now=False)` after `init_state()`: one per scene with no complete or reworded take, one per scene needing a sub-clause patch, one per b-roll note with no match, one per stale pick. `where` = scene id plus best file and timecode.
- No-storyboard mode: takes grouped by text similarity (`difflib` ratio ≥ 0.6 on token lists) into `G01`, `G02`… in order of first appearance; no completeness part in the score; no rename.

- [ ] **Step 1: Write failing tests** with fake transcripts injected through `RVS_TRANSCRIBE_CMD` pointing at a tiny test script that returns canned words per input file:
  - `test_run_end_to_end`: 2 recordings + 1 silent b-roll clip + storyboard with 3 spoken scenes and 1 b-roll note → `TAKES.md` lists scenes, best take first; `EDIT_CHECKLIST.md` has an issue for the scene with no take; frame folders listed for the b-roll clip.
  - `test_pick_and_rerun`: `pick S02A-T01`, then `run` again → still picked, `TAKES.md` says "picked by you".
  - `test_no_storyboard_mode`: no storyboard → warning line present, groups `G01`…, no completeness wording.
  - `test_runs_without_resolve`: none of the commands import or call `rvs.connect` (patch `rvs.connect` to raise).
- [ ] **Step 2: Run** `python3 -m unittest tests.test_organize_cli -v` — fail.
- [ ] **Step 3: Implement** `organize.py` and `report.py`.
- [ ] **Step 4: Run** tests and full suite — pass.
- [ ] **Step 5: Commit** `"video-organize: CLI, TAKES.md and checklist issues"`.

---

### Task 9: Rename with approval, Resolve relink and undo

**Precondition — spike first, with the user:** ask the user to open a throwaway Resolve project containing two clips, both used on a timeline with a cut and a grade. Run a short script that renames one file on disk and calls `MediaPoolItem.ReplaceClip(new_path)`. Record in this task whether the timeline item still has its in/out points and grade. If not, set `RENAME_USED_CLIPS = False` (clips already on a timeline are excluded from the rename and listed with the reason).

**Files:**
- Create: `skills/video-organize/scripts/rename.py`
- Modify: `skills/video-organize/scripts/organize.py` (subcommands)
- Test: `tests/test_rename.py`

**Interfaces:**
- Produces: `propose(data: dict, scenes) -> list[dict]` → `{"old", "new", "reason"}` following the naming table; b-roll and unmatched files excluded.
- Produces: `validate(rows) -> list[str]` — errors: target exists, two rows with the same target, same source twice, source missing. Any error blocks the whole batch.
- Produces: `apply(rows, workdir, resolve_project=None, never_imported=False) -> Path` — writes `renames.json` first, renames on disk, then (if the project is open and matches state) for each media pool item whose `File Path` is an old path: `ReplaceClip(new)` and `SetClipProperty("Clip Name", new_basename)`; updates paths in `takes.json`, `broll.json`, transcript cache keys and the bridge's current plan only. Refuses when Resolve is closed or a different project is open, unless `never_imported=True`.
- Produces: `undo(workdir, resolve_project=None) -> int` — reverses the last `renames.json` batch the same way.
- CLI: `organize.py rename` (prints the proposal, exit 0, changes nothing), `rename --apply [--never-imported]`, `rename --undo`.

- [ ] **Step 1: Write failing tests** (temp dirs with empty files; Resolve calls behind a small fake object):
  - `test_names_follow_table`: one-take file → `S02A_T03_IMG_1.MOV`; three takes of one shot → `S02A_T01-T03_IMG_2.MOV`; multi-scene → `S03A_S05A_S06A_IMG_3.MOV`; two shots of one scene → `S02A_S02B_IMG_4.MOV`; b-roll unchanged.
  - `test_collision_blocks_batch` (Review Focus 5): target exists → `validate` returns an error and `apply` renames nothing.
  - `test_odd_names`: source `My Clip é.MOV` with spaces and accents renames and undoes cleanly.
  - `test_undo_restores`: apply then undo → original names, `takes.json` paths restored.
  - `test_resolve_relink_called`: fake media pool item with the old path receives `ReplaceClip(new)`.
  - `test_refuses_without_project`: no project and `never_imported=False` → `RuntimeError`, nothing renamed.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_rename -v` — fail.
- [ ] **Step 3: Implement** `rename.py` and the CLI wiring.
- [ ] **Step 4: Run** tests — pass.
- [ ] **Step 5: Commit** `"video-organize: approved rename with Resolve relink and undo"`.

---

### Task 10: video-setup changes, skill file and docs

**Files:**
- Modify: `skills/video-setup/scripts/setup.py` (options and AGENTS.md text), `skills/video-setup/SKILL.md`
- Create: `skills/video-organize/SKILL.md`
- Modify: `README.md`, `docs/ROADMAP.md`
- Test: `tests/test_setup_config.py`

**Interfaces:**
- Produces: `setup.py write` options `--storyboard PATH`, `--approve-phrases ...`, `--reject-phrases ...`; config keys `storyboard`, `approve_phrases`, `reject_phrases`. An existing `phrases` key is kept and shown as "verdict phrases (meaning unknown)". `check` prints `WARN  No storyboard or script recorded` when `storyboard` is missing. `write` works with no timeline by calling `Session.init_state()`.
- Produces: AGENTS.md rule changes from "Never move, rename or overwrite source footage." to "Only rename source footage through `video-organize`, after the user approves the list. Never move or overwrite it."
- Produces: `skills/video-organize/SKILL.md` with frontmatter `name: video-organize` and a description that triggers on new footage, organising takes, picking takes, or renaming clips; workflow: setup check → `run` → describe frames and answer questions with the user → show `TAKES.md` → `pick` corrections → offer `rename` (show proposal, apply only on approval) → point to `video-assemble` (not built yet).

- [ ] **Step 1: Write failing tests:**
  - `test_phrase_lists_saved`: `write --approve-phrases "very nice" --reject-phrases "one more time"` → config has both lists.
  - `test_legacy_phrases_kept`: existing config with `phrases` stays and AGENTS.md shows it under the unknown label.
  - `test_write_without_timeline`: no `--timeline` and no state → state created with version 0, `EDIT_CHECKLIST.md` written, next step `organize`.
  - `test_agents_rename_rule`: generated AGENTS.md contains the new rename rule.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_setup_config -v` — fail.
- [ ] **Step 3: Implement**, then write `SKILL.md` and replace `video-ingest` with `video-organize` in `README.md` and `docs/ROADMAP.md` (mark it built).
- [ ] **Step 4: Run** the full suite `python3 -m unittest discover -s tests -v` — pass. Check the installer lists the skill: `npx -y skills add <repo path> --list` shows `video-organize`. Scan for user-specific text: `grep -rniE "johnnyfekete|life ?os|hungarian" skills docs README.md` prints nothing.
- [ ] **Step 5: Commit** `"video-organize: skill file, setup options, docs"`.

---

### Task 11: Real-footage check

No new code unless the check finds bugs.

- [ ] **Step 1:** With the user's approval, run `organize.py run` on a copy of a real project folder (scratch workdir, footage read in place, no rename) and a storyboard exported to the Markdown format.
- [ ] **Step 2:** Compare against what the user already knows about that footage: every scene has the takes they remember; on-set verdicts are picked up; chatter is not a take; known broken words are flagged. List mismatches.
- [ ] **Step 3:** Tune the thresholds in `takes.py` and `score.py` against the mismatches; add a regression test per fixed mismatch using a trimmed transcript fixture (no media in the repo).
- [ ] **Step 4:** Full suite passes; commit `"video-organize: thresholds tuned on real footage"`.
- [ ] **Step 5:** Ask the user before merging `video-organize` into `main` and pushing.
