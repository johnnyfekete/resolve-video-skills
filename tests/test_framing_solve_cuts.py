import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "video-framing" / "scripts"))

from geometry import Rect, zoom_of  # noqa: E402
from solve import Opts, solve_punches, solve_pushes  # noqa: E402
from timeline import item_key  # noqa: E402

OPTS = Opts()
EYES = (0.52, 0.30)
# Eyes centred and on the upper third, 1.54x zoom: room for a 1.15 punch under a 2.0 budget.
BASE = Rect(0.52, 0.30 + 0.65 / 6, 0.65)
SMALL = (0.45, 0.17, 0.59, 0.43)


def m(eyes=EYES, extent=SMALL, detected=1.0):
    return {"detected": detected, "eyes": list(eyes), "face_h": 0.12,
            "extent": list(extent), "joints_y": {}}


def screen_x(rect, eyes):
    return (eyes[0] - rect.left) / rect.w


def info(label="plain", punch_ok=True, rect=BASE, **kw):
    return {"label": label, "punch_ok": punch_ok, "rect": rect, **kw}


class Punches(unittest.TestCase):
    def solve(self, keys, measures=None, rects=None, budget=2.0, push_keys=(), sinfo=None,
              runs=None):
        measures = measures or {k: m() for k in keys}
        rects = rects or {k: BASE for k in keys}
        setup_of = {k: "s1" for k in keys}
        sinfo = sinfo or {"s1": info()}
        out, flags = solve_punches(runs or [list(keys)], rects, measures, setup_of, sinfo, budget,
                                   OPTS, set(push_keys))
        for r in out.values():
            self.assertTrue(r.inside_source(), r)
        return out, flags

    def slots(self, out, keys, base=BASE):
        return ["punch" if out[k].w < base.w - 1e-9 else "base" for k in keys]


class AlternationTest(Punches):
    def test_run_of_four_alternates(self):
        keys = ["a", "b", "c", "d"]
        out, flags = self.solve(keys)
        self.assertEqual(self.slots(out, keys), ["base", "punch", "base", "punch"])
        self.assertEqual(flags, [])
        self.assertAlmostEqual(BASE.w / out["b"].w, OPTS.punch, places=6)

    def test_push_item_takes_base_and_restarts(self):
        keys = ["a", "b", "c", "d"]
        out, _ = self.solve(keys, push_keys={"b"})
        self.assertEqual(self.slots(out, keys), ["base", "base", "punch", "base"])

    def test_outlier_restarts_and_is_not_punched(self):
        keys = ["a", "b", "c", "d"]
        own = Rect(0.52, 0.45, 0.9)
        rects = {"a": BASE, "b": own, "c": BASE, "d": BASE}
        out, _ = self.solve(keys, rects=rects, sinfo={"s1": info(outliers={"b"})})
        self.assertEqual(out["b"], own)
        self.assertEqual(self.slots(out, ["a", "c", "d"]), ["base", "punch", "base"])

    def test_outlier_recognised_by_own_size_without_the_set(self):
        keys = ["a", "b", "c"]
        own = Rect(0.52, 0.45, 0.9)
        out, _ = self.solve(keys, rects={"a": BASE, "b": own, "c": BASE})
        self.assertEqual(out["b"], own)
        self.assertEqual(self.slots(out, ["a", "c"]), ["base", "punch"])

    def test_singleton_run_is_not_punched(self):
        out, flags = self.solve(["a"])
        self.assertEqual(out["a"], BASE)
        self.assertEqual(flags, [])

    def test_punch_ok_false_drops_punches_with_flag(self):
        keys = ["a", "b", "c"]
        out, flags = self.solve(keys, sinfo={"s1": info(punch_ok=False)})
        self.assertEqual(self.slots(out, keys), ["base", "base", "base"])
        self.assertEqual(len(flags), 1)
        self.assertIn("no punch-ins", flags[0]["text"])


class PunchRectTest(Punches):
    def test_punch_keeps_eye_screen_position(self):
        out, _ = self.solve(["a", "b"])
        self.assertLess(abs(screen_x(out["b"], EYES) - screen_x(BASE, EYES)), 0.001)
        sy = (EYES[1] - out["b"].top) / out["b"].h
        self.assertLess(abs(sy - (EYES[1] - BASE.top) / BASE.h), 0.001)

    def test_hand_clipped_at_115_fits_at_110(self):
        ext = (0.45, 0.17, 0.795, 0.43)
        out, flags = self.solve(["a", "b"], measures={"a": m(extent=ext), "b": m(extent=ext)})
        self.assertAlmostEqual(BASE.w / out["b"].w, OPTS.punch_min, places=6)
        self.assertEqual(flags, [])

    def test_hand_clipped_at_both_stays_base_with_flag(self):
        ext = (0.45, 0.17, 0.81, 0.43)
        keys = ["a", "b", "c"]
        meas = {"a": m(extent=ext), "b": m(extent=ext), "c": m()}
        out, flags = self.solve(keys, measures=meas)
        self.assertEqual(out["b"], BASE)
        self.assertEqual([f["key"] for f in flags], ["b"])
        self.assertIn("gesture", flags[0]["text"])
        # The alternation restarts after the skipped item.
        self.assertEqual(self.slots(out, keys), ["base", "base", "punch"])

    def test_budget_reduces_punch(self):
        base = Rect(0.52, 0.30 + (1 / 1.8) / 6, 1 / 1.8)
        out, flags = self.solve(["a", "b"], rects={"a": base, "b": base},
                                sinfo={"s1": info(rect=base)})
        self.assertAlmostEqual(base.w / out["b"].w, 2.0 / 1.8, places=6)
        self.assertLessEqual(zoom_of(out["b"]), 2.0 + 1e-9)
        self.assertEqual(flags, [])

    def test_budget_below_punch_min_skips_with_flag(self):
        base = Rect(0.52, 0.30 + (1 / 1.85) / 6, 1 / 1.85)
        out, flags = self.solve(["a", "b"], rects={"a": base, "b": base},
                                sinfo={"s1": info(rect=base)})
        self.assertEqual(out["b"], base)
        self.assertEqual(len(flags), 1)
        self.assertIn("budget", flags[0]["text"])

    def test_punch_does_not_cut_keep_in(self):
        # A lamp fully in the base frame but straddling the 1.15 and 1.10 punch edges.
        lamp = (0.20, 0.50, 0.26, 0.60)
        keys = ["a", "b"]
        out, flags = self.solve(keys, sinfo={"s1": info(keep_in=[list(lamp)])})
        self.assertEqual(out["b"], BASE)
        self.assertEqual([f["key"] for f in flags], ["b"])


class EyeTraceTest(Punches):
    def test_five_percent_offset_is_corrected(self):
        eyes_b = (EYES[0] + 0.05 * BASE.w, EYES[1])
        meas = {"a": m(), "b": m(eyes=eyes_b, extent=(0.48, 0.17, 0.62, 0.43))}
        self.assertAlmostEqual(screen_x(BASE, eyes_b) - screen_x(BASE, EYES), 0.05)
        out, flags = self.solve(["a", "b"], measures=meas, sinfo={"s1": info(punch_ok=False)})
        self.assertLess(abs(screen_x(out["b"], eyes_b) - screen_x(out["a"], EYES)), 0.03)
        self.assertEqual(out["b"].w, BASE.w)

    def test_correction_applies_to_a_punched_incoming_frame(self):
        eyes_b = (EYES[0] + 0.05 * BASE.w, EYES[1])
        meas = {"a": m(), "b": m(eyes=eyes_b, extent=(0.48, 0.17, 0.62, 0.43))}
        out, _ = self.solve(["a", "b"], measures=meas)
        self.assertLess(out["b"].w, BASE.w)
        self.assertLess(abs(screen_x(out["b"], eyes_b) - screen_x(out["a"], EYES)), 0.03)

    def test_shift_that_would_add_clutter_is_left_and_flagged(self):
        eyes_b = (EYES[0] - 0.05 * BASE.w, EYES[1])
        # Clutter just left of the frame: moving b's frame left to match would bring it in.
        clutter = (BASE.left - 0.15, 0.2, BASE.left - 0.001, 0.4)
        meas = {"a": m(), "b": m(eyes=eyes_b, extent=(0.42, 0.17, 0.56, 0.43))}
        out, flags = self.solve(["a", "b"], measures=meas,
                                sinfo={"s1": info(punch_ok=False, keep_out=[list(clutter)])})
        self.assertGreaterEqual(out["b"].left, clutter[2] - 1e-9)
        eye = [f for f in flags if "eye" in f["text"]]
        self.assertEqual([f["key"] for f in eye], ["b"])


# ---------------------------------------------------------------- push-ins

FPS = 25


def vid(clip, track, start, end, media="both"):
    return {"clip": clip, "path": f"/m/{clip}", "media": media, "track": track,
            "start": start, "end": end, "src_start": 0}


def plan(*items):
    return {"schema": "rvs.plan/1", "timeline": {"fps": FPS}, "items": list(items)}


def push(start_s, end_s, text="line"):
    return {"start_s": start_s, "end_s": end_s, "text": text, "reason": "thesis"}


class Pushes(unittest.TestCase):
    def solve(self, push_ins, items, broll=(), label="plain", budget=2.0, rects=None,
              measures=None, cuts=None):
        cam = list(items)
        keys = [item_key(i) for i in cam]
        p = plan(*cam, *broll)
        if cuts is None:
            cuts = [(item_key(a), item_key(b)) for a, b in zip(cam, cam[1:]) if a["end"] == b["start"]]
        return solve_pushes(push_ins, p, cam, {"s1": info(label=label)},
                            rects or {k: BASE for k in keys}, budget, OPTS, cuts,
                            measures=measures or {k: m() for k in keys},
                            setup_of={k: "s1" for k in keys})


class PushTest(Pushes):
    ONE = [vid("S1_a.mov", 1, 0, 100 * FPS)]

    def test_valid_push(self):
        pushes, flags = self.solve([push(10, 15)], self.ONE)
        self.assertEqual(flags, [])
        self.assertEqual(len(pushes), 1)
        p = pushes[0]
        self.assertEqual((p["key"], p["start_frame"], p["end_frame"]), ("V1@0", 250, 375))
        self.assertEqual(p["from"], BASE)
        self.assertAlmostEqual(p["to"].w, p["from"].w / 1.06, places=9)
        self.assertLess(abs(screen_x(p["to"], EYES) - screen_x(p["from"], EYES)), 1e-9)
        self.assertEqual((p["text"], p["reason"]), ("line", "thesis"))

    def test_frames_are_item_relative(self):
        items = [vid("S1_a.mov", 1, 0, 40 * FPS), vid("S1_b.mov", 1, 40 * FPS, 100 * FPS)]
        pushes, _ = self.solve([push(50, 55)], items)
        self.assertEqual((pushes[0]["key"], pushes[0]["start_frame"]), ("V1@1000", 250))

    def test_dynamic_setup_dropped(self):
        pushes, flags = self.solve([push(10, 15)], self.ONE, label="dynamic")
        self.assertEqual(pushes, [])
        self.assertIn("dynamic", flags[0]["text"])

    def test_second_push_twenty_seconds_later_dropped(self):
        pushes, flags = self.solve([push(10, 15), push(30, 35)], self.ONE)
        self.assertEqual([p["start_frame"] for p in pushes], [250])
        self.assertEqual(len(flags), 1)
        self.assertIn("30 s", flags[0]["text"])

    def test_pushes_thirty_seconds_apart_on_separate_items_kept(self):
        items = [vid("S1_a.mov", 1, 0, 30 * FPS), vid("S1_b.mov", 1, 30 * FPS, 60 * FPS),
                 vid("S1_c.mov", 1, 60 * FPS, 100 * FPS)]
        pushes, flags = self.solve([push(5, 10), push(65, 70)], items)
        self.assertEqual(len(pushes), 2, flags)

    def test_push_under_broll_dropped(self):
        broll = [vid("screen.mov", 2, 41 * FPS, 44 * FPS, media="video")]
        pushes, flags = self.solve([push(40, 45)], self.ONE, broll=broll)
        self.assertEqual(pushes, [])
        self.assertIn("b-roll", flags[0]["text"])

    def test_broll_elsewhere_does_not_block(self):
        broll = [vid("screen.mov", 2, 50 * FPS, 54 * FPS, media="video")]
        pushes, _ = self.solve([push(40, 45)], self.ONE, broll=broll)
        self.assertEqual(len(pushes), 1)

    def test_push_across_cut_dropped(self):
        items = [vid("S1_a.mov", 1, 0, 40 * FPS), vid("S1_b.mov", 1, 40 * FPS, 100 * FPS)]
        pushes, flags = self.solve([push(38, 43)], items)
        self.assertEqual(pushes, [])
        self.assertIn("cut", flags[0]["text"])

    def test_push_on_next_item_in_run_dropped(self):
        items = [vid("S1_a.mov", 1, 0, 40 * FPS), vid("S1_b.mov", 1, 40 * FPS, 100 * FPS)]
        pushes, flags = self.solve([push(5, 10), push(70, 75)], items)
        self.assertEqual(len(pushes), 1)
        self.assertIn("in a row", flags[0]["text"])

    def test_over_budget_dropped(self):
        base = Rect(0.52, 0.30 + 0.52 / 6, 0.52)  # 1.92x: x1.06 is over 2.0
        pushes, flags = self.solve([push(10, 15)], self.ONE, rects={"V1@0": base})
        self.assertEqual(pushes, [])
        self.assertIn("budget", flags[0]["text"])

    def test_fast_movement_dropped(self):
        wide = (0.15, 0.17, 0.85, 0.80)
        pushes, flags = self.solve([push(10, 15)], self.ONE, rects={"V1@0": Rect(0.5, 0.5, 1.0)},
                                   measures={"V1@0": m(extent=wide)})
        self.assertEqual(pushes, [])
        self.assertIn("movement", flags[0]["text"])


if __name__ == "__main__":
    unittest.main()
