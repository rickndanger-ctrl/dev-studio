"""makeclip passes the picture-shape choice to both renders and its page offers both choices."""
import os
import unittest

from makeclip import server

PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.html")


class AspectWiringTests(unittest.TestCase):
    def test_promo_command_defaults_to_crop(self):
        command = server.promo_command("/py", "/plan.json", "/clip", layers=False, mp4=False)
        self.assertEqual(command[command.index("--aspect") + 1], "crop")

    def test_promo_command_passes_pad_through(self):
        command = server.promo_command("/py", "/plan.json", "/clip", layers=False, mp4=False, aspect="pad")
        self.assertEqual(command[command.index("--aspect") + 1], "pad")

    def test_the_page_offers_crop_and_pad_in_both_forms(self):
        with open(PAGE, encoding="utf-8") as handle:
            page = handle.read()
        self.assertIn('id="aspect"', page)
        self.assertIn('id="promo-aspect"', page)
        self.assertIn('value="crop"', page)
        self.assertIn('value="pad"', page)


if __name__ == "__main__":
    unittest.main()
