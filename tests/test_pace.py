import argparse
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "video-tighten" / "scripts"))
sys.path.insert(0, str(ROOT / "skills" / "video-setup" / "scripts"))
sys.path.insert(0, str(ROOT / "skills" / "resolve-bridge" / "scripts"))

import rvs  # noqa: E402
import setup  # noqa: E402
import tighten  # noqa: E402


def opts(pace=None, pause_max=None, pause_keep=None):
    return argparse.Namespace(pace=pace, pause_max=pause_max, pause_keep=pause_keep)


class PaceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def save(self, cfg):
        (self.dir / ".edit").mkdir(exist_ok=True)
        (self.dir / ".edit" / "config.json").write_text(json.dumps(cfg))

    def setup_write(self, *argv):
        with contextlib.redirect_stdout(io.StringIO()):
            return setup.main(["--workdir", str(self.dir), "write", *argv])

    def test_default_is_natural(self):
        o = opts()
        self.assertEqual(tighten.resolve_pace(o, self.dir), "default")
        self.assertEqual((o.pace, o.pause_max, o.pause_keep), ("natural", 0.75, 0.3))

    def test_project_setting_used(self):
        self.save({"pace": "brisk"})
        o = opts()
        self.assertEqual(tighten.resolve_pace(o, self.dir), "project setting")
        self.assertEqual((o.pause_max, o.pause_keep), (0.5, 0.25))

    def test_option_beats_project_setting(self):
        self.save({"pace": "brisk"})
        o = opts(pace="snappy")
        tighten.resolve_pace(o, self.dir)
        self.assertEqual((o.pause_max, o.pause_keep), (0.35, 0.18))

    def test_explicit_values_beat_pace(self):
        self.save({"pace": "snappy"})
        o = opts(pause_keep=0.4)
        tighten.resolve_pace(o, self.dir)
        self.assertEqual((o.pause_max, o.pause_keep), (0.35, 0.4))

    def test_unknown_saved_pace_falls_back(self):
        self.save({"pace": "turbo"})
        o = opts()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(tighten.resolve_pace(o, self.dir), "default")
        self.assertEqual(o.pace, rvs.DEFAULT_PACE)

    def test_presets_get_faster(self):
        values = [rvs.PACES[k] for k in ("natural", "brisk", "snappy")]
        for slow, fast in zip(values, values[1:]):
            self.assertGreater(slow["pause_max"], fast["pause_max"])
            self.assertGreater(slow["pause_keep"], fast["pause_keep"])
        for v in values:
            self.assertGreater(v["pause_max"], v["pause_keep"])

    def test_setup_saves_pace(self):
        self.assertEqual(self.setup_write("--pace", "brisk"), 0)
        self.assertEqual(json.loads((self.dir / ".edit" / "config.json").read_text())["pace"], "brisk")
        self.assertIn("**Pace:** `brisk`", (self.dir / "AGENTS.md").read_text())

    def test_setup_rejects_unknown_pace(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.setup_write("--pace", "turbo")

    def test_setup_keeps_pace_when_not_given(self):
        self.setup_write("--pace", "snappy")
        self.setup_write("--delivery", "1080p")
        self.assertEqual(json.loads((self.dir / ".edit" / "config.json").read_text())["pace"], "snappy")


if __name__ == "__main__":
    unittest.main()
