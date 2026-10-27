import json
import os
import tempfile
import unittest

from camera_motion.presets import load_catalog


class LoadCatalogTests(unittest.TestCase):
    def _write(self, doc):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(doc, f)
        self.addCleanup(os.remove, path)
        return path

    def test_bundled_catalog_is_a_list(self):
        self.assertEqual(len(load_catalog()), 14)

    def test_object_form_gives_the_same_list(self):
        presets = load_catalog()
        self.assertEqual(load_catalog(self._write({"presets": presets})), presets)

    def test_other_shapes_raise_value_error(self):
        for doc in ("text", 3, {"x": 1}, ["a"]):
            with self.subTest(doc=doc), self.assertRaises(ValueError):
                load_catalog(self._write(doc))


if __name__ == "__main__":
    unittest.main()
