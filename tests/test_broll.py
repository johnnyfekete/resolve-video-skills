import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "video-organize" / "scripts"))

import broll  # noqa: E402


def ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def make_clip(path, black=0, total=10):
    """`total` s clip: first `black` s black, the rest testsrc."""
    if black:
        ff("-f", "lavfi", "-i", f"color=black:s=320x240:r=10:d={black}",
           "-f", "lavfi", "-i", f"testsrc=s=320x240:r=10:d={total - black}",
           "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]", "-map", "[v]",
           "-pix_fmt", "yuv420p", path)
    else:
        ff("-f", "lavfi", "-i", f"testsrc=s=320x240:r=10:d={total}", "-pix_fmt", "yuv420p", path)


class BrollTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_frames_every_two_seconds(self):
        clip = str(self.dir / "a.mp4")
        make_clip(clip)
        frames = broll.extract_frames(clip, self.dir / "frames")
        self.assertEqual([f["t"] for f in frames], [0, 2, 4, 6, 8])
        for f in frames:
            self.assertTrue(Path(f["image"]).exists())
            self.assertEqual(set(f), {"t", "image", "sharpness", "luma", "motion"})
        self.assertGreater(frames[1]["sharpness"], 0)
        self.assertGreater(frames[1]["luma"], 16)

    def test_dark_frames_score_low(self):
        clip = str(self.dir / "d.mp4")
        make_clip(clip, black=4)
        frames = broll.extract_frames(clip, self.dir / "frames")
        self.assertLess(frames[0]["luma"], 20)
        moments = broll.best_moments(frames)
        self.assertTrue(moments)
        self.assertGreaterEqual(moments[0]["start"], 4)
        self.assertTrue(0 <= moments[0]["score"] <= 100)

    def test_record_rejects_bad_scene(self):
        clip = self.dir / "a.mp4"
        make_clip(str(clip))
        payload = {str(clip): {"frames": {"0": "desk"},
                               "matches": [{"scene": "S99", "start": 2.0, "end": 6.0, "why": "x"}]}}
        with self.assertRaises(ValueError):
            broll.record_descriptions(self.dir, payload, {"S01", "S03"})
        self.assertFalse((self.dir / "broll.json").exists())

    def test_record_rejects_times_outside_file(self):
        clip = self.dir / "a.mp4"
        make_clip(str(clip))
        payload = {str(clip): {"frames": {}, "matches": [
            {"scene": "S03", "start": 8.0, "end": 14.0, "why": "x"}]}}
        with self.assertRaises(ValueError):
            broll.record_descriptions(self.dir, payload, {"S03"})

    def test_record_merges_across_calls(self):
        clip = self.dir / "a.mp4"
        make_clip(str(clip))
        broll.record_descriptions(self.dir, {str(clip): {"frames": {"0": "desk"}, "matches": [
            {"scene": "S03", "start": 2.0, "end": 6.0, "why": "typing"}]}}, {"S03"})
        broll.record_descriptions(self.dir, {str(clip): {"frames": {"2": "hands"}, "matches": []}}, {"S03"})
        data = json.loads((self.dir / "broll.json").read_text())
        entry = data[str(clip)]
        self.assertEqual(entry["frames"], {"0": "desk", "2": "hands"})
        self.assertEqual(len(entry["matches"]), 1)


if __name__ == "__main__":
    unittest.main()
