import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "video-framing" / "scripts"))

from timeline import _top_at, hidden, item_key, on_camera, runs, shot_prefix, visible_cuts  # noqa: E402


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

    def test_oncamera_on_higher_track_covering_cut_hides_it(self):
        other = vid("S5_z.MOV", 2, 50, 150, media="both")
        got = self.cuts(other)
        self.assertNotIn(("V1@0", "V1@100"), got)

    def test_a_on_top_track_broll_below_ends_at_cut_is_visible(self):
        a = vid("S1_a.MOV", 3, 0, 100, media="both")
        b = vid("S1_b.MOV", 1, 100, 200, media="both")
        p = plan(a, b, vid("IMG_9.MOV", 2, 50, 100))
        got = visible_cuts(p, on_camera(p), {"V3@0": "s", "V1@100": "s"})
        self.assertEqual(got, [("V3@0", "V1@100")])

    def test_b_on_top_track_broll_below_covering_last_frame_of_a_hides(self):
        a = vid("S1_a.MOV", 1, 0, 100, media="both")
        b = vid("S1_b.MOV", 3, 100, 200, media="both")
        p = plan(a, b, vid("IMG_9.MOV", 2, 50, 100))
        got = visible_cuts(p, on_camera(p), {"V1@0": "s", "V3@100": "s"})
        self.assertEqual(got, [])

    def test_disabled_item_ignored(self):
        off = vid("IMG_9.MOV", 3, 80, 120)
        off["enabled"] = False
        self.assertEqual(len(self.cuts(off)), 1)

    def test_item_missing_from_setup_of_skipped(self):
        self.assertEqual(self.cuts(setup_of={"V1@0": "s"}), [])

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

    def test_branching_cuts_each_item_once(self):
        items = [vid("S1_a.MOV", 1, i * 10, i * 10 + 10, media="both") for i in range(3)]
        k = [item_key(i) for i in items]
        got = runs(items, [(k[0], k[1]), (k[0], k[2]), (k[1], k[2])])
        flat = [x for r in got for x in r]
        self.assertEqual(sorted(flat), sorted(k))
        self.assertEqual(got, [k])

    def test_singleton_run(self):
        items = [vid("S1_a.MOV", 1, 0, 10, media="both")]
        self.assertEqual(runs(items, []), [["V1@0"]])


class BrollNamesTest(unittest.TestCase):
    def test_video_only_item_on_a_broll_track_is_broll(self):
        p = plan(vid("S08_IMG_1.MOV", 3, 0, 100), vid("S08_IMG_1.MOV", 2, 0, 100),
                 aud("S08_IMG_2.MOV", 0, 100))
        p["tracks"] = {"video": [{"index": 1, "name": "On camera"}, {"index": 2, "name": "Voiceover"},
                                 {"index": 3, "name": "B-roll inserts"}]}
        self.assertEqual([i["track"] for i in on_camera(p)], [2])

    def test_track_name_variants(self):
        for name in ("BROLL", "b roll", "My b-Roll", "b_roll"):
            p = plan(vid("S08_IMG_1.MOV", 2, 0, 100), aud("S08_IMG_2.MOV", 0, 100))
            p["tracks"] = {"video": [{"index": 2, "name": name}]}
            self.assertEqual(on_camera(p), [], name)

    def test_file_named_broll_is_broll(self):
        p = plan(vid("S41_screen_broll.mov", 2, 0, 100), aud("S41_IMG_2.MOV", 0, 100),
                 vid("S42_demo_B-Roll.mov", 2, 100, 200, media="both"),
                 vid("S43_IMG_3.MOV", 1, 200, 300, media="both"))
        self.assertEqual([i["clip"] for i in on_camera(p)], ["S43_IMG_3.MOV"])

    def test_file_named_b_roll_with_underscore(self):
        p = plan(vid("S41_screen_b_roll.mov", 2, 0, 100), aud("S41_IMG_2.MOV", 0, 100))
        self.assertEqual(on_camera(p), [])

    def test_file_named_b_roll_in_the_path_only(self):
        it = vid("S41_x.mov", 2, 0, 100, path="/m/S41_screen-broll.mov")
        self.assertEqual(on_camera(plan(it, aud("S41_IMG_2.MOV", 0, 100))), [])


class HiddenTest(unittest.TestCase):
    def setUp(self):
        self.a = vid("S1_a.MOV", 1, 0, 100, media="both")

    def keys(self, *extra):
        return hidden(plan(self.a, *extra), [self.a])

    def test_fully_covered_is_hidden(self):
        self.assertEqual(self.keys(vid("broll.mov", 2, 0, 100)), {"V1@0"})

    def test_covered_by_two_back_to_back_items_is_hidden(self):
        self.assertEqual(self.keys(vid("b1.mov", 2, -10, 50), vid("b2.mov", 3, 50, 120)), {"V1@0"})

    def test_one_visible_frame_is_enough(self):
        self.assertEqual(self.keys(vid("b1.mov", 2, 0, 99)), set())
        self.assertEqual(self.keys(vid("b1.mov", 2, 1, 100)), set())
        self.assertEqual(self.keys(vid("b1.mov", 2, 0, 50), vid("b2.mov", 2, 51, 100)), set())

    def test_lower_track_or_disabled_does_not_cover(self):
        low = vid("b.mov", 1, 0, 100)
        a = vid("S1_a.MOV", 2, 0, 100, media="both")
        self.assertEqual(hidden(plan(a, low), [a]), set())
        off = {**vid("b.mov", 2, 0, 100), "enabled": False}
        self.assertEqual(self.keys(off), set())

    def test_agrees_with_top_at_frame_by_frame(self):
        extra = [vid("b1.mov", 2, 0, 40), vid("b2.mov", 3, 30, 70), vid("b3.mov", 2, 70, 100)]
        video = [self.a, *extra]
        never_top = all(_top_at(video, f) is not self.a for f in range(0, 100))
        self.assertTrue(never_top)
        self.assertEqual(self.keys(*extra), {"V1@0"})


if __name__ == "__main__":
    unittest.main()
