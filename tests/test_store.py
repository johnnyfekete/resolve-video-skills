import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "video-organize" / "scripts"))

import storyboard  # noqa: E402
import store  # noqa: E402
import takes  # noqa: E402


def take(file, scene, start, end, status="complete"):
    return takes.Take(file, scene, start, end, 1.0, status)


def old_take(id_, file, scene, start, end):
    return {"id": id_, "file": file, "scene": scene, "start": start, "end": end, "status": "complete"}


def by_name(file):
    """Filming order from the file name only (no ffprobe in unit tests)."""
    return (0.0, 0, file)


class IdTests(unittest.TestCase):
    def setUp(self):
        self.old = [
            old_take("S02A-T01", "/f/IMG_2.MOV", "S02", 1.0, 5.0),
            old_take("S02A-T02", "/f/IMG_2.MOV", "S02", 10.0, 14.0),
            old_take("S02A-T03", "/f/IMG_2.MOV", "S02", 20.0, 24.0),
        ]
        self.shots = {"S02": {"/f/IMG_2.MOV": "A", "/f/IMG_1.MOV": "A"}}

    def ids(self, result):
        return {(t["file"], t["start"]): t["id"] for t in result}

    def test_ids_stable_after_new_footage(self):
        new = [take("/f/IMG_1.MOV", "S02", 2.0, 6.0)] + [
            take(t["file"], "S02", t["start"], t["end"]) for t in self.old]
        result = store.assign_ids(self.old, new, self.shots, order_key=by_name)
        ids = self.ids(result)
        self.assertEqual(ids[("/f/IMG_2.MOV", 1.0)], "S02A-T01")
        self.assertEqual(ids[("/f/IMG_2.MOV", 10.0)], "S02A-T02")
        self.assertEqual(ids[("/f/IMG_2.MOV", 20.0)], "S02A-T03")
        self.assertEqual(ids[("/f/IMG_1.MOV", 2.0)], "S02A-T04")

    def test_ids_stable_after_threshold_change(self):
        new = [take(t["file"], "S02", t["start"] - 0.4, t["end"] + 0.4) for t in self.old]
        result = store.assign_ids(self.old, new, self.shots, order_key=by_name)
        self.assertEqual([t["id"] for t in result], ["S02A-T01", "S02A-T02", "S02A-T03"])

    def test_numbers_follow_filming_order(self):
        new = [take("/f/IMG_9.MOV", "S05", 3.0, 6.0), take("/f/IMG_3.MOV", "S05", 3.0, 6.0)]
        shots = {"S05": {"/f/IMG_9.MOV": "A", "/f/IMG_3.MOV": "A"}}
        result = store.assign_ids([], new, shots, order_key=by_name)
        ids = {t["file"]: t["id"] for t in result}
        self.assertEqual(ids, {"/f/IMG_3.MOV": "S05A-T01", "/f/IMG_9.MOV": "S05A-T02"})

    def test_vanished_id_is_not_reused(self):
        new = [take("/f/IMG_2.MOV", "S02", 1.0, 5.0), take("/f/IMG_2.MOV", "S02", 30.0, 34.0)]
        result = store.assign_ids(self.old, new, self.shots, order_key=by_name)
        self.assertEqual(sorted(t["id"] for t in result), ["S02A-T01", "S02A-T04"])

    def test_unmatched_take_gets_no_id(self):
        new = [take("/f/IMG_2.MOV", None, 40.0, 50.0, status="unmatched")]
        result = store.assign_ids([], new, {}, order_key=by_name)
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]["id"])
        self.assertEqual(result[0]["status"], "unmatched")

    def test_result_is_json(self):
        result = store.assign_ids([], [take("/f/IMG_2.MOV", "S02", 1.0, 5.0)], self.shots, order_key=by_name)
        json.dumps(result)


class IssuedTests(unittest.TestCase):
    def test_vanished_highest_number_not_reissued_across_runs(self):
        shots = {"S02": {"/f/a.MOV": "A"}}
        issued = {}
        run1 = store.assign_ids([], [take("/f/a.MOV", "S02", float(i * 10), i * 10 + 4.0) for i in range(3)],
                                shots, order_key=by_name, issued=issued)
        self.assertEqual([t["id"] for t in run1], ["S02A-T01", "S02A-T02", "S02A-T03"])
        self.assertEqual(issued, {"S02A": 3})
        run2 = store.assign_ids(run1, [take("/f/a.MOV", "S02", 0.0, 4.0), take("/f/a.MOV", "S02", 10.0, 14.0)],
                                shots, order_key=by_name, issued=issued)
        self.assertEqual([t["id"] for t in run2], ["S02A-T01", "S02A-T02"])
        run3 = store.assign_ids(run2, [t for t in [take("/f/a.MOV", "S02", 0.0, 4.0),
                                                   take("/f/a.MOV", "S02", 10.0, 14.0),
                                                   take("/f/a.MOV", "S02", 50.0, 54.0)]],
                                shots, order_key=by_name, issued=issued)
        self.assertEqual(run3[2]["id"], "S02A-T04")

    def test_relabeled_scene_gets_new_id(self):
        old = [old_take("S02A-T01", "/f/a.MOV", "S02", 1.0, 5.0)]
        result = store.assign_ids(old, [take("/f/a.MOV", "S03", 1.0, 5.0)],
                                  {"S03": {"/f/a.MOV": "A"}}, order_key=by_name)
        self.assertEqual(result[0]["id"], "S03A-T01")


class PickTests(unittest.TestCase):
    def data(self):
        old = [old_take("S02A-T01", "/f/a.MOV", "S02", 1.0, 5.0),
               old_take("S02A-T02", "/f/a.MOV", "S02", 10.0, 14.0)]
        return {"schema": store.SCHEMA, "takes": old, "picks": {}, "shots": {}, "questions": [], "issued": {}}

    def test_pick_survives_rerun(self):
        data = store.apply_pick(self.data(), "S02A-T02")
        self.assertEqual(data["picks"]["S02"]["file"], "/f/a.MOV")
        self.assertEqual(data["picks"]["S02"]["by"], "user")
        new = [take("/f/a.MOV", "S02", 0.7, 5.3), take("/f/a.MOV", "S02", 9.8, 14.4)]
        data["takes"] = store.assign_ids(data["takes"], new, {"S02": {"/f/a.MOV": "A"}}, order_key=by_name)
        self.assertEqual(store.stale_picks(data), [])
        pick = data["picks"]["S02"]
        match = [t for t in data["takes"] if t["id"] == pick["id"]][0]
        self.assertEqual(match["file"], pick["file"])
        self.assertLess(abs(match["start"] - pick["start"]), 1.0)

    def test_stale_pick_reported(self):
        data = store.apply_pick(self.data(), "S02A-T02")
        data["takes"] = [t for t in data["takes"] if t["id"] != "S02A-T02"]
        stale = store.stale_picks(data)
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]["id"], "S02A-T02")
        self.assertEqual(stale[0]["scene"], "S02")

    def test_pick_with_moved_file_is_stale(self):
        data = store.apply_pick(self.data(), "S02A-T02")
        for t in data["takes"]:
            t["file"] = "/f/b.MOV"
        self.assertEqual(len(store.stale_picks(data)), 1)

    def test_unknown_take_id(self):
        with self.assertRaises(KeyError):
            store.apply_pick(self.data(), "S09A-T01")


class ShotLetterTests(unittest.TestCase):
    def scene(self, setup=None):
        return storyboard.Scene("S02", "text", setup=setup)

    def fake_diff(self, table):
        calls = []

        def fake(fa, ta, fb, tb):
            calls.append((fa, ta, fb, tb))
            return table[frozenset((fa, fb))]
        fake.calls = calls
        return fake

    def test_shot_letters(self):
        f1, f2, f3, f4 = "/f/1.MOV", "/f/2.MOV", "/f/3.MOV", "/f/4.MOV"
        ts = [take(f, "S02", 2.0, 6.0) for f in (f1, f2, f3, f4)]
        fake = self.fake_diff({
            frozenset((f1, f2)): 0.05,
            frozenset((f1, f3)): 0.5, frozenset((f2, f3)): 0.5,
            frozenset((f1, f4)): 0.6, frozenset((f3, f4)): 0.25,
        })
        letters, questions = store.shot_letters(self.scene(), ts, fake, {})
        self.assertEqual(letters, {f1: "A", f2: "A", f3: "B", f4: "B"})
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0]["scene"], "S02")
        self.assertEqual(questions[0]["files"], [f3, f4])
        self.assertEqual(questions[0]["diff"], 0.25)
        self.assertEqual(fake.calls[0], (f1, 4.0, f2, 4.0))  # middle of the take

    def test_returning_to_a_setup_reuses_its_letter(self):
        f1, f2, f3 = "/f/1.MOV", "/f/2.MOV", "/f/3.MOV"
        ts = [take(f, "S02", 2.0, 6.0) for f in (f1, f2, f3)]
        fake = self.fake_diff({frozenset((f1, f2)): 0.6, frozenset((f1, f3)): 0.05,
                               frozenset((f2, f3)): 0.6})
        letters, questions = store.shot_letters(self.scene(), ts, fake, {})
        self.assertEqual(letters, {f1: "A", f2: "B", f3: "A"})
        self.assertEqual(questions, [])

    def test_rerun_keeps_prior_letters(self):
        f0, f1, f2 = "/f/0.MOV", "/f/1.MOV", "/f/2.MOV"
        ts = [take(f, "S02", 2.0, 6.0) for f in (f0, f1, f2)]  # f0 filmed earlier, new
        fake = self.fake_diff({frozenset((f0, f1)): 0.6, frozenset((f0, f2)): 0.05,
                               frozenset((f1, f2)): 0.6})
        prior = {f1: "A", f2: "B"}
        letters, _ = store.shot_letters(self.scene(), ts, fake, {}, prior)
        self.assertEqual(letters, {f1: "A", f2: "B", f0: "B"})

    def test_new_file_unlike_all_letters_gets_next_unused(self):
        f1, f2, f3 = "/f/1.MOV", "/f/2.MOV", "/f/3.MOV"
        ts = [take(f, "S02", 2.0, 6.0) for f in (f1, f2, f3)]
        letters, _ = store.shot_letters(self.scene(), ts, lambda *a: 0.9, {}, {f1: "A", f2: "C"})
        self.assertEqual(letters[f3], "B")

    def test_one_file_many_takes_is_one_shot(self):
        ts = [take("/f/1.MOV", "S02", 2.0, 6.0), take("/f/1.MOV", "S02", 20.0, 24.0)]
        letters, questions = store.shot_letters(self.scene(), ts, lambda *a: 1 / 0, {})
        self.assertEqual(letters, {"/f/1.MOV": "A"})
        self.assertEqual(questions, [])

    def test_shared_setup_keeps_letter(self):
        setup_letters = {"two-shot at desk": "B"}
        ts = [take("/f/1.MOV", "S02", 2.0, 6.0)]
        letters, _ = store.shot_letters(self.scene("two-shot at desk"), ts, lambda *a: 0.0, setup_letters)
        self.assertEqual(letters, {"/f/1.MOV": "B"})

    def test_setup_letter_recorded(self):
        setup_letters = {}
        ts = [take("/f/1.MOV", "S02", 2.0, 6.0)]
        store.shot_letters(self.scene("closeup"), ts, lambda *a: 0.0, setup_letters)
        self.assertEqual(setup_letters, {"closeup": "A"})


class StoreFileTests(unittest.TestCase):
    def test_load_missing_is_empty_store(self):
        with tempfile.TemporaryDirectory() as d:
            data = store.load(Path(d))
        self.assertEqual(data, {"schema": "rvs.takes/1", "takes": [], "picks": {}, "shots": {}, "questions": [], "issued": {}})

    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            data = store.load(Path(d))
            data["takes"].append({"id": "S02A-T01"})
            store.save(Path(d), data)
            self.assertEqual(store.load(Path(d)), data)
            self.assertTrue((Path(d) / "takes.json").exists())


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
class FrameDiffTests(unittest.TestCase):
    def test_frame_diff_real_clips(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / "a.mp4", Path(d) / "b.mp4"
            for out, src in ((a, "testsrc=size=320x180:rate=10"), (b, "color=c=black:size=320x180:rate=10")):
                subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", src, "-t", "2",
                                "-pix_fmt", "yuv420p", str(out)], check=True)
            same = store.frame_diff(str(a), 1.0, str(a), 1.0)
            different = store.frame_diff(str(a), 1.0, str(b), 1.0)
        self.assertEqual(same, 0.0)
        self.assertGreater(different, 0.2)
        self.assertLessEqual(different, 1.0)


if __name__ == "__main__":
    unittest.main()
