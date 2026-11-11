---
name: video-framing
description: Frames a talking-head cut in DaVinci Resolve Studio like a professional editor. Crops each camera setup to a clean, centred frame that removes unchosen clutter at the edges (appliances, magnets, spare chairs, lamps) and keeps chosen set dressing (plants, flowers), never cuts off the head or a gesture, adds punch-ins on visible jump cuts with the eyes kept in place, adds slow push-ins on key lines of plain setups, and stays inside the zoom budget of the source. Produces a new timeline version through resolve-bridge. Runs on its own and leaves a report to review afterwards. Use when the user says the frame looks cluttered, loose or amateur, jump cuts are too visible, the speaker is off centre, or asks for punch-ins, push-ins or a declutter crop.
---

# video-framing

Run this after `video-tighten` (it hides the jump cuts tighten creates) and before `video-color`. Requires the **resolve-bridge** skill installed next to this one (it finds it at `../resolve-bridge`, or set `RVS_BRIDGE`), plus `ffmpeg` and `swift` (macOS ships it; Apple Vision measures faces and hands).

Run scripts as `python3 <this skill's folder>/scripts/framing.py <command> --workdir <folder>`. Commands: `prepare`, `solve`, `apply`.

The scripts measure and do the maths. **You make the judgement calls** (what is clutter, which setups are plain, which lines get a push-in). **Never stop to ask for approval.** The user reviews afterwards. A call you are unsure about still gets your best answer and the build continues; mention it in the decision's `reason`, and it becomes an issue.

## Workflow

1. **Where are we.** `python3 <resolve-bridge>/scripts/rvs.py --workdir <folder> status`. Adopt the timeline first if nothing is tracked yet (see resolve-bridge).
2. **Prepare.** Changes nothing in Resolve:
   ```bash
   python3 scripts/framing.py prepare --workdir <folder>
   ```
   Finds the on-camera items, groups them into camera setups, measures face, eyes, body and hands (about 4 samples per second, cached after the first run), and writes:
   - `generated/framing/setups/<id>.jpg`: one still per setup with a labelled 10% grid (0.0 to 1.0 on both axes);
   - `.edit/framing/transcript.md`: `MM:SS.ss-MM:SS.ss <setup> <text>` per spoken segment, in timeline time;
   - `.edit/framing/decisions.json`: a template with every setup empty (a re-run keeps decisions for setups whose items did not change), `context.json` with budgets and stills.

   It prints one line per setup (items, zoom budget, still) and lists items that already carry hand framing: those are left exactly as they are.
3. **Decide.** Look at every setup still and fill in `decisions.json` (see *How to decide*). Then pick the push-in lines.
4. **Solve.** A dry run that changes nothing in Resolve:
   ```bash
   python3 scripts/framing.py solve --workdir <folder>
   ```
   Writes `generated/framing/REPORT.md`, `setups/<id>_before_after.jpg` per setup and `flags/<n>.jpg` per flagged item, and `.edit/framing/solution.json`. An invalid `decisions.json` (missing label, box outside 0..1) stops with the reason and exit code 2.
5. **Check the previews yourself.** Open every before/after image and the flag images. Look for the things the rules forbid: a tangent along the frame edge, a clipped hand, a head too close to the top, clutter still at an edge, a chosen plant cut off. If a frame looks wrong, fix **your own decisions** (move a box, change a label, drop a push-in) and solve again. Repeat until the previews are right or the remaining problems are ones only a person can judge. Read the report's flags and dropped push-ins too.
6. **Apply.**
   ```bash
   python3 scripts/framing.py apply --workdir <folder>
   ```
   Solves again, then forks `<base>_vNN_framing` through resolve-bridge (grades, effects and hand edits stay), sets Zoom, Pan and Tilt, adds the push-ins, archives the previous version and saves the project. Every flag becomes an issue in `EDIT_CHECKLIST.md`, located by clip name and timecode. Tell the user the new timeline name, the issue ids and where the report is. Exit code 2 means an expected failure with a message (no timeline tracked, timeline not set to scale full frame with crop, and so on).

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
  "reclassify": {"<item key>": "broll"},
  "push_ins": [
    {"start_s": 41.2, "end_s": 47.0, "text": "Writing is thinking.",
     "reason": "the thesis, first time it is said plainly"}
  ]
}
```

Boxes are `[left, top, right, bottom]`, normalised 0..1 on the setup still (read them off its grid). `start_s` and `end_s` are timeline seconds from `transcript.md`. `items` is filled by `prepare`; leave it.

- **Keep-out** boxes: everything nobody chose to show, near the edges. Appliances, magnets, spare chairs, lamps, cables, thermostats, door frames, a sofa sliver. The solver crops them out where it can without clipping a gesture.
- **Keep-in** boxes: set dressing the speaker chose. Plants, flowers, a vase. The solver keeps them whole.
- **Label** each setup `plain` or `dynamic`. `plain` is front-on, eye level and static (seated at a table). `dynamic` is a high or low angle, an unusual position (sitting on a counter) or a strong diagonal. Dynamic setups get no push-ins.
- **Reason**: one line per setup, always. If you are unsure whether something is clutter or chosen, give your best answer and say so there.
- **Reclassify** only to correct the grouping: an item the script treated as on-camera that is really b-roll (`broll`), or the other way round (`on_camera`). The key is the item key printed in the report or `context.json`.
- **Push-in lines** come from the script and `transcript.md`: the thesis, a turn in the argument, a punchline, a call to action. Not filler, not setup sentences. Keep them at least 30 s apart, put them on plain setups, and write a reason for each. Fewer is better than weak ones. The solver drops any that break the rules and says why in the report.

## The framing rules

These are what the scripts enforce. The reasons are here so you can judge shots the rules do not cover.

**Base frame, one per setup**
- Eyes on or just above the upper third line, a small and equal headroom for every clip. *Eyes near the upper third read as natural and engaged; too much wall above looks amateur. A frame that cuts the forehead or touches the hair looks like a mistake.*
- Centred on the face for direct address, shifting off centre by at most 5% of the width to keep clutter out. *Looking straight into the lens implies symmetry; off-centre faces read as a mistake unless the shot is clearly composed that way.*
- The bottom edge never sits on a joint (chin, neck, elbows, wrists, waist, knees). *A frame that ends at a joint looks amputated; between joints it looks intended.*
- No tangents. *An object whose edge runs along the frame edge pulls the eye. Include it clearly or crop it clearly: a laptop cut by the edge is fine, a sofa arm showing as a sliver is not.* Check this on the previews, the solver cannot see it.
- Contains every head and hand position of every clip of the setup, with a margin of 4% of the frame height. *A hand that leaves the frame mid-gesture is the most noticeable framing error. Gestures always win over decluttering:* if clutter can only go by clipping a gesture, the gesture stays, the clutter goes as far as possible, and a flag is raised.
- One rectangle for the whole setup. *When the frame changes inside a setup it should only be the deliberate punch-in.* A single take whose gestures would loosen the whole setup by more than 10% zoom gets its own looser frame and a flag.
- Never outside the source (no black edges) and inside the zoom budget. *Zooming past the source resolution makes the picture soft.*

**Punch-ins on jump cuts**
- Only on visible jump cuts: same setup on both sides, nothing covering the cut on a higher track. *The punch hides the jump by making it look like a deliberate second camera.*
- Alternate base, punched, base, punched. Punch is 1.15x the base. *Smaller reads as a mistake (a wobble), bigger looks like a different shot.* Down to 1.10x when a gesture is tight, otherwise skipped.
- The incoming eyes land within about 3% of the frame width of the outgoing eyes. *The viewer's gaze does not jump across the cut.*
- A piece with a push-in is not also punched.

**Push-ins on key lines**
- Plain setups only, on lines that carry the video, slow and eased from 1.00 to about 1.06 over 3 to 8 s, anchored on the eyes. *A push-in is emphasis: it only works if it is rare, slow and on something that matters.*
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
| Gesture margin | 4% of frame height | fixed |
| Eye-trace tolerance | 3% of frame width | fixed |
| Max off-centre shift | 5% of frame width | fixed |
| Zoom budget | source height / delivery height | fixed |

`--punch`, `--push`, `--no-punch` and `--no-push` go on `solve` and `apply`.

## What it does to the timeline

- **Mode:** a fork. Framing only changes properties of existing items (the jump cuts already split them), so grades, titles, effects and hand edits survive. It never moves a cut.
- **On-camera items** are linked audio+video items and video-only items whose shot matches a dialogue item (voiceover splits). Everything else is b-roll and keeps its framing.
- **Hand framing is protected.** On the first run an on-camera item with non-default Zoom, Pan, Tilt, crop or a Fusion comp is left exactly as it is and listed in the report. Later runs re-solve only items that still match `.edit/framing/applied.json`; zoom never stacks.
- **Scaling:** the timeline must scale sources with "scale full frame with crop", otherwise `apply` stops. The Zoom, Pan, Tilt maths is calibrated for that mode.
- **Push-ins** are Fusion Transform comps on the item (Resolve's Dynamic Zoom cannot be set by script). They are invisible to plain plans, so resolve-bridge detects them and warns before a later build would drop them. Details and limits in `references/resolve-notes.md`.

## Not handled yet

- Sources whose aspect differs from the timeline (4:3, vertical, square): the maths for them is derived, not measured. Check the previews and treat as unsupported.
- Variable-frame-rate sources: a Fusion push-in can end a frame or two off. `apply` checks the comp length and raises an issue when it differs by more than one frame. Phone footage is often VFR, so check these by eye.
- Retimed or speed-changed items: push-ins there are unverified.
- Framing b-roll, stabilisation, vertical reframing (`video-shorts`) and colour or vignettes (`video-color`).
