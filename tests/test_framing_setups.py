import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "video-framing" / "scripts"))

from setups import group_setups, signature  # noqa: E402
from timeline import item_key  # noqa: E402


def item(start, path, track=1):
    return {"clip": Path(path).name, "path": path, "media": "both", "track": track,
            "start": start, "end": start + 100, "src_start": 0}


FLAT = [100] * 144
NEAR = [104] * 144
FAR = [180] * 144


class GroupSetupsTest(unittest.TestCase):
    def test_near_identical_signatures_merge_into_one_setup(self):
        a, b = item(0, "/m/a.mov"), item(100, "/m/b.mov")
        sigs = {item_key(a): FLAT, item_key(b): NEAR}
        self.assertEqual(group_setups([a, b], sigs), {"A": [item_key(a), item_key(b)]})

    def test_different_signatures_make_two_setups(self):
        a, b = item(0, "/m/a.mov"), item(100, "/m/b.mov")
        sigs = {item_key(a): FLAT, item_key(b): FAR}
        self.assertEqual(group_setups([a, b], sigs), {"A": [item_key(a)], "B": [item_key(b)]})

    def test_same_path_groups_first_and_ids_follow_timeline_order(self):
        b1, a1, b2 = item(0, "/m/b.mov"), item(100, "/m/a.mov"), item(200, "/m/b.mov")
        sigs = {item_key(b1): FLAT, item_key(a1): FAR, item_key(b2): FAR}
        out = group_setups([a1, b2, b1], sigs)
        self.assertEqual(out, {"A": [item_key(b1), item_key(b2)], "B": [item_key(a1)]})

    def test_threshold_is_respected(self):
        a, b = item(0, "/m/a.mov"), item(100, "/m/b.mov")
        sigs = {item_key(a): FLAT, item_key(b): NEAR}
        self.assertEqual(len(group_setups([a, b], sigs, threshold=3.0)), 2)

    def test_item_without_signature_keeps_its_own_group(self):
        a, b = item(0, "/m/a.mov"), item(100, "/m/b.mov")
        out = group_setups([a, b], {item_key(a): FLAT})
        self.assertEqual(out, {"A": [item_key(a)], "B": [item_key(b)]})


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
class SignatureTest(unittest.TestCase):
    def test_signature_is_16x9_grayscale(self):
        with tempfile.TemporaryDirectory() as d:
            png = Path(d) / "grey.png"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                            "color=c=gray:s=320x180", "-frames:v", "1", str(png)], check=True)
            sig = signature(png)
        self.assertEqual(len(sig), 144)
        self.assertTrue(all(isinstance(v, int) and 0 <= v <= 255 for v in sig))
        self.assertTrue(all(abs(v - sig[0]) <= 2 for v in sig))


if __name__ == "__main__":
    unittest.main()
