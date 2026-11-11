import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "resolve-bridge" / "scripts"))

import rvs  # noqa: E402
import rvs_checklist  # noqa: E402


class BridgeStateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def session(self):
        return rvs.Session(self.dir, connect_now=False)

    def test_init_state_without_timeline(self):
        s = self.session()
        state = s.init_state("Demo")
        self.assertEqual(state["current"], {"version": 0, "timeline": None, "plan": None})
        self.assertIsNone(state["resolve_project"])
        self.assertEqual(state["base_name"], "Demo")
        self.assertEqual(state["archive_folder"], "Archive")
        on_disk = json.loads((self.dir / ".edit" / "state.json").read_text())
        self.assertEqual(on_disk["current"]["version"], 0)
        issue = s.add_issue("organize", "S04 has no take")
        self.assertEqual(issue["id"], "I1")
        text = (self.dir / "EDIT_CHECKLIST.md").read_text()
        self.assertIn("S04 has no take", text)
        self.assertIn("Next step: **organize**", text)
        self.assertIn("no timeline tracked yet", text)

    def test_record_step_without_timeline(self):
        s = self.session()
        s.init_state("Demo")
        self.assertEqual(rvs_checklist.next_step(s.state)[0], "organize")
        entry = s.record_step("organize", "3 recordings")
        self.assertEqual((entry["version"], entry["kind"], entry["timeline"], entry["plan"]),
                         (0, "record", None, None))
        again = self.session()
        self.assertEqual(rvs_checklist.next_step(again.state)[0], "assemble")
        text = (self.dir / "EDIT_CHECKLIST.md").read_text()
        self.assertIn("- [x] **organize**", text)
        self.assertNotIn("None", text)
        again.record_step("organize", "4 recordings")
        self.assertEqual(len(self.session().state["history"]), 1)
        self.assertEqual(self.session().state["history"][0]["summary"], "4 recordings")

    def test_init_state_keeps_existing(self):
        s = self.session()
        s.init_state("Demo")
        s.add_issue("organize", "S04 has no take")
        again = self.session().init_state("Other")
        self.assertEqual(again["history"], [])
        self.assertEqual(len(again["issues"]), 1)
        self.assertEqual(again["base_name"], "Demo")

    def test_legacy_ingest_counts_as_organize(self):
        s = self.session()
        s.init_state("Demo")
        s.state["skipped"] = {"ingest": "done by hand"}
        s._save_state()
        self.assertIn("~~organize~~", (self.dir / "EDIT_CHECKLIST.md").read_text())
        self.assertNotEqual(rvs_checklist.next_step(s.state)[0], "organize")

    def test_legacy_ingest_history_counts_as_organize(self):
        state = {"base_name": "D", "current": {"version": 1, "timeline": "t"}, "issues": [], "skipped": {},
                 "history": [{"step": "ingest", "version": 1, "timeline": "t", "date": "2026-01-01T00:00:00",
                              "summary": "x"}]}
        self.assertNotEqual(rvs_checklist.next_step(state)[0], "organize")
        self.assertIn("**organize** — v01", rvs_checklist.render(state, None, "2026-01-01T00:00:00"))

    def test_skip_ingest_alias(self):
        s = self.session()
        s.init_state("Demo")
        s.skip_step("ingest", "x")
        self.assertEqual(s.state["skipped"], {"organize": "x"})

    def test_check_without_timeline(self):
        s = self.session()
        s.init_state("Demo")
        with self.assertRaises(rvs.RvsError) as cm:
            s.check()
        self.assertIn("No timeline tracked yet.", str(cm.exception))

    def test_session_loads_timeline_less_state_without_project_check(self):
        self.session().init_state("Demo")
        self.assertIsNone(self.session().state["resolve_project"])


if __name__ == "__main__":
    unittest.main()
