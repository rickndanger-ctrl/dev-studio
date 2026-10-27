import json
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.join(HERE, "catalog.json")
HELP = os.path.join(HERE, "help.json")
MAX_CHARS = 90
KEYS = ("when", "feel")
BANNED = ("epic", "stunning", "cinematic", "breathtaking", "amazing", "magic", "mesmerizing",
          "jaw-dropping", "hype")
DIRECTION_SUFFIXES = {"_left": "left", "_right": "right", "_up": "up", "_down": "down"}


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class HelpTests(unittest.TestCase):
    def setUp(self):
        self.catalog_ids = {preset["id"] for preset in _load(CATALOG)}
        self.help = _load(HELP)

    def test_same_ids_as_catalog(self):
        self.assertEqual(set(self.help), self.catalog_ids)
        self.assertEqual(len(self.help), 14)

    def test_each_entry_has_both_keys_and_nothing_else(self):
        for preset_id, entry in self.help.items():
            with self.subTest(id=preset_id):
                self.assertEqual(set(entry), set(KEYS))

    def test_lines_are_short_single_sentences(self):
        for preset_id, entry in self.help.items():
            for key in KEYS:
                text = entry[key]
                with self.subTest(id=preset_id, key=key):
                    self.assertIsInstance(text, str)
                    self.assertTrue(text.strip())
                    self.assertLessEqual(len(text), MAX_CHARS)
                    self.assertNotIn("\n", text)
                    self.assertTrue(text.endswith("."))
                    self.assertEqual(text.count(". "), 0, "more than one sentence")

    def test_no_hype_words(self):
        for preset_id, entry in self.help.items():
            for key in KEYS:
                words = set(re.findall(r"[a-z\-]+", entry[key].lower()))
                with self.subTest(id=preset_id, key=key):
                    for banned in BANNED:
                        self.assertNotIn(banned, words, f"banned word {banned!r}")

    def test_direction_words_match_the_preset(self):
        for preset_id in self.catalog_ids:
            for suffix, word in DIRECTION_SUFFIXES.items():
                if not preset_id.endswith(suffix):
                    continue
                feel = self.help[preset_id]["feel"].lower()
                with self.subTest(id=preset_id):
                    self.assertRegex(feel, rf"\b{word}\b")


if __name__ == "__main__":
    unittest.main()
