"""video-framing CLI: solve, REPORT.md, hand-framed items and the Resolve apply, without Vision,
ffmpeg or Resolve (fake measures go straight into the measure cache)."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "video-framing" / "scripts"))
sys.path.insert(0, str(ROOT / "skills" / "resolve-bridge" / "scripts"))

import apply_resolve  # noqa: E402
import decisions  # noqa: E402
import framing  # noqa: E402
import measure  # noqa: E402

FPS = 24.0

MEASURE = {"detected": 1.0, "eyes": [0.5, 0.42], "face_h": 0.12,
           "extent": [0.42, 0.3, 0.58, 0.62], "joints_y": {}}

PUSH = {"start_s": 1.0, "end_s": 5.0, "text": "This is the whole point.",
        "reason": "thesis of the video"}


def _item(path, start, end, src_start, clip, props=None):
    d = {"clip": clip, "path": str(path), "start": start, "end": end, "src_start": src_start,
         "src_end": src_start + (end - start) * 2, "src_fps": 48.0, "media": "both", "track": 1,
         "audio_track": 1}
    if props:
        d["props"] = props
    return d


class Workdir:
    """A fixture project folder: state, plan, source stand-ins, cached measures, decisions."""

    def __init__(self, root: Path):
        self.root = root
        src = root / "src"
        src.mkdir()
        self.a, self.b = src / "S01_A.MOV", src / "S02_B.MOV"
        self.a.write_bytes(b"a")
        self.b.write_bytes(b"b")
        self.items = [
            _item(self.a, 0, 240, 0, "S01_A.MOV"),
            _item(self.a, 240, 480, 600, "S01_A.MOV"),
            _item(self.b, 480, 720, 0, "S02_B.MOV"),
            _item(self.b, 720, 960, 600, "S02_B.MOV", props={"ZoomX": 1.3, "ZoomY": 1.3}),
        ]
        self.plan = {"schema": "rvs.plan/1",
                     "timeline": {"name": "T_v01", "fps": FPS, "width": 1920, "height": 1080,
                                  "start_frame": 86400},
                     "tracks": {}, "items": self.items, "unsupported": []}
        edit = root / ".edit"
        (edit / "plans").mkdir(parents=True)
        (edit / "plans" / "v01_adopt.json").write_text(json.dumps(self.plan))
        (edit / "state.json").write_text(json.dumps({
            "schema": "rvs.state/1", "resolve_project": "scratch", "base_name": "T",
            "archive_folder": "Archive", "history": [],
            "current": {"version": 1, "timeline": "T_v01", "plan": "plans/v01_adopt.json"},
            "issues": [], "skipped": {}}))
        mdir = edit / "framing" / "measure"
        mdir.mkdir(parents=True)
        for it in self.items:
            (mdir / measure._cache_name(it, Path(it["path"]))).write_text(json.dumps(MEASURE))

    def decide(self, label_b="dynamic", push_ins=(PUSH,)):
        d = decisions.template({"A": ["V1@0", "V1@240"], "B": ["V1@480", "V1@720"]})
        d["setups"]["A"].update(label="plain", reason="front-on at the desk")
        d["setups"]["B"].update(label=label_b, reason="on the counter")
        d["push_ins"] = list(push_ins)
        decisions.save(self.root / ".edit" / "framing" / "decisions.json", d)

    def solve(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(framing, "source_size", return_value=(3840, 2160)), \
                mock.patch.object(framing, "write_previews", return_value={}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = framing.main(["solve", "--workdir", str(self.root), *extra])
        return code, out.getvalue() + err.getvalue()

    def solution(self):
        return json.loads((self.root / ".edit" / "framing" / "solution.json").read_text())

    def report(self):
        return (self.root / "generated" / "framing" / "REPORT.md").read_text()


class SolveCliTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.w = Workdir(Path(self._td.name))

    def tearDown(self):
        self._td.cleanup()

    def test_missing_label_exits_2_naming_the_setup(self):
        self.w.decide(label_b=None)
        code, out = self.w.solve()
        self.assertEqual(code, 2)
        self.assertIn("setup B", out)
        self.assertFalse((self.w.root / ".edit" / "framing" / "solution.json").exists())

    def test_writes_solution_and_report_with_push_text_and_reason(self):
        self.w.decide()
        code, out = self.w.solve()
        self.assertEqual(code, 0, out)
        sol = self.w.solution()
        self.assertEqual(set(sol["items"]), {"V1@0", "V1@240", "V1@480"})
        for entry in sol["items"].values():
            self.assertTrue({"ZoomX", "ZoomY", "Pan", "Tilt"} <= set(entry["props"]))
        self.assertEqual(len(sol["pushes"]), 1)
        report = self.w.report()
        self.assertIn(PUSH["text"], report)
        self.assertIn(PUSH["reason"], report)
        self.assertIn("plain", report)
        self.assertIn("front-on at the desk", report)

    def test_dropped_push_is_still_in_the_report_with_text_and_reason(self):
        dropped = {"start_s": 21.0, "end_s": 25.0, "text": "On the counter now.",
                   "reason": "turn in the argument"}
        self.w.decide(push_ins=(PUSH, dropped))
        code, out = self.w.solve()
        self.assertEqual(code, 0, out)
        report = self.w.report()
        for p in (PUSH, dropped):
            self.assertIn(p["text"], report)
            self.assertIn(p["reason"], report)
        self.assertEqual(len(self.w.solution()["pushes"]), 1)

    def test_hand_framed_item_left_alone(self):
        self.w.decide()
        code, out = self.w.solve()
        self.assertEqual(code, 0, out)
        self.assertNotIn("V1@720", self.w.solution()["items"])
        report = self.w.report()
        self.assertIn("left alone", report.lower())
        section = report.lower().split("left alone", 1)[1]
        self.assertIn("v1@720", section)

    def test_report_names_the_cut_budget_when_setups_differ(self):
        self.w.decide()
        sizes = {str(self.w.a): (3840, 2160), str(self.w.b): (2560, 1440)}
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(framing, "source_size", side_effect=lambda p: sizes[str(p)]), \
                mock.patch.object(framing, "write_previews", return_value={}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(framing.main(["solve", "--workdir", str(self.w.root)]), 0, out.getvalue())
        self.assertIn("one budget 1.33x", self.w.report())
        self.assertEqual(self.w.solution()["cuts"]["budget"], 1.3333)

    def test_on_camera_matches_include_hand_framed(self):
        self.w.decide()
        self.w.solve()
        self.assertIn(decisions.match_key(self.w.items[3]), self.w.solution()["on_camera_matches"])

    def test_no_push_option(self):
        self.w.decide()
        code, out = self.w.solve("--no-push")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.w.solution()["pushes"], [])

    def test_every_flag_has_a_where_with_clip_and_timecode(self):
        self.w.decide()
        with mock.patch.dict(MEASURE, {"detected": 0.2}):
            for it in self.w.items:
                p = self.w.root / ".edit" / "framing" / "measure" / measure._cache_name(it, Path(it["path"]))
                p.write_text(json.dumps(MEASURE))
            code, out = self.w.solve()
        self.assertEqual(code, 0, out)
        flags = self.w.solution()["flags"]
        self.assertTrue(flags)
        for f in flags:
            self.assertRegex(f["where"], r"S0\d_[AB]\.MOV at \d\d:\d\d\.\d\d")


class TranscriptTest(unittest.TestCase):
    def test_lines_map_source_time_to_timeline_time(self):
        plan = {"timeline": {"fps": 24.0}}
        it = {"start": 240, "end": 480, "src_start": 1200, "src_end": 1680, "src_fps": 48.0,
              "path": "/x.mov", "track": 1}
        # The item shows source 25.0-35.0 s at timeline 10.0-20.0 s.
        tr = {"segments": [{"text": " before ", "start": 20.0, "end": 24.0},
                           {"text": "Hello there.", "start": 26.0, "end": 28.5},
                           {"text": "Cut in half", "start": 34.0, "end": 37.0}]}
        lines = framing.transcript_lines(plan, [(it, "A")], {"/x.mov": tr})
        self.assertEqual(lines, ["00:11.00-00:13.50 A Hello there.",
                                 "00:19.00-00:20.00 A Cut in half"])


# ---------------------------------------------------------------- apply with fake Resolve objects

class FakeItem:
    def __init__(self, name, start):
        self.name, self.start, self.props, self.comps = name, start, {}, {}

    def GetName(self):
        return self.name

    def GetStart(self):
        return self.start

    def SetProperties(self, props):
        self.props.update(props)
        return True

    def SetProperty(self, k, v):
        self.props[k] = v
        return True

    def GetProperty(self, k=None):
        return self.props.get(k, 0.0) if k else dict(self.props)

    def GetFusionCompCount(self):
        return len(self.comps)

    def GetFusionCompNameList(self):
        return list(self.comps) if self.comps else {}

    def GetFusionCompByName(self, name):
        return self.comps.get(name)

    def AddFusionComp(self):
        n = len(self.comps) + 1
        while f"Composition {n}" in self.comps:
            n += 1
        self.comps[f"Composition {n}"] = c = FakeComp()
        return c

    def DeleteFusionCompByName(self, name):
        if name not in self.comps or len(self.comps) == 1:  # Resolve keeps an item's only comp
            return False
        del self.comps[name]
        return True

    def RenameFusionCompByName(self, old, new):
        if old not in self.comps or new in self.comps:
            return False
        self.comps = {new if k == old else k: v for k, v in self.comps.items()}
        return True


class FakeTool:
    def __init__(self, comp, name):
        self.comp, self.name, self.inputs, self.keys = comp, name, {}, None
        self.Size = self

    def SetAttrs(self, attrs):
        self.comp.tools[attrs["TOOLS_Name"]] = self.comp.tools.pop(self.name)
        self.name = attrs["TOOLS_Name"]

    def ConnectInput(self, name, tool):
        self.inputs[name] = tool

    def SetInput(self, name, value, t=None):
        self.inputs[(name, t)] = value

    def AddModifier(self, name, kind):
        return True

    def GetConnectedOutput(self):
        return self

    def GetTool(self):
        return self

    def SetKeyFrames(self, keys, replace):
        self.keys = keys

    def Delete(self):
        del self.comp.tools[self.name]


class FakeComp:
    render_end = 599.0   # 600 source frames: a 240-frame item at 60 fps on a 24 fps timeline

    def __init__(self):
        self.tools = {}
        for n in ("MediaIn1", "MediaOut1"):
            self.tools[n] = FakeTool(self, n)
        self.tools["MediaOut1"].inputs["Input"] = self.tools["MediaIn1"]

    def FindTool(self, name):
        return self.tools.get(name)

    def GetAttrs(self):
        return {"COMPN_RenderStart": 0.0, "COMPN_RenderEnd": self.render_end}

    def AddTool(self, kind, x, y):
        t = FakeTool(self, f"{kind}1")
        self.tools[t.name] = t
        return t


class FakeTimeline:
    def __init__(self, items):
        self.items = items

    def GetStartFrame(self):
        return 86400

    def GetTrackCount(self, kind):
        return 1 if kind == "video" else 0

    def GetItemListInTrack(self, kind, t):
        return self.items


class FakeSession:
    def __init__(self, tl, fail=False):
        self.tl, self.fail, self.calls, self.issues = tl, fail, [], []
        self.dir = None

    def begin_step(self, step):
        self.calls.append(("begin", step))
        return self.tl

    def finish_step(self, summary):
        if self.fail:
            raise RuntimeError("save failed")
        self.calls.append(("finish", summary))
        return {"version": 2, "timeline": "T_v02_framing"}

    def abandon_step(self):
        self.calls.append(("abandon",))

    def add_issue(self, step, text, where=None):
        self.issues.append((step, text, where))
        return {"id": f"I{len(self.issues)}"}


def _solution(items):
    return {"timeline": {"fps": 24.0, "width": 1920, "height": 1080},
            "items": {f"V1@{s}": {"track": 1, "start": s, "clip": "S01_A.MOV",
                                  "match": f"/a.mov|{s}|240",
                                  "props": {"ZoomX": 1.5, "ZoomY": 1.5, "Pan": 10.0, "Tilt": -5.0}}
                      for s in items},
            "pushes": [], "setups": {"A": {"zoom": 1.5}}, "hand_framed": [],
            "flags": [{"key": "V1@0", "setup": "A", "text": "check me", "where": "S01_A.MOV at 00:00.00"}]}


class ApplyTest(unittest.TestCase):
    def test_sets_props_records_applied_and_issues(self):
        items = [FakeItem("S01_A.MOV", 86400), FakeItem("S01_A.MOV", 86640)]
        s = FakeSession(FakeTimeline(items))
        with tempfile.TemporaryDirectory() as td:
            applied_path = Path(td) / "applied.json"
            apply_resolve.apply(s, _solution([0, 240]), applied_path)
            applied = json.loads(applied_path.read_text())
        self.assertEqual(items[1].props["ZoomX"], 1.5)
        self.assertEqual(items[0].props["Pan"], 10.0)
        self.assertEqual(applied["/a.mov|240|240"]["props"]["ZoomX"], 1.5)
        self.assertEqual(applied["/a.mov|240|240"]["fusion_comps"], 0)
        self.assertEqual([c[0] for c in s.calls], ["begin", "finish"])
        self.assertEqual(s.issues[0][0], "framing")
        self.assertEqual(s.issues[0][2], "S01_A.MOV at 00:00.00")

    def test_abandons_on_error(self):
        items = [FakeItem("S01_A.MOV", 86400)]
        s = FakeSession(FakeTimeline(items), fail=True)
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(RuntimeError):
                apply_resolve.apply(s, _solution([0]), Path(td) / "applied.json")
            self.assertFalse((Path(td) / "applied.json").exists())
            self.assertFalse((Path(td) / "applied.json.pending").exists())
        self.assertEqual(s.calls[-1], ("abandon",))

    def test_missing_item_abandons(self):
        s = FakeSession(FakeTimeline([FakeItem("S01_A.MOV", 86400)]))
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(Exception, "V1@240"):
                apply_resolve.apply(s, _solution([0, 240]), Path(td) / "applied.json")
        self.assertEqual(s.calls[-1], ("abandon",))

    PUSH = {"key": "V1@0", "comp_start": 5.0, "comp_end": 200.0, "comp_len": 600.0, "size": 1.06,
            "pivot": [0.5, 0.3]}

    def _apply(self, item, pushes, session=None):
        sol = _solution([0])
        sol["pushes"] = pushes
        with tempfile.TemporaryDirectory() as td:
            apply_resolve.apply(session or FakeSession(FakeTimeline([item])), sol, Path(td) / "applied.json")
            return json.loads((Path(td) / "applied.json").read_text())

    def test_push_comp_built_and_named(self):
        item = FakeItem("S01_A.MOV", 86400)
        applied = self._apply(item, [self.PUSH])
        self.assertEqual(list(item.comps), ["rvs_pushin"])
        tr = item.comps["rvs_pushin"].FindTool("rvs_pushin")
        self.assertEqual(tr.inputs[("Pivot", None)], {1: 0.5, 2: 0.7})
        self.assertEqual(sorted(tr.keys), [5.0, 200.0])
        self.assertIs(item.comps["rvs_pushin"].FindTool("MediaOut1").inputs["Input"], tr)
        self.assertEqual(applied["/a.mov|0|240"]["fusion_comps"], 1)

    def test_rerun_replaces_the_old_push_comp(self):
        item = FakeItem("S01_A.MOV", 86400)
        self._apply(item, [self.PUSH])
        self._apply(item, [{**self.PUSH, "comp_start": 50.0}])
        self.assertEqual(list(item.comps), ["rvs_pushin"])
        self.assertEqual(sorted(item.comps["rvs_pushin"].FindTool("rvs_pushin").keys), [50.0, 200.0])

    def test_rerun_without_push_neutralises_the_only_comp(self):
        item = FakeItem("S01_A.MOV", 86400)
        self._apply(item, [self.PUSH])
        applied = self._apply(item, [])
        self.assertEqual(list(item.comps), ["rvs_neutral"])
        comp = item.comps["rvs_neutral"]
        self.assertIsNone(comp.FindTool("rvs_pushin"))
        self.assertIs(comp.FindTool("MediaOut1").inputs["Input"], comp.FindTool("MediaIn1"))
        self.assertEqual(applied["/a.mov|0|240"]["fusion_comps"], 0)  # the bridge ignores it too
        # a later push replaces the neutral comp
        applied = self._apply(item, [self.PUSH])
        self.assertEqual(list(item.comps), ["rvs_pushin"])
        self.assertEqual(applied["/a.mov|0|240"]["fusion_comps"], 1)

    def test_failed_rename_abandons(self):
        item = FakeItem("S01_A.MOV", 86400)
        item.RenameFusionCompByName = lambda old, new: False
        s = FakeSession(FakeTimeline([item]))
        with self.assertRaisesRegex(apply_resolve.ApplyError, "rename"):
            self._apply(item, [self.PUSH], s)
        self.assertEqual(s.calls[-1], ("abandon",))

    def test_comp_length_mismatch_is_flagged_and_push_kept(self):
        item = FakeItem("S01_A.MOV", 86400)
        s = FakeSession(FakeTimeline([item]))
        with mock.patch.object(FakeComp, "render_end", 596.0):
            self._apply(item, [self.PUSH], s)
        self.assertIsNotNone(item.comps["rvs_pushin"].FindTool("rvs_pushin"))
        timing = [i for i in s.issues if "push-in timing" in i[1]]
        self.assertEqual(len(timing), 1)
        self.assertIn("S01_A.MOV", timing[0][1])

    def test_comp_length_within_a_frame_is_not_flagged(self):
        item = FakeItem("S01_A.MOV", 86400)
        s = FakeSession(FakeTimeline([item]))
        self._apply(item, [self.PUSH], s)
        self.assertFalse([i for i in s.issues if "push-in timing" in i[1]])

    def test_applied_prunes_items_no_longer_on_camera(self):
        sol = _solution([0])
        sol["on_camera_matches"] = ["/a.mov|0|240", "/hand.mov|0|10"]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "applied.json"
            path.write_text(json.dumps({"/gone.mov|0|10": {"props": {}}, "/hand.mov|0|10": {"props": {}}}))
            apply_resolve.apply(FakeSession(FakeTimeline([FakeItem("S01_A.MOV", 86400)])), sol, path)
            self.assertEqual(sorted(json.loads(path.read_text())), ["/a.mov|0|240", "/hand.mov|0|10"])
            self.assertFalse(path.with_name("applied.json.pending").exists())

    def test_user_comp_is_never_touched(self):
        item = FakeItem("S01_A.MOV", 86400)
        item.AddFusionComp()
        self._apply(item, [self.PUSH])
        self.assertEqual(list(item.comps), ["Composition 1", "rvs_pushin"])
        self.assertIsNone(item.comps["Composition 1"].FindTool("rvs_pushin"))

    def test_refuses_wrong_scaling_mode(self):
        class TL:
            def GetSetting(self, k):
                return "scaleToFit" if k == "timelineInputResMismatchBehavior" else ""
        with self.assertRaisesRegex(Exception, "scale full frame with crop"):
            apply_resolve.check_scaling(TL(), None)


if __name__ == "__main__":
    unittest.main()
