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

    def test_rerun_with_larger_interval_has_no_stale_frames(self):
        clip = str(self.dir / "a.mp4")
        make_clip(clip)
        out = self.dir / "frames"
        self.assertEqual(len(broll.extract_frames(clip, out)), 5)
        frames = broll.extract_frames(clip, out, every=5.0)
        self.assertEqual([f["t"] for f in frames], [0, 5])
        self.assertEqual(len(list(out.rglob("*.jpg"))), 2)

    def test_same_stem_files_do_not_mix(self):
        a, b = str(self.dir / "a.mov"), str(self.dir / "a.mp4")
        make_clip(a, total=10)
        make_clip(b, total=4)
        out = self.dir / "frames"
        fa = broll.extract_frames(a, out)
        fb = broll.extract_frames(b, out)
        self.assertEqual(len(fa), 5)
        self.assertEqual(len(fb), 2)
        self.assertTrue({f["image"] for f in fa}.isdisjoint(f["image"] for f in fb))

    def test_shake_measured_against_next_frame(self):
        still, moving = str(self.dir / "s.mp4"), str(self.dir / "m.mp4")
        ff("-f", "lavfi", "-i", "color=gray:s=320x240:r=10:d=6", "-pix_fmt", "yuv420p", still)
        ff("-f", "lavfi", "-i", "testsrc2=s=320x240:r=10:d=6", "-vf", "rotate=t*3:fillcolor=gray",
           "-pix_fmt", "yuv420p", moving)
        ms = [f["motion"] for f in broll.extract_frames(still, self.dir / "f1")]
        mm = [f["motion"] for f in broll.extract_frames(moving, self.dir / "f2")]
        self.assertLess(max(ms), 0.5)
        self.assertGreater(min(mm[:-1]), 5 * max(ms) + 5)


def frame(t, sharp=10.0, luma=120.0, motion=0.0):
    return {"t": t, "image": "x.jpg", "sharpness": sharp, "luma": luma, "motion": motion}


class BestMomentsTest(unittest.TestCase):
    def test_shaky_run_excluded_or_lower(self):
        steady = [frame(t) for t in (0, 2, 4)]
        mild = [frame(t, motion=broll.MAX_MOTION / 2) for t in (0, 2, 4)]
        self.assertGreater(broll.best_moments(steady)[0]["score"], broll.best_moments(mild)[0]["score"])
        shaky = [frame(t, motion=broll.MAX_MOTION + 1) for t in (0, 2, 4)]
        self.assertEqual(broll.best_moments(shaky), [])

    def test_blurry_frames_excluded(self):
        frames = [frame(0, sharp=1), frame(2, sharp=1), frame(4), frame(6), frame(8)]
        m = broll.best_moments(frames)
        self.assertEqual((m[0]["start"], m[0]["end"]), (4, 10))

    def test_min_len_cutoff(self):
        frames = [frame(0), frame(2, sharp=1), frame(4, sharp=1), frame(6), frame(8), frame(10, sharp=1)]
        # runs: [0,2) is 2 s (too short), [6,10) is 4 s
        m = broll.best_moments(frames)
        self.assertEqual([(r["start"], r["end"]) for r in m], [(6, 10)])
        self.assertEqual(len(broll.best_moments(frames, min_len=2.0)), 2)


if __name__ == "__main__":
    unittest.main()
