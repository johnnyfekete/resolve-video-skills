import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "video-framing" / "scripts"))

import measure  # noqa: E402
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
        near = {"neck": [0.5, 0.6], "left_wrist": [0.06, 0.70]}
        frames = [frame() for _ in range(59)] + [frame(hands=[(0.02, 0.60, 0.10, 0.70)], joints=near)]
        s = summarize(frames)
        self.assertAlmostEqual(s["extent"][0], HEAD[0])
        self.assertAlmostEqual(s["extent"][1], HEAD[1])
        self.assertAlmostEqual(s["extent"][2], HEAD[2])
        self.assertAlmostEqual(s["extent"][3], HEAD[3])

    def test_frequent_hands_in_extent(self):
        hand = (0.10, 0.60, 0.20, 0.75)
        near = {"neck": [0.5, 0.6], "left_wrist": [0.15, 0.65]}
        frames = [frame(hands=[hand], joints=near) for _ in range(20)] + [frame() for _ in range(40)]
        s = summarize(frames)
        self.assertAlmostEqual(s["extent"][0], 0.10)
        self.assertAlmostEqual(s["extent"][1], HEAD[1])
        self.assertAlmostEqual(s["extent"][2], HEAD[2])
        self.assertAlmostEqual(s["extent"][3], 0.75)

    def test_hands_resting_at_the_source_bottom_do_not_set_the_bottom(self):
        rest = [(0.20, 0.85, 0.30, 0.99), (0.70, 0.85, 0.80, 1.0)]
        wrists = {"neck": [0.5, 0.6], "left_wrist": [0.25, 0.88], "right_wrist": [0.75, 0.88]}
        s = summarize([frame(hands=rest, joints=wrists) for _ in range(20)])
        self.assertEqual([round(v, 6) for v in s["extent"]], [0.20, HEAD[1], 0.80, HEAD[3]])

    def test_hands_ending_just_above_the_source_bottom_count_as_resting(self):
        rest = [(0.20, 0.80, 0.30, 0.94), (0.70, 0.82, 0.80, 0.93)]
        wrists = {"neck": [0.5, 0.6], "left_wrist": [0.25, 0.85], "right_wrist": [0.75, 0.85]}
        s = summarize([frame(hands=rest, joints=wrists) for _ in range(20)])
        self.assertEqual([round(v, 6) for v in s["extent"]], [0.20, HEAD[1], 0.80, HEAD[3]])

    def test_hand_ending_below_the_threshold_still_sets_the_bottom(self):
        low = (0.65, 0.78, 0.75, 0.92)
        wrists = {"neck": [0.5, 0.6], "right_wrist": [0.70, 0.82]}
        s = summarize([frame(hands=[low], joints=wrists) for _ in range(20)])
        self.assertAlmostEqual(s["extent"][3], 0.92)

    def test_raised_hand_still_sets_the_bottom_next_to_resting_ones(self):
        rest = (0.20, 0.85, 0.30, 0.99)
        up = (0.65, 0.55, 0.75, 0.70)
        wrists = {"neck": [0.5, 0.6], "left_wrist": [0.25, 0.88], "right_wrist": [0.70, 0.66]}
        s = summarize([frame(hands=[rest, up], joints=wrists) for _ in range(20)])
        self.assertEqual([round(v, 6) for v in s["extent"]], [0.20, HEAD[1], 0.75, 0.70])

    def test_detected_fraction_uses_only_detected_frames(self):
        frames = [frame(eyes=(0.30, 0.40)) for _ in range(3)] + \
                 [frame(face=None, joints={"neck": [0.9, 0.95]}) for _ in range(7)]
        s = summarize(frames)
        self.assertAlmostEqual(s["detected"], 0.3)
        self.assertAlmostEqual(s["eyes"][0], 0.30)
        self.assertAlmostEqual(s["eyes"][1], 0.40)
        self.assertEqual([round(v, 6) for v in s["extent"]], HEAD)
        self.assertAlmostEqual(s["joints_y"]["neck"], 0.6)

    def test_hands_in_faceless_frames_count_for_extent(self):
        hand = (0.02, 0.55, 0.12, 0.70)
        frames = [frame() for _ in range(10)] + [frame(face=None, hands=[hand]) for _ in range(5)]
        s = summarize(frames)
        self.assertAlmostEqual(s["detected"], 10 / 15)
        self.assertAlmostEqual(s["extent"][0], 0.02)
        self.assertAlmostEqual(s["extent"][3], 0.70)
        self.assertAlmostEqual(s["eyes"][0], 0.5)

    def test_background_hand_far_from_wrists_dropped(self):
        joints = {"neck": [0.5, 0.6], "left_wrist": [0.35, 0.7], "right_wrist": [0.65, 0.7]}
        own = (0.30, 0.65, 0.38, 0.75)
        stranger = (0.90, 0.50, 0.98, 0.60)
        s = summarize([frame(hands=[own, stranger], joints=joints) for _ in range(30)])
        self.assertAlmostEqual(s["extent"][0], 0.30)
        self.assertAlmostEqual(s["extent"][2], HEAD[2])
        self.assertAlmostEqual(s["extent"][3], 0.75)

    def test_without_wrists_hand_must_be_near_face(self):
        near = (0.60, 0.40, 0.70, 0.50)   # centre 0.2 from the face centre
        far = (0.92, 0.80, 0.99, 0.95)    # centre well over 0.5 away
        s = summarize([frame(hands=[near, far], joints={"neck": [0.5, 0.6]}) for _ in range(30)])
        self.assertAlmostEqual(s["extent"][2], 0.70)
        self.assertAlmostEqual(s["extent"][3], HEAD[3])

    # A gesturing hand 0.35 from the face centre (0.5, 0.35); its own wrist is not always found.
    GESTURE = (0.81, 0.31, 0.89, 0.39)

    def test_face_one_wrist_gesture_kept(self):
        joints = {"neck": [0.5, 0.6], "left_wrist": [0.2, 0.8]}
        s = summarize([frame(hands=[self.GESTURE], joints=joints) for _ in range(20)])
        self.assertAlmostEqual(s["extent"][2], 0.89)

    def test_no_face_one_wrist_gesture_kept(self):
        joints = {"neck": [0.5, 0.6], "left_wrist": [0.2, 0.8]}
        frames = [frame() for _ in range(10)] + \
                 [frame(face=None, hands=[self.GESTURE], joints=joints) for _ in range(10)]
        s = summarize(frames)
        self.assertAlmostEqual(s["extent"][2], 0.89)

    def test_two_wrists_gesture_near_its_wrist_kept(self):
        joints = {"neck": [0.5, 0.6], "left_wrist": [0.2, 0.8], "right_wrist": [0.84, 0.38]}
        s = summarize([frame(hands=[self.GESTURE], joints=joints) for _ in range(20)])
        self.assertAlmostEqual(s["extent"][2], 0.89)

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


class MeasureItemsTest(unittest.TestCase):
    """measure_items with the helper and ffmpeg stubbed out."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.src = self.dir / "clip.mov"
        self.src.write_bytes(b"x" * 10)
        self.calls = []
        self.n_frames = 3
        self._saved = (measure.helper_path, measure.extract_frames, measure.probe_frames)

        def fake_extract(path, t0, t1, out_dir, fps=4.0):
            self.calls.append((str(path), t0, t1))
            return [Path(out_dir) / f"f_{i}.jpg" for i in range(self.n_frames)]

        measure.helper_path = lambda cache: Path("/nonexistent/face_probe")
        measure.extract_frames = fake_extract
        measure.probe_frames = lambda binary, images: [frame() for _ in images]

    def tearDown(self):
        measure.helper_path, measure.extract_frames, measure.probe_frames = self._saved
        self.tmp.cleanup()

    def item(self, **kw):
        d = {"path": str(self.src), "clip": "clip.mov", "media": "both", "track": 1,
             "start": 0, "end": 24, "src_start": 120, "src_end": 240, "src_fps": 60.0}
        d.update(kw)
        return d

    def run_items(self, items):
        return measure.measure_items({"timeline": {"fps": 24.0}}, items, self.dir / "work")

    def test_range_comes_from_src_end(self):
        # 24 timeline frames at 24 fps = 1 s, but the retimed item shows 2 s of source.
        out = self.run_items([self.item()])
        self.assertEqual(self.calls, [(str(self.src), 2.0, 4.0)])
        self.assertAlmostEqual(out["V1@0"]["eyes"][0], 0.5)

    def test_progress_per_item(self):
        seen = []
        items = [self.item(), self.item(start=24, end=48, src_start=240, src_end=360)]
        measure.measure_items({"timeline": {"fps": 24.0}}, items[:1], self.dir / "work")
        measure.measure_items({"timeline": {"fps": 24.0}}, items, self.dir / "work",
                              progress=lambda n, total, it, cached: seen.append((n, total, it["start"], cached)))
        self.assertEqual(seen, [(1, 2, 0, True), (2, 2, 24, False)])

    def test_cached_items_are_skipped(self):
        self.run_items([self.item()])
        self.run_items([self.item()])
        self.assertEqual(len(self.calls), 1)

    def test_changed_file_invalidates_cache(self):
        self.run_items([self.item()])
        st = self.src.stat()
        os.utime(self.src, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        self.run_items([self.item()])
        self.assertEqual(len(self.calls), 2)

    def test_version_bump_invalidates_cache(self):
        self.run_items([self.item()])
        saved = measure.MEASURE_VERSION
        measure.MEASURE_VERSION = saved + 1
        try:
            self.run_items([self.item()])
        finally:
            measure.MEASURE_VERSION = saved
        self.assertEqual(len(self.calls), 2)

    def test_zero_frames_not_cached(self):
        self.n_frames = 0
        out = self.run_items([self.item()])
        self.assertEqual(out["V1@0"]["detected"], 0.0)
        self.assertEqual(list((self.dir / "work" / ".edit" / "framing" / "measure").glob("*.json")), [])

    def test_item_without_path_names_the_item(self):
        with self.assertRaises(RuntimeError) as cm:
            self.run_items([self.item(path=None, clip="S07_take.mov", start=48)])
        self.assertIn("V1@48", str(cm.exception))
        self.assertIn("S07_take.mov", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
