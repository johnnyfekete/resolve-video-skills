import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "video-organize" / "scripts"))
sys.path.insert(0, str(ROOT / "skills" / "resolve-bridge" / "scripts"))

import organize  # noqa: E402
import rename  # noqa: E402
import rvs  # noqa: E402
import store  # noqa: E402


class FakeClip:
    def __init__(self, path, replace_ok=True):
        self.props = {"File Path": path, "Clip Name": Path(path).name, "Type": "Video"}
        self.replaced = []
        self.replace_ok = replace_ok

    def GetClipProperty(self, key):
        return self.props.get(key)

    def SetClipProperty(self, key, value):
        self.props[key] = value
        return True

    def ReplaceClip(self, path):
        self.replaced.append(path)
        if not self.replace_ok:
            return False
        self.props["File Path"] = path
        return True


class FakeFolder:
    def __init__(self, clips=(), subs=()):
        self.clips, self.subs = list(clips), list(subs)

    def GetClipList(self):
        return self.clips

    def GetSubFolderList(self):
        return self.subs


class FakeItem:
    def __init__(self, clip):
        self.clip = clip

    def GetMediaPoolItem(self):
        return self.clip


class FakeTimeline:
    def __init__(self, video=(), audio=()):
        self.tracks = {"video": [list(video)], "audio": [list(audio)]}

    def GetTrackCount(self, kind):
        return len(self.tracks[kind])

    def GetItemListInTrack(self, kind, i):
        return self.tracks[kind][i - 1]


class FakeProject:
    def __init__(self, root, timelines=(), name="Proj"):
        self.root, self.timelines, self.name = root, list(timelines), name

    def GetName(self):
        return self.name

    def GetMediaPool(self):
        return self

    def GetRootFolder(self):
        return self.root

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, i):
        return self.timelines[i - 1]


def take(file, tid, start=0.0):
    scene = tid.split("-")[0][:-1]
    return {"id": tid, "scene": scene, "file": str(file), "start": start, "end": start + 5, "status": "complete"}


class RenameTest(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.dir = Path(td.name).resolve()
        self.work = self.dir / "work"
        self.footage = self.dir / "footage"
        self.footage.mkdir(parents=True)
        self.work.mkdir()

    def file(self, name):
        p = self.footage / name
        p.write_bytes(b"x")
        return p

    def names(self, data, **kw):
        return {Path(r["old"]).name: Path(r["new"]).name for r in rename.propose(data, [], **kw)}

    def test_names_follow_table(self):
        a, b, c, d, e = (self.file(n) for n in ("IMG_1.MOV", "IMG_2.MOV", "IMG_3.MOV", "IMG_4.MOV", "IMG_5.MOV"))
        data = {"takes": [
            take(a, "S02A-T03"),
            take(b, "S02A-T03", 0), take(b, "S02A-T01", 10), take(b, "S02A-T02", 20),
            take(c, "S03A-T01", 0), take(c, "S05A-T01", 10), take(c, "S06A-T02", 20),
            take(d, "S02A-T01", 0), take(d, "S02B-T01", 10),
            {"id": None, "scene": None, "file": str(e), "start": 0, "end": 3, "status": "unmatched"}],
            "files": [{"file": str(e), "kind": "broll"}]}
        self.assertEqual(self.names(data), {
            "IMG_1.MOV": "S02A_T03_IMG_1.MOV", "IMG_2.MOV": "S02A_T01-T03_IMG_2.MOV",
            "IMG_3.MOV": "S03A_S05A_S06A_IMG_3.MOV", "IMG_4.MOV": "S02A_S02B_IMG_4.MOV"})

    def test_already_named_is_stable(self):
        a = self.file("S02A_T03_IMG_1.MOV")
        data = {"takes": [take(a, "S02A-T03")]}
        self.assertEqual(rename.propose(data, []), [])
        data = {"takes": [take(a, "S02A-T03"), take(a, "S02A-T04", 9)]}
        self.assertEqual(self.names(data), {"S02A_T03_IMG_1.MOV": "S02A_T03-T04_IMG_1.MOV"})

    def test_no_storyboard_grouping_offers_nothing(self):
        data = {"mode": "none", "takes": [take(self.file("IMG_1.MOV"), "G01A-T01")]}
        self.assertEqual(rename.propose(data, []), [])

    def test_used_clip_is_skipped_with_reason(self):
        a, b = self.file("IMG_1.MOV"), self.file("IMG_2.MOV")
        data = {"takes": [take(a, "S01A-T01"), take(b, "S02A-T01")]}
        rows = rename.propose(data, [], frozenset({str(a)}))
        self.assertEqual(rows[0]["skip"], "used on a timeline in Resolve")
        self.assertNotIn("skip", rows[1])
        self.assertEqual(len(rename.active(rows)), 1)

    def test_collision_blocks_batch(self):
        a, b = self.file("IMG_1.MOV"), self.file("IMG_2.MOV")
        self.file("S01A_T01_IMG_1.MOV")
        rows = [{"old": str(a), "new": str(self.footage / "S01A_T01_IMG_1.MOV"), "reason": ""},
                {"old": str(b), "new": str(self.footage / "S01A_T01_IMG_2.MOV"), "reason": ""}]
        self.assertTrue(any("target exists" in e for e in rename.validate(rows)))
        with self.assertRaises(ValueError):
            rename.apply(rows, self.work, never_imported=True)
        self.assertTrue(a.exists() and b.exists())
        self.assertFalse((self.footage / "S01A_T01_IMG_2.MOV").exists())

    def test_validate_duplicates_and_missing(self):
        a = self.file("IMG_1.MOV")
        t = str(self.footage / "X.MOV")
        rows = [{"old": str(a), "new": t, "reason": ""}, {"old": str(a), "new": t.lower(), "reason": ""},
                {"old": str(self.footage / "gone.MOV"), "new": str(self.footage / "Y.MOV"), "reason": ""}]
        text = "\n".join(rename.validate(rows))
        self.assertIn("twice as a source", text)
        self.assertIn("both become", text)
        self.assertIn("source missing", text)

    def test_skipped_rows_ignored_by_validate_and_apply(self):
        a, b = self.file("IMG_1.MOV"), self.file("IMG_2.MOV")
        rows = [{"old": str(a), "new": str(self.footage / "S01A_T01_IMG_1.MOV"), "reason": "", "skip": "used"},
                {"old": str(b), "new": str(self.footage / "S02A_T01_IMG_2.MOV"), "reason": ""}]
        rename.apply(rows, self.work, never_imported=True)
        self.assertTrue(a.exists())
        self.assertTrue((self.footage / "S02A_T01_IMG_2.MOV").exists())
        record = json.loads((self.work / ".edit/organize/renames.json").read_text())
        self.assertEqual([r["old"] for r in record["batches"][0]["rows"]], [str(b)])

    def test_odd_names(self):
        a = self.file("My Clip é.MOV")
        data = {"takes": [take(a, "S01A-T01")]}
        store.save(self.work / ".edit/organize", data)
        rows = rename.propose(data, [])
        rename.apply(rows, self.work, never_imported=True)
        new = self.footage / "S01A_T01_My Clip é.MOV"
        self.assertTrue(new.exists() and not a.exists())
        self.assertEqual(rename.undo(self.work), 1)
        self.assertTrue(a.exists() and not new.exists())

    def test_undo_restores(self):
        a = self.file("IMG_1.MOV")
        org = self.work / ".edit/organize"
        data = {"takes": [take(a, "S01A-T01")], "picks": {"S01": {"id": "S01A-T01", "file": str(a)}},
                "shots": {"S01": {str(a): "A"}}}
        store.save(org, data)
        (org / "broll.json").write_text(json.dumps({str(a): {"frames": {}}}))
        cache = org / "transcripts"
        cache.mkdir()
        (cache / (hashlib.sha1(str(a).encode()).hexdigest() + ".json")).write_text(json.dumps({"file": str(a)}))
        plan = self.work / ".edit/plans/v01_x.json"
        plan.parent.mkdir(parents=True)
        plan.write_text(json.dumps({"items": [{"path": str(a)}]}))
        old_plan = self.work / ".edit/plans/v00_old.json"
        old_plan.write_text(json.dumps({"items": [{"path": str(a)}]}))
        (self.work / ".edit/state.json").write_text(json.dumps(
            {"resolve_project": None, "current": {"version": 1, "timeline": "t", "plan": "plans/v01_x.json"}}))

        rename.apply(rename.propose(data, []), self.work, never_imported=True)
        new = str(self.footage / "S01A_T01_IMG_1.MOV")
        loaded = store.load(org)
        self.assertEqual(loaded["takes"][0]["file"], new)
        self.assertEqual(loaded["picks"]["S01"]["file"], new)
        self.assertIn(new, loaded["shots"]["S01"])
        self.assertIn(new, json.loads((org / "broll.json").read_text()))
        moved = cache / (hashlib.sha1(new.encode()).hexdigest() + ".json")
        self.assertEqual(json.loads(moved.read_text())["file"], new)
        self.assertEqual(json.loads(plan.read_text())["items"][0]["path"], new)
        self.assertEqual(json.loads(old_plan.read_text())["items"][0]["path"], str(a))

        rename.undo(self.work)
        self.assertTrue(a.exists())
        self.assertEqual(store.load(org)["takes"][0]["file"], str(a))
        self.assertIn(str(a), json.loads((org / "broll.json").read_text()))
        self.assertEqual(json.loads(plan.read_text())["items"][0]["path"], str(a))
        self.assertTrue((cache / (hashlib.sha1(str(a).encode()).hexdigest() + ".json")).exists())
        with self.assertRaises(RuntimeError):
            rename.undo(self.work)

    def test_resolve_relink_called(self):
        a, b = self.file("IMG_1.MOV"), self.file("IMG_2.MOV")
        ca, cb = FakeClip(str(a)), FakeClip(str(b))
        sub = FakeFolder([cb])
        project = FakeProject(FakeFolder([ca], [sub]))
        new = str(self.footage / "S01A_T01_IMG_1.MOV")
        rows = [{"old": str(a), "new": new, "reason": ""}]
        rename.apply(rows, self.work, project)
        self.assertEqual(ca.replaced, [new])
        self.assertEqual(ca.props["Clip Name"], "S01A_T01_IMG_1.MOV")
        self.assertEqual(cb.replaced, [])
        rename.undo(self.work, project)
        self.assertEqual(ca.replaced, [new, str(a)])
        self.assertEqual(ca.props["Clip Name"], "IMG_1.MOV")

    def test_used_clip_never_relinked(self):
        a = self.file("IMG_1.MOV")
        ca = FakeClip(str(a))
        project = FakeProject(FakeFolder([ca]), [FakeTimeline(video=[FakeItem(ca)])])
        self.assertEqual(rename.used_paths(project), {str(a)})
        stale = [{"old": str(a), "new": str(self.footage / "S01A_T01_IMG_1.MOV"), "reason": ""}]
        with self.assertRaises(RuntimeError):
            rename.apply(stale, self.work, project)
        self.assertTrue(a.exists())
        self.assertEqual(ca.replaced, [])

    def test_used_paths_walks_audio_and_every_timeline(self):
        a, b = self.file("IMG_1.MOV"), self.file("IMG_2.MOV")
        ca, cb = FakeClip(str(a)), FakeClip(str(b))
        project = FakeProject(FakeFolder(), [FakeTimeline(video=[FakeItem(ca)]), FakeTimeline(audio=[FakeItem(cb)])])
        self.assertEqual(rename.used_paths(project), {str(a), str(b)})

    def test_relink_failure_rolls_back(self):
        a = self.file("IMG_1.MOV")
        ca = FakeClip(str(a), replace_ok=False)
        with self.assertRaises(RuntimeError):
            rename.apply([{"old": str(a), "new": str(self.footage / "S01A_T01_IMG_1.MOV"), "reason": ""}],
                         self.work, FakeProject(FakeFolder([ca])))
        self.assertTrue(a.exists())
        self.assertFalse((self.footage / "S01A_T01_IMG_1.MOV").exists())
        self.assertEqual(rename._load_record(self.work)["batches"], [])

    def test_refuses_without_project(self):
        a = self.file("IMG_1.MOV")
        with self.assertRaises(RuntimeError):
            rename.apply([{"old": str(a), "new": str(self.footage / "S01A_T01_IMG_1.MOV"), "reason": ""}],
                         self.work)
        self.assertTrue(a.exists())
        self.assertFalse((self.work / ".edit/organize/renames.json").exists())

    def test_refuses_other_project(self):
        a = self.file("IMG_1.MOV")
        (self.work / ".edit").mkdir()
        (self.work / ".edit/state.json").write_text(json.dumps({"resolve_project": "Mine", "current": {}}))
        with self.assertRaises(RuntimeError):
            rename.apply([{"old": str(a), "new": str(self.footage / "S01A_T01_IMG_1.MOV"), "reason": ""}],
                         self.work, FakeProject(FakeFolder(), name="Other"))
        self.assertTrue(a.exists())

    def test_cli_propose_apply_undo(self):
        a, b = self.file("IMG_1.MOV"), self.file("IMG_2.MOV")
        org = self.work / ".edit/organize"
        store.save(org, {"takes": [take(a, "S01A-T01"), take(b, "S02A-T01")]})

        def run(*args):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                    mock.patch.object(rvs, "connect", side_effect=rvs.RvsError("closed")):
                code = organize.main(["--workdir", str(self.work), "rename", *args])
            return code, out.getvalue(), err.getvalue()

        code, out, _ = run("--never-imported")
        self.assertEqual(code, 0)
        self.assertIn("IMG_1.MOV  ->  S01A_T01_IMG_1.MOV", out)
        self.assertTrue(a.exists())
        code, _, err = run("--apply")
        self.assertEqual(code, 1)
        self.assertIn("closed", err)
        self.assertTrue(a.exists())
        code, out, _ = run("--apply", "--never-imported")
        self.assertEqual(code, 0)
        self.assertFalse(a.exists())
        self.assertTrue((self.footage / "S02A_T01_IMG_2.MOV").exists())
        code, out, _ = run("--undo")
        self.assertEqual(code, 0)
        self.assertTrue(a.exists() and b.exists())


if __name__ == "__main__":
    unittest.main()
