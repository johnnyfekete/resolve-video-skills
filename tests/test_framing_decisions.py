import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "video-framing" / "scripts"))

import decisions  # noqa: E402
from decisions import hand_framed, load, load_applied, match_key, merge, save_applied, template  # noqa: E402
import json  # noqa: E402


def item(start=0, end=100, props=None, comps=0, src=10, path="/a.mov"):
    d = {"track": 1, "start": start, "end": end, "path": path, "src_start": src, "media": "both"}
    if props:
        d["props"] = props
    if comps:
        d["fusion_comps"] = comps
    return d


class LoadTest(unittest.TestCase):
    def _load(self, setup, push=None):
        d = template({"A": ["V1@0"]})
        d["setups"]["A"].update(setup)
        if push:
            d["push_ins"] = push
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "decisions.json"
            p.write_text(json.dumps(d))
            return load(p)

    def test_valid(self):
        self.assertEqual(self._load({"label": "plain", "keep_out": [[0.1, 0.1, 0.3, 0.3]]})["setups"]["A"]["label"], "plain")

    def test_box_out_of_range(self):
        with self.assertRaisesRegex(ValueError, "setup A keep_out"):
            self._load({"label": "plain", "keep_out": [[0.2, 0.1, 1.3, 0.9]]})

    def test_bad_label(self):
        with self.assertRaisesRegex(ValueError, "setup A.*boring"):
            self._load({"label": "boring"})

    def test_missing_label(self):
        with self.assertRaisesRegex(ValueError, "setup A.*label"):
            self._load({})

    def test_push_end_before_start(self):
        with self.assertRaisesRegex(ValueError, "push_ins"):
            self._load({"label": "plain"}, [{"start_s": 5, "end_s": 5, "text": "x", "reason": "y"}])


class MergeTest(unittest.TestCase):
    def test_keeps_unchanged_and_adds_new(self):
        old = template({"A": ["V1@0", "V1@100"]})
        old["setups"]["A"].update(label="dynamic", keep_out=[[0, 0, 0.2, 0.2]])
        old["push_ins"] = [{"start_s": 1, "end_s": 2, "text": "t", "reason": "r"}]
        new = merge(old, {"A": ["V1@100", "V1@0"], "B": ["V1@200"]})
        self.assertEqual(new["setups"]["A"]["keep_out"], [[0, 0, 0.2, 0.2]])
        self.assertEqual(new["setups"]["A"]["label"], "dynamic")
        self.assertIsNone(new["setups"]["B"]["label"])
        self.assertEqual(len(new["push_ins"]), 1)

    def test_changed_item_set_is_redecided(self):
        old = template({"A": ["V1@0"]})
        old["setups"]["A"].update(label="plain")
        self.assertIsNone(merge(old, {"A": ["V1@0", "V1@9"]})["setups"]["A"]["label"])


class HandFramedTest(unittest.TestCase):
    def test_first_run(self):
        self.assertEqual(hand_framed([item(props={"ZoomX": 1.3})], None), {"V1@0"})
        self.assertEqual(hand_framed([item()], None), set())
        self.assertEqual(hand_framed([item(comps=1)], None), {"V1@0"})

    def test_rerun_matches_applied(self):
        it = item(props={"ZoomX": 1.15, "ZoomY": 1.15})
        applied = {match_key(it): {"props": {"ZoomX": 1.15, "ZoomY": 1.15}, "fusion_comps": 0}}
        self.assertEqual(hand_framed([it], applied), set())
        # Resolve round-trip noise is tolerated
        noisy = item(props={"ZoomX": 1.150001, "ZoomY": 1.15})
        self.assertEqual(hand_framed([noisy], applied), set())

    def test_rerun_differs(self):
        it = item(props={"ZoomX": 1.3, "ZoomY": 1.3})
        applied = {match_key(it): {"props": {"ZoomX": 1.15, "ZoomY": 1.15}, "fusion_comps": 0}}
        self.assertEqual(hand_framed([it], applied), {"V1@0"})

    def test_rerun_new_comp_is_hand(self):
        it = item(comps=1)
        applied = {match_key(it): {"props": {}, "fusion_comps": 0}}
        self.assertEqual(hand_framed([it], applied), {"V1@0"})

    def test_rippled_item_still_matched(self):
        applied = {match_key(item(start=0, end=100)): {"props": {"ZoomX": 1.15, "ZoomY": 1.15}, "fusion_comps": 0}}
        moved = item(start=500, end=600, props={"ZoomX": 1.15, "ZoomY": 1.15})
        self.assertEqual(hand_framed([moved], applied), set())

    def test_new_item_on_later_run_uses_first_run_rule(self):
        applied = {"other|1|1": {"props": {}, "fusion_comps": 0}}
        self.assertEqual(hand_framed([item(props={"Pan": 20.0})], applied), {"V1@0"})
        self.assertEqual(hand_framed([item()], applied), set())

    def test_rerun_after_user_tweak_does_not_stack(self):
        """Re-run: framing-owned clip is re-solved, the tweaked clip is left untouched."""
        a, b = item(start=0, end=100, src=0), item(start=100, end=200, src=100)
        z = {"ZoomX": 1.15, "ZoomY": 1.15}
        applied = {match_key(a): {"props": z, "fusion_comps": 0}, match_key(b): {"props": z, "fusion_comps": 0}}
        a["props"] = dict(z)
        b["props"] = {"ZoomX": 1.4, "ZoomY": 1.4}  # user tweak
        self.assertEqual(hand_framed([a, b], applied), {"V1@100"})


class AppliedIoTest(unittest.TestCase):
    def test_roundtrip_atomic(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "framing" / "applied.json"
            self.assertIsNone(load_applied(p))
            save_applied(p, {"k": {"props": {"ZoomX": 1.1}, "fusion_comps": 0}})
            self.assertEqual(load_applied(p)["k"]["props"]["ZoomX"], 1.1)
            self.assertEqual([f.name for f in p.parent.iterdir()], ["applied.json"])


if __name__ == "__main__":
    unittest.main()
