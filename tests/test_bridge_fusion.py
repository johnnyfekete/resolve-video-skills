import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "resolve-bridge" / "scripts"))

import rvs  # noqa: E402


class FakeMpi:
    def GetClipProperty(self, k):
        return {"File Path": "/f/a.mov", "FPS": "24"}.get(k)


class FakeItem:
    def __init__(self, name, start, end, comps):
        self.name, self.start, self.end, self.comps = name, start, end, comps

    def GetName(self): return self.name
    def GetMediaPoolItem(self): return FakeMpi()
    def GetStart(self): return self.start
    def GetEnd(self): return self.end
    def GetSourceStartFrame(self): return 0
    def GetSourceEndFrame(self): return self.end - self.start
    def GetClipEnabled(self): return True
    def GetUniqueId(self): return self.name
    def GetLinkedItems(self): return []
    def GetProperties(self): return {}
    def GetFades(self): return {}
    def GetFusionCompCount(self): return self.comps
    def GetSourceAudioChannelMapping(self): return "{}"


class FakeTimeline:
    def __init__(self, items):
        self.items = items

    def GetStartFrame(self): return 0
    def GetName(self): return "T"
    def GetSetting(self, k): return None
    def GetTrackName(self, k, i): return ""
    def GetTrackCount(self, k): return 1 if k == "video" else 0
    def GetItemListInTrack(self, k, t): return self.items if k == "video" else []


class BridgeFusionTest(unittest.TestCase):
    def test_export_records_fusion_comps(self):
        plan = rvs.export_plan(FakeTimeline([FakeItem("a", 0, 48, 2), FakeItem("b", 48, 96, 0)]))
        a, b = plan["items"]
        self.assertEqual(a["fusion_comps"], 2)
        self.assertNotIn("fusion_comps", b)

    def test_neutral_comps_are_not_counted(self):
        neutral, push = FakeItem("a", 0, 48, 1), FakeItem("b", 48, 96, 1)
        neutral.GetFusionCompNameList = lambda: ["rvs_neutral"]
        push.GetFusionCompNameList = lambda: ["rvs_pushin"]
        a, b = rvs.export_plan(FakeTimeline([neutral, push]))["items"]
        self.assertNotIn("fusion_comps", a)
        self.assertEqual(b["fusion_comps"], 1)

    def test_empty_comps_are_not_counted(self):
        class Tool:
            def __init__(self, reg): self.reg = reg
            def GetAttrs(self, k): return self.reg

        class Comp:
            def __init__(self, *regs): self.tools = {f"t{i}": Tool(r) for i, r in enumerate(regs)}
            def GetToolList(self, selected): return self.tools

        comps = {"Composition 1": Comp("MediaIn", "MediaOut", "AudioDisplay"),
                 "Composition 2": Comp("MediaIn", "Transform", "MediaOut")}
        empty, used = FakeItem("a", 0, 48, 1), FakeItem("b", 48, 96, 2)
        empty.GetFusionCompNameList = lambda: ["Composition 1"]
        empty.GetFusionCompByName = comps.get
        used.GetFusionCompNameList = lambda: ["Composition 1", "Composition 2"]
        used.GetFusionCompByName = comps.get
        a, b = rvs.export_plan(FakeTimeline([empty, used]))["items"]
        self.assertNotIn("fusion_comps", a)
        self.assertEqual(b["fusion_comps"], 1)

    def test_name_list_failure_falls_back_to_count(self):
        it = FakeItem("a", 0, 48, 2)
        it.GetFusionCompNameList = lambda: (_ for _ in ()).throw(AttributeError())
        self.assertEqual(rvs.export_plan(FakeTimeline([it]))["items"][0]["fusion_comps"], 2)

    def test_export_survives_missing_api(self):
        it = FakeItem("a", 0, 48, 0)
        it.GetFusionCompCount = lambda: (_ for _ in ()).throw(AttributeError())
        plan = rvs.export_plan(FakeTimeline([it]))
        self.assertNotIn("fusion_comps", plan["items"][0])

    def test_diff_reports_fusion_change(self):
        old = rvs.export_plan(FakeTimeline([FakeItem("a", 0, 48, 0)]))
        new = rvs.export_plan(FakeTimeline([FakeItem("a", 0, 48, 1)]))
        self.assertEqual(len(rvs.diff_plans(old, new)), 1)

    def test_build_refuses_when_fusion_would_be_lost(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        s = rvs.Session(Path(tmp.name), connect_now=False)
        s.init_state("Demo")
        plan = rvs.export_plan(FakeTimeline([FakeItem("a", 0, 48, 1)]))
        s.check = lambda: []
        s.load_plan = lambda rel=None: plan
        with self.assertRaises(rvs.RvsError) as cm:
            s.build(plan, "cut", "x")
        msg = str(cm.exception)
        self.assertIn("a V1 @0-48", msg)
        # allowed: gets past the check and reaches timeline creation
        s._put_new_timeline_at_top = lambda: None
        class Reached(Exception): pass
        def stop(*a, **k): raise Reached()
        orig = rvs.build_timeline
        rvs.build_timeline = stop
        self.addCleanup(setattr, rvs, "build_timeline", orig)
        with self.assertRaises(Reached):
            s.build(plan, "cut", "x", allow_fusion_loss=True)


if __name__ == "__main__":
    unittest.main()
