---
name: video-framing
description: Frames a talking-head cut in DaVinci Resolve Studio like a professional editor. Crops each camera setup to a clean, centred frame that removes unchosen clutter at the edges (appliances, magnets, spare chairs, lamps) and keeps chosen set dressing (plants, flowers), never cuts off the head or a gesture, adds punch-ins on visible jump cuts with the eyes kept in place, adds slow push-ins on key lines of plain setups, and stays inside the zoom budget of the source. Produces a new timeline version through resolve-bridge. Runs on its own and leaves a report to review afterwards. Use when the user says the frame looks cluttered, loose or amateur, jump cuts are too visible, the speaker is off centre, or asks for punch-ins, push-ins or a declutter crop.
---

# video-framing

Run this after `video-tighten` (it hides the jump cuts tighten creates) and before `video-color`. Requires the **resolve-bridge** skill installed next to this one (it finds it at `../resolve-bridge`, or set `RVS_BRIDGE`), plus `ffmpeg`, `ffprobe` and `swift` (macOS ships it; Apple Vision measures faces and hands). The transcript needs `uv` (local Whisper) or a transcriber command in `RVS_TRANSCRIBE_CMD`; if transcription fails, `prepare` continues with a warning and the transcript is incomplete.

Run scripts as `python3 <this skill's folder>/scripts/framing.py <command> --workdir <folder>`. Commands: `prepare`, `solve`, `apply`, `pushes`.

The scripts measure and do the maths. **You make the judgement calls** (what is clutter, which setups are plain, which lines get a push-in). **Never stop to ask for approval.** The user reviews afterwards. A call you are unsure about still gets your best answer and the build continues; add it to the `issues` list in `decisions.json` (see *How to decide*), and `apply` turns it into a checklist issue. The `reason` fields only go into the report.

## Workflow

1. **Where are we.** `python3 <resolve-bridge>/scripts/rvs.py --workdir <folder> status`. Adopt the timeline first if nothing is tracked yet (see resolve-bridge).
2. **Prepare.** Changes nothing in Resolve:
   ```bash
   python3 scripts/framing.py prepare --workdir <folder>
   ```
   If Resolve is open and the tracked timeline has hand edits that are not recorded yet, `prepare` first records them as a new version (`manual`, "hand edits before framing") through resolve-bridge, and says so. Tell the user. If Resolve is open and its scaling mode would make `apply` refuse (see *What it does to the timeline*), `prepare` prints a warning at the start, so the user can change it before the long run.

   Finds the on-camera items, groups them into camera setups, measures face, eyes, body and hands (about 4 samples per second), and writes:
   - `generated/framing/setups/<id>.jpg`: one still per setup with a labelled 10% grid (0.0 to 1.0 on both axes);
   - `.edit/framing/transcript.md`: `MM:SS.ss-MM:SS.ss <setup> <text>`, one line per sentence heard on camera, in timeline time. Each on-camera piece has only the words inside its own source range (word times from Whisper), so nothing repeats across pieces, and the times are those of the first and last word. Pick push-in ranges straight from these lines;
   - `.edit/framing/decisions.json`: a template with every setup empty (a re-run keeps decisions for setups whose items did not change), `context.json` with budgets and stills.

   **The first `prepare` is slow:** measuring takes about 1.5 s per second of source, and Whisper runs once per source file. Run it in the background and expect tens of minutes on a 5-minute video. It prints a progress line per item and per source (flushed, so a redirected log shows it). Later runs are cached and fast; a skill update that changes how measuring works measures once more.

   It prints one line per setup (items, zoom budget, still) and the number of items that already carry hand framing: those are left exactly as they are, and the list is in `context.json` and `REPORT.md`.
3. **Decide.** Look at every setup still and fill in `decisions.json` (see *How to decide*). Then pick the push-in lines.
4. **Solve.** A dry run that changes nothing in Resolve and never connects to it. It always works from the recorded plan of the current version in the tracked state (a timeline adopted through resolve-bridge), so hand edits made in Resolve since the last recorded version are not in it: `prepare` and `apply` record them first.
   ```bash
   python3 scripts/framing.py solve --workdir <folder>
   ```
   Writes `generated/framing/REPORT.md`, `setups/<id>_before_after.jpg` per setup and `flags/<n>.jpg` per flagged item, and `.edit/framing/solution.json`. An invalid `decisions.json` (missing label, box outside 0..1) stops with the reason and exit code 2.
5. **Check the previews yourself.** Open every before/after image and the flag images. Each preview shows **one frame**: the setup's first item, or the flagged item, at its middle. A gesture in another item or at another moment is not in it, so check every flagged item's preview, not only the setup ones, and read the flag text. For a hand that is low in the frame (reaching toward the lens, gesturing near the bottom edge), consider `"bottom_hands": "gesture"` even when the one frame shown looks fine. Layout: the left half is the whole source with the frame that ships in green, keep-out boxes in red and keep-in boxes in blue (a push-in's end frame in cyan; on a flag about a skipped punch-in or a dropped push-in, the rejected attempt in orange); the right half is the result as the viewer sees it, with thirds. Look for the things the rules forbid: a tangent along the frame edge, a clipped hand (if a hand is cut at the bottom edge while it reaches toward the lens or gestures, not resting on the knees or a table, set `"bottom_hands": "gesture"` on that setup and solve again), a head too close to the top, clutter still at an edge, a chosen plant cut off. If a frame looks wrong, fix **your own decisions** (move a box, change a label, drop a push-in) and solve again. Repeat until the previews are right or the remaining problems are ones only a person can judge. Read the report's flags and dropped push-ins too: the report gives each flag's raw detail (box coordinates, item keys) under it; the checklist issues get the short plain-words text.
6. **Apply.** Needs Resolve open with the project loaded.
   ```bash
   python3 scripts/framing.py apply --workdir <folder>
   ```
   Solves again, then forks `<base>_vNN_framing` through resolve-bridge (grades, effects and hand edits stay), sets Zoom, Pan and Tilt (a push-in clip gets its start frame), archives the previous version and saves the project. After the step is recorded, every flag and every entry of your `issues` list becomes an issue in `EDIT_CHECKLIST.md`, located by clip name and timecode. Flags are merged first: all skipped punch-ins of a setup become one issue with their reasons counted, and identical flags on one setup become one. Tell the user the new timeline name, the issue ids and where the report is. Exit code 2 means an expected failure with a message (no timeline tracked, a scaling mode framing is not calibrated for, and so on).
7. **Keyframe the push-ins in the Inspector.** Resolve's Scripting API cannot create keyframes, so `apply` leaves each push-in clip on its start frame, writes `.edit/framing/keyframes_todo.json` and adds one checklist issue per push-in with the exact values. If you have computer use, do them now; otherwise tell the user the issues are theirs (about a minute per clip), and they are checked off by `pushes check` after the user's next hand-edit sync.
   ```bash
   python3 scripts/framing.py pushes begin --workdir <folder>     # forks <base>_vNN_pushins, lists the push-ins
   python3 scripts/framing.py pushes goto 1 start --workdir <folder>
   python3 scripts/framing.py pushes finish --workdir <folder>    # checks the keyframes, saves, closes the issues
   ```
   For each push-in, in the Resolve Edit page Inspector (Video > Transform). Background app control cannot type into Resolve, so this needs full-screen control:
   1. `pushes goto N start`. The script moves the playhead, and the Inspector follows it. Clicking a clip can move the playhead a frame, so do not click the timeline afterwards. Check that the Inspector names the clip.
   2. Click the keyframe diamonds of **Zoom** and **Position**, at the right of each row. They turn red. The values already are the start frame.
   3. `pushes goto N end`. Double-click Zoom X, then select all, type the end zoom and press Return (Zoom Y follows when linked). Do the same for Position X and Y. New keyframes appear on their own.
   4. Easing: right-click each red diamond. At the end, choose **Ease In**. At the start, once the end exists, choose **Ease In and Out**. A keyframe's menu only offers the easings its neighbours allow, and the tick does not always update, so check the result: halfway through, Zoom should be close to the average of the two values, and at a quarter it should be below the straight-line value.
   5. **Never set a property by script on a keyframed clip**: Resolve deletes all its keyframes.

   `pushes finish` reads the keyframes back (through an FCPXML export) and compares frames and values. If one is missing or wrong, it says which and leaves the step open. `--partial` saves anyway and leaves those issues open. `pushes check` does the same check without saving. `apply --pushes fusion` makes Fusion Transform push-ins instead (the old way, fully scripted).

## How to decide

`decisions.json`:

```json
{
  "setups": {
    "A": {
      "items": ["..."],
      "label": "plain",
      "reason": "seated at the table, front-on, static. Microwave with magnets at the right edge, spare chair at the far left; the plant stays.",
      "keep_out": [[0.00, 0.45, 0.12, 1.00], [0.86, 0.30, 1.00, 0.80]],
      "keep_in":  [[0.60, 0.20, 0.74, 0.62]]
    },
    "B": {"items": ["..."], "label": "dynamic", "reason": "sitting on the counter, high angle", "keep_out": [], "keep_in": []}
  },
  "reclassify": {"V1@1234": "broll"},
  "push_ins": [
    {"start_s": 41.2, "end_s": 47.0, "text": "Writing is thinking.",
     "reason": "the thesis, first time it is said plainly"}
  ],
  "issues": [
    {"text": "Plant at the right edge cropped with the coffee tables; unsure it was clutter",
     "where": "S04_IMG_1234.MOV at 00:14.00"}
  ]
}
```

Boxes are `[left, top, right, bottom]`, normalised 0..1 on the setup still (read them off its grid). `start_s` and `end_s` are timeline seconds from `transcript.md`. `items` is filled by `prepare`; leave it.

- **Keep-out** boxes: everything nobody chose to show, near the edges. Appliances, magnets, spare chairs, lamps, cables, thermostats, door frames, a sofa sliver. The solver crops them out where it can without clipping a gesture.
- **Keep-in** boxes: set dressing the speaker chose. Plants, flowers, a vase. The solver keeps them whole, or crops them out clearly, never a sliver. Box the part that matters (the bouquet, the plant's leaves), not down to the source edge: a box that touches the edge of the source can never be kept whole inside a crop.
- **Label** each setup `plain` or `dynamic`. `plain` is front-on, eye level and static (seated at a table). `dynamic` is a high or low angle, an unusual position (sitting on a counter) or a strong diagonal. Dynamic setups get no push-ins.
- **Reason**: one line per setup, always. It goes into the report.
- **Bottom hands** (optional): `"bottom_hands": "gesture"` on a setup. By default, hands touching the bottom of the source (from 93% of its height down) count as resting: the frame may stop above them. Hands reaching toward the lens touch the bottom edge too, and that rule would cut them. If a before/after or flag preview shows a hand cut at the bottom edge while it reaches or gestures, set this on that setup and solve again: those hands then count fully, and the frame is looser.
- **Issues**: every call you are unsure about (clutter or chosen, which of two things to keep, a line you nearly picked), with your best answer applied. `text` says what you decided and what a person should check; `where` is the clip name and timecode (from `context.json`), for example `S04_IMG_1234.MOV at 00:14.00`. Both must be non-empty. They become checklist issues when `apply` finishes.
- **Reclassify** only to correct the grouping: an item the script treated as on-camera that is really b-roll (`broll`), or the other way round (`on_camera`). The key is `V<track>@<start frame>` (for example `V1@1234`): the keys of `items` in `.edit/framing/context.json`, which also gives each item's clip name and timecode. A key that matches no item is ignored, so check it against `context.json`. A setup whose items are all reclassified to `broll` needs no label.
- **Push-in lines** come from the script and `transcript.md`: the thesis, a turn in the argument, a punchline, a call to action. Not filler, not setup sentences. Keep them 3 to 8 s long and at least 30 s apart, put them on plain setups where no b-roll covers the speaker, and write a reason for each. Fewer is better than weak ones. The solver drops any that break the rules and says which rule in the report (and shows the rejected push in orange on its flag preview). Push-ins are kept in timeline seconds, so `solve` checks each one's `text` against the transcript of the current timeline: when the line is no longer said in its range (the timeline changed since you picked it), the push-in is dropped with an issue to re-pick it. Copy `text` from `transcript.md` so the check can find it. Without a cached transcript for the range the push-in is kept and the report says it was not checked.

## The framing rules

These are what the scripts enforce. The reasons are here so you can judge shots the rules do not cover.

**Base frame, one per setup**
- Eyes on or just above the upper third line, a small and equal headroom for every clip. *Eyes near the upper third read as natural and engaged; too much wall above looks amateur. A frame that cuts the forehead or touches the hair looks like a mistake.* When hands low in the frame pull the frame down and push the eyes above the third, the frame tilts back to keep about 10% of the frame height above the head (estimated from the face size), at the same zoom; where gestures, clutter or a joint leave no room, the eye line alone decides.
- Centred on the face for direct address, shifting off centre by at most 5% of the width to keep clutter out. *Looking straight into the lens implies symmetry; off-centre faces read as a mistake unless the shot is clearly composed that way.*
- The bottom edge never sits on a joint (chin, neck, elbows, wrists, waist, knees). *A frame that ends at a joint looks amputated; between joints it looks intended.*
- No tangents. *An object whose edge runs along the frame edge pulls the eye. Include it clearly or crop it clearly: a laptop cut by the edge is fine, a sofa arm showing as a sliver is not.* Check this on the previews, the solver cannot see it.
- Never tighter than the face-size cap for tightness alone: the face is at most 28% of the frame height. *A face filling the frame reads as an extreme close-up.* This is why close setups barely tighten. Only clutter removal may go past it (keep-out comes before tightness).
- Contains every head and hand position of every clip of the setup, with a margin of 4% of the frame height. Hands resting at the source's bottom edge (on the knees or a table, already cut by the source) do not hold the bottom of the frame down; they still count for the left, right and top, and the bottom edge still avoids joints. A setup marked `"bottom_hands": "gesture"` counts them fully. *A hand that leaves the frame mid-gesture is the most noticeable framing error. Gestures always win over decluttering:* if clutter can only go by clipping a gesture, the gesture stays, the clutter goes as far as possible, and a flag is raised.
- One rectangle for the whole setup. *When the frame changes inside a setup it should only be the deliberate punch-in.* A single take whose gestures would loosen the whole setup by more than 10% zoom gets its own looser frame and a flag.
- Never outside the source (no black edges) and inside the zoom budget. *Zooming past the source resolution makes the picture soft.*

**Punch-ins on jump cuts**
- Only on visible jump cuts: same setup on both sides, nothing covering the cut on a higher track. *The punch hides the jump by making it look like a deliberate second camera.*
- Alternate base, punched, base, punched. Punch is 1.15x the base. *Smaller reads as a mistake (a wobble), bigger looks like a different shot.* Down to 1.10x when a gesture is tight, otherwise skipped.
- The incoming eyes land within about 3% of the frame width of the outgoing eyes. *The viewer's gaze does not jump across the cut.*
- A piece with a push-in is not also punched.

**Push-ins on key lines**
- Plain setups only, on lines that carry the video, slow and eased from 1.00 to about 1.06 over 3 to 8 s (enforced: shorter or longer lines are dropped), anchored on the eyes. *A push-in is emphasis: it only works if it is rare, slow and on something that matters.*
- At most one per 30 s, never across a cut, never two in a row on one setup, never during fast movement, never while b-roll covers the speaker.

**Zoom budget**
- Budget = source height / delivery height (2.0 for 4K into 1080p). Base x punch x push never exceeds it. If it would, the punch shrinks first; the declutter crop is never reduced. Sources with no headroom (1080p into 1080p) get declutter crops up to 1.15x and a warning, never punch-ins or push-ins.

**Honest limits.** Gestures always win, so setups where the speaker's hands fill the frame get few or no punch-ins and push-ins. Heavy decluttering can use up the zoom room on that setup, and the report says "no room for punch-ins". Both are correct results, not bugs.

## Options

| | Default | Option |
|---|---|---|
| Punch-in factor | 1.15x (minimum 1.10x when a gesture is tight) | `--punch` |
| Push-in factor | 1.06x, at most one per 30 s | `--push` |
| No punch-ins | off | `--no-punch` |
| No push-ins | off | `--no-push` |
| Push-ins as Inspector keyframes or Fusion comps (`apply` only) | keyframes | `--pushes keyframes\|fusion` |
| Gesture margin | 4% of frame height | fixed |
| Eye-trace tolerance | 3% of frame width | fixed |
| Max off-centre shift | 5% of frame width | fixed |
| Zoom budget | source height / delivery height | fixed |

`--punch`, `--push`, `--no-punch` and `--no-push` go on `solve` and `apply`.

## What it does to the timeline

- **Mode:** a fork. Framing only changes properties of existing items (the jump cuts already split them), so grades, titles, effects and hand edits survive. It never moves a cut.
- **On-camera items** are linked audio+video items and video-only items whose shot matches a dialogue item (voiceover splits). Everything else is b-roll and keeps its framing. A file whose name contains "broll", "b-roll", "b_roll" or "b roll", and a video-only item on a track named that way, is b-roll unless you reclassify it `on_camera`.
- **Covered by a higher track:** an on-camera item that video on a higher track covers for its whole length is never seen, so it is left as it is: not framed, not flagged, listed in the report under "Covered by a higher track (left as is)". It still counts for grouping setups. An item covered only in part is framed as usual.
- **Hand framing is protected.** On the first run an on-camera item with non-default Zoom, Pan, Tilt, crop or a Fusion comp is left exactly as it is and listed in the report. Later runs re-solve only items that still match `.edit/framing/applied.json`; zoom never stacks. An item with **Inspector keyframes** is always left alone, push-ins framing keyframed included: setting a property by script would delete them. To re-frame such a clip, delete its keyframes first. `apply` also stops if a clip got keyframes after `solve`.
- **Scaling:** the Zoom, Pan, Tilt maths is calibrated for "Scale full frame with crop" (`scaleToCrop`). "Scale entire image to fit" (`scaleToFit`) is accepted too when every framed source has the timeline's aspect (within 0.5%): the two scale the same then. Otherwise `apply` stops and names the clips with another aspect; any other mode stops it as well. `prepare` warns about this up front when Resolve is open.
- **Push-ins** are Zoom and Position keyframes in the Inspector, eased (step 7). With `--pushes fusion` they are Fusion Transform comps instead (Resolve's Dynamic Zoom cannot be set by script). resolve-bridge sees both kinds and refuses a later build that would drop them. Details and limits are in `references/resolve-notes.md`.

## Not handled yet

- Sources whose aspect differs from the timeline (4:3, vertical, square): the maths for them is derived, not measured. Check the previews and treat as unsupported.
- Variable-frame-rate sources: a Fusion push-in (`--pushes fusion`) can end a frame or two off. `apply` checks the comp length and raises an issue when it differs from the item's length by more than one timeline frame (a source frame rate above the timeline's makes a source frame or two of difference normal rounding). Phone footage is often VFR, so check these by eye.
- Retimed or speed-changed items: push-ins there are unverified.
- Framing b-roll, stabilisation, vertical reframing (`video-shorts`) and colour or vignettes (`video-color`).
