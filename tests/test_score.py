import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "video-organize" / "scripts"))

import score  # noqa: E402
import storyboard  # noqa: E402
import takes  # noqa: E402

WORD = 0.3
PAUSE = 0.4

SCENE_TEXT = "I opened the editor, I wrote the line, and I hit publish"


def scene(text=SCENE_TEXT, voiceover=False):
    return storyboard.Scene("S01", text, voiceover=voiceover, clauses=storyboard.split_clauses(text))


def take(*parts, start=0.0, status="complete", missing=(), file="/f/IMG_1.MOV"):
    """parts: phrases said back to back; a PAUSE of silence separates the parts."""
    words, t = [], start
    for part in parts:
        for w in part.split():
            words.append({"w": w, "start": round(t, 3), "end": round(t + WORD, 3), "p": 0.9})
            t += WORD
        t += PAUSE
    end = words[-1]["end"]
    return takes.Take(file, "S01", words[0]["start"], end, 1.0, status, list(missing), [], words, "en")


GOOD = ("I opened the editor", "I wrote the line", "and I hit publish")
CLEAN_METRICS = {"lufs": -18.0, "peak_db": -6.0, "noise_db": -70.0, "clipped": False}


class BrokenPartsTest(unittest.TestCase):
    def test_restart_detected(self):
        sc = scene("Make it sound better")
        t = take("make it it sound better")
        found = score.broken_parts(t, sc)
        self.assertEqual([b["kind"] for b in found], ["restart"])
        self.assertEqual(found[0]["clause"], 0)

    def test_clean_take_has_no_broken_parts(self):
        self.assertEqual(score.broken_parts(take(*GOOD), scene()), [])

    def test_script_repetition_is_not_a_restart(self):
        sc = scene("It was very very good")
        self.assertEqual(score.broken_parts(take("it was very very good"), sc), [])

    def test_stumble_inside_clause(self):
        found = score.broken_parts(take("I opened the", "uh um", "editor", "I wrote the line", "and I hit publish"),
                                   scene())
        self.assertEqual([(b["kind"], b["clause"]) for b in found], [("stumble", 0)])

    def test_cut_word_at_take_end(self):
        found = score.broken_parts(take("I opened the editor", "I wrote the line", "and I hit publ"), scene())
        self.assertEqual([(b["kind"], b["clause"]) for b in found], [("cut", 2)])

    def test_cut_word_at_take_start(self):
        found = score.broken_parts(take("pened the editor", "I wrote the line", "and I hit publish"), scene())
        self.assertEqual([(b["kind"], b["clause"]) for b in found], [("cut", 0)])


class VerdictTest(unittest.TestCase):
    def test_window_and_precedence(self):
        t = take(*GOOD)
        approve, reject = ["very nice"], ["one more time"]
        near = {"start": t.end + 1.0, "end": t.end + 2.0, "text": "Very nice!", "reason": "short"}
        far = {"start": t.end + 20.0, "end": t.end + 21.0, "text": "one more time", "reason": "short"}
        before = {"start": t.end - 5.0, "end": t.end - 4.0, "text": "one more time", "reason": "short"}
        self.assertEqual(score.verdict(t, [near, far, before], approve, reject), "approved")
        both = {"start": t.end + 3.0, "end": t.end + 4.0, "text": "one more time", "reason": "short"}
        self.assertEqual(score.verdict(t, [near, both], approve, reject), "rejected")
        self.assertIsNone(score.verdict(t, [far], approve, reject))


class ScoreTest(unittest.TestCase):
    def test_clean_take_scores_full(self):
        self.assertEqual(score.score(take(*GOOD), scene(), CLEAN_METRICS, []), (100, []))

    def test_voiceover_weights_audio(self):
        quiet = dict(CLEAN_METRICS, lufs=-32.0)
        t = take(*GOOD)
        camera, _ = score.score(t, scene(), quiet, [])
        voice, reasons = score.score(t, scene(voiceover=True), quiet, [])
        self.assertLess(voice, camera)
        self.assertTrue(any("quiet" in r for r in reasons))

    def test_clipped_loses_all_audio_points(self):
        got, reasons = score.score(take(*GOOD), scene(), dict(CLEAN_METRICS, clipped=True, peak_db=0.0), [])
        self.assertEqual(got, 75)
        self.assertIn("clipped audio", reasons)

    def test_reworded_and_broken_and_incomplete_cost_points(self):
        base, _ = score.score(take(*GOOD), scene(), CLEAN_METRICS, [])
        reworded, why = score.score(take(*GOOD, status="reworded"), scene(), CLEAN_METRICS, [])
        self.assertEqual(reworded, base - 5)
        self.assertIn("reworded", why)
        broken = [{"clause": 1, "kind": "restart", "at": 1.0}]
        self.assertLess(score.score(take(*GOOD), scene(), CLEAN_METRICS, broken)[0], base)
        inc = take("I opened the editor", status="incomplete", missing=["i", "wrote", "the", "line"])
        self.assertLess(score.score(inc, scene(), CLEAN_METRICS, [])[0], base - 25)


def entry(t, sc, verdict=None):
    return {"take": t, "score": sc, "verdict": verdict}


class RankTest(unittest.TestCase):
    def test_rejected_ranks_last(self):
        a = take(*GOOD, start=0.0)
        b = take(*GOOD, start=20.0)
        ranked = score.rank([entry(a, 95, "rejected"), entry(b, 80)])
        self.assertEqual([e["take"] for e in ranked], [b, a])

    def test_only_complete_take_kept_even_if_rejected(self):
        complete = take(*GOOD, start=0.0)
        inc = take("I opened the editor", start=20.0, status="incomplete")
        ranked = score.rank([entry(inc, 40), entry(complete, 70, "rejected")])
        self.assertEqual(ranked[0]["take"], complete)

    def test_approved_first_then_score_then_filming_order(self):
        a, b, c, d = (take(*GOOD, start=s) for s in (0.0, 20.0, 40.0, 60.0))
        ranked = score.rank([entry(a, 80), entry(b, 90), entry(c, 90), entry(d, 50, "approved")])
        self.assertEqual([e["take"] for e in ranked], [d, b, c, a])

    def test_unmatched_takes_are_not_ranked(self):
        a = take(*GOOD)
        u = take(*GOOD, start=20.0, status="unmatched")
        self.assertEqual([e["take"] for e in score.rank([entry(u, 99), entry(a, 10)])], [a])


class PatchTest(unittest.TestCase):
    BAD_MIDDLE = ("I opened the editor", "I wrote the the line", "and I hit publish")

    def test_patch_needs_whole_clause(self):
        sc = scene()
        best = take(*self.BAD_MIDDLE, start=0.0)
        fixer = take(*GOOD, start=20.0)
        found = score.patches(best, [fixer], sc)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["clauses"], [1])
        self.assertIs(found[0]["base"], best)
        self.assertIs(found[0]["from"], fixer)
        self.assertEqual(found[0]["from_text"], "I wrote the line")
        also_bad = take(*self.BAD_MIDDLE, start=40.0)
        self.assertEqual(score.patches(best, [also_bad], sc), [])

    def test_missing_clause_is_patchable_and_clean_best_is_not(self):
        sc = scene()
        best = take("I opened the editor", "and I hit publish", start=0.0, status="incomplete")
        fixer = take(*GOOD, start=20.0)
        self.assertEqual(score.patches(best, [fixer], sc)[0]["clauses"], [1])
        self.assertEqual(score.patches(fixer, [best], sc), [])

    def test_no_pause_no_patch(self):
        sc = scene()
        best = take(*self.BAD_MIDDLE, start=0.0)
        fixer = take(*GOOD, start=20.0)
        # close the pause after "line" in the fixer: no natural cut point
        for w in fixer.words[8:]:
            w["start"] -= PAUSE
            w["end"] -= PAUSE
        self.assertEqual(score.patches(best, [fixer], sc), [])

    def test_unmatched_never_a_source(self):
        sc = scene()
        best = take(*self.BAD_MIDDLE, start=0.0)
        fixer = take(*GOOD, start=20.0, status="unmatched")
        self.assertEqual(score.patches(best, [fixer], sc), [])


@unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
class AudioMetricsTest(unittest.TestCase):
    def make(self, tmp, name, expr):
        path = str(Path(tmp) / name)
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"aevalsrc={expr}:d=3:s=48000",
                        "-c:a", "pcm_s16le", path], check=True)
        return path

    def test_audio_metrics_on_tone(self):
        with tempfile.TemporaryDirectory() as tmp:
            tone = self.make(tmp, "tone.wav", "0.1*sin(2*PI*440*t)")
            m = score.audio_metrics(tone, 0.5, 2.5)
            self.assertAlmostEqual(m["peak_db"], -20.0, delta=1.0)
            self.assertIs(m["clipped"], False)
            self.assertIsNotNone(m["lufs"])
            square = self.make(tmp, "square.wav", "sgn(sin(2*PI*440*t))")
            self.assertIs(score.audio_metrics(square, 0.5, 2.5)["clipped"], True)


if __name__ == "__main__":
    unittest.main()
