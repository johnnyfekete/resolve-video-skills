"""Framing solver for video-framing: base frames per camera setup.

Pure: no Resolve, no I/O. Inputs are `measure.summarize` results per item key and the agent's
per-setup decision; output is one crop Rect per setup plus per-item Rects and flags.

Priorities when rules conflict: gesture safety and no black edges (hard), then keep-out clutter,
then tightness and composition (eyes on the upper third, centred on the face).
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

from geometry import EPS, OUT_ASPECT, Box, Rect, zoom_of
from timeline import item_key, runs as _runs

WEAK_DETECTION = 0.5   # items whose face is found in fewer frames than this get the setup frame
STEP = 0.001           # width search step (fraction of source width)
EYE_HIGH = 0.25        # the eye line may sit between this and 1/3 of the frame height from the top
N_SAMPLES = 9          # positions tried per axis when a frame must move (keep-out, keep-in, joints)
EYE_BAND = (0.25, 0.36)  # an eye line outside this band (fraction of frame height) is flagged
SLIVER = 0.03          # keep-out visible as a strip thinner than this (of the frame) is a tangent


@dataclass(frozen=True)
class Opts:
    punch: float = 1.15
    punch_min: float = 1.10
    push: float = 1.06
    push_spacing_s: float = 30.0
    gesture_margin: float = 0.04   # of frame height
    eye_tol: float = 0.03          # of frame width
    max_shift: float = 0.05        # of frame width
    outlier: float = 1.10
    declutter_cap: float = 1.15
    face_h_max: float = 0.28       # of frame height
    joint_clear: float = 0.03      # of frame height
    fast_move: float = 0.60        # gesture extent wider than this of the frame: no push-in
    push_min_s: float = 3.0        # push-in line length range, seconds
    push_max_s: float = 8.0
    aspect: float = 16 / 9         # source aspect (width / height)


# ---------------------------------------------------------------- helpers

def _h(w: float, opts: Opts) -> float:
    return w * opts.aspect / OUT_ASPECT


def _w_max(opts: Opts) -> float:
    return min(1.0, OUT_ASPECT / opts.aspect)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _strong(m: dict | None) -> bool:
    return bool(m) and m.get("detected", 0.0) >= WEAK_DETECTION and m.get("eyes") is not None


def _extent(m: dict) -> Box:
    if m.get("extent"):
        return tuple(m["extent"])
    ex, ey = m["eyes"]
    fh = m.get("face_h") or 0.12
    return (ex - 0.6 * fh, ey - 0.9 * fh, ex + 0.6 * fh, ey + 0.8 * fh)


def _group(ms: list[dict]) -> dict:
    """Combined speaker data for a set of items: median eyes and face, union of extents, all joints."""
    exts = [_extent(m) for m in ms]
    faces = [m["face_h"] for m in ms if m.get("face_h")]
    return {
        "eyes": (statistics.median(m["eyes"][0] for m in ms),
                 statistics.median(m["eyes"][1] for m in ms)),
        "extent": (min(e[0] for e in exts), min(e[1] for e in exts),
                   max(e[2] for e in exts), max(e[3] for e in exts)),
        "face_h": statistics.median(faces) if faces else None,
        "joints": sorted({y for m in ms for y in (m.get("joints_y") or {}).values()}),
    }


def _ranges(g: dict, w: float, opts: Opts):
    """(hard_x, hard_y, soft_x, soft_y) centre ranges at width w; a range is (lo, hi) or None.

    Hard: the gesture extent grown by the margin stays inside, and the rect stays inside the source.
    Soft: the centre is within the off-centre allowance of the eyes, and the eye line sits between
    EYE_HIGH and 1/3 of the frame height from the top.
    """
    h = _h(w, opts)
    my = opts.gesture_margin * h
    mx = my / opts.aspect  # same pixel distance horizontally
    l, t, r, b = g["extent"]
    l, t, r, b = max(0.0, l - mx), max(0.0, t - my), min(1.0, r + mx), min(1.0, b + my)

    def rng(lo, hi):
        return (lo, hi) if lo <= hi + EPS else None

    hx = rng(max(r - w / 2, w / 2), min(l + w / 2, 1 - w / 2))
    hy = rng(max(b - h / 2, h / 2), min(t + h / 2, 1 - h / 2))
    ex, ey = g["eyes"]
    s = opts.max_shift * w
    sx = rng(max(hx[0], ex - s), min(hx[1], ex + s)) if hx else None
    sy = rng(max(hy[0], ey + h / 6), min(hy[1], ey + h * (0.5 - EYE_HIGH))) if hy else None
    return hx, hy, sx, sy


def _preferred(g: dict, w: float, opts: Opts, soft: bool = True) -> Rect | None:
    """Centred on the eyes, eye line on the upper third, moved only as far as the ranges demand."""
    hx, hy, sx, sy = _ranges(g, w, opts)
    xr, yr = (sx, sy) if soft else (hx, hy)
    if xr is None or yr is None:
        return None
    ex, ey = g["eyes"]
    h = _h(w, opts)
    return Rect(_clamp(ex, *xr), _clamp(ey + h / 6, *yr), w, opts.aspect)


def _placements(g: dict, w: float, opts: Opts, soft: bool = True) -> list[Rect]:
    """Gesture-safe rects at width w, nearest to the preferred placement first.

    With `soft`, only rects that also keep the composition (off-centre allowance, eye line).
    """
    hx, hy, sx, sy = _ranges(g, w, opts)
    xr, yr = (sx, sy) if soft else (hx, hy)
    if xr is None or yr is None:
        return []
    ex, ey = g["eyes"]
    h = _h(w, opts)
    px, py = _clamp(ex, *xr), _clamp(ey + h / 6, *yr)

    def samples(lo, hi, p):
        vals = {p, lo, hi}
        vals.update(lo + (hi - lo) * i / (N_SAMPLES - 1) for i in range(N_SAMPLES))
        return vals

    pts = [(x, y) for x in samples(*xr, px) for y in samples(*yr, py)]
    pts.sort(key=lambda p: abs(p[0] - px) / w + abs(p[1] - py) / h)
    return [Rect(x, y, w, opts.aspect) for x, y in pts]


def _widths(start: float, stop: float) -> list[float]:
    """Widths from start to stop inclusive, STEP apart, in that direction."""
    n = int(abs(stop - start) / STEP + EPS)
    sign = 1 if stop >= start else -1
    out = [start + sign * STEP * i for i in range(n + 1)]
    if abs(out[-1] - stop) > EPS:
        out.append(stop)
    return out


def _clutter(r: Rect, boxes) -> float:
    """Absolute area of keep-out boxes visible in the rect."""
    total = 0.0
    for l, t, rt, b in boxes:
        iw = min(rt, r.right) - max(l, r.left)
        ih = min(b, r.bottom) - max(t, r.top)
        if iw > 0 and ih > 0:
            total += iw * ih
    return total


def _cut(r: Rect, box) -> bool:
    """A keep-in box that is neither clearly in nor clearly out: a sliver or tangent on the edge."""
    return r.overlaps(tuple(box)) > 1e-6 and not r.contains(tuple(box))


def _slivers(r: Rect, boxes) -> list[tuple]:
    """Keep-out boxes that show only as a thin strip along a frame edge (a tangent).

    A small box wholly inside the frame is not a sliver; it is "clutter left in frame".
    """
    out = []
    for box in boxes:
        l, t, rt, b = box
        iw = min(rt, r.right) - max(l, r.left)
        ih = min(b, r.bottom) - max(t, r.top)
        if iw <= EPS or ih <= EPS:
            continue
        thin_x = iw < SLIVER * r.w and (l <= r.left + EPS or rt >= r.right - EPS)
        thin_y = ih < SLIVER * r.h and (t <= r.top + EPS or b >= r.bottom - EPS)
        if thin_x or thin_y:
            out.append(box)
    return out


def _cuts(r: Rect, boxes) -> int:
    return sum(_cut(r, b) for b in boxes)


def _on_joint(r: Rect, joints, opts: Opts) -> bool:
    if r.bottom >= 1 - EPS:  # the source's own bottom edge: no framing choice to make there
        return False
    return any(abs(r.bottom - y) < opts.joint_clear * r.h for y in joints)


def _floors(budget: float, opts: Opts) -> tuple[float, float, bool]:
    """(size floor, keep-out floor, punches allowed by budget).

    Size floor: with room for punch x push the base frame keeps that room; otherwise it never goes
    past the budget (no softening for composition or tightness). Keep-out floor: clutter removal may
    eat into punch room (never reduce the declutter crop; the punch is reduced first), and only
    clutter removal may soften the image, up to declutter_cap total zoom.
    """
    wmax = _w_max(opts)
    ko = min(wmax, 1 / max(budget, opts.declutter_cap))
    if budget >= opts.punch * opts.push - EPS:
        return min(wmax, opts.punch * opts.push / budget), ko, True
    return min(wmax, 1 / budget), ko, False


def _face_w(g: dict, opts: Opts) -> float:
    """Narrowest width that keeps the face at most face_h_max of the frame height."""
    return g["face_h"] / opts.face_h_max * OUT_ASPECT / opts.aspect if g["face_h"] else 0.0


def _in_band(frac: float) -> bool:
    return EYE_BAND[0] - EPS <= frac <= EYE_BAND[1] + EPS


def _gesture_cut(r: Rect, ext: Box) -> float:
    """Area of the gesture extent that falls outside the rect."""
    l, t, rt, b = ext
    area = max(0.0, rt - l) * max(0.0, b - t)
    iw = max(0.0, min(rt, r.right) - max(l, r.left))
    ih = max(0.0, min(b, r.bottom) - max(t, r.top))
    return area - iw * ih


def _eye_frac(g: dict, r: Rect) -> float:
    """Eye line position as a fraction of the frame height from the top."""
    return (g["eyes"][1] - r.top) / r.h


def _base_w(g: dict, floor: float, opts: Opts) -> float:
    """Tightest width at or above the floor and the face cap that keeps gestures inside.

    Composition (centring, eye line) is kept when some width allows it. Otherwise only the hard rules
    hold, and the width is the one whose eye line comes closest to the upper third (the tightest
    among near-equals).
    """
    wmax = _w_max(opts)
    ws = _widths(min(max(floor, _face_w(g, opts)), wmax), wmax)
    for w in ws:
        if _preferred(g, w, opts) is not None:
            return w
    dist = {}
    for w in ws:
        p = _preferred(g, w, opts, soft=False)
        if p is not None:
            dist[w] = abs(_eye_frac(g, p) - 1 / 3)
    if not dist:
        return wmax
    best = min(dist.values())
    return min(w for w, d in dist.items() if d <= best + 0.005)


def _fmt(box) -> str:
    return "(" + ", ".join(f"{v:.2f}" for v in box) + ")"


# ---------------------------------------------------------------- one frame for a group of items

def _solve_group(g: dict, decision: dict, budget: float, opts: Opts) -> tuple[Rect, list[str]]:
    floor, ko_floor, _ = _floors(budget, opts)
    wmax = _w_max(opts)
    notes: list[str] = []
    keep_out = [tuple(b) for b in decision.get("keep_out") or []]
    keep_in = [tuple(b) for b in decision.get("keep_in") or []]

    w = _base_w(g, floor, opts)
    # Composition is kept in every later search when the base frame could keep it.
    soft = _preferred(g, w, opts) is not None
    r = _preferred(g, w, opts, soft)
    if r is None:  # the gestures do not fit even the widest frame: centre it, flag below
        h = _h(w, opts)
        r = Rect(_clamp(g["eyes"][0], w / 2, 1 - w / 2), _clamp(0.5, h / 2, 1 - h / 2), w, opts.aspect)
        notes.append(f"gestures {_fmt(g['extent'])} do not fit the widest frame")
        return r, notes

    # Keep-out: shift within the allowance, then tighten; the gesture-safe frame wins otherwise.
    if keep_out and _clutter(r, keep_out) > EPS:
        def first_clear(ws):
            for wi in ws:
                p = next((p for p in _placements(g, wi, opts, soft) if _clutter(p, keep_out) <= EPS), None)
                if p:
                    return p
            return None

        def least(ws):
            best, best_c = None, None
            for wi in ws:
                for p in _placements(g, wi, opts, soft):
                    c = _clutter(p, keep_out)
                    if best_c is None or c < best_c - EPS:
                        best, best_c = p, c
            return best

        widths = _widths(r.w, min(ko_floor, r.w))
        best = first_clear(widths) or least(widths)
        reason = None
        if _clutter(best, keep_out) > EPS:
            # Would a frame tighter than the floor clear it? Then the budget is what blocks it.
            tighter = _widths(min(ko_floor, r.w), 0.05)[1::5]
            reason = "budget" if first_clear(tighter) else "gestures"
        r = best
        if reason:
            boxes = ", ".join(_fmt(b) for b in keep_out if _clutter(r, [b]) > EPS)
            notes.append("clutter left in frame: keep-out " + boxes + {
                "gestures": " cannot be removed without cutting gestures or going off centre; "
                            "gestures kept",
                "budget": f" needs more zoom than the source allows (zoom budget {budget:.2f}, "
                          f"declutter up to {1 / ko_floor:.2f}x); not a gesture problem",
            }[reason])

    # Keep-in: never a sliver on the edge. Include it whole, else exclude it clearly.
    if keep_in and _cuts(r, keep_in):
        clutter = _clutter(r, keep_out)
        cut_now = [b for b in keep_in if _cut(r, b)]

        def ok(p):
            return _cuts(p, keep_in) == 0 and _clutter(p, keep_out) <= clutter + EPS

        found = None
        for wi in _widths(r.w, wmax):
            found = next((p for p in _placements(g, wi, opts, soft)
                          if ok(p) and all(p.contains(b) for b in cut_now)), None)
            if found:
                break
        if found is None:
            for wi in _widths(r.w, min(ko_floor, r.w)):
                found = next((p for p in _placements(g, wi, opts, soft) if ok(p)), None)
                if found:
                    break
        if found:
            r = found
        else:
            notes.append("set dressing cut at frame edge: keep-in "
                         + ", ".join(_fmt(b) for b in cut_now))

    # Joints: the bottom edge cuts between joints. Change size first, then position.
    if g["joints"] and _on_joint(r, g["joints"], opts):
        clutter, cuts = _clutter(r, keep_out), _cuts(r, keep_in)
        lo = max(ko_floor, min(r.w, max(floor, _face_w(g, opts))))
        grow, shrink = _widths(r.w, wmax), _widths(r.w, lo)
        order = [r.w]
        for i in range(1, max(len(grow), len(shrink))):
            if i < len(grow):
                order.append(grow[i])
            if i < len(shrink):
                order.append(shrink[i])

        def ok(p):
            return (not _on_joint(p, g["joints"], opts) and _clutter(p, keep_out) <= clutter + EPS
                    and _cuts(p, keep_in) <= cuts)

        found = None
        for wi in order:
            p = _preferred(g, wi, opts, soft)
            if p and ok(p):
                found = p
                break
        if found is None:
            for wi in order:
                found = next((p for p in _placements(g, wi, opts, soft) if ok(p)), None)
                if found:
                    break
        if found:
            r = found
        else:
            notes.append(f"bottom edge sits on a joint at y={r.bottom:.2f}")

    frac = _eye_frac(g, r)
    if not _in_band(frac):
        notes.append(f"composition: eye line at {round(100 * frac)}% of frame height")
    return r, notes


# ---------------------------------------------------------------- setup

_NO_HEADROOM = "source has no zoom headroom (budget {budget:.2f}): declutter only, no punch-ins or push-ins"


def _follow(k: str, gi: dict, g: dict, rect: Rect, shift: float, keep_out, keep_in, opts: Opts,
            flags: list) -> Rect:
    """The setup rect for one item: pan to follow a drifted speaker, fix a stray eye line.

    Drift is measured from the setup's median eye x, so the setup's own off-centre shift (made to
    clear clutter) is not mistaken for drift and is kept when the item pans. Size never changes.
    """
    r = rect
    hx, hy_hard, _, _ = _ranges(gi, rect.w, opts)
    hx = hx or (rect.w / 2, 1 - rect.w / 2)
    ex, ey = gi["eyes"]
    if abs(ex - g["eyes"][0]) > opts.max_shift * rect.w + EPS:
        target = _clamp(ex + shift, *hx)
        n = 40
        xs = sorted({target, *(hx[0] + (hx[1] - hx[0]) * i / n for i in range(n + 1))},
                    key=lambda x: abs(x - target))
        base_c = _clutter(rect, keep_out)
        x = next((x for x in xs if abs(x - target) <= opts.max_shift * rect.w + EPS
                  and _clutter(Rect(x, rect.cy, rect.w, rect.aspect), keep_out) <= base_c + EPS), target)
        r = Rect(x, rect.cy, rect.w, rect.aspect)
        if _clutter(r, keep_out) > base_c + EPS:
            flags.append({"key": k, "text": "clutter left in frame: panning to follow the "
                                            "speaker brings keep-out back in"})
        if _cuts(r, keep_in) > _cuts(rect, keep_in):
            flags.append({"key": k, "text": "set dressing cut at frame edge after pan follow"})

    # Eye line: tilt back toward the band, but declutter, keep-in, joints and gestures come first.
    if not _in_band(_eye_frac(gi, r)):
        want = _clamp(_eye_frac(gi, r), EYE_HIGH, 1 / 3)
        lo, hi = hy_hard or (r.h / 2, 1 - r.h / 2)
        target = _clamp(ey + r.h / 2 - want * r.h, lo, hi)
        base = (_clutter(r, keep_out), _cuts(r, keep_in), _on_joint(r, gi["joints"], opts),
                _gesture_cut(r, gi["extent"]))

        def ok(p):
            return (_clutter(p, keep_out) <= base[0] + EPS and _cuts(p, keep_in) <= base[1]
                    and (base[2] or not _on_joint(p, gi["joints"], opts))
                    and _gesture_cut(p, gi["extent"]) <= base[3] + EPS)

        n = 40  # full tilt first, then partway back toward the setup's position
        for i in range(n):
            p = Rect(r.cx, target + (r.cy - target) * i / n, r.w, r.aspect)
            if ok(p):
                r = p
                break
    frac = _eye_frac(gi, r)
    if not _in_band(frac):
        flags.append({"key": k, "text": f"composition: eye line at {round(100 * frac)}% "
                                        f"of frame height"})
    return r


def solve_base(setup_items: list[str], measures: dict, decision: dict, budget: float,
               opts: Opts) -> dict:
    """One base frame for a camera setup.

    Returns {"rect": Rect, "items": {key: Rect}, "flags": [{"key", "text"}], "punch_ok": bool,
    "outliers": set[key]}. `punch_ok` is False when the budget leaves no room for a punch-in of at
    least punch_min. `outliers` are the items with their own frame (not punched; see solve_punches).
    """
    flags: list[dict] = []
    keep_out = [tuple(b) for b in decision.get("keep_out") or []]
    keep_in = [tuple(b) for b in decision.get("keep_in") or []]
    strong = [k for k in setup_items if _strong(measures.get(k))]
    floor, _, budget_ok = _floors(budget, opts)

    if not strong:
        w = _w_max(opts)
        rect = Rect(0.5, 0.5, w, opts.aspect)
        for k in setup_items:
            flags.append({"key": k, "text": "speaker not detected in this setup: full source frame kept"})
        if not budget_ok:
            flags.append({"key": None, "text": _NO_HEADROOM.format(budget=budget)})
        return {"rect": rect, "items": {k: rect for k in setup_items}, "flags": flags,
                "punch_ok": False, "outliers": set()}

    def group(keys):
        return _group([measures[k] for k in keys])

    # Outliers: an item that loosens the setup by more than opts.outlier gets its own frame.
    # Leave-one-out needs at least 3 strong items, and fewer than half of them may become outliers.
    remaining, outliers = list(strong), []
    while len(remaining) - 1 > len(strong) / 2:
        w_all = _base_w(group(remaining), floor, opts)
        ratios = {k: w_all / _base_w(group([j for j in remaining if j != k]), floor, opts)
                  for k in remaining}
        k = max(remaining, key=lambda j: ratios[j])
        if ratios[k] <= opts.outlier + EPS:
            break
        remaining.remove(k)
        outliers.append((k, ratios[k]))

    g = group(remaining)
    rect, notes = _solve_group(g, decision, budget, opts)
    # The eye line is checked per item in _follow, on the frames that ship.
    flags.extend({"key": remaining[0], "text": n} for n in notes if not n.startswith("composition:"))
    setup_shift = rect.cx - g["eyes"][0]   # off-centre shift the setup chose (keep-out), kept by pans

    items: dict[str, Rect] = {}
    for k in setup_items:
        m = measures.get(k)
        if k in remaining:
            items[k] = _follow(k, group([k]), g, rect, setup_shift, keep_out, keep_in, opts, flags)
        elif k in strong:  # outlier
            ratio = dict(outliers)[k]
            own, own_notes = _solve_group(group([k]), decision, budget, opts)
            items[k] = own
            flags.append({"key": k, "text": f"outlier: gestures would loosen the setup frame "
                                            f"{ratio:.2f}x; this item has its own looser frame"})
            flags.extend({"key": k, "text": n} for n in own_notes)
        else:  # weak or missing detection
            items[k] = rect
            pct = round(100 * (m or {}).get("detected", 0.0))
            flags.append({"key": k, "text": f"speaker found in only {pct}% of sampled frames: "
                                            f"setup frame used, check it"})
        if k in strong and not m.get("extent"):
            flags.append({"key": k, "text": "no head or hand extent measured: frame sized from the "
                                            "face only, check that no gesture is cut"})

    if not budget_ok:
        flags.append({"key": None, "text": _NO_HEADROOM.format(budget=budget)})
    punch_ok = budget_ok and budget * rect.w >= opts.punch_min - EPS
    for k, r in items.items():  # whatever the steps above did, a clutter tangent is never silent
        for box in _slivers(r, keep_out):
            flags.append({"key": k, "text": f"clutter sliver at frame edge: keep-out {_fmt(box)} "
                                            f"shows as a thin strip"})
    unique = list({(f["key"], f["text"]): f for f in flags}.values())  # setup and item checks overlap
    return {"rect": rect, "items": items, "flags": unique, "punch_ok": punch_ok,
            "outliers": {k for k, _ in outliers}}


# ---------------------------------------------------------------- punch-ins and eye trace
#
# setup_info: {setup_id: {"label": "plain"|"dynamic", "punch_ok": bool, "rect": Rect,
#   "outliers": set[key], "keep_out": [Box], "keep_in": [Box]}}, built by setup_info_from.
# An item whose width differs from the setup rect is treated as an outlier too (pan follow and
# eye-line tilt never change the width). The boxes let a punch, a push or an eye-trace pan check
# that it never adds clutter or cuts set dressing.

def setup_info_from(setup_results: dict, decisions: dict) -> dict:
    """setup_info for solve_cuts from solve_base results and per-setup decisions, both by setup id.

    Each decision is the dict solve_base took: {"keep_out", "keep_in", "label", ...}.
    """
    out = {}
    for sid, res in setup_results.items():
        if sid not in decisions:
            raise ValueError(f"no decision for setup {sid!r}")
        d = decisions[sid]
        out[sid] = {"label": d.get("label"), "punch_ok": bool(res["punch_ok"]), "rect": res["rect"],
                    "outliers": set(res.get("outliers") or ()),
                    "keep_out": [tuple(b) for b in d.get("keep_out") or []],
                    "keep_in": [tuple(b) for b in d.get("keep_in") or []]}
    return out


def _info(setup_info: dict, setup_of: dict, key: str) -> dict:
    sid = setup_of.get(key)
    if sid not in setup_info:
        raise ValueError(f"setup info missing for setup {sid!r} (item {key})")
    return setup_info[sid]


def _boxes(info: dict) -> tuple[list, list]:
    return ([tuple(b) for b in info.get("keep_out") or []],
            [tuple(b) for b in info.get("keep_in") or []])


def _grown(ext: Box, r: Rect, opts: Opts) -> Box:
    """The gesture extent grown by the margin at the rect's height, clipped to the source."""
    my = opts.gesture_margin * r.h
    mx = my / opts.aspect
    l, t, rt, b = ext
    return max(0.0, l - mx), max(0.0, t - my), min(1.0, rt + mx), min(1.0, b + my)


def _zoom_check(base: Rect, m: dict, f: float, keep_out, keep_in, opts: Opts) -> tuple[Rect, list[str]]:
    """base zoomed in by f around the eyes, and every reason it cannot ship (empty when it can)."""
    r = base.scaled(f, anchor=tuple(m["eyes"]))
    why = []
    if not r.inside_source():
        why.append("show outside the source")
    if not r.contains(_grown(_extent(m), r, opts)):
        why.append("cut a gesture")
    joints = sorted((m.get("joints_y") or {}).values())
    if _on_joint(r, joints, opts) and not _on_joint(base, joints, opts):
        why.append("put the bottom edge on a joint")
    if _cuts(r, keep_in) > _cuts(base, keep_in):
        why.append("cut set dressing at the edge")
    if len(_slivers(r, keep_out)) > len(_slivers(base, keep_out)):
        why.append("leave a clutter sliver at the edge")
    return r, why


def _why(reasons: list[str]) -> str:
    return ", ".join(reasons[:-1]) + (" and " if len(reasons) > 1 else "") + reasons[-1]


def _zoomed(base: Rect, m: dict, f: float, keep_out, keep_in, opts: Opts) -> Rect | None:
    """base zoomed in by f around the eyes, or None if that clips a gesture, shows outside the
    source, cuts set dressing, leaves a clutter sliver or puts the bottom edge on a joint where
    the base frame did not."""
    r, why = _zoom_check(base, m, f, keep_out, keep_in, opts)
    return None if why else r


def _eye_x(r: Rect, m: dict) -> float:
    """Eye position as a fraction of the frame width from the left."""
    return (m["eyes"][0] - r.left) / r.w


def _trace(prev: Rect, mp: dict, r: Rect, m: dict, keep_out, keep_in, opts: Opts) -> Rect:
    """Pan r (same width) so the eyes land where they were in prev, as far as the hard rules allow.

    The pan never adds clutter, cuts set dressing or clips more gesture than r does already.
    """
    want = _eye_x(prev, mp)
    target = _clamp(m["eyes"][0] - want * r.w + r.w / 2, r.w / 2, 1 - r.w / 2)
    g = _grown(_extent(m), r, opts)
    base = (_clutter(r, keep_out), _cuts(r, keep_in), len(_slivers(r, keep_out)), _gesture_cut(r, g))

    def ok(p):
        return (_clutter(p, keep_out) <= base[0] + EPS and _cuts(p, keep_in) <= base[1]
                and len(_slivers(p, keep_out)) <= base[2] and _gesture_cut(p, g) <= base[3] + EPS)

    n = 100  # the full pan first, then partway back toward where the frame was
    for i in range(n):
        p = Rect(r.cx + (target - r.cx) * (1 - i / n), r.cy, r.w, r.aspect)
        if ok(p):
            return p
    return r


def solve_punches(runs: list[list[str]], item_rects: dict, measures: dict, setup_of: dict,
                  setup_info: dict, budget: float, opts: Opts,
                  push_keys: set[str]) -> tuple[dict, list[dict]]:
    """Punch-ins along each run of visible jump cuts, then eye trace across every cut.

    Slots alternate base, punch, base, punch. A push-in item, an outlier (own frame) and an item
    whose punch fails all take the base slot, and the alternation restarts from them. A punch is
    opts.punch around the eyes, reduced to the budget, else opts.punch_min; below that it is skipped
    and flagged (never a soft or gesture-clipping punch). Returns ({key: Rect} for every item in
    item_rects, flags).
    """
    out = dict(item_rects)
    flags: list[dict] = []
    for run in runs:
        info = _info(setup_info, setup_of, run[0])
        keep_out, keep_in = _boxes(info)
        setup_rect = info.get("rect")
        outliers = set(info.get("outliers") or ())
        allowed = bool(info.get("punch_ok"))
        if len(run) > 1 and not allowed:
            flags.append({"key": run[0], "text": "no punch-ins on these jump cuts: the zoom budget "
                                                 f"({budget:.2f}) leaves no room for a "
                                                 f"{opts.punch_min:.2f}x punch"})
        want = False  # the first item of a run is a base slot
        for k in run:
            base = item_rects[k]
            outlier = k in outliers or (setup_rect is not None and abs(base.w - setup_rect.w) > 1e-6)
            if not want or not allowed or k in push_keys or outlier:
                want = True
                continue
            want = True  # a skipped punch restarts the alternation after it
            m = measures.get(k)
            if not _strong(m):  # solve_base already flags weak detection
                continue
            fmax = min(opts.punch, budget / zoom_of(base))
            if fmax < opts.punch_min - EPS:
                flags.append({"key": k, "text": f"punch-in skipped: base zoom {zoom_of(base):.2f}x "
                                                f"leaves only {fmax:.2f}x under the zoom budget "
                                                f"({budget:.2f}); minimum is {opts.punch_min:.2f}x"})
                continue
            # fmax first, then 0.01 steps down, exact punch_min last: the first that fits wins.
            factors = [fmax]
            if fmax > opts.punch_min + EPS:
                hi, lo = math.ceil(fmax * 100 - 1e-6) - 1, round(opts.punch_min * 100)
                factors += [i / 100 for i in range(hi, lo, -1)] + [opts.punch_min]
            p = next((r for f in factors
                      if (r := _zoomed(base, m, f, keep_out, keep_in, opts)) is not None), None)
            if p is None:
                att, why = _zoom_check(base, m, opts.punch_min, keep_out, keep_in, opts)
                flags.append({"key": k, "attempt": att,
                              "text": f"punch-in skipped: even {opts.punch_min:.2f}x would {_why(why)}"})
                continue
            out[k] = p
            want = False

        # Eye trace: in timeline order, so a corrected frame is the reference for the next cut.
        for a, b in zip(run, run[1:]):
            ma, mb = measures.get(a), measures.get(b)
            if not (_strong(ma) and _strong(mb)):
                continue
            if abs(_eye_x(out[a], ma) - _eye_x(out[b], mb)) <= opts.eye_tol + EPS:
                continue
            out[b] = _trace(out[a], ma, out[b], mb, keep_out, keep_in, opts)
            jump = abs(_eye_x(out[a], ma) - _eye_x(out[b], mb))
            if jump > opts.eye_tol + EPS:
                flags.append({"key": b, "text": f"eye trace: eyes jump {round(100 * jump)}% of frame "
                                                f"width across the cut from {a}; a pan to match "
                                                f"would add clutter, cut set dressing or a gesture"})
    return out, flags


# ---------------------------------------------------------------- push-ins

def solve_pushes(push_ins: list[dict], plan: dict, cam_items: list[dict], setup_info: dict,
                 item_rects: dict, budget: float, opts: Opts, cuts: list[tuple[str, str]], *,
                 measures: dict, setup_of: dict) -> tuple[list[dict], list[dict]]:
    """Validate the proposed push-ins and compute their rects.

    push_ins: [{"start_s", "end_s", "text", "reason"}] in timeline seconds.
    Returns ([{"key", "start_frame", "end_frame", "from", "to", "text", "reason"}], flags) with
    frames relative to the item's start. `from` is the item's rect in item_rects; `to` zooms it by
    opts.push around the eyes. Every rule that fails drops the push with a flag. The fast-movement
    check uses the gesture extent of the whole item, not only the pushed range.
    """
    fps = (plan.get("timeline") or {}).get("fps")
    if not fps:
        raise ValueError("plan has no timeline fps")
    fps = float(fps)
    video = [i for i in plan.get("items", [])
             if i.get("media") in ("video", "both") and i.get("track") is not None
             and i.get("enabled", True) is not False]
    prev_in_run = {}
    for run in _runs(cam_items, cuts):
        for i, k in enumerate(run):
            prev_in_run[k] = run[i - 1] if i else None

    pushes: list[dict] = []
    flags: list[dict] = []
    last: tuple[float, str] | None = None   # (start_s, key) of the last kept push
    for pi in sorted(push_ins, key=lambda p: p["start_s"]):
        s, e = round(pi["start_s"] * fps), round(pi["end_s"] * fps)
        item = next((it for it in cam_items if it["start"] <= s and e <= it["end"]), None)
        key = item_key(item) if item else None

        def why():
            if e <= s:
                return "empty range"
            length = (e - s) / fps
            if not opts.push_min_s - EPS <= length <= opts.push_max_s + EPS:
                return (f"the line is {length:.1f} s long; push-ins run {opts.push_min_s:.0f} to "
                        f"{opts.push_max_s:.0f} s")
            if item is None:
                return "the range is not inside one on-camera item (a push-in never crosses a cut)"
            label = _info(setup_info, setup_of, key).get("label")
            if label != "plain":
                return ("the setup is labelled dynamic: it has energy already" if label == "dynamic"
                        else "the setup is not labelled plain")
            # The viewer sees the topmost enabled item: anything on a higher track covers the speaker.
            if any(v is not item and v["track"] > item["track"] and v["start"] < e and s < v["end"]
                   for v in video):
                return "b-roll covers the speaker during the line"
            if last and pi["start_s"] - last[0] < opts.push_spacing_s - EPS:
                return (f"starts {pi['start_s'] - last[0]:.1f} s after the previous push-in "
                        f"(minimum {opts.push_spacing_s:.0f} s)")
            if last and last[1] in (key, prev_in_run.get(key)):
                return "the previous push-in is on the item just before: never two in a row"
            base = item_rects[key]
            if zoom_of(base) * opts.push > budget + EPS:
                return (f"base zoom {zoom_of(base):.2f}x x {opts.push:.2f} is over the zoom budget "
                        f"({budget:.2f})")
            m = measures.get(key)
            if not _strong(m):
                return "speaker not detected well enough to anchor the push on the eyes"
            l, _, r, _ = _extent(m)
            if r - l > opts.fast_move * base.w + EPS:
                return (f"fast movement: gestures span {round(100 * (r - l) / base.w)}% of the "
                        f"frame width")
            keep_out, keep_in = _boxes(_info(setup_info, setup_of, key))
            att, bad = _zoom_check(base, m, opts.push, keep_out, keep_in, opts)
            if bad:
                attempt[0] = att
                return f"the push would {_why(bad)}"
            return None

        attempt = [None]
        reason = why()
        if reason:
            flag = {"key": key, "text": f"push-in dropped at {pi['start_s']:.1f}-"
                                        f"{pi['end_s']:.1f} s ({pi.get('text', '')!r}): {reason}"}
            if attempt[0] is not None:
                flag["attempt"] = attempt[0]
            flags.append(flag)
            continue
        base = item_rects[key]
        pushes.append({"key": key, "start_frame": s - item["start"], "end_frame": e - item["start"],
                       "from": base, "to": base.scaled(opts.push, anchor=tuple(measures[key]["eyes"])),
                       "text": pi.get("text", ""), "reason": pi.get("reason", "")})
        last = (pi["start_s"], key)
    return pushes, flags


# ---------------------------------------------------------------- all cut effects

def solve_cuts(plan: dict, cam_items: list[dict], cuts: list[tuple[str, str]], item_rects: dict,
               measures: dict, setup_of: dict, setup_info: dict, push_ins: list[dict],
               budget: float, opts: Opts) -> tuple[dict, list[dict], list[dict]]:
    """Push-ins, punch-ins and eye trace in the right order.

    item_rects are the base rects from solve_base; setup_info comes from setup_info_from.
    Returns (rects per item, pushes, flags). A push starts from its item's final rect (eye trace
    may have panned it); if the push no longer fits there it is dropped and the item stays base.
    """
    pushes, flags = solve_pushes(push_ins, plan, cam_items, setup_info, item_rects, budget, opts,
                                 cuts, measures=measures, setup_of=setup_of)
    rects, pflags = solve_punches(_runs(cam_items, cuts), item_rects, measures, setup_of,
                                  setup_info, budget, opts, {p["key"] for p in pushes})
    flags.extend(pflags)
    kept = []
    for p in pushes:
        k = p["key"]
        keep_out, keep_in = _boxes(_info(setup_info, setup_of, k))
        to, bad = _zoom_check(rects[k], measures[k], opts.push, keep_out, keep_in, opts)
        if bad:
            flags.append({"key": k, "attempt": to,
                          "text": f"push-in dropped ({p['text']!r}): after eye trace the push "
                                  f"would {_why(bad)}"})
            continue
        kept.append({**p, "from": rects[k], "to": to})
    return rects, kept, flags
