import contextlib
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "video-organize" / "scripts"))
sys.path.insert(0, str(ROOT / "skills" / "resolve-bridge" / "scripts"))

import organize  # noqa: E402
import rvs  # noqa: E402

FAKE = ROOT / "tests" / "fixtures" / "fake_transcriber.py"

STORYBOARD = """# Storyboard

## S01
I open the laptop and stare at the blank page.

## S02
Then I write the first line by hand, slowly.

## S03
Finally I read it out loud to the cat.

## S04
B-roll: thesaurus window
"""

S01 = "I open the laptop and stare at the blank page."
S02 = "Then I write the first line by hand, slowly."


def say(t0, text):
    """Words 0.3 s long, 0.4 s apart, starting at t0."""
    return [[w, round(t0 + i * 0.4, 3), round(t0 + i * 0.4 + 0.3, 3)] for i, w in enumerate(text.split())]


# Keyed by file length in seconds (see fixtures/fake_transcriber.py).
CANNED = {
    "20": say(1.0, S01) + say(6.0, "one more time") + say(8.0, S01) + say(13.0, "very nice") + say(15.0, S02),
    "24": say(2.0, S02) + say(10.0, "this coffee is getting cold again today honestly") + say(16.0, "okay"),
    "10": [],
}


def ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def recording(path, seconds, video="smptebars", audio="sine=f=440"):
    ff("-f", "lavfi", "-i", f"{video}=s=320x240:r=10:d={seconds}",
       "-f", "lavfi", "-i", audio,
       "-t", str(seconds), "-pix_fmt", "yuv420p", "-c:a", "aac", path)


class OrganizeCliTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._media = tempfile.TemporaryDirectory()
        cls.footage = Path(cls._media.name) / "footage"
        (cls.footage / "day1").mkdir(parents=True)
        cls.rec1 = cls.footage / "day1" / "IMG_0001.MOV"
        cls.rec2 = cls.footage / "IMG_0002.MOV"
        cls.broll = cls.footage / "day1" / "BROLL_0003.MOV"
        recording(str(cls.rec1), 20)
        recording(str(cls.rec2), 24)
        recording(str(cls.broll), 10, video="testsrc", audio="anullsrc=r=48000:cl=mono")
        (cls.footage / "._IMG_0001.MOV").write_bytes(b"")  # hidden: must be skipped
        cls.words = Path(cls._media.name) / "words.json"
        cls.words.write_text(json.dumps(CANNED))

    @classmethod
    def tearDownClass(cls):
        cls._media.cleanup()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.work = Path(self._tmp.name) / "work"
        (self.work / ".edit").mkdir(parents=True)
        self.storyboard = self.work / "storyboard.md"
        self.storyboard.write_text(STORYBOARD)
        env = mock.patch.dict(os.environ, {
            "RVS_TRANSCRIBE_CMD": f"{shlex.quote(sys.executable)} {shlex.quote(str(FAKE))}",
            "RVS_FAKE_WORDS": str(self.words)})
        env.start()
        self.addCleanup(env.stop)

    def config(self, **values):
        (self.work / ".edit" / "config.json").write_text(json.dumps(values))

    def cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = organize.main(["--workdir", str(self.work), *args])
        self.assertEqual(code, 0, out.getvalue())
        return out.getvalue()

    def run_all(self, *extra):
        return self.cli("run", "--footage", str(self.footage), *extra, "--yes")

    def takes_md(self):
        return (self.work / "TAKES.md").read_text()

    def section(self, text, heading):
        start = text.index(f"## {heading}\n")
        end = text.find("\n## ", start + 1)
        return text[start:end if end > 0 else None]

    def data(self):
        return json.loads((self.work / ".edit" / "organize" / "takes.json").read_text())

    def test_run_end_to_end(self):
        self.config(storyboard=str(self.storyboard), approve_phrases=["very nice"],
                    reject_phrases=["one more time"])
        preview = self.cli("run", "--footage", str(self.footage))
        self.assertIn("3 recordings", preview)
        self.assertIn("0 audio-only", preview)
        self.assertIn("4 scenes", preview)
        self.assertFalse((self.work / "TAKES.md").exists())

        out = self.run_all()
        md = self.takes_md()
        s01 = self.section(md, "S01")
        self.assertLess(s01.index("S01A-T02"), s01.index("S01A-T01"))  # approved before rejected
        self.assertIn("approved on set", s01)
        self.assertIn("rejected on set", s01)
        self.assertIn("S02A-T01", self.section(md, "S02"))
        self.assertIn("S02A-T02", self.section(md, "S02"))
        self.assertIn("IMG_0002.MOV", self.section(md, "S02"))
        self.assertIn("S03", self.section(md, "Scenes with no usable take"))
        self.assertIn("this coffee is getting cold", self.section(md, "Unmatched spoken clips"))
        self.assertIn("IMG_0001.MOV: 2", self.section(md, "On-set chatter"))
        self.assertIn("BROLL_0003.MOV", self.section(md, "B-roll"))
        self.assertNotIn("._IMG", md)
        checklist = (self.work / "EDIT_CHECKLIST.md").read_text()
        self.assertIn("S03", checklist)
        self.assertIn("no complete or reworded take", checklist)

        frame_dirs = [Path(l.strip()) for l in out.splitlines() if l.strip().startswith(str(self.work.resolve()))]
        self.assertEqual(len(frame_dirs), 1, out)
        self.assertTrue(list(frame_dirs[0].glob("*.jpg")))
        self.assertIn("BROLL_0003", frame_dirs[0].name)

        payload = self.work / "payload.json"
        payload.write_text(json.dumps({str(self.broll.resolve()): {
            "frames": {"2": "a thesaurus window on a laptop"},
            "matches": [{"scene": "S04", "start": 2, "end": 6, "why": "thesaurus on screen"}]}}))
        self.cli("describe", str(payload))
        broll = self.section(self.takes_md(), "B-roll")
        self.assertIn("S04", broll)
        self.assertIn("thesaurus on screen", broll)

    def test_broll_note_without_match_is_an_issue(self):
        self.config(storyboard=str(self.storyboard))
        self.run_all()
        payload = self.work / "payload.json"
        payload.write_text(json.dumps({str(self.broll.resolve()): {"frames": {"2": "a test pattern"}}}))
        self.cli("describe", str(payload))
        checklist = (self.work / "EDIT_CHECKLIST.md").read_text()
        self.assertIn("thesaurus window", checklist)

    def test_pick_and_rerun(self):
        self.run_all("--storyboard", str(self.storyboard))
        self.cli("pick", "S02A-T01")
        self.assertEqual(self.data()["picks"]["S02"]["id"], "S02A-T01")
        self.run_all("--storyboard", str(self.storyboard))
        self.assertEqual(self.data()["picks"]["S02"]["id"], "S02A-T01")
        s02 = self.section(self.takes_md(), "S02")
        first = next(l for l in s02.splitlines() if "S02A-T" in l)
        self.assertIn("S02A-T01", first)
        self.assertIn("picked by you", first)

    def test_no_storyboard_mode(self):
        self.run_all()
        md = self.takes_md()
        self.assertIn("No storyboard or script: takes are grouped by similar text and not scored for "
                      "completeness", md)
        g01 = self.section(md, "G01")
        self.assertEqual(g01.count("G01A-T"), 2)
        self.assertIn("G02", md)
        self.assertNotIn("incomplete", md)
        self.assertNotIn("reworded", md)
        self.assertNotIn("no usable take", md)
        self.assertNotIn("## S0", md)

    def test_legacy_phrases_shown_not_ranked(self):
        self.config(storyboard=str(self.storyboard), phrases=["very nice"])
        self.run_all()
        s01 = self.section(self.takes_md(), "S01")
        self.assertIn('heard after the take: "very nice"', s01)
        self.assertIn("meaning unknown", s01)
        self.assertNotIn("on set", s01)

    def test_runs_without_resolve(self):
        with mock.patch.object(rvs, "connect", side_effect=AssertionError("Resolve must not be used")):
            self.run_all("--storyboard", str(self.storyboard))
            payload = self.work / "payload.json"
            payload.write_text(json.dumps({str(self.broll.resolve()): {"frames": {"0": "test pattern"}}}))
            self.cli("describe", str(payload))
            self.cli("answer", "S02", str(self.rec2), "B")
            ids = {t["file"]: t["id"] for t in self.data()["takes"] if t["scene"] == "S02"}
            self.assertEqual(ids[str(self.rec2.resolve())], "S02B-T01")
            self.cli("pick", "S02B-T01")
            self.cli("report")
        self.assertIn("S02B-T01", self.section(self.takes_md(), "S02"))


if __name__ == "__main__":
    unittest.main()
