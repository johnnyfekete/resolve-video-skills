import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "video-framing" / "scripts"))

from measure import summarize  # noqa: E402

HEAD = [0.40, 0.20, 0.60, 0.50]
FACE = [0.42, 0.25, 0.58, 0.45]


def frame(eyes=(0.5, 0.35), face=FACE, head=HEAD, hands=(), joints=None):
    if face is None:
        return {"face": None, "eyes": None, "head": None, "hands": [list(h) for h in hands], "joints": joints or {}}
    return {"face": list(face), "eyes": list(eyes), "head": list(head), "hands": [list(h) for h in hands],
            "joints": joints if joints is not None else {"neck": [0.5, 0.6], "left_wrist": [0.3, 0.8]}}


class SummarizeTest(unittest.TestCase):
    def test_median_eyes_ignores_outlier(self):
        frames = [frame(eyes=(0.50, 0.35)) for _ in range(10)] + [frame(eyes=(0.90, 0.35))]
        s = summarize(frames)
        self.assertAlmostEqual(s["eyes"][0], 0.50)
        self.assertAlmostEqual(s["eyes"][1], 0.35)
        self.assertAlmostEqual(s["face_h"], 0.20)
        self.assertAlmostEqual(s["joints_y"]["neck"], 0.6)
        self.assertAlmostEqual(s["joints_y"]["left_wrist"], 0.8)

    def test_single_hand_frame_dropped_from_extent(self):
        frames = [frame() for _ in range(59)] + [frame(hands=[(0.02, 0.60, 0.10, 0.70)])]
        s = summarize(frames)
        self.assertAlmostEqual(s["extent"][0], HEAD[0])
        self.assertAlmostEqual(s["extent"][1], HEAD[1])
        self.assertAlmostEqual(s["extent"][2], HEAD[2])
        self.assertAlmostEqual(s["extent"][3], HEAD[3])

    def test_frequent_hands_in_extent(self):
        hand = (0.10, 0.60, 0.20, 0.75)
        frames = [frame(hands=[hand]) for _ in range(20)] + [frame() for _ in range(40)]
        s = summarize(frames)
        self.assertAlmostEqual(s["extent"][0], 0.10)
        self.assertAlmostEqual(s["extent"][1], HEAD[1])
        self.assertAlmostEqual(s["extent"][2], HEAD[2])
        self.assertAlmostEqual(s["extent"][3], 0.75)

    def test_detected_fraction_uses_only_detected_frames(self):
        far = (0.01, 0.01, 0.05, 0.05)
        frames = [frame(eyes=(0.30, 0.40)) for _ in range(3)] + \
                 [frame(face=None, hands=[far], joints={"neck": [0.9, 0.95]}) for _ in range(7)]
        s = summarize(frames)
        self.assertAlmostEqual(s["detected"], 0.3)
        self.assertAlmostEqual(s["eyes"][0], 0.30)
        self.assertAlmostEqual(s["eyes"][1], 0.40)
        self.assertEqual([round(v, 6) for v in s["extent"]], HEAD)
        self.assertAlmostEqual(s["joints_y"]["neck"], 0.6)

    def test_no_detections(self):
        s = summarize([frame(face=None) for _ in range(5)])
        self.assertEqual(s["detected"], 0.0)
        self.assertIsNone(s["eyes"])
        self.assertIsNone(s["face_h"])
        self.assertIsNone(s["extent"])
        self.assertEqual(s["joints_y"], {})

    def test_empty_input(self):
        s = summarize([])
        self.assertEqual(s["detected"], 0.0)
        self.assertIsNone(s["eyes"])


if __name__ == "__main__":
    unittest.main()
