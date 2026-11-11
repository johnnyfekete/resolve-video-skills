import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "video-setup" / "scripts"))
sys.path.insert(0, str(ROOT / "skills" / "resolve-bridge" / "scripts"))

import setup  # noqa: E402


def run_setup(workdir, *argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = setup.main(["--workdir", str(workdir), *argv])
    return code, out.getvalue()


class SetupConfigTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def config(self):
        return json.loads((self.dir / ".edit" / "config.json").read_text())

    def agents(self):
        return (self.dir / "AGENTS.md").read_text()

    def test_phrase_lists_saved(self):
        code, _ = run_setup(self.dir, "write", "--approve-phrases", "very nice",
                            "--reject-phrases", "one more time", "again")
        self.assertEqual(code, 0)
        cfg = self.config()
        self.assertEqual(cfg["approve_phrases"], ["very nice"])
        self.assertEqual(cfg["reject_phrases"], ["one more time", "again"])
        self.assertIn("`very nice`", self.agents())
        self.assertIn("one more time, again", self.agents())

    def test_storyboard_saved(self):
        run_setup(self.dir, "write", "--storyboard", "storyboard.md")
        self.assertEqual(self.config()["storyboard"], "storyboard.md")
        self.assertIn("**Storyboard:** `storyboard.md`", self.agents())

    def test_legacy_phrases_kept(self):
        (self.dir / ".edit").mkdir()
        (self.dir / ".edit" / "config.json").write_text(json.dumps({"phrases": ["good one"]}))
        run_setup(self.dir, "write", "--approve-phrases", "very nice")
        self.assertEqual(self.config()["phrases"], ["good one"])
        self.assertIn("**Verdict phrases (meaning unknown):** `good one`", self.agents())

    def test_write_without_timeline(self):
        code, out = run_setup(self.dir, "write")
        self.assertEqual(code, 0)
        state = json.loads((self.dir / ".edit" / "state.json").read_text())
        self.assertEqual(state["current"]["version"], 0)
        self.assertIsNone(state["current"]["timeline"])
        checklist = (self.dir / "EDIT_CHECKLIST.md").read_text()
        self.assertIn("organize", checklist)
        self.assertIn("video-organize", checklist)

    def test_write_twice_keeps_state(self):
        run_setup(self.dir, "write", "--skip", "assemble=later")
        run_setup(self.dir, "write")
        state = json.loads((self.dir / ".edit" / "state.json").read_text())
        self.assertIn("assemble", state["skipped"])

    def test_agents_rename_rule(self):
        run_setup(self.dir, "write")
        text = self.agents()
        self.assertIn("Only rename source footage through `video-organize`, after the user approves the list. "
                      "Never move or overwrite it.", text)
        self.assertNotIn("Never move, rename or overwrite source footage.", text)

    def test_check_warns_without_storyboard(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rep = setup.Report()
            setup.check_storyboard(rep, {})
            self.assertEqual(rep.warned, 1)
            rep = setup.Report()
            setup.check_storyboard(rep, {"storyboard": "sb.md"})
            self.assertEqual(rep.warned, 0)
        self.assertIn("WARN  No storyboard or script recorded", out.getvalue())


if __name__ == "__main__":
    unittest.main()
