import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "resolve-bridge" / "scripts"))

import rvs  # noqa: E402

# Shape of Resolve 21's FCPXML 1.10 export: V1 in the spine, V2 as a connected clip (lane 1),
# timeline starting at 01:00:00:00 (3600 s, frame 86400 at 24 fps).
FCPXML = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.10"><library><event><project><sequence tcStart="3600/1s">
<spine>
  <clip name="a.mov" start="6607/30s" duration="139/24s" offset="30849/8s">
    <adjust-transform anchor="0 0" scale="1.0417 1.0417" position="-3.7037 0.212343">
      <param name="scale" value="1.0417 1.0417"><keyframeAnimation>
        <keyframe value="1.0417 1.0417" interp="easeOut" time="5285/24s"/>
        <keyframe value="1.1042 1.1042" interp="easeIn" time="5411/24s"/>
      </keyframeAnimation></param>
      <param name="position" value="-3.7037 0.212343"><keyframeAnimation>
        <keyframe value="-3.7037 0.212343" time="5285/24s" curve="linear"/>
        <keyframe value="-3.29385 -0.77513" time="5411/24s" curve="linear"/>
      </keyframeAnimation></param>
    </adjust-transform>
  </clip>
  <clip name="b.mov" start="0/1s" duration="2s" offset="3610s">
    <adjust-transform scale="1.2 1.2" position="0 0"/>
  </clip>
  <gap name="Gap" start="3600s" duration="10s" offset="3620s">
    <clip name="c.mov" lane="1" start="10s" duration="2s" offset="3601s">
      <adjust-transform scale="1 1"><param name="rotation" value="0"><keyframeAnimation>
        <keyframe value="0" time="10s"/><keyframe value="5" time="11s"/>
      </keyframeAnimation></param></adjust-transform>
    </clip>
  </gap>
</spine></sequence></project></event></library></fcpxml>"""


class ParseKeyframesTest(unittest.TestCase):
    def setUp(self):
        self.kf = rvs.parse_fcpxml_keyframes(FCPXML, 24.0, 86400)

    def test_spine_clip_with_keyframes_is_keyed_by_v1_and_plan_start(self):
        a = self.kf[(1, 6147)]
        self.assertEqual(a["transform.scale"], [[0, "1.042 1.042", "easeOut"], [126, "1.104 1.104", "easeIn"]])
        self.assertEqual([k[0] for k in a["transform.position"]], [0, 126])

    def test_clip_without_keyframes_is_absent(self):
        self.assertNotIn((1, 240), self.kf)

    def test_connected_clip_maps_to_its_lane_track_and_absolute_start(self):
        # gap at 3620 s with start 3600 s: child offset 3601 s -> 3621 s -> frame 86904 - 86400
        c = self.kf[(2, 504)]
        self.assertEqual(c["transform.rotation"], [[0, "0.000", ""], [24, "5.000", ""]])

    def test_no_spine_gives_nothing(self):
        self.assertEqual(rvs.parse_fcpxml_keyframes("<fcpxml/>", 24.0, 0), {})


class KeyframeDiffTest(unittest.TestCase):
    def _plan(self, kf=None):
        d = {"clip": "a.mov", "media": "video", "track": 1, "start": 0, "end": 48, "src_start": 0,
             "path": "/a.mov"}
        if kf:
            d["keyframes"] = kf
        return {"timeline": {"fps": 24}, "items": [d]}

    def test_added_keyframes_are_a_hand_edit(self):
        kf = {"transform.scale": [[0, "1.000 1.000", ""], [40, "1.100 1.100", ""]]}
        lines = rvs.diff_plans(self._plan(), self._plan(kf))
        self.assertEqual(len(lines), 1)
        self.assertIn("keyframes none -> transform.scale x2", lines[0])

    def test_same_keyframes_are_no_change(self):
        kf = {"transform.scale": [[0, "1.000 1.000", ""]]}
        self.assertEqual(rvs.diff_plans(self._plan(kf), self._plan(kf)), [])


if __name__ == "__main__":
    unittest.main()
