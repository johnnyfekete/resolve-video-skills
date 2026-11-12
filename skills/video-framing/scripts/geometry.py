"""Framing geometry: crop rects in normalised source coordinates and Resolve item properties.

Coordinates are normalised source coordinates, 0..1, origin top-left. A Box is
(left, top, right, bottom). The calibration formula is in references/resolve-notes.md.
Pure maths: no Resolve, no I/O.
"""
from dataclasses import dataclass

Box = tuple[float, float, float, float]

OUT_ASPECT = 16 / 9  # a crop rect always has the (16:9) output aspect
EPS = 1e-9


@dataclass(frozen=True)
class Rect:
    """Crop rect: centre (cx, cy), width w as a fraction of source width.

    `aspect` is the SOURCE aspect (width / height), default 16/9. The rect itself has the
    output aspect (16:9), so its height as a fraction of source height is
    h = w * aspect / (16/9). For a 16:9 source, h == w.
    """
    cx: float
    cy: float
    w: float
    aspect: float = 16 / 9

    @property
    def h(self) -> float:
        return self.w * self.aspect / OUT_ASPECT

    @property
    def left(self) -> float:
        return self.cx - self.w / 2

    @property
    def right(self) -> float:
        return self.cx + self.w / 2

    @property
    def top(self) -> float:
        return self.cy - self.h / 2

    @property
    def bottom(self) -> float:
        return self.cy + self.h / 2

    def contains(self, box: Box) -> bool:
        l, t, r, b = box
        return (l >= self.left - EPS and t >= self.top - EPS
                and r <= self.right + EPS and b <= self.bottom + EPS)

    def overlaps(self, box: Box) -> float:
        """Fraction of the box's area that lies inside the rect (0 for an empty box)."""
        l, t, r, b = box
        area = (r - l) * (b - t)
        if area <= 0:
            return 0.0
        iw = min(r, self.right) - max(l, self.left)
        ih = min(b, self.bottom) - max(t, self.top)
        if iw <= 0 or ih <= 0:
            return 0.0
        return iw * ih / area

    def inside_source(self) -> bool:
        return (self.left >= -EPS and self.top >= -EPS
                and self.right <= 1 + EPS and self.bottom <= 1 + EPS)

    def scaled(self, f: float, anchor: tuple[float, float]) -> "Rect":
        """Zoom in by f (w becomes w / f), keeping `anchor` at the same screen position."""
        ax, ay = anchor
        w = self.w / f
        h = self.h / f
        left = ax - (ax - self.left) / f
        top = ay - (ay - self.top) / f
        return Rect(left + w / 2, top + h / 2, w, self.aspect)


def zoom_of(rect: Rect) -> float:
    return 1 / rect.w


def zoom_budget(src_h: int, out_h: int) -> float:
    """Largest zoom before the crop is upscaled: source pixels per output pixel."""
    return src_h / out_h


def _fit(tl_w, tl_h, src_w, src_h):
    """(s * SW, s * SH): source size in timeline pixels at Zoom 1, s = max(TW/SW, TH/SH)."""
    if src_w is None or src_h is None:  # source aspect == timeline aspect: s * SW = TW
        return float(tl_w), float(tl_h)
    s = max(tl_w / src_w, tl_h / src_h)
    return src_w * s, src_h * s


def rect_to_props(rect: Rect, tl_w: int, tl_h: int, src_w: int | None = None,
                  src_h: int | None = None) -> dict:
    """Crop rect -> Resolve item properties (scale full frame with crop).

    src_w/src_h None means the source has the timeline's aspect (the measured case,
    Zoom = 1/w). With sizes given and a different aspect, the notes' derived (unmeasured)
    general form is used.
    """
    fw, fh = _fit(tl_w, tl_h, src_w, src_h)
    zoom = tl_w / (rect.w * fw)
    return {
        "ZoomX": round(zoom, 4),
        "ZoomY": round(zoom, 4),
        "Pan": round((0.5 - rect.cx) * fw * zoom, 4),
        "Tilt": round((rect.cy - 0.5) * fh * zoom, 4),
    }


def props_to_rect(props: dict, tl_w: int, tl_h: int, src_w: int | None = None,
                  src_h: int | None = None) -> Rect:
    """Inverse of rect_to_props (ZoomX is used as the zoom)."""
    fw, fh = _fit(tl_w, tl_h, src_w, src_h)
    zoom = props["ZoomX"]
    aspect = src_w / src_h if src_w and src_h else tl_w / tl_h
    return Rect(0.5 - props["Pan"] / (fw * zoom),
                0.5 + props["Tilt"] / (fh * zoom),
                tl_w / (zoom * fw), aspect)
