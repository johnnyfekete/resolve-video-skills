import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "video-framing" / "scripts"))

import geometry  # noqa: E402
from geometry import Rect, props_to_rect, rect_to_props, zoom_budget, zoom_of  # noqa: E402


class RectTest(unittest.TestCase):
    def test_inside_source(self):
        self.assertTrue(Rect(0.5, 0.5, 1.0).inside_source())
        self.assertFalse(Rect(0.3, 0.5, 0.8).inside_source())

    def test_h_equals_w_at_matching_aspect(self):
        r = Rect(0.5, 0.5, 0.6)
        self.assertAlmostEqual(r.h, 0.6)
        self.assertAlmostEqual(r.left, 0.2)
        self.assertAlmostEqual(r.bottom, 0.8)

    def test_scaled_keeps_anchor_screen_position(self):
        r = Rect(0.45, 0.5, 0.7)
        eye = (0.4, 0.42)
        s = r.scaled(1.15, anchor=eye)
        self.assertAlmostEqual(s.w, 0.7 / 1.15)
        self.assertAlmostEqual((eye[0] - s.left) / s.w, (eye[0] - r.left) / r.w, places=9)
        self.assertAlmostEqual((eye[1] - s.top) / s.h, (eye[1] - r.top) / r.h, places=9)

    def test_contains_and_overlaps(self):
        r = Rect(0.5, 0.5, 0.5)  # 0.25..0.75
        self.assertTrue(r.contains((0.3, 0.3, 0.7, 0.7)))
        self.assertFalse(r.contains((0.2, 0.3, 0.7, 0.7)))
        self.assertAlmostEqual(r.overlaps((0.3, 0.3, 0.7, 0.7)), 1.0)
        self.assertAlmostEqual(r.overlaps((0.65, 0.4, 0.85, 0.6)), 0.5)
        self.assertEqual(r.overlaps((0.8, 0.8, 0.9, 0.9)), 0.0)

    def test_zoom(self):
        self.assertAlmostEqual(zoom_of(Rect(0.5, 0.5, 0.8)), 1.25)
        self.assertEqual(zoom_budget(2160, 1080), 2.0)


class PropsTest(unittest.TestCase):
    def test_full_frame(self):
        self.assertEqual(rect_to_props(Rect(0.5, 0.5, 1.0), 1920, 1080),
                         {"ZoomX": 1.0, "ZoomY": 1.0, "Pan": 0.0, "Tilt": 0.0})

    def test_hand_computed(self):
        # Zoom = 1/0.6; Pan = 0.1 * 1920 * Zoom = 320; Tilt = -0.05 * 1080 * Zoom = -90
        p = rect_to_props(Rect(0.40, 0.45, 0.60), 1920, 1080)
        self.assertEqual(p, {"ZoomX": 1.6667, "ZoomY": 1.6667, "Pan": 320.0, "Tilt": -90.0})

    def test_round_trip(self):
        for r in (Rect(0.40, 0.45, 0.60), Rect(0.62, 0.38, 0.75), Rect(0.30, 0.65, 0.50)):
            back = props_to_rect(rect_to_props(r, 1920, 1080), 1920, 1080)
            for a in ("cx", "cy", "w"):
                self.assertAlmostEqual(getattr(back, a), getattr(r, a), places=3)

    def test_source_size_matching_aspect_agrees(self):
        r = Rect(0.62, 0.38, 0.75)
        self.assertEqual(rect_to_props(r, 1920, 1080),
                         rect_to_props(r, 1920, 1080, 3840, 2160))

    def test_4_3_full_width_is_zoom_one(self):
        r = Rect(0.5, 0.5, 1.0, aspect=4 / 3)
        p = rect_to_props(r, 1920, 1080, 1440, 1080)
        self.assertEqual((p["ZoomX"], p["Pan"], p["Tilt"]), (1.0, 0.0, 0.0))

    def test_4_3_round_trip(self):
        r = Rect(0.45, 0.4, 0.7, aspect=4 / 3)
        back = props_to_rect(rect_to_props(r, 1920, 1080, 1440, 1080), 1920, 1080, 1440, 1080)
        for a in ("cx", "cy", "w"):
            self.assertAlmostEqual(getattr(back, a), getattr(r, a), places=3)
        self.assertAlmostEqual(back.aspect, 4 / 3)


if __name__ == "__main__":
    unittest.main()
