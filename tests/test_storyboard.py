import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "video-organize" / "scripts"))

import storyboard  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class StoryboardTest(unittest.TestCase):
    def test_storyboard_blocks(self):
        scenes, mode = storyboard.load(str(FIXTURES / "storyboard.md"))
        self.assertEqual(mode, "storyboard")
        self.assertEqual([s.id for s in scenes], ["S01", "S03", "S05"])
        s01, s03, s05 = scenes
        self.assertEqual(s01.setup, "counter top")
        self.assertFalse(s01.voiceover)
        self.assertTrue(s03.voiceover)
        self.assertEqual(s03.broll, "note")
        self.assertEqual(s05.text, "")
        self.assertEqual(s05.clauses, [])
        self.assertEqual(s05.broll, "thesaurus window")

    def test_clauses(self):
        text = "Remove em dashes (even though I love them), and make it sound human."
        self.assertEqual(
            storyboard.split_clauses(text),
            ["Remove em dashes", "even though I love them", "and make it sound human"],
        )
        for clause in storyboard.split_clauses("Well, I tried. Honestly, nothing worked at all."):
            self.assertGreater(len(clause.split()), 1)

    def test_one_word_clause_merges(self):
        self.assertEqual(
            storyboard.split_clauses("Yes, I did it, really, again and again."),
            ["Yes I did it really", "again and again"],
        )

    def test_plain_script(self):
        scenes, mode = storyboard.load(str(FIXTURES / "script.md"))
        self.assertEqual(mode, "script")
        self.assertEqual([s.id for s in scenes], ["S01", "S02", "S03"])
        self.assertTrue(all(s.clauses for s in scenes))

    def test_no_file(self):
        self.assertEqual(storyboard.load(None), ([], "none"))

    def test_tokens(self):
        self.assertEqual(storyboard.tokens("I’m 21st-century, café!"), ["im", "21st", "century", "cafe"])


if __name__ == "__main__":
    unittest.main()
