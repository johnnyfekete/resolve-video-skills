# Resolve notes for video-framing

Measured on **DaVinci Resolve Studio 21.2.0.6** (`resolve.GetVersionString()`), macOS, 2026-11-11,
with `scripts/spike_resolve.py` in the scratch project `rvs_scratch_framing`. Re-run the spike after
a Resolve update:

    python3 scripts/spike_resolve.py --workdir DIR enter
    python3 scripts/spike_resolve.py --workdir DIR calibrate --source CLIP_4K.MOV
    python3 scripts/spike_resolve.py --workdir DIR dynzoom
    python3 scripts/spike_resolve.py --workdir DIR fusion
    python3 scripts/spike_resolve.py --workdir DIR leave

Test setup: a 3840x2160 60 fps chart (white dots centred exactly on `(i/10, j/10)`) and a real
3840x2160 60 fps HLG clip, on a 1920x1080 24 fps timeline (and a 1280x720 one). The spike sets
the scaling mode both as project setting and as timeline setting:
`timelineInputResMismatchBehavior = "scaleToCrop"` ("Scale full frame with crop"), the timeline one
with `useCustomSettings = "1"`. Valid values: `centerCrop`, `scaleToFit`, `scaleToCrop`, `stretch`.
Item property `Scaling` was `0` (use the timeline setting). Stills come from
`Project.ExportCurrentFrameAsStill(path)` after `Timeline.SetCurrentTimecode(tc)`, from the Edit
page; they are written at timeline resolution.

## Calibration

Inputs: crop rect in normalised **source** coordinates, origin top-left, centre `cx, cy`, width `w`
(fraction of source width; at matching aspect the rect has the source aspect, so its height is `w`
of source height).
`SW, SH` = source size in pixels, `TW, TH` = timeline size in pixels.

**Measured and validated only for a source with the timeline's aspect** (16:9 source on a 16:9
timeline). For that case:

    s    = max(TW / SW, TH / SH)        # "scale full frame with crop" fit factor (0.5 for 4K -> 1080p)
    Zoom = 1 / w                        # set ZoomX = ZoomY = Zoom
    Pan  = (0.5 - cx) * SW * s * Zoom   # timeline pixels
    Tilt = (cy - 0.5) * SH * s * Zoom   # timeline pixels

Equivalently, source point `(u, v)` lands at timeline pixel

    x = TW/2 + (u - 0.5) * SW * s * ZoomX + Pan
    y = TH/2 + (v - 0.5) * SH * s * ZoomY - Tilt

- **Signs:** `+Pan` moves the image right. `+Tilt` moves the image **up** (screen y runs down, so `-Tilt`).
- **Units:** Pan and Tilt are timeline pixels, added after the zoom. They do not scale with Zoom.
  They do depend on timeline resolution: the same Pan is 2/3 as far on a 720p timeline as on 1080p,
  so the formula takes `TW, TH`.
- Zoom scales about the frame centre (AnchorPointX/Y = 0). With matching aspect, Zoom 1 shows the
  whole source.
- `ZoomGang` defaults to True; with it on, setting only `ZoomX` also sets `ZoomY`. Set both anyway.
- For matching aspect, `scaleToFit` gives the same `s`; the two differ only when aspects differ
  (`scaleToFit` uses `min`).
- Valid rects stay inside the source: `w <= 1`, `cx - w/2 >= 0`, `cx + w/2 <= 1`, same for `cy`.
- `spike_resolve.py`'s `rect_to_props(cx, cy, w, tl_w, tl_h)` is the same maths as the later
  `rect_to_props(rect, tl_w, tl_h)`; only the argument shape differs.

**Non-matching aspect (4:3, vertical, square sources): derived, NOT measured.** Under scale full
frame with crop such a source is already cropped at Zoom 1, so `Zoom = 1/w` and "Zoom 1 shows the
whole source" do not hold. The general form follows from the point mapping above, which was only
measured at matching aspect:

    s    = max(TW / SW, TH / SH)
    Zoom = TW / (w * SW * s)            # rect width w (of source width) fills the timeline width
    Pan  = (0.5 - cx) * SW * s * Zoom
    Tilt = (cy - 0.5) * SH * s * Zoom
    # visible source at Zoom 1: TW/s by TH/s source pixels; the rect has the TIMELINE aspect,
    # height = w * SW * TH / TW source pixels (= w of source height only when aspects match)

At matching aspect `TW / (SW * s) = 1`, so this reduces to `Zoom = 1/w`. Callers should treat sources
whose aspect differs from the timeline as unsupported, or use this derived form and say so in the
report, until it is measured.

Evidence (`calibration.json`):
- Six fit cases (Zoom 1, 1.5, 2, 1.3x1.1 non-ganged; Pan/Tilt from -150 to 100): every chart dot within
  0.002 px of the formula, 0.16 px for non-integer zooms (resampling).
- Three rects not used in the fit, through `rect_to_props`: (0.40, 0.45, 0.60), (0.62, 0.38, 0.75),
  (0.30, 0.65, 0.50). The rect centre lands at (960.0, 540.0), (960.12, 540.11), (960.0, 540.0); the rect
  width measures 1920.0 px in all three. Max dot error 0.16 px.
- 1280x720 timeline: two cases within 0.34 px.
- Real HLG footage, source frame 0, compared by edge correlation with an ffmpeg crop+scale of the same
  rect: residual shift (-0.01, 0.01), (0.05, 0.10) and (-0.03, -0.03) px at 1080p.

## Dynamic Zoom

**Start and end rectangles cannot be set by script.** `GetProperty()` lists only
`DynamicZoomEnabled` and `DynamicZoomEase`, and only those two are settable
(`SetProperty("DynamicZoomEnabled", True)`, `SetProperty("DynamicZoomEase", 3)` with the
`resolve.DYNAMIC_ZOOM_EASE_*` constants). Every other key tried
(`DynamicZoomStart`, `...End`, `...StartRect`, `...EndRect`, `...StartZoom`, `...EndZoom`,
`...StartPan`, `...EndPan`, `...StartPosition`, `...EndPosition`, `...StartX/Y`, `...StartScale`,
`...EndScale`, `...Swap`, `...Mode`) with float, list and dict values returns False through both
`SetProperty` and `SetProperties`. Enabling it uses Resolve's default rects, a zoom **out** from
about 1.24x to 1.0x over the item, which is not what push-ins need. Not usable.

## Fusion push-in

Works. A Fusion Transform on the item, keyframed and eased, renders on the Edit page and in stills.

```python
comp = item.AddFusionComp()                       # item.GetFusionCompCount() goes 0 -> 1
mi, mo = comp.FindTool("MediaIn1"), comp.FindTool("MediaOut1")
tr = comp.AddTool("Transform", -32768, -32768)
tr.SetAttrs({"TOOLS_Name": "rvs_pushin"})         # findable later with comp.FindTool("rvs_pushin")
tr.ConnectInput("Input", mi)
mo.ConnectInput("Input", tr)
tr.SetInput("Pivot", {1: eye_u, 2: 1.0 - eye_v})  # anchor; Center stays at its default 0.5, 0.5
tr.AddModifier("Size", "BezierSpline")
tr.SetInput("Size", 1.00, t0)
tr.SetInput("Size", 1.06, t1)
spline = tr.Size.GetConnectedOutput().GetTool()
dt = (t1 - t0) / 3
spline.SetKeyFrames({t0: {1: 1.00, "RH": {1: dt, 2: 0.0}},
                     t1: {1: 1.06, "LH": {1: -dt, 2: 0.0}}}, True)   # ease in and out
```

- **Use exactly these call forms.** A first attempt with attribute assignment
  (`tr.Input = mi.Output`, `mo.Input = tr.Output`, `tr.Size = comp.BezierSpline()`, `tr.Size[t] = v`,
  inside `comp.Lock()/Unlock()`) built a correct-looking graph that the Fusion page showed, but the
  Edit page and the stills never rendered it, even after visiting the Fusion page. The
  `ConnectInput/SetInput/AddModifier` forms above rendered immediately with or without Lock/Unlock.
- **Easing:** `SetInput` on a BezierSpline makes linear keys (handles at 1/3 along the line).
  Handles in `GetKeyFrames/SetKeyFrames` are **relative** `{1: dtime, 2: dvalue}`. Flat handles at a
  third of the span give ease in/out. `SetKeyFrames` returns None but applies. Measured zoom at
  comp times 15/30/60/90 of a 0..120 ramp 1->2: 1.044, 1.156, 1.500, 1.844; the Bezier gives 1.043, 1.156, 1.500, 1.844.
- **Comp time:** 0 is the item's first frame (also for an item trimmed to start at source frame 60),
  and comp time counts **source frames**, not timeline frames: `t = item_frame * source_fps / timeline_fps`
  (a 60 fps clip on a 24 fps timeline: timeline frame 24 of the item = comp time 60).
  Measured only for a constant 60 fps clip on a 24 fps timeline at 100% speed. Not verified for
  variable-frame-rate sources (phone footage often is) or retimed/speed-changed items; check comp
  times there (e.g. against `COMPN_RenderEnd` vs. item duration) or skip push-ins on such items.
- **Coordinates:** Fusion `Pivot`/`Center` are normalised to the source image, origin **bottom-left**
  (y up), so a source point `(u, v)` with top-left origin is `{1: u, 2: 1 - v}`. The comp works at
  source resolution (3840x2160 here, `MediaIn` `TOOLI_Clip_Width/Height`).
- **Anchor:** Size scales about `Pivot`; `Center` is a translation (0.5, 0.5 = none). With Pivot on
  the eyes and Center left at 0.5, 0.5 the eye dot stayed at (1152.0, 324.0) at every frame from 1x to 2x.
- **Order:** the Fusion comp applies **before** the Inspector, in source space. Fusion Size 2 about
  source (0.6, 0.3) plus Inspector Zoom 1.5, Pan 120, Tilt -60 put source points exactly where
  "Fusion first, then the calibration formula" predicts ((1368, 276) and (792, 600), within 0.002 px).
  So the eye anchor is given in source coordinates and does not change with the item's base framing,
  and the total zoom is `Fusion Size * Inspector Zoom`.
- **Moving the anchor (optional):** `tr.AddModifier("Center", "XYPath")`, then
  `tr.SetInput("Center", {1: x, 2: y}, t)` per key works (value at comp time 30 of a 0.5 -> 0.6 ramp
  was 0.525 and the image moved +48 px at 1080p as expected). Not needed for a pivot-anchored push-in.
- **Detecting:** `item.GetFusionCompCount()` (0 for an item without comps),
  `item.GetFusionCompNameList()` (`["Composition 1"]`), then
  `item.GetFusionCompByIndex(i).FindTool("rvs_pushin")`. Push-ins do not show in `GetProperty()`,
  and `item.GetEffects("fusion")` stays empty for a comp added this way.
- **Removing / replacing (re-runs):** `TimelineItem.DeleteFusionCompByName(name) -> bool` (in the
  shipped `DaVinciResolveScript.pyi`; the scripting folder has README.md, not README.txt).
  **Resolve refuses to delete an item's only comp** (returns False, measured 2026-11-11 in the scratch
  project); with a second comp present the same call works. `RenameFusionCompByName(old, new)` works.
  So `apply_resolve.py` replaces a push-in in this order:
  1. rename framing's old comp (`rvs_pushin` -> `rvs_pushin_old<n>`) to free the name;
  2. add the new comp and build the push-in in it;
  3. rename the new comp to `rvs_pushin`;
  4. delete the old comp (it is no longer the only one);
  5. check that exactly one comp holds the `rvs_pushin` tool.

  Removing a push-in when framing's comp is the item's only comp: neutralise it in place (delete the
  `rvs_pushin` Transform, `MediaOut1.ConnectInput("Input", MediaIn1)`) and rename it `rvs_neutral`.
  A neutral comp is a pass-through; `rvs.export_plan` does not count comps whose names start with
  `rvs_neutral` (`rvs.fusion_comp_count`), so it never blocks a build and is not a hand edit. A later
  push-in deletes it as in step 4. Framing's own comps are those named `rvs_pushin*` or `rvs_neutral*`
  or holding the `rvs_pushin` tool; every other comp is the user's and stays. Every rename result is
  checked. Replace and neutralise were run live (60 fps HLG clip, 24 fps timeline) and render as
  expected; the neutralised comp was then still named `rvs_pushin`, the `rvs_neutral` name came later and
  is only covered by unit tests. `GetFusionCompNameList()` returns an empty dict, not a list, for an
  item without comps.
- **Comp length (VFR):** a 132-frame item of a 60 fps phone clip (330 source frames expected) gave a comp
  with `COMPN_RenderStart..COMPN_RenderEnd` = 0..327 (328 frames). `apply_resolve.py` compares that
  length to the item length in source frames after adding a push-in and raises an issue when they
  differ by more than one timeline frame (the source-frame difference times timeline fps / source
  fps; 2 source frames of a 60 fps clip on a 24 fps timeline is 0.8, not flagged); the push-in is kept.

## Inspector keyframes (Resolve 21)

- **The Scripting API has no keyframe calls for timeline items.** This was checked against the v20 (May 2025) and v21 (July 2026) README. `SetProperty("ZoomX", v)` sets one value. On an item that has keyframes, it **deletes all of them** and leaves that value. `GetProperty` then returns one value (the first keyframe's) whatever the playhead.
- **Reading:** `Timeline.Export(path, resolve.EXPORT_FCPXML_1_10, resolve.EXPORT_NONE)` writes a bundle `path/Info.fcpxml`. A keyframed clip has `<adjust-transform><param name="scale|position"><keyframeAnimation><keyframe value time interp|curve>`.
  - Position is in percent of the timeline height: Pan -40 px at 1080p is -3.7037.
  - Keyframe `time` is source time, on the source's frame grid, so it can sit up to one timeline frame early: a keyframe set on frame 0 reads -0.6. Round up.
  - Scale carries the easing (`easeOut`, `easeIn`). Position shows `curve="linear"` (the spatial path), even when its timing is eased.
  - OTIO and DRT exports were tried. OTIO has no keyframes; DRT is a zip and was not explored.
- **Writing by hand (computer use):**
  - `Timeline.SetCurrentTimecode` moves the playhead, and the Inspector follows it, showing the clip under the playhead.
  - Background app control can click but cannot type into Resolve or drive the timeline keys, so full-screen control is needed.
  - Inspector diamonds: a click adds a keyframe at the playhead. Typing a value on another frame of a keyframed parameter adds one there.
  - Right-clicking a red diamond offers Linear / Ease In / Ease Out / Ease In and Out, but only those its neighbours allow: the last keyframe gets Linear and Ease In. The tick can be stale, so check the numbers instead.
  - The timeline's own keyframe lane needs a tall track and a zoomed timeline; the Inspector menu is easier.
