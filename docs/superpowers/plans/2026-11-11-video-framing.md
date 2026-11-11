# video-framing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `video-framing` skill that gives every on-camera clip a clean, centred, professionally framed picture: one declutter base frame per camera setup, alternating punch-ins on visible jump cuts with the eyes held in place, and slow push-ins on key lines of plain setups. It runs without stopping for approval and leaves a report, before/after previews and checklist issues for review.

**Architecture:** Small stdlib-only Python modules under `skills/video-framing/scripts/`, one per responsibility, driven by one CLI (`framing.py`) with three commands:
- `prepare` measures the clips and writes the material the agent judges from.
- `solve` turns measurements plus the agent's `decisions.json` into frames, previews and a report. It is a dry run.
- `apply` forks a new version through `resolve-bridge`.

Face, eye, body and hand detection runs in a Swift helper on Apple Vision, compiled once and cached. The solver is pure functions over plain dicts, so almost everything is unit-tested without Resolve.

**Tech Stack:** Python 3.11+ stdlib, `ffmpeg`/`ffprobe` (with `zscale`/`tonemap` when present), Swift + Apple Vision (`swiftc`, macOS), DaVinci Resolve Studio scripting API (fork, item properties, Fusion), `unittest`. Transcription uses the existing local Whisper runner (`uv` + `mlx-whisper`, or `RVS_TRANSCRIBE_CMD`).

**Spec:** `docs/superpowers/specs/2026-11-11-video-framing-design.md`

## Global Constraints

- Python 3.11+, standard library only inside the skill. Vision only through the Swift helper. Whisper only through the existing subprocess runner.
- macOS only for measuring (Apple Vision). On other platforms `prepare` stops with a clear message.
- Repo text is agent-neutral and contains nothing specific to one user's video.
- Commits are authored by the repo owner only: no `Co-Authored-By` or "Generated with" lines.
- Work on branch `video-framing` cut from `main`. Merge only when the user approves.
- Mode is a **fork** (`Session.begin_step("framing")` / `finish_step`). Framing never splits items or moves an edit.
- Never touch b-roll items. Never touch an on-camera item framed by hand (see Task 9).
- Defaults:
  - punch-in 1.15x, minimum 1.10x when a gesture is tight
  - push-in 1.06x, at most one per 30 s
  - gesture margin 4% of frame height
  - eye-trace tolerance 3% of frame width
  - maximum off-centre shift 5% of frame width
  - outlier threshold 10% zoom
  - zoom budget = source height / delivery height
  - declutter cap 1.15x for sources with no headroom
- Eyes sit on or just above the upper third line. The bottom edge never sits on a joint. No black edges. Total zoom ≤ budget, and when over budget the punch is reduced first, never the base.
- Spikes and calibration run in a scratch Resolve project, never in a user's edit.
- Tests run with `python3 -m unittest discover -s tests -v` from the repo root.

## Review Focus

1. **The speaker leaves or enters the frame mid-clip** (stands up, walks in, leans out). Detection gaps must not shrink the gesture box or centre on an empty wall. Expected: frames with no detection are ignored; under 50% detected frames → setup base frame, no punch or push, flag. Test in Task 6 (`summarize`) and Task 7.
2. **Keep-out clutter that overlaps the gesture box.** Gestures win. Expected: the frame contains the gestures, clutter is reduced as much as possible, flag. Test in Task 7.
3. **A jump cut covered by b-roll on a higher track**, including b-roll that covers only part of either side. Only the cut frame matters. Test in Task 5.
4. **Re-running framing on an already framed timeline**, and a user tweak to one clip's zoom in between. Zoom must not stack, and the tweaked clip stays untouched. Test in Task 9.
5. **Low-resolution sources** (1080p in a 1080p timeline, or a 1440p phone clip). Budget below punch × push. Expected: declutter only (capped 1.15x), punches and pushes dropped with a report line, no soft punch-ins. Test in Task 7 and Task 8.

---

### Task 1: Resolve spike and calibration (scratch project)

Answers the three questions the rest of the plan depends on and records them. There's no TDD here: it's an exploration script plus notes.

**Files:**
- Create: `skills/video-framing/scripts/spike_resolve.py`
- Create: `skills/video-framing/references/resolve-notes.md`

**Interfaces:**
- Produces: `resolve-notes.md` with three sections, which later tasks read:
  - **Calibration**: the exact formula from a source-normalised crop rectangle (centre `cx, cy`, width `w`, all 0..1, origin top-left) to `ZoomX/ZoomY/Pan/Tilt`, under "scale full frame with crop", including signs and units.
  - **Dynamic Zoom**: whether start/end rectangles can be set by script. Expected answer: no.
  - **Fusion push-in**: the exact API calls that add a Transform with keyframed `Size` and `Center` on a timeline item, ease in/out, at given item-relative frames. Also how to detect them (`GetFusionCompCount`).

- [ ] **Step 1: Create branch** `git checkout -b video-framing main`.
- [ ] **Step 2: Write `spike_resolve.py`.** It connects with `rvs.connect()` and refuses to run unless the open project's name starts with `rvs_scratch`. It then:
  - appends a 4K test clip to a new 1080p timeline;
  - sets known Zoom/Pan/Tilt values and grabs stills (`ExportCurrentFrameAsStill`);
  - tries the Dynamic Zoom property keys;
  - adds a Fusion comp with a keyframed Transform.
- [ ] **Step 3: Run it** in a scratch project with any 4K clip. Measure where a marked source point lands in each still, and fit the formula.
- [ ] **Step 4: Write `resolve-notes.md`** with the three answers. If Fusion keyframes also fail, write "push-ins: not supported in v1"; Task 10 then skips push-ins and the report says so.
- [ ] **Step 5: Commit** `video-framing: Resolve spike notes (calibration, dynamic zoom, fusion push-in)`.

---

### Task 2: Bridge keeps Fusion work safe

**Files:**
- Modify: `skills/resolve-bridge/scripts/rvs.py` (`export_plan`, `_CMP_KEYS`, `Session.build`, CLI `build`)
- Test: `tests/test_bridge_fusion.py`

**Interfaces:**
- Produces: plan items may carry `"fusion_comps": int` (only when > 0). `Session.build(plan, step, summary, allow_fusion_loss: bool = False)`. CLI flag `build --allow-fusion-loss`.

- [ ] **Step 1: Write failing tests** with a minimal fake timeline and items (fake `GetItemListInTrack`, `GetFusionCompCount`, etc.):
  - `test_export_records_fusion_comps`: an item with 2 comps → `item["fusion_comps"] == 2`; an item with 0 → key absent.
  - `test_diff_reports_fusion_change`: `diff_plans` on two plans that differ only in `fusion_comps` → one change line.
  - `test_build_refuses_when_fusion_would_be_lost`: the state's current plan has an item with `fusion_comps`; `build()` raises `RvsError`, and the message names the clip and timecode. With `allow_fusion_loss=True` it gets past the check (stub `build_timeline`).
- [ ] **Step 2: Run** `python3 -m unittest tests.test_bridge_fusion -v`. Expect failures.
- [ ] **Step 3: Implement.** Use `it.GetFusionCompCount()` in a try block (0 on error), add `"fusion_comps"` to `_CMP_KEYS`, and put the check in `build` before `build_timeline`.
- [ ] **Step 4: Run** the whole suite. All pass.
- [ ] **Step 5: Commit** `resolve-bridge: record Fusion comps in plans, refuse builds that would drop them`.

---

### Task 3: Shared transcriber in the bridge

Move the transcriber so framing (and later captions) can use it without depending on `video-organize`.

**Files:**
- Create: `skills/resolve-bridge/scripts/rvs_transcribe.py`, `skills/resolve-bridge/scripts/whisper_worker.py` (moved with `git mv`)
- Modify: `skills/video-organize/scripts/transcribe.py` (becomes `from rvs_transcribe import *` plus its `main`), and `organize.py` imports if needed
- Test: `tests/test_transcribe.py` (update imports only)

**Interfaces:**
- Produces: `rvs_transcribe.transcribe(path: str, cache_dir: Path, *, stream: int = 0, runner=None) -> dict` (same return shape as today: `words[{w,start,end,p}]`, `segments[{text,start,end,language}]`).

- [ ] **Step 1: Move the code**, keeping behaviour identical. The `WORKER` path points at the bridge's `whisper_worker.py`.
- [ ] **Step 2: Run** the whole suite. All pass, with no test logic changed.
- [ ] **Step 3: Commit** `resolve-bridge: shared transcriber (moved from video-organize)`.

---

### Task 4: Geometry

**Files:**
- Create: `skills/video-framing/scripts/geometry.py`
- Test: `tests/test_framing_geometry.py`

**Interfaces:**
- Consumes: the calibration formula from `references/resolve-notes.md`.
- Produces:
  - `Rect` dataclass (`cx`, `cy`, `w`, plus `aspect` = source aspect, default 16/9). Properties `h`, `left`, `top`, `right`, `bottom`; methods `contains(box)`, `overlaps(box) -> float` (overlap area as a fraction of the box), `inside_source() -> bool`, `scaled(f, anchor=(x, y)) -> Rect` (zoom by `f` keeping `anchor` at the same screen position).
  - `Box = tuple[float, float, float, float]` (left, top, right, bottom), normalised source coordinates.
  - `zoom_budget(src_h: int, out_h: int) -> float`
  - `zoom_of(rect) -> float` (= 1 / w)
  - `rect_to_props(rect, tl_w, tl_h) -> dict` (`ZoomX`, `ZoomY`, `Pan`, `Tilt`, rounded to 4 decimals)
  - `props_to_rect(props, tl_w, tl_h) -> Rect`

- [ ] **Step 1: Write failing tests:**
  - `Rect(0.5, 0.5, 1.0).inside_source()` is True; `Rect(0.3, 0.5, 0.8)` is False.
  - `scaled(1.15, anchor=eye)` keeps `(eye - left) / w` unchanged to 1e-9.
  - `zoom_budget(2160, 1080) == 2.0`.
  - `props_to_rect(rect_to_props(r)) ≈ r` for three rects.
  - The full frame gives `{"ZoomX": 1.0, "ZoomY": 1.0, "Pan": 0.0, "Tilt": 0.0}`.
  - One rect with the hand-computed props from the notes' formula.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_framing_geometry -v`. Fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Pass.
- [ ] **Step 5: Commit** `video-framing: geometry`.

---

### Task 5: Timeline reading (on-camera items, setups' raw groups, visible jump cuts)

**Files:**
- Create: `skills/video-framing/scripts/timeline.py`
- Test: `tests/test_framing_timeline.py`

**Interfaces:**
- Consumes: bridge plan dicts (`rvs.plan/1`).
- Produces:
  - `item_key(d) -> str`: `f"V{track}@{start}"`, stable within one plan.
  - `shot_prefix(clip: str) -> str | None`: the leading run of `S\d+` tokens joined by `_`, e.g. `"S19_IMG_5404_cfr60.mov" → "S19"`, `"S20_S22_IMG_5785.MOV" → "S20_S22"`. None when the clip has no prefix.
  - `on_camera(plan) -> list[dict]`: every `both` video item, plus every video-only item whose `shot_prefix` matches the shot prefix of an audio or `both` item that overlaps it in time, or whose `path` equals one. Sorted by start.
  - `visible_cuts(plan, cam_items, setup_of: dict[str, str]) -> list[tuple[str, str]]`: pairs `(key_a, key_b)` where `a.end == b.start`, both are in the same setup, and no b-roll video item on a higher track covers frame `b.start` (or `b.start - 1`).
  - `runs(cam_items, cuts) -> list[list[str]]`: maximal chains joined by visible cuts.

- [ ] **Step 1: Write failing tests** with small handmade plans:
  - A `both` item counts as on-camera.
  - A video-only `S19_IMG_5404_cfr60_from40s.mov` with an audio item `S19_IMG_5404.MOV` overlapping it counts.
  - A video-only b-roll `IMG_5807.MOV` on V3 does not count.
  - A video-only `S12_S13_IMG_5777.MOV` with overlapping audio `S12_IMG_5778.MOV` counts (prefix `S12_S13` shares `S12` with `S12`; match when the prefix token sets intersect).
  - Cut covered by V3 b-roll spanning the cut frame → not visible. B-roll ending one frame before the cut → visible. B-roll over only the first half of item b → visible.
  - Two same-setup items with a 1-frame gap → no cut.
  - `runs` on 5 items with cuts 1-2, 2-3 and 4-5 → `[[1, 2, 3], [4, 5]]`.
- [ ] **Step 2: Run.** Fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Pass.
- [ ] **Step 5: Commit** `video-framing: on-camera items and visible jump cuts`.

---

### Task 6: Measuring (Swift Vision helper + summaries)

**Files:**
- Create: `skills/video-framing/scripts/face_probe.swift`
- Create: `skills/video-framing/scripts/measure.py`
- Test: `tests/test_framing_measure.py`

**Interfaces:**
- `face_probe.swift`: takes `in.json` (a list of image paths) and writes `out.json` with one object per image: `{"face": Box|null, "eyes": [x, y]|null, "head": Box|null, "hands": [Box...], "joints": {name: [x, y]}}`. Coordinates are normalised, origin top-left (convert from Vision's bottom-left). It uses `VNDetectFaceLandmarksRequest` (eye centre = mean of both eye-region points), `VNDetectHumanBodyPoseRequest` (joints: nose, neck, shoulders, elbows, wrists, hips, knees; confidence ≥ 0.3) and `VNDetectHumanHandPoseRequest` (`maximumHandCount = 2`, box over points with confidence ≥ 0.3). With several faces, it keeps the largest.
- `measure.py` produces:
  - `helper_path(cache: Path) -> Path`: compiles with `swiftc -O` into `cache/bin/face_probe` once. It recompiles when the `.swift` file's mtime is newer. It raises `RuntimeError("video-framing measures with Apple Vision and needs macOS with Xcode command line tools")` when `swiftc` is missing.
  - `extract_frames(path, src_start_s, src_end_s, out_dir, fps=4.0) -> list[Path]`: uses ffmpeg at 960 px wide. It tone-maps (`zscale` + `tonemap=hable`) when the stream's `color_transfer` is `arib-std-b67` or `smpte2084` and ffmpeg has `zscale`, and extracts plain frames otherwise.
  - `summarize(frames: list[dict]) -> dict`, pure. It returns `{"detected": float, "eyes": [x, y], "face_h": float, "extent": Box, "joints_y": {name: y}}`:
    - frames with no face are ignored;
    - `extent` is the per-edge 2nd/98th percentile of the union of head and hand boxes;
    - `eyes` and `joints_y` are medians;
    - `detected` is the fraction of frames with a face.
  - `measure_items(plan, cam_items, workdir) -> dict[str, dict]`: one summary per `item_key`. Each item's summary is cached in `.edit/framing/measure/<sha1(path:src_start:src_end)>.json`, and cached items are skipped.

- [ ] **Step 1: Write failing tests** for `summarize` only (pure):
  - 10 frames with eyes at x=0.50 plus one at x=0.90 → median eyes x is 0.50.
  - A hand box in one frame out of 60 at the far left → not in `extent` (percentile drops it).
  - Hand boxes in 20 of 60 frames → in `extent`.
  - 3 of 10 frames detected → `detected == 0.3`, and the summary uses only those 3.
  - No detections → `detected == 0.0`, `eyes is None`.
- [ ] **Step 2: Run.** Fail.
- [ ] **Step 3: Implement** `summarize`, then the helper, `extract_frames` and `measure_items`.
- [ ] **Step 4: Run.** Pass. Then a manual check: run `measure_items` on one real 4K clip. The eyes and extent drawn on a frame land on the face and hands.
- [ ] **Step 5: Commit** `video-framing: Apple Vision measuring`.

---

### Task 7: Base frames per setup

**Files:**
- Create: `skills/video-framing/scripts/setups.py`
- Create: `skills/video-framing/scripts/solve.py` (base-frame part)
- Test: `tests/test_framing_setups.py`, `tests/test_framing_solve_base.py`

**Interfaces:**
- `setups.py` produces:
  - `signature(frame_path) -> list[int]`: a 16×9 grayscale thumbnail via ffmpeg.
  - `group_setups(cam_items, signatures: dict[key, list[int]], threshold=12.0) -> dict[str, list[str]]`. It first groups by source path, then merges groups whose mean absolute signature difference is below `threshold`. Setup ids are `A`, `B`, ... in timeline order.
- `solve.py` produces `solve_base(setup_items: list[str], measures: dict, decision: dict, budget: float, opts: Opts) -> SetupResult`.
  - `decision` = `{"keep_out": [Box], "keep_in": [Box], "label": "plain"|"dynamic", "reason": str}`.
  - `Opts` is a dataclass holding the Global Constraints defaults.
  - `SetupResult` = `{"rect": Rect, "items": {key: Rect}, "flags": [{"key", "text"}]}`.

Algorithm, the part tests don't fully determine:
- **Target size.** The tightest width `w` such that:
  - every included item's `extent` grown by the gesture margin fits inside;
  - median face height ≤ 0.28 × frame height;
  - `w ≥ punch × push / budget`, but only when the budget allows punches. Otherwise `w ≥ 1 / min(budget, 1.15)`, and punches and pushes are disabled for the setup.
- **Position.**
  - `cx` = median eye x over the setup's items.
  - `cy` puts the eyes on the upper third: `cy = eye_y + h / 6`, then clamps so the head extent plus margin stays inside.
  - Then clamp inside the source.
- **Keep-out.** If the rect overlaps a keep-out box:
  1. shift `cx` toward the free side, up to 5% of `w`;
  2. then tighten `w` (down to the budget floor) while gestures still fit;
  3. if it still overlaps, keep the gesture-safe rect and flag "clutter left in frame: …".
- **Keep-in.** If a keep-in box is cut by the frame edge (overlap between 0.1 and 0.9), widen or shift to include it whole, else exclude it clearly. A sliver is never left on the edge (no tangents).
- **Joints.** If the bottom edge is within 3% of `h` of any `joints_y` value, grow `w` until it is clear (or shrink, if that keeps gestures).
- **Outliers.** For each item, compute the setup `w` without it. If including it loosens the setup by more than 10% (`w_with / w_without > 1.10`), that item gets its own rect sized for its own extent, and a flag.
- **Pan follow.** Any item whose median eye x differs from the setup `cx` by more than 5% of `w` gets the same `w` with `cx` = its own eye x (clamped).
- **Weak detection.** Items with `detected < 0.5` get the setup rect and a flag.

- [ ] **Step 1: Write failing tests:**
  - `group_setups`: two paths with near-identical signatures → one setup; different signatures → two.
  - Centring: eyes at (0.52, 0.30) → `rect.cx ≈ 0.52` and the eyes at `top + h / 3 ± 0.01`.
  - Gestures: a hand extent reaching y=0.95 → `rect.bottom ≥ 0.95 + margin`, or the frame is the full source.
  - Keep-out on the left `(0, 0, 0.29, 1)`, face at x=0.53 → rect left ≥ 0.29 and `cx` within 5% of `w` of 0.53. Same keep-out but a hand extent reaching x=0.2 → the gesture is inside and the flag text contains "clutter left in frame".
  - Keep-in sliver: a plant box half inside → afterwards it is fully inside or fully outside.
  - Joint: a shoulder line at the bottom edge → moved by at least 3% of `h`.
  - Outlier: 5 items, one with arms overhead → the setup `w` matches the 4 others, and the outlier has its own rect and a flag.
  - Budget 1.0 (1080p source) → zoom ≤ 1.15 and the result says punches are off.
  - Never `inside_source() == False`, for every test rect.
- [ ] **Step 2: Run.** Fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Pass.
- [ ] **Step 5: Commit** `video-framing: base frame solver`.

---

### Task 8: Punch-ins and push-ins

**Files:**
- Modify: `skills/video-framing/scripts/solve.py`
- Test: `tests/test_framing_solve_cuts.py`

**Interfaces:**
- Consumes: `SetupResult`, `runs()` and `visible_cuts()` from Task 5, `Rect.scaled` from Task 4.
- Produces:
  - `solve_punches(runs, item_rects, measures, setup_of, setup_info, budget, opts, push_keys: set[str]) -> (dict[key, Rect], list[flag])`.
  - `solve_pushes(push_ins: list[dict], plan, cam_items, setup_info, item_rects, budget, opts, cuts) -> (list[Push], list[flag])`.
  - `Push` = `{"key", "start_frame", "end_frame", "from": Rect, "to": Rect, "text", "reason"}`, with frames relative to the item.
  - `push_ins` input entries are `{"start_s", "end_s", "text", "reason"}` in timeline seconds.
  - `setup_info: dict[setup_id, {"label": str, "punch_ok": bool, "rect": Rect}]`, built from the decisions and Task 7's `SetupResult` (`punch_ok` is False when the budget disabled punches).

Rules:
- **Punch alternation.** In each run, slots alternate base, punch, base, punch. A push-in item always takes the base slot, and the alternation restarts from it. An outlier item (own rect) also restarts the alternation and is not punched.
- **Punch rect.** `item_rect.scaled(1.15, anchor=eyes)`. If it is not `inside_source` or doesn't contain extent + margin, try 1.10. If that also fails, skip (base) and restart the alternation after it. If the base zoom × punch is over budget, use `budget / base_zoom` if ≥ 1.10, else skip.
- **Eye trace.** For every visible cut, if the eye screen positions on the two sides differ by more than 3% of frame width, shift the incoming rect's `cx` (same `w`, clamped) to match.
- **Push validity.** The push is dropped with a flag if any of these hold:
  - the item's setup is labelled `dynamic`;
  - the range isn't inside one on-camera item;
  - b-roll covers any frame of the range;
  - it starts less than 30 s after the previous push's start;
  - the previous push is on the item just before in the same run;
  - base zoom × 1.06 > budget;
  - fast movement: the item's gesture `extent` is wider than 60% of the base rect's width.
- **Push rect.** `to = from.scaled(1.06, anchor=eyes)`. `from` is the item's base rect.

- [ ] **Step 1: Write failing tests:**
  - A run of 4 items → slots `[base, punch, base, punch]`; with item 2 as a push → `[base, base(push), punch, base]`.
  - The punch keeps the eye screen position (difference < 0.001).
  - A punch that would clip a hand at 1.15 but fit at 1.10 → zoom ratio 1.10. Clipping at both → base, with a flag.
  - Budget 2.0, base zoom 1.8 → punch ratio `2.0 / 1.8 ≈ 1.11`. Base zoom 1.85 → `2.0 / 1.85 < 1.10` → no punch.
  - An eye-trace offset of 5% across a cut → corrected to < 3%.
  - Pushes:
    - on a `dynamic` setup → dropped, flag mentions "dynamic";
    - two pushes 20 s apart → the second dropped;
    - a push under b-roll → dropped;
    - a valid push → `to.w ≈ from.w / 1.06`, eyes anchored.
- [ ] **Step 2: Run.** Fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Pass.
- [ ] **Step 5: Commit** `video-framing: punch-ins, eye trace and push-ins`.

---

### Task 9: Decisions, applied record and hand-framed items

**Files:**
- Create: `skills/video-framing/scripts/decisions.py`
- Test: `tests/test_framing_decisions.py`

**Interfaces:**
- Produces:
  - `template(setups: dict[str, list[str]]) -> dict`. The decisions skeleton is `{"setups": {id: {"items": [...], "keep_out": [], "keep_in": [], "label": null, "reason": ""}}, "reclassify": {key: "broll"|"on_camera"}, "push_ins": []}`.
  - `load(path) -> dict`. Validates and raises `ValueError` naming the problem: a box outside 0..1, a label not in {plain, dynamic}, a missing label, a push with `end_s <= start_s`.
  - `merge(old: dict, new_setups) -> dict`. Keeps every decision for setups whose item set is unchanged. Items that move setup carry over. New setups come in with an empty template.
  - `hand_framed(cam_items, applied: dict | None) -> set[str]`:
    - on the first run (`applied is None`), keys of items with any non-default Zoom, Pan, Tilt, Crop*, Rotation or `fusion_comps`;
    - on later runs, items whose current props or `fusion_comps` differ from `applied[match_key]`;
    - `match_key = f"{path}|{src_start}|{end - start}"`, so a key survives ripples.
  - `save_applied(path, entries: dict[match_key, {"props", "fusion_comps"}])`.

- [ ] **Step 1: Write failing tests:**
  - `load` rejects a box `(0.2, 0.1, 1.3, 0.9)`, a label `"boring"`, and a missing label.
  - `merge` keeps keep-out boxes for an unchanged setup and adds a new setup with `label is None`.
  - First run: an item with `ZoomX: 1.3` is hand-framed; an item with no props is not.
  - Re-run: an item whose props equal applied → not hand-framed (it will be re-solved from scratch). Props that differ → hand-framed. The item rippled to a new start but has the same path, src_start and length → still matched.
- [ ] **Step 2: Run.** Fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Pass.
- [ ] **Step 5: Commit** `video-framing: decisions and hand-edit protection`.

---

### Task 10: Previews, report, Resolve apply and the CLI

**Files:**
- Create: `skills/video-framing/scripts/preview.py`, `skills/video-framing/scripts/apply_resolve.py`, `skills/video-framing/scripts/framing.py`
- Test: `tests/test_framing_cli.py`

**Interfaces:**
- `preview.py`:
  - `setup_still(frame, out, grid=True)`: a 10% grid with labels 0.0 to 1.0 on both axes, so the agent can read coordinates.
  - `before_after(frame, rect, out, keep_out=[])`: left half is the source with the rect drawn (green) and keep-out boxes (red); right half is the crop scaled to 960×540.
- `framing.py prepare --workdir W`:
  - status check;
  - export the current plan;
  - `on_camera`, `measure_items` and `group_setups`;
  - write `generated/framing/setups/<id>.jpg` (gridded) and `.edit/framing/context.json` (setups, items, timecodes, measure summaries, budget, source and timeline sizes);
  - write `.edit/framing/transcript.md` with the on-camera speech, one line per segment as `MM:SS.ss-MM:SS.ss <setup> <text>`, mapped from source time to timeline time, via `rvs_transcribe`;
  - write or merge `.edit/framing/decisions.json`;
  - print what the agent must fill in.
- `framing.py solve --workdir W [--no-punch] [--no-push] [--punch F] [--push F]`:
  - load decisions (it exits 2 with the validation message if any setup has no label);
  - skip hand-framed items;
  - run Tasks 7 and 8;
  - write `.edit/framing/solution.json`;
  - write `generated/framing/REPORT.md`, listing per setup the label, zoom and reason, the punch list, push lines with text and reason, hand-framed items left alone, and flags;
  - write `generated/framing/setups/<id>_before_after.jpg` and `generated/framing/flags/<n>.jpg`.
  - It changes nothing in Resolve.
- `framing.py apply --workdir W`: runs `solve` and then `apply_resolve.apply(session, solution)`:
  - `begin_step("framing")`;
  - for each solved item, find the item on the new timeline by track + start and `SetProperties(rect_to_props(...))`;
  - add pushes per `resolve-notes.md`;
  - write `applied.json`;
  - `finish_step(summary)` with a summary like "N setups framed (zoom a-b), P punch-ins, Q push-ins, H hand-framed left alone";
  - then `add_issue("framing", text, where)` for each flag, with clip name and timecode.
  - On any exception it calls `abandon_step()` and re-raises.

- [ ] **Step 1: Write failing CLI tests** with a fixture workdir, a small plan, and fake measures written straight into the cache, so no Vision or ffmpeg runs:
  - `solve` with a missing label exits 2 and the message names the setup.
  - `solve` with complete decisions writes `solution.json` and `REPORT.md`, and `REPORT.md` contains every push's text and reason.
  - A hand-framed item is listed under "left alone" and is absent from `solution.json`.
- [ ] **Step 2: Run.** Fail.
- [ ] **Step 3: Implement** `preview.py`, `framing.py` and `apply_resolve.py`.
- [ ] **Step 4: Run** the whole suite. All pass.
- [ ] **Step 5: Manual check in a scratch project:** `prepare`, fill in the decisions, `solve`, look at the previews, `apply`. The new `_vNN_framing` timeline shows the same framing as the previews, the previous version is archived, and issues appear in the checklist.
- [ ] **Step 6: Commit** `video-framing: previews, report, apply and CLI`.

---

### Task 11: Skill file, docs and the first real run

**Files:**
- Create: `skills/video-framing/SKILL.md`
- Modify: `README.md` (framing row: mark available), `docs/ROADMAP.md` (status, next skill), `skills/resolve-bridge/scripts/rvs_checklist.py` only if the framing line text changes

**Interfaces:**
- `SKILL.md` frontmatter `name: video-framing`, with a description covering: declutter crop per camera setup, centred direct-address framing, gesture-safe, punch-ins on visible jump cuts with eye trace, push-ins on key lines of plain setups, zoom budget, a new version through resolve-bridge, and autonomous with review afterwards.

- [ ] **Step 1: Write `SKILL.md`** in the style of `video-tighten`, with these sections:
  - **Workflow:** status, `prepare`, decide, `solve`, check the previews yourself, `apply`.
  - **How to decide:**
    - Read each gridded setup still and mark keep-out boxes for unchosen objects at the edges (appliances, magnets, spare chairs, lamps, cables, thermostats, sofa slivers).
    - Mark keep-in boxes for chosen set dressing (plants, flowers, a vase).
    - Label each setup `plain` (front-on, eye level, static) or `dynamic` (high or low angle, an unusual position, a strong diagonal).
    - Pick push-in lines from the script and `transcript.md`: the thesis, turns, punchlines, calls to action. Space them out and write a reason for each.
  - **Never stop for approval.** Fix your own decisions after looking at the previews, and turn uncertain calls into issues.
  - **The framing rules**, with the reasons from the spec, so the agent can handle shots the rules don't cover.
  - **Options table.**
  - **Not handled yet.**
- [ ] **Step 2: Update** the README and ROADMAP.
- [ ] **Step 3: Run** the whole suite. All pass.
- [ ] **Step 4: Commit** `video-framing: skill file and docs`.
- [ ] **Step 5: First real run.** On the user's current video, as its real framing step: `prepare`, decide, `solve`, check the previews, `apply`. Report the new timeline name and the issue ids. Anything that looks wrong in the result becomes a fix on this branch before merging.
