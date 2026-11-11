import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "video-framing" / "scripts"))

from geometry import zoom_of  # noqa: E402
from solve import Opts, solve_base  # noqa: E402

OPTS = Opts()
PLAIN = {"keep_out": [], "keep_in": [], "label": "plain", "reason": "test"}


def m(eyes=(0.52, 0.30), face_h=0.12, extent=None, joints=None, detected=1.0):
    ex, ey = eyes
    if extent is None:
        extent = (ex - 0.07, ey - 0.13, ex + 0.07, ey + 0.13)
    return {"detected": detected, "eyes": list(eyes), "face_h": face_h,
            "extent": list(extent), "joints_y": dict(joints or {})}


def decision(keep_out=(), keep_in=()):
    return {"keep_out": [list(b) for b in keep_out], "keep_in": [list(b) for b in keep_in],
            "label": "plain", "reason": "test"}


class Base(unittest.TestCase):
    def check_inside(self, res):
        self.assertTrue(res["rect"].inside_source(), res["rect"])
        for r in res["items"].values():
            self.assertTrue(r.inside_source(), r)

    def solve(self, measures, dec=PLAIN, budget=2.0, keys=None):
        res = solve_base(keys or list(measures), measures, dec, budget, OPTS)
        self.check_inside(res)
        return res


class CentringTest(Base):
    def test_centred_on_eyes_with_eyes_on_upper_third(self):
        res = self.solve({"k": m(eyes=(0.52, 0.30))})
        r = res["rect"]
        self.assertAlmostEqual(r.cx, 0.52, delta=0.002)
        self.assertAlmostEqual(0.30, r.top + r.h / 3, delta=0.01)
        self.assertEqual(res["items"]["k"], r)
        self.assertTrue(res["punch_ok"])
        self.assertEqual(res["flags"], [])

    def test_leaves_room_for_punch_and_push(self):
        r = self.solve({"k": m()})["rect"]
        self.assertLessEqual(zoom_of(r) * OPTS.punch * OPTS.push, 2.0 + 1e-6)

    def test_face_never_more_than_cap_of_frame_height(self):
        r = self.solve({"k": m(face_h=0.25)}, budget=4.0)["rect"]
        self.assertLessEqual(0.25, OPTS.face_h_max * r.h + 1e-6)


class GestureTest(Base):
    def test_hand_low_in_frame_is_inside_with_margin(self):
        res = self.solve({"k": m(eyes=(0.52, 0.30), extent=(0.35, 0.17, 0.70, 0.95))})
        r = res["rect"]
        self.assertTrue(r.bottom >= 0.95 + OPTS.gesture_margin * r.h - 1e-6 or r.w >= 1 - 1e-6, r)
        self.assertLessEqual(r.top, 0.17)


class KeepOutTest(Base):
    def test_clutter_on_left_is_removed_within_shift_allowance(self):
        res = self.solve({"k": m(eyes=(0.53, 0.30))}, decision(keep_out=[(0, 0, 0.29, 1)]), budget=2.5)
        r = res["rect"]
        self.assertGreaterEqual(r.left, 0.29 - 1e-6)
        self.assertLessEqual(abs(r.cx - 0.53), OPTS.max_shift * r.w + 1e-6)
        self.assertFalse(any("clutter" in f["text"] for f in res["flags"]))
        self.assertTrue(res["punch_ok"])

    def test_sliver_kept_rather_than_losing_punch_room(self):
        # At budget 2 clearing x<0.29 needs zoom ~1.88, leaving no room for a 1.10 punch. The gain
        # is a thin strip, so the punch room is kept and the sliver is flagged.
        res = self.solve({"k": m(eyes=(0.53, 0.30))}, decision(keep_out=[(0, 0, 0.29, 1)]))
        r = res["rect"]
        self.assertTrue(res["punch_ok"])
        self.assertGreaterEqual(2.0 * r.w, OPTS.punch_min - 1e-6)
        self.assertGreater(r.left, 0.25)  # most of the clutter still goes
        texts = [f["text"] for f in res["flags"]]
        self.assertTrue(any("clutter left in frame" in t and "punch" in t for t in texts), texts)

    def test_gesture_wins_over_clutter_and_is_flagged(self):
        ext = (0.20, 0.17, 0.66, 0.70)
        res = self.solve({"k": m(eyes=(0.53, 0.30), extent=ext)}, decision(keep_out=[(0, 0, 0.29, 1)]))
        r = res["rect"]
        self.assertTrue(r.contains(ext), r)
        self.assertLessEqual(r.left, 0.20 - 1e-6)
        self.assertLessEqual(abs(r.cx - 0.53), OPTS.max_shift * r.w + 1e-6)  # never past the allowance
        texts = [f["text"] for f in res["flags"]]
        self.assertTrue(any("clutter left in frame" in t for t in texts), texts)
        self.assertEqual(res["flags"][0]["key"], "k")


class KeepInTest(Base):
    def test_plant_half_inside_ends_fully_inside_or_outside(self):
        base = self.solve({"k": m()})["rect"]
        plant = (base.right - 0.05, 0.40, base.right + 0.05, 0.70)
        self.assertAlmostEqual(base.overlaps(plant), 0.5, places=6)
        r = self.solve({"k": m()}, decision(keep_in=[plant]))["rect"]
        ov = r.overlaps(plant)
        self.assertTrue(ov == 0.0 or r.contains(plant), (ov, r))


class JointTest(Base):
    def test_bottom_edge_moves_off_shoulder_line(self):
        base = self.solve({"k": m()})["rect"]
        y = base.bottom
        res = self.solve({"k": m(joints={"left_shoulder": y, "right_shoulder": y + 0.005})})
        r = res["rect"]
        for jy in (y, y + 0.005):
            self.assertGreaterEqual(abs(r.bottom - jy), 0.03 * r.h - 1e-6, r)


class OutlierTest(Base):
    def test_arms_overhead_item_gets_own_frame(self):
        normal = (0.36, 0.17, 0.68, 0.80)
        meas = {f"k{i}": m(eyes=(0.52, 0.30), extent=normal) for i in range(4)}
        meas["k4"] = m(eyes=(0.52, 0.30), extent=(0.22, 0.01, 0.84, 0.80))
        res = self.solve(meas)
        four = self.solve({k: meas[k] for k in ("k0", "k1", "k2", "k3")})
        self.assertAlmostEqual(res["rect"].w, four["rect"].w, places=6)
        own = res["items"]["k4"]
        self.assertGreater(own.w, res["rect"].w)
        self.assertTrue(own.contains((0.22, 0.01, 0.84, 0.80)) or own.w >= 1 - 1e-6)
        for k in ("k0", "k1", "k2", "k3"):
            self.assertEqual(res["items"][k], res["rect"])
        self.assertEqual([f["key"] for f in res["flags"]], ["k4"])


class CompositionTest(Base):
    def test_tall_head_keeps_margin_above_head_top(self):
        ext = (0.42, 0.12, 0.62, 0.50)
        r = self.solve({"k": m(eyes=(0.52, 0.36), extent=ext)})["rect"]
        self.assertLessEqual(r.top, 0.12 - OPTS.gesture_margin * r.h + 1e-6)

    def test_eye_line_off_third_is_flagged_in_hard_mode(self):
        ext = (0.40, 0.10, 0.64, 0.55)
        res = self.solve({"k": m(eyes=(0.52, 0.40), extent=ext)})
        r = res["rect"]
        self.assertLessEqual(r.top, 0.10 - OPTS.gesture_margin * r.h + 1e-6)
        frac = (0.40 - r.top) / r.h
        texts = [f["text"] for f in res["flags"]]
        self.assertEqual(texts, [f"composition: eye line at {round(100 * frac)}% of frame height"])

    def test_missing_extent_is_flagged(self):
        meas = m()
        meas["extent"] = None
        res = self.solve({"k": meas})
        self.assertTrue(any("gesture" in f["text"] for f in res["flags"] if f["key"] == "k"))


class PanFollowTest(Base):
    def test_keeps_setup_shift_and_ignores_small_drift(self):
        meas = {f"k{i}": m(eyes=(0.50, 0.30)) for i in range(3)}
        meas["k3"] = m(eyes=(0.485, 0.30))
        res = self.solve(meas, decision(keep_out=[(0, 0, 0.215, 1)]))
        r = res["rect"]
        self.assertGreater(r.cx, 0.51)  # shifted right to clear the clutter
        self.assertEqual(res["items"]["k3"], r)
        self.assertEqual(res["flags"], [])

    def test_item_eye_line_off_band_is_tilted_back(self):
        meas = {f"k{i}": m(eyes=(0.52, 0.30)) for i in range(3)}
        meas["k3"] = m(eyes=(0.52, 0.40))
        res = self.solve(meas)
        it = res["items"]["k3"]
        self.assertEqual(it.w, res["rect"].w)
        self.assertTrue(0.25 - 1e-6 <= (0.40 - it.top) / it.h <= 0.36 + 1e-6, it)
        self.assertEqual(res["flags"], [])

    def test_drifted_item_pans_at_same_zoom(self):
        meas = {f"k{i}": m(eyes=(0.50, 0.30)) for i in range(3)}
        meas["k3"] = m(eyes=(0.62, 0.30))
        res = self.solve(meas)
        r = res["rect"]
        self.assertAlmostEqual(r.cx, 0.50, delta=0.002)
        moved = res["items"]["k3"]
        self.assertEqual(moved.w, r.w)
        self.assertEqual(moved.cy, r.cy)
        self.assertAlmostEqual(moved.cx, 0.62, delta=0.002)
        for k in ("k0", "k1", "k2"):
            self.assertEqual(res["items"][k], r)


class BudgetTest(Base):
    def test_1080p_source_declutters_only_and_disables_punches(self):
        res = self.solve({"k": m()}, budget=1.0)
        self.assertLessEqual(zoom_of(res["rect"]), OPTS.declutter_cap + 1e-6)
        self.assertFalse(res["punch_ok"])

    def test_1080p_without_clutter_stays_at_zoom_1(self):
        res = self.solve({"k": m()}, budget=1.0)
        self.assertAlmostEqual(zoom_of(res["rect"]), 1.0)
        self.assertIn({"key": None, "text": "source has no zoom headroom (budget 1.00): declutter "
                                            "only, no punch-ins or push-ins"}, res["flags"])

    def test_1080p_declutters_up_to_cap_only_for_clutter(self):
        res = self.solve({"k": m(eyes=(0.53, 0.30))}, decision(keep_out=[(0, 0, 0.1, 1)]), budget=1.0)
        r = res["rect"]
        self.assertGreaterEqual(r.left, 0.1 - 1e-6)
        self.assertGreater(zoom_of(r), 1.0)
        self.assertLessEqual(zoom_of(r), OPTS.declutter_cap + 1e-6)
        self.assertFalse(any("clutter left in frame" in f["text"] for f in res["flags"]))

    def test_budget_not_gestures_blamed_when_cap_blocks_declutter(self):
        res = self.solve({"k": m(eyes=(0.50, 0.30))}, decision(keep_out=[(0, 0, 0.2, 1)]), budget=1.0)
        self.assertLessEqual(zoom_of(res["rect"]), OPTS.declutter_cap + 1e-6)
        texts = [f["text"] for f in res["flags"] if "clutter left in frame" in f["text"]]
        self.assertEqual(len(texts), 1)
        self.assertIn("budget", texts[0])
        self.assertNotIn("cutting gestures", texts[0])

    def test_small_budget_without_clutter_never_softens(self):
        res = self.solve({"k": m()}, budget=1.2)
        self.assertLessEqual(zoom_of(res["rect"]), 1.2 + 1e-6)
        self.assertFalse(res["punch_ok"])

    def test_headroom_flag_absent_when_budget_allows_punches(self):
        res = self.solve({"k": m()}, budget=2.0)
        self.assertFalse(any(f["key"] is None for f in res["flags"]))


class WeakDetectionTest(Base):
    def test_weak_item_gets_setup_rect_and_flag(self):
        meas = {"k0": m(), "k1": m(), "weak": m(eyes=(0.2, 0.6), detected=0.3)}
        res = self.solve(meas)
        self.assertEqual(res["items"]["weak"], res["rect"])
        self.assertAlmostEqual(res["rect"].cx, 0.52, delta=0.002)
        self.assertEqual([f["key"] for f in res["flags"]], ["weak"])

    def test_missing_measure_counts_as_weak(self):
        res = self.solve({"k0": m()}, keys=["k0", "gone"])
        self.assertEqual(res["items"]["gone"], res["rect"])
        self.assertEqual([f["key"] for f in res["flags"]], ["gone"])


if __name__ == "__main__":
    unittest.main()
