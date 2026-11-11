import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "video-framing" / "scripts"))

from timeline import item_key, on_camera, runs, shot_prefix, visible_cuts  # noqa: E402


def vid(clip, track, start, end, media="video", path=None):
    return {"clip": clip, "path": path or f"/m/{clip}", "media": media,
            "track": track, "start": start, "end": end, "src_start": 0}


def aud(clip, start, end, track=1, path=None):
    return {"clip": clip, "path": path or f"/m/{clip}", "media": "audio",
            "audio_track": track, "start": start, "end": end, "src_start": 0}


def plan(*items):
    return {"schema": "rvs.plan/1", "items": list(items)}


class ShotPrefixTest(unittest.TestCase):
    def test_prefixes(self):
        self.assertEqual(shot_prefix("S19_IMG_5404_cfr60.mov"), "S19")
        self.assertEqual(shot_prefix("S20_S22_IMG_5785.MOV"), "S20_S22")
        self.assertIsNone(shot_prefix("IMG_5807.MOV"))

    def test_item_key(self):
        self.assertEqual(item_key(vid("a.mov", 2, 40, 50)), "V2@40")


class OnCameraTest(unittest.TestCase):
    def clips(self, p):
        return [i["clip"] for i in on_camera(p)]

    def test_both_counts(self):
        p = plan(vid("S1_IMG_1.MOV", 1, 0, 100, media="both"))
        self.assertEqual(self.clips(p), ["S1_IMG_1.MOV"])

    def test_cfr_copy_with_original_audio(self):
        p = plan(vid("S19_IMG_5404_cfr60_from40s.mov", 1, 0, 100),
                 aud("S19_IMG_5404.MOV", 0, 100))
        self.assertEqual(self.clips(p), ["S19_IMG_5404_cfr60_from40s.mov"])

    def test_broll_does_not_count(self):
        p = plan(vid("S19_IMG_5404.MOV", 1, 0, 100, media="both"),
                 vid("IMG_5807.MOV", 3, 10, 50))
        self.assertEqual(self.clips(p), ["S19_IMG_5404.MOV"])

    def test_combined_prefix_matches_part(self):
        p = plan(vid("S12_S13_IMG_5777.MOV", 1, 0, 100),
                 aud("S12_IMG_5778.MOV", 0, 100))
        self.assertEqual(self.clips(p), ["S12_S13_IMG_5777.MOV"])

    def test_non_overlapping_audio_does_not_count(self):
        p = plan(vid("S12_IMG_1.MOV", 1, 0, 100), aud("S12_IMG_2.MOV", 100, 200))
        self.assertEqual(self.clips(p), [])

    def test_equal_path_counts_without_prefix(self):
        p = plan(vid("x.mov", 1, 0, 100, path="/m/x.mov"),
                 aud("x.mov", 50, 150, path="/m/x.mov"))
        self.assertEqual(self.clips(p), ["x.mov"])

    def test_sorted_by_start(self):
        p = plan(vid("S2_a.MOV", 1, 100, 200, media="both"),
                 vid("S1_a.MOV", 1, 0, 100, media="both"))
        self.assertEqual([i["start"] for i in on_camera(p)], [0, 100])


class VisibleCutsTest(unittest.TestCase):
    def setUp(self):
        self.a = vid("S1_a.MOV", 1, 0, 100, media="both")
        self.b = vid("S1_b.MOV", 1, 100, 200, media="both")
        self.setup = {item_key(self.a): "s", item_key(self.b): "s"}

    def cuts(self, *extra, setup_of=None):
        p = plan(self.a, self.b, *extra)
        return visible_cuts(p, on_camera(p), setup_of or self.setup)

    def test_plain_cut_visible(self):
        self.assertEqual(self.cuts(), [("V1@0", "V1@100")])

    def test_covered_by_broll_spanning_cut(self):
        self.assertEqual(self.cuts(vid("IMG_9.MOV", 3, 80, 120)), [])

    def test_broll_starting_at_cut_covers(self):
        self.assertEqual(self.cuts(vid("IMG_9.MOV", 3, 100, 130)), [])

    def test_broll_ending_one_frame_before_cut_is_visible(self):
        self.assertEqual(len(self.cuts(vid("IMG_9.MOV", 3, 50, 99))), 1)

    def test_broll_ending_at_cut_frame_covers_last_frame_of_a(self):
        # end exclusive: covers frame 99 (b.start - 1), not frame 100
        self.assertEqual(self.cuts(vid("IMG_9.MOV", 3, 50, 100)), [])

    def test_broll_over_first_half_of_b_only_after_cut(self):
        # covers 101..150, not the cut frame 100 or 99
        self.assertEqual(len(self.cuts(vid("IMG_9.MOV", 3, 101, 150))), 1)

    def test_broll_over_part_of_a_only(self):
        self.assertEqual(len(self.cuts(vid("IMG_9.MOV", 3, 20, 60))), 1)

    def test_broll_on_lower_track_ignored(self):
        a = vid("S1_a.MOV", 2, 0, 100, media="both")
        b = vid("S1_b.MOV", 2, 100, 200, media="both")
        p = plan(a, b, vid("IMG_9.MOV", 1, 50, 150))
        got = visible_cuts(p, on_camera(p), {"V2@0": "s", "V2@100": "s"})
        self.assertEqual(len(got), 1)

    def test_oncamera_on_higher_track_is_not_broll(self):
        other = vid("S5_z.MOV", 2, 50, 150, media="both")
        self.assertEqual(len(self.cuts(other)), 1)

    def test_gap_means_no_cut(self):
        b = vid("S1_b.MOV", 1, 101, 200, media="both")
        p = plan(self.a, b)
        self.assertEqual(visible_cuts(p, on_camera(p), {"V1@0": "s", "V1@101": "s"}), [])

    def test_different_setup_no_cut(self):
        self.assertEqual(self.cuts(setup_of={"V1@0": "s", "V1@100": "t"}), [])


class RunsTest(unittest.TestCase):
    def test_chains(self):
        items = [vid("S1_a.MOV", 1, i * 10, i * 10 + 10, media="both") for i in range(5)]
        keys = [item_key(i) for i in items]
        cuts = [(keys[0], keys[1]), (keys[1], keys[2]), (keys[3], keys[4])]
        self.assertEqual(runs(items, cuts), [keys[0:3], keys[3:5]])

    def test_singleton_run(self):
        items = [vid("S1_a.MOV", 1, 0, 10, media="both")]
        self.assertEqual(runs(items, []), [["V1@0"]])


if __name__ == "__main__":
    unittest.main()
