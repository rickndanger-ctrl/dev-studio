import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

from camera_motion.cli import main
from camera_motion.export.formats import parse_re10k
from camera_motion.preview.make_anim import update_readme

CATALOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "presets", "catalog.json")
with open(CATALOG, encoding="utf-8") as _f:
    ALL_IDS = {preset["id"] for preset in json.load(_f)}
STUB_HELP = {
    "dolly_in": {"when": "Move toward the subject.", "feel": "Draws the viewer in."},
    "crane_up": {"when": "Reveal the scene from above.", "feel": "Rising and open."},
}
FRAMES = 73  # default 3 s at 24 fps, plus the first frame


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def out_path(self, name):
        return os.path.join(self.dir, name)

    def test_list_has_all_14_ids(self):
        code, out, _ = _run(["list"])
        self.assertEqual(code, 0)
        ids = {line.split("\t")[0] for line in out.splitlines() if line}
        self.assertEqual(len(ALL_IDS), 14)
        self.assertEqual(ids, ALL_IDS, f"missing {ALL_IDS - ids}, extra {ids - ALL_IDS}")

    def test_list_plain_has_no_help_lines(self):
        with mock.patch("camera_motion.cli.load_help", return_value=STUB_HELP):
            code, out, _ = _run(["list"])
        self.assertEqual(code, 0)
        self.assertNotIn("when:", out)
        self.assertTrue(all("\t" in line for line in out.splitlines()))

    def test_list_long_prints_when_and_feel(self):
        with mock.patch("camera_motion.cli.load_help", return_value=STUB_HELP):
            code, out, _ = _run(["list", "--long"])
        self.assertEqual(code, 0)
        lines = out.splitlines()
        i = next(n for n, line in enumerate(lines) if line.split("\t")[0] == "dolly_in")
        self.assertEqual(lines[i + 1], "  when: Move toward the subject.")
        self.assertEqual(lines[i + 2], "  feel: Draws the viewer in.")
        ids = [line.split("\t")[0] for line in lines if line and not line.startswith(" ")]
        self.assertEqual(set(ids), ALL_IDS)  # presets without help still get their id line

    def test_list_long_uses_bundled_help_for_every_preset(self):
        code, out, _ = _run(["list", "--long"])
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual(sum(1 for line in lines if line.startswith("  when: ")), len(ALL_IDS))
        self.assertEqual(sum(1 for line in lines if line.startswith("  feel: ")), len(ALL_IDS))

    def test_path_prints_first_and_last_frame(self):
        code, out, _ = _run(["path", "dolly_in"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["first"]["t"], 0)
        self.assertAlmostEqual(data["last"]["t"], 3.0)

    def test_preview_writes_parseable_svg(self):
        target = self.out_path("dolly_in.svg")
        code, _, _ = _run(["preview", "dolly_in", "-o", target])
        self.assertEqual(code, 0)
        self.assertEqual(ET.parse(target).getroot().tag.split("}")[-1], "svg")

    def test_preview_animate_writes_one_motion_path(self):
        target = self.out_path("dolly_in_anim.svg")
        code, _, _ = _run(["preview", "dolly_in", "--animate", "-o", target])
        self.assertEqual(code, 0)
        with open(target, encoding="utf-8") as f:
            text = f.read()
        ET.fromstring(text)  # well-formed XML
        self.assertEqual(text.count("<animateMotion"), 1)

    def test_export_re10k_has_73_frames(self):
        target = self.out_path("dolly_in.txt")
        code, _, _ = _run(["export", "dolly_in", "--format", "re10k", "-o", target])
        self.assertEqual(code, 0)
        with open(target, encoding="utf-8") as f:
            parsed = parse_re10k(f.read().splitlines())
        self.assertEqual(len(parsed), FRAMES)

    def test_export_json_loads(self):
        target = self.out_path("dolly_in.json")
        code, _, _ = _run(["export", "dolly_in", "--format", "json", "-o", target])
        self.assertEqual(code, 0)
        with open(target, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(len(data["frames"]), FRAMES)

    def test_export_blender_compiles(self):
        target = self.out_path("dolly_in.py")
        code, _, _ = _run(["export", "dolly_in", "--format", "blender", "-o", target])
        self.assertEqual(code, 0)
        with open(target, encoding="utf-8") as f:
            source = f.read()
        compile(source, target, "exec")

    def test_export_gltf_has_animations(self):
        target = self.out_path("dolly_in.gltf")
        code, _, _ = _run(["export", "dolly_in", "--format", "gltf", "-o", target])
        self.assertEqual(code, 0)
        with open(target, encoding="utf-8") as f:
            data = json.load(f)
        self.assertTrue(data["animations"])

    def test_preset_file_is_accepted(self):
        preset_file = self.out_path("preset.json")
        with open(preset_file, "w", encoding="utf-8") as f:
            json.dump({"type": "tilt", "params": {"angle": 15}, "easing": "linear"}, f)
        code, out, _ = _run(["path", preset_file])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["last"]["rotation"][1], 15)

    def test_unknown_id_exits_2_and_lists_ids(self):
        code, _, err = _run(["path", "no_such_preset"])
        self.assertEqual(code, 2)
        self.assertIn("no_such_preset", err)
        self.assertIn("dolly_in", err)


class ReadmeGridTests(unittest.TestCase):
    PRESETS = [{"id": f"p{i}", "name": f"Preset {i}"} for i in range(5)]

    def test_inserts_under_heading_when_no_markers(self):
        out = update_readme("# camera-motion\n\nIntro line.\n", self.PRESETS)
        self.assertTrue(out.startswith("# camera-motion\n\n<!-- anim:start -->"))
        self.assertIn("Intro line.", out)

    def test_replaces_between_markers_and_is_idempotent(self):
        once = update_readme("# t\n\nIntro.\n", self.PRESETS)
        twice = update_readme(once, self.PRESETS[:2])
        self.assertEqual(twice.count("<!-- anim:start -->"), 1)
        self.assertNotIn('src="docs/anim/p4.svg"', twice)
        self.assertEqual(update_readme(twice, self.PRESETS[:2]), twice)

    def test_four_thumbnails_per_row(self):
        presets = [{"id": f"p{i}", "name": "x"} for i in range(9)]
        out = update_readme("# t\n", presets)
        self.assertEqual(out.count("<tr>"), 3)
        self.assertEqual(out.count('<img src="docs/anim/'), 9)


class OutputPathTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def test_export_creates_missing_folders(self):
        target = os.path.join(self.dir, "a", "b", "dolly_in.txt")
        code, _, err = _run(["export", "dolly_in", "--format", "re10k", "-o", target])
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isfile(target))

    def test_preview_creates_missing_folders(self):
        target = os.path.join(self.dir, "a", "b", "dolly_in.svg")
        code, _, err = _run(["preview", "dolly_in", "-o", target])
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isfile(target))

    def test_export_unwritable_output_is_one_line_error(self):
        blocker = os.path.join(self.dir, "blocker")
        with open(blocker, "w", encoding="utf-8") as f:  # a file where a folder is needed
            f.write("x")
        target = os.path.join(blocker, "dolly_in.txt")
        code, _, err = _run(["export", "dolly_in", "--format", "re10k", "-o", target])
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith("error: "), err)
        self.assertEqual(err.strip().count("\n"), 0, err)
        self.assertNotIn("Traceback", err)

    def test_preview_unwritable_output_is_one_line_error(self):
        blocker = os.path.join(self.dir, "blocker")
        with open(blocker, "w", encoding="utf-8") as f:
            f.write("x")
        target = os.path.join(blocker, "dolly_in.svg")
        code, _, err = _run(["preview", "dolly_in", "-o", target])
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith("error: "), err)
        self.assertEqual(err.strip().count("\n"), 0, err)
        self.assertNotIn("Traceback", err)


class ImportMatchTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def test_match_ranks_exported_re10k_file_first(self):
        target = os.path.join(self.dir, "tilt_up.txt")
        code, _, err = _run(["export", "tilt_up", "--format", "re10k", "-o", target])
        self.assertEqual(code, 0, err)
        code, out, err = _run(["match", target])
        self.assertEqual(code, 0, err)
        lines = out.splitlines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[0].startswith("tilt_up  "), lines[0])

    def test_gltf_dolly_zoom_ties_with_dolly_and_prints_note(self):
        source = os.path.join(self.dir, "dolly_zoom_in.gltf")
        code, _, err = _run(["export", "dolly_zoom_in", "--format", "gltf", "-o", source])
        self.assertEqual(code, 0, err)
        code, out, err = _run(["match", source])
        self.assertEqual(code, 0, err)
        lines = out.splitlines()
        self.assertEqual(lines[-1], "note: glTF has no zoom track; dolly and dolly_zoom look the same")
        top = lines[:-1]
        self.assertEqual(len(top), 3, out)
        self.assertEqual({line.split()[0] for line in top[:2]}, {"dolly_in", "dolly_zoom_in"}, out)
        self.assertEqual(top[0].split()[-1], top[1].split()[-1], out)

    def test_import_writes_fps_and_frames_json(self):
        source = os.path.join(self.dir, "dolly_in.txt")
        code, _, err = _run(["export", "dolly_in", "--format", "re10k", "-o", source])
        self.assertEqual(code, 0, err)
        target = os.path.join(self.dir, "out", "dolly_in.json")
        code, _, err = _run(["import", source, "-o", target])
        self.assertEqual(code, 0, err)
        with open(target, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(len(data["frames"]), FRAMES)
        self.assertIn("fps", data)
        self.assertEqual(set(data["frames"][0]), {"t", "position", "rotation", "fov"})


if __name__ == "__main__":
    unittest.main()
