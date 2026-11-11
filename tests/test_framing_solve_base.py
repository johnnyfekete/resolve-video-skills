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
        res = self.solve({"k": m(eyes=(0.53, 0.30))}, decision(keep_out=[(0, 0, 0.29, 1)]))
        r = res["rect"]
        self.assertGreaterEqual(r.left, 0.29 - 1e-6)
        self.assertLessEqual(abs(r.cx - 0.53), OPTS.max_shift * r.w + 1e-6)
        self.assertFalse(any("clutter" in f["text"] for f in res["flags"]))

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


class PanFollowTest(Base):
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

    def test_small_budget_caps_declutter_at_cap(self):
        res = self.solve({"k": m()}, decision(keep_out=[(0, 0, 0.2, 1)]), budget=1.2)
        self.assertLessEqual(zoom_of(res["rect"]), OPTS.declutter_cap + 1e-6)
        self.assertFalse(res["punch_ok"])


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
