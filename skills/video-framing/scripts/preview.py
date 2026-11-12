"""Preview stills for video-framing, drawn with ffmpeg (no Python image packages).

- `grab_frame`: one source frame as a JPEG, HDR tone-mapped like the measuring frames.
- `setup_still`: a setup frame with a 10% grid labelled 0.0 to 1.0 on both axes, so the agent can
  read keep-out and keep-in boxes off it in normalised source coordinates.
- `before_after`: the source with the crop rect (green), keep-out boxes (red), keep-in boxes
  (blue) and an optional rejected attempt (orange) on the left; the crop as it will look, at
  960x540, on the right.

Coordinates are normalised source coordinates 0..1, origin top-left. A box is (left, top, right, bottom).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from measure import HDR_TRANSFERS, _color_transfer, _has_zscale

HALF_W, HALF_H = 960, 540
_FONTS = ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc",
          "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")


def available() -> bool:
    return bool(shutil.which("ffmpeg"))


def _run(args: list[str], what: str) -> None:
    if not available():
        raise RuntimeError("previews need ffmpeg on PATH")
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg could not write {what}:\n{r.stderr.strip()}")


def _font() -> str:
    f = next((p for p in _FONTS if Path(p).exists()), None)
    return f"fontfile='{f}'" if f else "font='Sans'"


def grab_frame(path, t: float, out, width: int = 1920) -> Path:
    """The source frame at `t` seconds as a JPEG `width` px wide (rotation applied by ffmpeg)."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    scale = f"scale={width}:-2"
    if _color_transfer(str(path)) in HDR_TRANSFERS and _has_zscale():
        vf = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable:desat=0,"
              f"zscale=t=bt709:m=bt709:r=tv,format=yuv420p,{scale}")
    else:
        vf = scale
    _run(["-ss", f"{max(t, 0.0):.3f}", "-i", str(path), "-frames:v", "1", "-vf", vf, "-q:v", "2",
          str(out)], str(out))
    if not out.exists():
        raise RuntimeError(f"ffmpeg wrote no frame for {path} at {t:.2f}s")
    return out


def _label(text: str, x: str, y: str, size: str = "h/34") -> str:
    return (f"drawtext={_font()}:text='{text}':x='{x}':y='{y}':fontsize={size}:fontcolor=white:"
            f"box=1:boxcolor=black@0.65:boxborderw=4")


def setup_still(frame, out, grid: bool = True, title: str | None = None) -> Path:
    """The frame at 1920 px wide with a labelled 10% grid (0.0 .. 1.0 on both axes)."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    f = ["scale=1920:-2"]
    if grid:
        f.append("drawgrid=w=iw/10:h=ih/10:t=2:c=yellow@0.55")
        for i in range(11):
            v = i / 10
            f.append(_label(f"{v:.1f}", f"min(max(0,w*{v}-tw/2),w-tw)", "6"))
            f.append(_label(f"{v:.1f}", f"min(max(0,w*{v}-tw/2),w-tw)", "h-th-6"))
            if 0 < i < 10:
                f.append(_label(f"{v:.1f}", "6", f"h*{v}-th/2"))
                f.append(_label(f"{v:.1f}", "w-tw-6", f"h*{v}-th/2"))
    if title:
        safe = title.replace("\\", "").replace("'", "").replace(":", " ")
        f.append(_label(safe, "(w-tw)/2", "h*0.05", "h/26"))
    _run(["-i", str(frame), "-vf", ",".join(f), "-q:v", "2", str(out)], str(out))
    return out


def _box(box, color: str, t: int = 3) -> str:
    l, tp, r, b = (min(max(float(v), 0.0), 1.0) for v in box)
    return (f"drawbox=x=iw*{l:.4f}:y=ih*{tp:.4f}:w=iw*{r - l:.4f}:h=ih*{b - tp:.4f}:"
            f"color={color}:t={t}")


def before_after(frame, rect, out, keep_out=(), keep_in=(), inner=None, title: str | None = None,
                 attempt=None) -> Path:
    """Left: the source with `rect` (the shipped frame) in green, keep-out boxes red, keep-in blue,
    an optional `inner` rect (a push-in's end frame) in cyan and an optional `attempt` (a rejected
    punch-in or push-in) in orange. Right: the crop of `rect` at 960x540 with thirds."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    boxes = [_box(b, "red@0.9") for b in keep_out] + [_box(b, "blue@0.9") for b in keep_in]
    boxes.append(_box((rect.left, rect.top, rect.right, rect.bottom), "lime", 4))
    if inner is not None:
        boxes.append(_box((inner.left, inner.top, inner.right, inner.bottom), "cyan", 2))
    if attempt is not None:
        boxes.append(_box((attempt.left, attempt.top, attempt.right, attempt.bottom), "orange", 3))
    left = (f"[a]{','.join(boxes)},scale={HALF_W}:{HALF_H}:force_original_aspect_ratio=decrease,"
            f"pad={HALF_W}:{HALF_H}:(ow-iw)/2:(oh-ih)/2")
    if title:
        safe = title.replace("\\", "").replace("'", "").replace(":", " ")
        left += "," + _label(safe, "8", "8", "20")
    left += "[l]"
    l, t = max(rect.left, 0.0), max(rect.top, 0.0)
    w, h = min(rect.w, 1.0 - l), min(rect.h, 1.0 - t)
    right = (f"[b]crop=w=iw*{w:.5f}:h=ih*{h:.5f}:x=iw*{l:.5f}:y=ih*{t:.5f},"
             f"scale={HALF_W}:{HALF_H},drawgrid=w=iw/3:h=ih/3:t=1:c=white@0.35[r]")
    graph = f"[0:v]split[a][b];{left};{right};[l][r]hstack"
    _run(["-i", str(frame), "-filter_complex", graph, "-frames:v", "1", "-q:v", "2", str(out)], str(out))
    return out
