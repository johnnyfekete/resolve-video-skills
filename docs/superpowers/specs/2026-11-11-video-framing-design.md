# video-framing: design

Status: draft for approval. Step 4 of the pipeline (after `video-tighten`, before `video-color`).

## Why

Talking-head footage shot on a phone is usually framed wide "to be safe". The result looks amateur in two ways:

1. **Clutter at the edges.** Things nobody chose to show: a microwave covered in magnets, a spare chair, a random lamp, a half-visible sofa arm, a thermostat on a bare wall.
2. **Loose framing.** The speaker sits in the middle of a lot of empty wall, with too much headroom.

4K source delivered at 1080p gives up to 2x of zoom with no loss of sharpness. A good editor spends that zoom on a clean, centred frame per camera setup, a subtle punch-in on visible jump cuts, and a few slow push-ins on the lines that matter. `video-framing` does that job the way a professional editor would. The goal is a video that looks professionally framed.

## Who decides

The skill decides everything itself and does not stop to ask. The user reviews afterwards. The user's words: when the whole video is edited, nothing should stop halfway to wait for approvals.

- The agent running the skill makes the judgement calls: what counts as clutter, which setups are plain, which lines get a push-in. Scripts do the measuring and the maths.
- Every decision is written down with its reason, so the user can review it after the build (see Output).
- A call the agent is unsure about still gets its best answer and the build continues. It also becomes an issue in `EDIT_CHECKLIST.md`.
- The user fixes anything by hand in Resolve. Hand edits are recorded by `resolve-bridge` and survive later runs.

## Goals

1. **Declutter base frame per camera setup.** The tightest clean frame that removes unchosen objects at the edges and fixes loose framing. Chosen set dressing (a plant, flowers, a vase) stays.
2. **Centred on the speaker** for direct-to-camera address. Horizontally centred on the face; vertically by headroom rules.
3. **Never cut off the head or hands mid-gesture.** The frame contains every gesture in the clip.
4. **Punch-in on visible jump cuts**, alternating base and tighter frame, with the eyes kept in place across the cut.
5. **Slow push-ins on key lines**, only on plain setups, spaced out.
6. **Zoom budget**: total zoom never exceeds the source-to-delivery ratio (2.0 for 4K to 1080p).

## Non-goals

- Framing b-roll (screen recordings, cutaways, inserts). B-roll keeps whatever framing it has.
- Stabilisation, Smart Reframe for vertical formats (that is `video-shorts`), and colour or vignettes (`video-color`).
- Changing cut points or timing. Framing never moves an edit. (Splitting an item for a push-in at the same source frame, which leaves no visible cut, is allowed.)
- Running the whole pipeline with one subagent per step. That is a separate, later skill (see Follow-ups).

## Anti-goals: a failure even if it technically works

- **Cutting off the head or hands mid-gesture.** The user named this one. If clutter can only be removed by clipping a gesture, the gesture wins: keep the gesture, remove as much clutter as possible, and raise an issue.
- Black edges or soft images: any pan, tilt or zoom that shows outside the source frame, or goes past the zoom budget.
- A punch-in so small it looks like a mistake, or so big it looks like a different shot.
- Push-ins that drift on unimportant lines, cross a cut, or come one after another.
- Overwriting framing the user set by hand.
- Cropping out set dressing the user chose.

## Framing rules (professional practice)

These are the defaults the scripts enforce. The skill file explains the reasons, so the agent can apply them to shots the rules do not cover.

**Base frame**
- Eyes on or just above the upper third line. Headroom above the hair is small and the same for every clip of a setup. The top of the head never touches the frame edge, and the frame does not cut through the forehead unless the shot is a deliberate close-up.
- Horizontally centred on the face for direct address. If centring would bring clutter back in, the frame may shift off centre by up to 5% of its width; past that, keep it centred, zoom tighter, or raise an issue.
- The bottom edge never sits on a joint (chin, neck, elbows, wrists, waist, knees). It cuts between joints.
- No tangents: an object's edge should not run along the frame edge. Either include it clearly or crop it clearly. A laptop cut at the edge is fine. A sofa arm showing as a sliver is not.
- Gesture safety: the frame contains the union of head and hand positions over the whole clip, with a margin of about 4% of frame height. Measured, not guessed (see Measuring).
- Stays inside the source with no black edges, and under the zoom budget with room left for punch-ins and push-ins.

**Punch-ins on jump cuts**
- Only on visible jump cuts: the same camera setup on both sides of the cut, and no b-roll covering the cut on a higher track.
- Alternate base, punched, base, punched along a run of same-setup pieces. The punch is 1.15x the base by default (configurable). Smaller changes read as a mistake. The 1.1x mentioned during design was raised to 1.15x for that reason.
- Eye trace: the incoming clip's eyes land within about 3% of frame width of the outgoing clip's eyes, so the viewer's gaze does not jump.
- Gesture safety and the zoom budget still apply. If the punched frame would clip a gesture, use a smaller punch (down to 1.10x) or skip the punch on that cut.

**Push-ins on key lines**
- Only on setups the agent labels **plain**: front-on, eye level, static, for example seated at a table. Setups labelled **dynamic** (high or low angle, an unusual position such as sitting on a counter, a strong diagonal) get none. They already have energy.
- On a line that carries the video: the thesis, a turn in the argument, a punchline, a call to action. The agent proposes the lines from the script and the transcript and writes down why it chose each one.
- Slow and eased: 1.00 to about 1.06 of the frame it starts from, over the line (typically 3 to 8 s), ease in and out.
- At most one every 30 s. Never across a cut, never two in a row on the same setup, never during fast movement, never while b-roll covers the speaker.
- A piece that has a push-in is not also punched. Its neighbours alternate around it.

**Zoom budget**
- Budget = source height / delivery height (2.0 for 2160 to 1080). If base × punch × push would go over budget, reduce the punch first. Never reduce the declutter crop.
- Sources that give no headroom (1080p into 1080p) get declutter crops of up to 1.15x and a warning. Some softening is accepted only for clutter removal, never for punch-ins or push-ins.

## How it works

1. **Status** through `resolve-bridge`, as with every step.
2. **Find the on-camera items.** An item is on-camera if it is either of these:
   - a linked `both` item;
   - a video-only item whose shot matches a dialogue item's shot. Matching uses the shot prefix of the clip name (`S19_...`), so a constant-frame-rate copy or proxy still matches its original. This covers voiceover splits, where the picture is on camera for only part of its length; the whole item is framed.

   Everything else is b-roll and is left alone. The agent can reclassify items in the decisions file.
3. **Group into camera setups.** Cluster on-camera items by source clip and background similarity of a sampled frame. Write one still per setup to `generated/framing/setups/`. The agent can correct the grouping in the decisions file.
4. **Measure** (script). For every on-camera item, sample frames (about 4 per second) and detect face, eyes, body and hands. Store the results per item in `.edit/framing/measure/`, so re-runs are fast.
5. **Decide** (agent). The agent looks at each setup still and writes `.edit/framing/decisions.json`:
   - per setup: keep-out rectangles for clutter, keep-in rectangles for set dressing that must stay, the label `plain` or `dynamic`, and a one-line reason;
   - push-in lines: timeline range and reason, chosen from the script plus a transcript of the on-camera pieces.
6. **Solve** (script). From the measurements and decisions, compute per item the base frame, the punch frame, the push-in pieces, and the Zoom, Pan and Tilt values, following the rules above. Anything the rules cannot satisfy becomes a flag.
7. **Preview** (script). Before/after stills per setup and per flagged item into `generated/framing/`, plus `REPORT.md`. The agent looks at the previews itself and fixes its own decisions if something looks wrong (a tangent, a cut hand) before building.
8. **Fork** a new version `<base>_vNN_framing` through `resolve-bridge`, set the properties (and push-in transforms) on it, then save and record the step. Flags become issues with clip name and timecode in the new timeline.

Dry run by default, `--apply` to build, the same as `video-tighten`. The agent runs both in one go when running autonomously.

## Measuring

- Apple Vision through a small Swift helper (macOS ships `swift`; no Python packages are needed): face rectangle and landmarks (eye centres), human body pose (head, shoulders, elbows, wrists) and hand pose. It runs on frames extracted with `ffmpeg`.
- HLG or HDR sources are tone-mapped to SDR for detection only.
- Per item the helper reports, per sampled frame: face box, eye centre, and a bounding box of head plus hands. The solver uses the union over the item, ignoring single-frame outliers.
- If the speaker is not detected in an item, it gets the setup's base frame, no punch or push, and a flag.

## One frame per setup (decided)

- Each setup has **one base rectangle**, and every item of the setup uses it. Consistency across cuts beats per-clip tightness: when the frame changes within a setup, it should only be the deliberate punch-in.
- The rectangle is sized to contain the gestures of all items in the setup.
- **Outlier exception:** if one item's gestures would loosen the whole setup by more than 10% zoom, that item gets its own looser frame instead and is flagged. The cuts into and out of it then count as that run's size change: no extra punch is added on those cuts, and the alternation restarts after it.
- Centring uses the median eye position over the setup. If the speaker drifts between takes by more than the off-centre allowance, Pan follows the face per item at the same zoom. A pan with no size change is not a visible frame change.

## Resolve specifics (to verify while building)

- Framing values are item properties the bridge already carries in plans: `ZoomX/ZoomY`, `Pan`, `Tilt`. The mapping from a crop rectangle to these values depends on the timeline's mismatched-resolution setting. The skill checks that the timeline scales sources with "scale full frame with crop" (or computes for the actual setting) and calibrates the mapping once with a test still.
- **Mode: fork (decided).** Base frames and punch-ins only change properties of existing items, since the jump cuts already split the items. A fork keeps grades, effects and anything the user added by hand, as the project rules prefer. Framing never splits items.
- **Push-ins (decided):** inside the item, over the line's range, with no split. First spike: whether a Dynamic Zoom's start and end rectangles can be set from a script. Expected: they cannot. Then a Fusion Transform with keyframes on the item (ease in and out, 1.00 to 1.06, anchored on the eyes). If neither works, push-ins are left out of v1 and the report says so; base frames and punch-ins ship regardless.
- Push-ins are recorded in `.edit/framing/decisions.json`, so a re-run finds them. Fusion comps are invisible to bridge plans, so `resolve-bridge` must be extended to: (a) detect Fusion comps on items when exporting a plan, and (b) refuse or warn on any later **build** that would drop them, naming the items. That bridge change is part of this work.
- Spikes and calibration run in a separate scratch Resolve project, never in a user's real edit.

## Re-runs and hand edits

- Decisions persist in `.edit/framing/decisions.json`. A re-run reuses them and only decides what is new (new setups, new items).
- Framing records the exact values it set per item in `.edit/framing/applied.json`.
- **First run:** an on-camera item that already has non-default Zoom, Pan, Tilt or crop counts as hand-framed. It is left exactly as it is and listed in the report. Framing only fills in items that are still at default.
- **Later runs:** an item whose values still match `applied.json` is re-solved from scratch, starting from the source rather than on top of existing zoom, so zoom never stacks. An item whose values differ from `applied.json` was changed by hand and is left alone.

## Output for review

- `generated/framing/REPORT.md`: per setup, the label, the base zoom and the reason; every punch-in; every push-in line with its text and the reason it was chosen; every flag.
- `generated/framing/setups/<setup>_before_after.jpg`: one before/after pair per setup, plus one per flagged item.
- Issues in `EDIT_CHECKLIST.md` only for calls the agent was unsure about or the rules could not satisfy, with clip name and timecode.

## Options

| | Default |
|---|---|
| Punch-in | 1.15x (min 1.10x when a gesture is tight) |
| Push-in | 1.06x, at most one per 30 s |
| Gesture margin | 4% of frame height |
| Eye-trace tolerance | 3% of frame width |
| Max off-centre shift | 5% of frame width |
| Zoom budget | source height / delivery height |
| `--no-punch`, `--no-push` | off |

## Testing

- Unit tests for the solver with synthetic measurements: centring, headroom, the gesture union, keep-out and keep-in, joint-free bottom edges, budget overflow order (punch reduced before base), eye-trace alignment, push-in spacing and placement rules.
- Spikes (Dynamic Zoom, Fusion keyframes, Zoom/Pan/Tilt calibration) in a scratch Resolve project.
- The first real run is the real framing step of a real video, run as usual: dry run, previews checked by the agent, then apply. It is a normal new version, so the previous one stays in the archive.

## Follow-ups (separate work)

- **Pipeline runner skill**: runs the whole edit from start to end with one subagent per step (tighten, framing, color, sound, and so on), collects issues, and only reports at the end. The user asked for this. It is out of scope here.
- Shared transcription of the timeline's on-camera pieces, reused by framing, captions and filler-word removal.

## Left to the builder

- Detection sampling rate, clustering method and thresholds.
- Exact `decisions.json` schema.
- How the transcript is produced (reuse `video-organize`'s transcriber where possible).
- Calibration method for Zoom, Pan and Tilt.
