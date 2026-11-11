import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "video-organize" / "scripts"))
sys.path.insert(0, str(ROOT / "skills" / "resolve-bridge" / "scripts"))

import rvs_audio  # noqa: E402
import transcribe  # noqa: E402


def ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def tone_silence(path, total, tone=8, gap=2):
    """total seconds of `tone` s sine / `gap` s digital silence, repeating."""
    period = tone + gap
    ff("-f", "lavfi", "-i", f"sine=f=440:r=16000:d={total}", "-af",
       f"volume=0:enable='gte(mod(t,{period}),{tone})'", "-ac", "1", path)


class SilenceCutsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_silence_cuts_land_in_silence(self):
        p = str(self.dir / "a.wav")
        tone_silence(p, 100)
        cuts = rvs_audio.silence_cuts(p)
        self.assertGreater(len(cuts), 2)
        self.assertEqual(cuts[0][0], 0.0)
        self.assertAlmostEqual(cuts[-1][1], rvs_audio.probe_duration(p), places=2)
        for (a, b), (c, d) in zip(cuts, cuts[1:]):
            self.assertEqual(b, c)
            self.assertLessEqual(b - a, 45.0)
            self.assertTrue(8.0 <= b % 10 <= 10.0, f"cut {b} not inside a silence")

    def test_silence_cuts_hard_cut(self):
        p = str(self.dir / "b.wav")
        ff("-f", "lavfi", "-i", "sine=f=440:d=60", p)
        cuts = rvs_audio.silence_cuts(p, max_len=45)
        self.assertLessEqual(cuts[0][1], 45.0)
        self.assertEqual(cuts[-1][1], round(rvs_audio.probe_duration(p), 3))


class TranscribeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.cache = self.dir / "cache"
        self.wav = str(self.dir / "t.wav")
        tone_silence(self.wav, 100)

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def echo(calls=None):
        def run(slices):
            if calls is not None:
                calls.append(len(slices))
            return [{"index": s["index"], "language": "en",
                     "segments": [{"text": "hi", "start": 1.0, "end": 1.5}],
                     "words": [{"w": "hi", "start": 1.0, "end": 1.5, "p": 0.9}]} for s in slices]
        return run

    def test_cache_reused(self):
        calls = []
        transcribe.transcribe(self.wav, self.cache, runner=self.echo(calls))
        self.assertEqual(len(calls), 1)
        transcribe.transcribe(self.wav, self.cache, runner=self.echo(calls))
        self.assertEqual(len(calls), 1)
        st = os.stat(self.wav)
        os.utime(self.wav, (st.st_atime, st.st_mtime + 5))
        transcribe.transcribe(self.wav, self.cache, runner=self.echo(calls))
        self.assertEqual(len(calls), 2)

    def test_times_are_absolute(self):
        cuts = rvs_audio.silence_cuts(self.wav)
        res = transcribe.transcribe(self.wav, self.cache, runner=self.echo())
        self.assertAlmostEqual(res["words"][1]["start"], cuts[1][0] + 1.0, places=2)
        self.assertAlmostEqual(res["segments"][1]["end"], cuts[1][0] + 1.5, places=2)
        self.assertEqual(res["language"], "en")
        self.assertEqual(res["segments"][0]["language"], "en")

    def test_repeats_survive(self):
        long = str(self.dir / "long.wav")
        tone_silence(long, 170)
        n = len(rvs_audio.silence_cuts(long))
        res = transcribe.transcribe(long, self.cache, runner=self.echo())
        self.assertGreaterEqual(n, 4)
        self.assertEqual(len(res["words"]), n)
        self.assertEqual({w["w"] for w in res["words"]}, {"hi"})

    def test_missing_slice_result_raises(self):
        def drop_one(slices):
            return self.echo()(slices)[:-1]
        with self.assertRaises(RuntimeError):
            transcribe.transcribe(self.wav, self.cache, runner=drop_one)
        self.assertFalse(list(self.cache.glob("*.json")) if self.cache.exists() else [])

    def test_out_of_order_results_are_matched_by_index(self):
        cuts = rvs_audio.silence_cuts(self.wav)
        res = transcribe.transcribe(self.wav, self.cache, runner=lambda s: self.echo()(s)[::-1])
        self.assertAlmostEqual(res["words"][1]["start"], cuts[1][0] + 1.0, places=2)

    def test_cache_respects_stream(self):
        out = str(self.dir / "two2.mkv")
        ff("-f", "lavfi", "-i", "sine=f=440:d=5", "-f", "lavfi", "-i", "sine=f=880:d=5",
           "-map", "0:a", "-map", "1:a", "-ac", "2", out)
        calls = []
        transcribe.transcribe(out, self.cache, stream=0, runner=self.echo(calls))
        transcribe.transcribe(out, self.cache, stream=1, runner=self.echo(calls))
        self.assertEqual(len(calls), 2)
        self.assertEqual(transcribe.transcribe(out, self.cache, stream=1, runner=self.echo(calls))["stream"], 1)
        self.assertEqual(len(calls), 2)

    def test_default_runner_isolated_from_caller_project(self):
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"], seen["cwd"] = cmd, kw.get("cwd")
            Path(cmd[-1]).write_text("[]")
        env = {k: v for k, v in os.environ.items() if k != "RVS_TRANSCRIBE_CMD"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(transcribe.shutil, "which", return_value="/usr/bin/uv"), \
                mock.patch.object(transcribe.subprocess, "run", fake_run):
            transcribe.default_runner([])
        self.assertIn("--no-project", seen["cmd"])
        self.assertIsNotNone(seen["cwd"])
        self.assertNotEqual(os.path.realpath(seen["cwd"]), os.path.realpath(os.getcwd()))

    def test_multiple_streams_warn(self):
        out = str(self.dir / "two.mkv")
        ff("-f", "lavfi", "-i", "sine=f=440:d=5", "-f", "lavfi", "-i", "sine=f=880:d=5",
           "-map", "0:a", "-map", "1:a", "-ac", "2", out)
        res = transcribe.transcribe(out, self.cache, runner=self.echo())
        self.assertIn("2 audio streams", res["warning"])
        one = transcribe.transcribe(self.wav, self.cache, runner=self.echo())
        self.assertNotIn("warning", one)


if __name__ == "__main__":
    unittest.main()
