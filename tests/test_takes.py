import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "video-organize" / "scripts"))

import storyboard  # noqa: E402
import takes  # noqa: E402

WORD = 0.3  # seconds per spoken word in the fixtures


def transcript(*phrases, file="/footage/IMG_1.MOV"):
    """phrases: (text, start) or (text, start, language). Words are 0.3 s each, back to back."""
    words, segments = [], []
    for phrase in phrases:
        text, start = phrase[0], phrase[1]
        lang = phrase[2] if len(phrase) > 2 else "en"
        t = start
        for w in text.split():
            words.append({"w": w, "start": round(t, 3), "end": round(t + WORD, 3), "p": 0.9})
            t += WORD
        segments.append({"text": text, "start": start, "end": round(t, 3), "language": lang})
    end = segments[-1]["end"] if segments else 0.0
    return {"file": file, "size": 1, "mtime": 0.0, "duration": end + 1.0, "language": "en",
            "stream": 0, "words": words, "segments": segments}


def scene(id_, text):
    return storyboard.Scene(id_, text, clauses=storyboard.split_clauses(text))


CONFESSION = "Let me start with a confession: AI helped me plan this video"


class FindTakesTest(unittest.TestCase):
    def test_three_attempts_three_takes(self):
        line = "let me start with a confession"
        t = transcript((line, 0.0), (line, 4.8), (line, 9.6))
        found, chatter = takes.find_takes(t, [scene("S01", "Let me start with a confession.")], "en")
        self.assertEqual([k.scene for k in found], ["S01", "S01", "S01"])
        self.assertEqual([k.start for k in found], [0.0, 4.8, 9.6])
        self.assertTrue(all(k.status == "complete" for k in found))
        self.assertTrue(all(len(k.words) == 6 for k in found))
        self.assertEqual(chatter, [])

    def test_reworded(self):
        t = transcript(("Let me begin with a confession: AI helped me plan this video", 0.0))
        found, _ = takes.find_takes(t, [scene("S01", CONFESSION)], "en")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].status, "reworded")
        self.assertEqual(found[0].missing, [])
        self.assertEqual(found[0].extra, ["begin"])

    def test_reworded_clause_is_not_incomplete(self):
        script = "So I stopped asking the machine and started reading my old notes again."
        t = transcript(("so I quit using chatgpt and started reading my old notes again", 0.0))
        found, _ = takes.find_takes(t, [scene("S05", script)], "en")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].status, "reworded")
        self.assertEqual(found[0].missing, [])
        self.assertEqual(found[0].extra, ["quit", "using", "chatgpt"])

    def test_missing_last_words_is_incomplete(self):
        t = transcript(("Let me start with a confession AI helped me plan", 0.0))
        found, _ = takes.find_takes(t, [scene("S01", CONFESSION)], "en")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].status, "incomplete")
        self.assertEqual(found[0].missing, ["this", "video"])

    def test_missing_first_words_is_incomplete(self):
        t = transcript(("start with a confession AI helped me plan this video", 0.0))
        found, _ = takes.find_takes(t, [scene("S01", CONFESSION)], "en")
        self.assertEqual(found[0].status, "incomplete")
        self.assertEqual(found[0].missing, ["let", "me"])

    def test_one_dropped_inner_word_stays_complete(self):
        t = transcript(("Let me start with confession AI helped me plan this video", 0.0))
        found, _ = takes.find_takes(t, [scene("S01", CONFESSION)], "en")
        self.assertEqual(found[0].status, "complete")
        self.assertEqual(found[0].missing, ["a"])

    def test_missing_ending_is_incomplete(self):
        t = transcript(("Let me start with a confession", 0.0))
        found, _ = takes.find_takes(t, [scene("S01", CONFESSION)], "en")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].status, "incomplete")
        self.assertEqual(found[0].missing, ["ai", "helped", "me", "plan", "this", "video"])
        self.assertAlmostEqual(found[0].coverage, 0.5)

    def test_take_split_by_pause_is_one_take(self):
        t = transcript(("Let me start with a confession:", 0.0), ("AI helped me plan this video", 3.0))
        found, chatter = takes.find_takes(t, [scene("S01", CONFESSION)], "en")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].status, "complete")
        self.assertEqual((found[0].start, found[0].end), (0.0, 4.8))
        self.assertEqual(chatter, [])

    def test_chatter_not_take(self):
        line = "let me start with a confession"
        t = transcript((line, 0.0), ("ok one more time", 3.5), (line, 6.0),
                       ("three two one", 9.5), (line, 12.0))
        found, chatter = takes.find_takes(t, [scene("S01", "Let me start with a confession.")], "en")
        self.assertEqual([k.start for k in found], [0.0, 6.0, 12.0])
        self.assertTrue(all(k.scene == "S01" for k in found))
        self.assertEqual([c["text"] for c in chatter], ["ok one more time", "three two one"])
        self.assertEqual(chatter[0]["start"], 3.5)
        self.assertEqual(chatter[0]["end"], 4.7)

    def test_other_language(self):
        hungarian = "na jó akkor ezt most még egyszer elmondom mert nem volt elég jó"
        t = transcript(("let me start with a confession", 0.0), (hungarian, 3.0, "hu"))
        found, chatter = takes.find_takes(t, [scene("S01", "Let me start with a confession.")], "en")
        self.assertEqual([k.scene for k in found], ["S01"])
        self.assertEqual(found[0].language, "en")
        self.assertEqual(len(chatter), 1)
        self.assertEqual(chatter[0]["reason"], "other language")
        self.assertEqual(chatter[0]["text"], hungarian)

    def test_long_off_script_speech_is_unmatched_take(self):
        aside = "by the way the coffee machine in this studio makes a terrible noise every morning"
        t = transcript(("let me start with a confession", 0.0), (aside, 3.0))
        found, chatter = takes.find_takes(t, [scene("S01", "Let me start with a confession.")], "en")
        self.assertEqual([(k.scene, k.status) for k in found], [("S01", "complete"), (None, "unmatched")])
        self.assertEqual(found[1].start, 3.0)
        self.assertEqual(chatter, [])

    def test_multi_scene_recording(self):
        scenes = [
            scene("S01", CONFESSION),
            scene("S03", "Every draft I wrote sounded like a corporate press release."),
            scene("S04", ""),
            scene("S05", "So I stopped asking the machine and started reading my old notes again."),
        ]
        t = transcript(("Every draft I wrote sounded like a corporate press release", 0.0),
                       ("So I stopped asking the machine and started reading my old notes again", 5.0))
        found, chatter = takes.find_takes(t, scenes, "en")
        self.assertEqual([k.scene for k in found], ["S03", "S05"])
        self.assertTrue(all(k.status == "complete" for k in found))
        self.assertEqual(chatter, [])

    def test_no_script_makes_unmatched_takes(self):
        aside = "this is a recording without any script at all just talking freely"
        found, _ = takes.find_takes(transcript((aside, 0.0)), [], None)
        self.assertEqual([k.status for k in found], ["unmatched"])


class IsSpokenTest(unittest.TestCase):
    def test_is_spoken(self):
        self.assertFalse(takes.is_spoken(transcript(("hello there you", 0.0))))
        twenty_seconds = " ".join(["word"] * 67)  # 67 * 0.3 s = 20.1 s
        self.assertTrue(takes.is_spoken(transcript((twenty_seconds, 0.0))))

    def test_short_utterances_add_up(self):
        line = "let me start with a confession"  # 1.8 s each, 1 s pauses
        self.assertTrue(takes.is_spoken(transcript((line, 0.0), (line, 2.8), (line, 5.6))))

    def test_silence_is_not_spoken(self):
        self.assertFalse(takes.is_spoken(transcript()))


if __name__ == "__main__":
    unittest.main()
