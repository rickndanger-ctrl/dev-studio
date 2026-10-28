"""Every size: one render writes a preview for each size and sizes.zip; the page lists them and serves them."""
import io
import os
import zipfile

from makeclip import server as clipserver
from makeclip.test_server import ServerCase, fake_renderer, good_render

SIZE_IDS = ("square", "wide", "vertical")


def every_renderer(commands):
    """Records each command and writes what stillmove render --sizes writes: <id>/preview.mp4 and sizes.zip."""
    def render(command, env, clip_dir, expected):
        commands.append(command)
        for size in SIZE_IDS:
            os.makedirs(os.path.join(clip_dir, size), exist_ok=True)
            with open(os.path.join(clip_dir, size, "preview.mp4"), "wb") as f:
                f.write(b"\x00\x00\x00\x18ftypmp42")
        with zipfile.ZipFile(os.path.join(clip_dir, "sizes.zip"), "w") as archive:
            for size in SIZE_IDS:
                archive.writestr("%s/preview.mp4" % size, b"mp4")
        return 0, ""
    return render


def every_render(**over):
    body = good_render(**over)
    body["size"] = "every"
    return body


class EverySizeTests(ServerCase):
    def setUp(self):
        self.commands = []
        self.renderer = every_renderer(self.commands)
        super().setUp()
        self.add_input()

    def test_every_size_builds_one_sizes_command(self):
        status, body, _ = self.client.post("/api/render", every_render())
        self.assertEqual(status, 202)
        done = self.client.wait_done()
        self.assertEqual(done["state"], "done", done.get("message"))
        command = self.commands[0]
        self.assertEqual(command[command.index("--sizes") + 1], "square,wide,vertical")
        self.assertNotIn("--preset", command)
        self.assertIn("-o", command)

    def test_result_lists_three_files_and_the_zip(self):
        self.client.post("/api/render", every_render())
        done = self.client.wait_done()
        self.assertEqual([row["size"] for row in done["files"]], list(SIZE_IDS))
        for row in done["files"]:
            self.assertTrue(row["mp4"].endswith("/%s/preview.mp4" % row["size"]), row)
        self.assertTrue(done["zip"].endswith("/sizes.zip"))

    def test_zip_route_serves_an_archive(self):
        self.client.post("/api/render", every_render())
        done = self.client.wait_done()
        status, body, extra = self.client.get(done["zip"])
        self.assertEqual(status, 200)
        self.assertEqual(extra.get("Content-Type"), "application/zip")
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            self.assertEqual(sorted(archive.namelist()), ["square/preview.mp4", "vertical/preview.mp4",
                                                          "wide/preview.mp4"])

    def test_a_size_preview_downloads_with_a_plain_name(self):
        self.client.post("/api/render", every_render())
        done = self.client.wait_done()
        url = done["files"][1]["mp4"]
        status, _body, extra = self.client.get(url + "?download=1")
        self.assertEqual(status, 200)
        self.assertEqual(extra.get("Content-Type"), "video/mp4")
        self.assertIn("attachment", extra.get("Content-Disposition", ""))

    def test_the_clip_is_listed_with_its_files(self):
        self.client.post("/api/render", every_render())
        self.client.wait_done()
        clips = self.studio.clips()
        self.assertEqual(len(clips), 1)
        self.assertEqual(len(clips[0]["files"]), 3)
        self.assertTrue(clips[0]["zip"].endswith("/sizes.zip"))

    def test_a_size_missing_from_the_output_is_an_error(self):
        def partial(command, env, clip_dir, expected):
            os.makedirs(os.path.join(clip_dir, "square"), exist_ok=True)
            with open(os.path.join(clip_dir, "square", "preview.mp4"), "wb") as f:
                f.write(b"mp4")
            return 0, ""
        self.studio.renderer = partial
        self.client.post("/api/render", every_render())
        done = self.client.wait_done()
        self.assertEqual(done["state"], "error")
        self.assertIn("every size", done["message"])

    def test_a_single_clip_is_unchanged(self):
        single = fake_renderer()

        def recording(command, env, clip_dir, expected):
            self.commands.append(command)
            return single(command, env, clip_dir, expected)
        self.studio.renderer = recording
        self.client.post("/api/render", good_render())
        done = self.client.wait_done()
        self.assertEqual(done["state"], "done")
        self.assertNotIn("files", done)
        self.assertNotIn("zip", done)
        self.assertNotIn("--sizes", self.commands[-1])


if __name__ == "__main__":
    import unittest

    unittest.main()
