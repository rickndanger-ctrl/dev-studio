"""Tests for "Make a promo" on the make-a-clip server: plan building from the form, loading a plan
with missing pictures, the 2-to-6 shot limits, the crossfade rule, path safety, the job states
(including 409 across both tabs), the header guard on the new POSTs, and the read-only plans root.

Temporary folders only, and a fake renderer: the real ~/Pictures is never touched."""

import json
import os
import threading
import time
import sys
import unittest

from makeclip import server as clipserver
from makeclip.test_server import JPG, PNG, ServerCase, fake_renderer, good_render


def promo_body(shots, **over):
    body = {"size": "square", "source": None, "shots": shots}
    body.update(over)
    return body


def form_shot(image, preset="dolly_in", amount=0.4, seconds=2, transition="cut", crossfade=None, layers=False):
    shot = {"image": image, "preset": preset, "amount": amount, "seconds": seconds,
            "transition": transition, "layers": layers}
    if crossfade is not None:
        shot["crossfade"] = crossfade
    return shot


def gated_renderer(gate):
    """A renderer that waits for gate (set by the test) before it writes anything, then writes like
    the fake one."""
    def render(command, env, clip_dir, expected):
        gate.wait(10)
        return fake_renderer()(command, env, clip_dir, expected)
    return render


class PromoCase(ServerCase):
    def add_pictures(self, *names):
        for name in names:
            self.add_input(name, JPG if name.endswith((".jpg", ".jpeg")) else PNG)

    def write_plan(self, rel, data, pictures=()):
        """A plan file under the plans root, with its own pictures next to it."""
        full = os.path.join(self.plans, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            json.dump(data, f)
        for name in pictures:
            with open(os.path.join(os.path.dirname(full), name), "wb") as f:
                f.write(JPG if name.endswith((".jpg", ".jpeg")) else PNG)
        return full

    def sample_shop_shape(self):
        """A plan of three shots whose photos shot-1..3 are not on disk, except shot-2."""
        return {"id": "sample_shop", "name": "Sample Shop", "fps": 24, "width": 1080, "height": 1080, "shots": [
            {"id": "shot_1", "image": "shot-1.jpg", "sequence": {"segments": [
                {"preset": "dolly_in", "duration_s": 2, "params": {"distance": 0.4}}]},
             "duration": 2, "transition": {"crossfade": 0.5}},
            {"id": "shot_2", "image": "shot-2.jpg", "sequence": {"segments": [
                {"preset": "orbit_left", "duration_s": 2, "params": {"angle": -6}}]},
             "duration": 2, "transition": {"crossfade": 0.5}},
            {"id": "shot_3", "image": "shot-3.jpg", "sequence": {"segments": [
                {"preset": "crane_up", "duration_s": 2, "params": {"distance": 0.25, "frame": "camera"}}]},
             "duration": 2, "transition": "cut"},
        ]}

    def clip_folder(self, status):
        return os.path.join(self.outputs, status["clip"])


class PlanBuildTests(PromoCase):
    def test_the_form_becomes_the_exact_plan_json(self):
        self.add_pictures("a.png", "b.png")
        status, job, _ = self.client.post("/api/promo/render", promo_body([
            form_shot("a.png", "dolly_in", 0.4, 2, "crossfade", 0.5),
            form_shot("b.png", "orbit_left", 8, 3, "cut"),
        ]))
        self.assertEqual(status, 202, job)
        done = self.client.wait_done()
        self.assertEqual(done["state"], "done", done)
        with open(os.path.join(self.clip_folder(done), "plan.json"), encoding="utf-8") as f:
            plan = json.load(f)
        self.assertEqual(plan, {
            "id": "promo", "name": "Promo", "fps": 24, "width": 1080, "height": 1080,
            "shots": [
                {"id": "shot_1", "image": "images/shot-1.png",
                 "sequence": {"segments": [{"preset": "dolly_in", "duration_s": 2, "params": {"distance": 0.4}}]},
                 "duration": 2, "transition": {"crossfade": 0.5}},
                {"id": "shot_2", "image": "images/shot-2.png",
                 "sequence": {"segments": [{"preset": "orbit_left", "duration_s": 3, "params": {"angle": -8}}]},
                 "duration": 3, "transition": "cut"},
            ]})

    def test_the_pictures_are_copied_into_the_clip_and_the_clip_is_listed_as_a_promo(self):
        self.add_pictures("a.png", "b.png")
        self.client.post("/api/promo/render", promo_body([form_shot("a.png"), form_shot("b.png")]))
        done = self.client.wait_done()
        with open(os.path.join(self.clip_folder(done), "images", "shot-2.png"), "rb") as f:
            self.assertEqual(f.read(), PNG)
        _, clips, _ = self.client.get("/api/clips")
        self.assertEqual(clips[0]["kind"], "promo")
        self.assertEqual(clips[0]["shots"], 2)
        self.assertEqual(clips[0]["name"], done["clip"])
        self.assertTrue(clips[0]["mp4"].endswith("/preview.mp4"))

    def test_a_form_shot_with_a_missing_picture_is_400(self):
        self.add_pictures("a.png")
        status, body, _ = self.client.post("/api/promo/render", promo_body([form_shot("a.png"),
                                                                           form_shot("gone.png")]))
        self.assertEqual(status, 400)
        self.assertIn("shot 2", body["error"])
        self.assertIn("gone.png", body["error"])


class PlanLoadTests(PromoCase):
    def test_a_plan_loads_with_its_missing_photos_listed(self):
        self.write_plan("shop/plan.json", self.sample_shop_shape(), pictures=["shot-2.jpg"])
        status, loaded, _ = self.client.post("/api/promo/load", {"path": "shop/plan.json"})
        self.assertEqual(status, 200, loaded)
        self.assertEqual(loaded["missing"], ["shot-1.jpg", "shot-3.jpg"])
        self.assertEqual([s["found"] for s in loaded["shots"]], [False, True, False])
        first = loaded["shots"][0]
        self.assertEqual((first["preset"], first["amount"], first["seconds"]), ("dolly_in", 0.4, 2.0))
        self.assertEqual((first["transition"], first["crossfade"], first["editable"]), ("crossfade", 0.5, True))
        self.assertEqual(loaded["shots"][1]["amount"], 6.0)
        self.assertEqual(loaded["size"], "square")

    def test_a_missing_photo_blocks_the_render_with_a_plain_message(self):
        self.write_plan("shop/plan.json", self.sample_shop_shape(), pictures=["shot-2.jpg"])
        status, body, _ = self.client.post("/api/promo/render", promo_body(
            [form_shot("shot-1.jpg"), form_shot("shot-2.jpg")], source="shop/plan.json"))
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "Still needed: shot-1.jpg")

    def test_a_shot_the_form_cannot_show_is_read_only_but_listed(self):
        plan = self.sample_shop_shape()
        plan["shots"][0]["sequence"]["segments"].append({"preset": "pan_left", "duration_s": 2,
                                                         "params": {"angle": 6}})
        plan["shots"][1]["sequence"]["segments"][0]["params"]["radius"] = 5.0
        self.write_plan("shop/plan.json", plan, pictures=["shot-1.jpg", "shot-2.jpg", "shot-3.jpg"])
        _, loaded, _ = self.client.post("/api/promo/load", {"path": "shop/plan.json"})
        self.assertFalse(loaded["shots"][0]["editable"])
        self.assertIsNone(loaded["shots"][0]["preset"])
        self.assertFalse(loaded["shots"][1]["editable"])
        self.assertTrue(loaded["shots"][2]["editable"])

    def test_an_amount_outside_the_safe_range_is_read_only(self):
        plan = self.sample_shop_shape()
        plan["shots"][0]["sequence"]["segments"][0]["params"]["distance"] = 2.0
        self.write_plan("shop/plan.json", plan, pictures=["shot-1.jpg", "shot-2.jpg", "shot-3.jpg"])
        _, loaded, _ = self.client.post("/api/promo/load", {"path": "shop/plan.json"})
        self.assertFalse(loaded["shots"][0]["editable"])

    def test_the_plan_listing_goes_three_folders_deep_and_no_further(self):
        self.write_plan("a/b/c/plan.json", {"name": "deep enough", "shots": []})
        self.write_plan("a/b/c/d/too-deep.json", {"name": "too deep", "shots": []})
        self.write_plan("top.json", {"name": "top", "shots": [{}, {}]})
        self.write_plan("notes.json", {"name": "not a plan"})
        _, plans, _ = self.client.get("/api/promo/plans")
        self.assertEqual([p["path"] for p in plans], ["a/b/c/plan.json", "top.json"])
        self.assertEqual(next(p for p in plans if p["path"] == "top.json")["shots"], 2)

    def test_the_plans_root_is_never_written_to(self):
        self.write_plan("shop/plan.json", self.sample_shop_shape(), pictures=["shot-2.jpg"])

        def snapshot():
            seen = {}
            for folder, _, files in os.walk(self.plans):
                for name in files:
                    full = os.path.join(folder, name)
                    with open(full, "rb") as f:
                        seen[full] = (f.read(), os.path.getmtime(full))
            return seen

        before = snapshot()
        self.client.get("/api/promo/plans")
        self.client.post("/api/promo/load", {"path": "shop/plan.json"})
        self.client.post("/api/promo/render", promo_body(
            [form_shot("shot-2.jpg"), form_shot("shot-2.jpg")], source="shop/plan.json"))
        self.client.wait_done()
        self.assertEqual(snapshot(), before)


class PathSafetyTests(PromoCase):
    def test_a_path_outside_pictures_is_400(self):
        outside = os.path.join(self.outside, "plan.json")
        with open(outside, "w", encoding="utf-8") as f:
            json.dump({"shots": []}, f)
        for rel in ("../outside/plan.json", "/etc/passwd.json", "shop/../../outside/plan.json", "plan.txt"):
            with self.subTest(rel=rel):
                self.assertEqual(self.client.post("/api/promo/load", {"path": rel})[0], 400)

    def test_a_plan_file_symlinked_out_is_400(self):
        outside = os.path.join(self.outside, "plan.json")
        with open(outside, "w", encoding="utf-8") as f:
            json.dump({"shots": []}, f)
        os.symlink(outside, os.path.join(self.plans, "link.json"))
        self.assertEqual(self.client.post("/api/promo/load", {"path": "link.json"})[0], 400)

    def test_a_folder_symlinked_out_is_400(self):
        with open(os.path.join(self.outside, "plan.json"), "w", encoding="utf-8") as f:
            json.dump({"shots": []}, f)
        os.symlink(self.outside, os.path.join(self.plans, "linked"))
        self.assertEqual(self.client.post("/api/promo/load", {"path": "linked/plan.json"})[0], 400)

    def test_a_missing_plan_is_404(self):
        self.assertEqual(self.client.post("/api/promo/load", {"path": "nope/plan.json"})[0], 404)

    def test_a_picture_symlinked_out_of_the_plans_root_counts_as_missing(self):
        secret = os.path.join(self.outside, "secret.png")
        with open(secret, "wb") as f:
            f.write(PNG)
        plan = self.sample_shop_shape()
        plan["shots"][0]["image"] = "steal.png"
        self.write_plan("shop/plan.json", plan)
        os.symlink(secret, os.path.join(self.plans, "shop", "steal.png"))
        _, loaded, _ = self.client.post("/api/promo/load", {"path": "shop/plan.json"})
        self.assertFalse(loaded["shots"][0]["found"])
        self.assertIn("steal.png", loaded["missing"])

    def test_a_render_from_a_plan_outside_pictures_is_400(self):
        status, _, _ = self.client.post("/api/promo/render", promo_body(
            [form_shot("a.png"), form_shot("a.png")], source="../outside/plan.json"))
        self.assertEqual(status, 400)

    def test_a_picture_path_with_dots_in_a_plan_is_not_used(self):
        plan = self.sample_shop_shape()
        plan["shots"][0]["image"] = "../../outside/secret.png"
        self.write_plan("shop/plan.json", plan)
        _, loaded, _ = self.client.post("/api/promo/load", {"path": "shop/plan.json"})
        self.assertFalse(loaded["shots"][0]["found"])


class ShotLimitTests(PromoCase):
    def test_one_shot_is_400(self):
        self.add_pictures("a.png")
        status, body, _ = self.client.post("/api/promo/render", promo_body([form_shot("a.png")]))
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "a promo needs 2 to 6 shots")

    def test_seven_shots_is_400(self):
        self.add_pictures("a.png")
        status, _, _ = self.client.post("/api/promo/render", promo_body([form_shot("a.png")] * 7))
        self.assertEqual(status, 400)

    def test_two_and_six_shots_are_accepted(self):
        self.add_pictures("a.png")
        status, _, _ = self.client.post("/api/promo/render", promo_body([form_shot("a.png")] * 2))
        self.assertEqual(status, 202)
        self.client.wait_done()
        status, _, _ = self.client.post("/api/promo/render", promo_body([form_shot("a.png")] * 6))
        self.assertEqual(status, 202)
        self.client.wait_done()


class CrossfadeRuleTests(PromoCase):
    def render(self, shots):
        self.add_pictures("a.png")
        return self.client.post("/api/promo/render", promo_body(shots))[0]

    def test_a_crossfade_as_long_as_its_own_shot_is_400(self):
        self.assertEqual(self.render([form_shot("a.png", seconds=2, transition="crossfade", crossfade=2),
                                      form_shot("a.png", seconds=4)]), 400)

    def test_a_crossfade_as_long_as_the_next_shot_is_400(self):
        self.assertEqual(self.render([form_shot("a.png", seconds=4, transition="crossfade", crossfade=2),
                                      form_shot("a.png", seconds=2)]), 400)

    def test_a_crossfade_shorter_than_both_neighbours_is_accepted(self):
        self.assertEqual(self.render([form_shot("a.png", seconds=4, transition="crossfade", crossfade=1.5),
                                      form_shot("a.png", seconds=3)]), 202)
        self.client.wait_done()

    def test_the_last_shot_cannot_crossfade(self):
        self.assertEqual(self.render([form_shot("a.png"),
                                      form_shot("a.png", seconds=4, transition="crossfade", crossfade=1)]), 400)

    def test_a_crossfade_must_be_above_zero(self):
        self.assertEqual(self.render([form_shot("a.png", seconds=4, transition="crossfade", crossfade=0),
                                      form_shot("a.png", seconds=4)]), 400)

    def test_a_transition_other_than_cut_or_crossfade_is_400(self):
        self.assertEqual(self.render([form_shot("a.png", transition="fade"), form_shot("a.png")]), 400)


class PromoJobTests(PromoCase):
    def test_promo_progress_shows_the_shot_then_the_joining(self):
        self.add_pictures("a.png", "b.png")
        pause = threading.Event()
        steps = threading.Event()

        def stepped(command, env, clip_dir, expected):
            plan_path = command[command.index("render") + 1]
            with open(plan_path, encoding="utf-8") as f:
                shot_ids = [s["id"] for s in json.load(f)["shots"]]
            first = os.path.join(clip_dir, "shots", shot_ids[0], "frames")
            os.makedirs(first)
            for i in range(3):
                open(os.path.join(first, "%04d.png" % i), "wb").close()
            pause.set()
            steps.wait(10)
            second = os.path.join(clip_dir, "shots", shot_ids[1], "frames")
            os.makedirs(second)
            os.makedirs(os.path.join(clip_dir, "frames"))
            with open(os.path.join(clip_dir, "preview.mp4"), "wb") as f:
                f.write(b"\x00\x00\x00\x18ftypmp42")
            return 0, ""

        self.studio.renderer = stepped
        self.client.post("/api/promo/render", promo_body([form_shot("a.png"), form_shot("b.png")]))
        pause.wait(10)
        _, status, _ = self.client.get("/api/render/status")
        self.assertEqual((status["kind"], status["stage"], status["shot"], status["shots"]),
                         ("promo", "shots", 1, 2))
        self.assertEqual(status["frame"], 3)
        steps.set()
        done = self.client.wait_done()
        self.assertEqual(done["state"], "done")
        self.assertEqual((done["stage"], done["shot"], done["frame"]), ("joining", 2, done["frames"]))

    def test_the_first_poll_reports_the_first_shots_own_frame_total(self):
        self.add_pictures("a.png", "b.png")
        pause = threading.Event()
        release = threading.Event()

        def held(command, env, clip_dir, expected):
            pause.set()
            release.wait(10)
            return 0, ""

        self.studio.renderer = held
        self.client.post("/api/promo/render", promo_body([form_shot("a.png", seconds=2), form_shot("b.png", seconds=3)]))
        pause.wait(10)
        _, status, _ = self.client.get("/api/render/status")
        # Nothing written yet: the total is shot 1's own frames (2 s), not the promo's 5 s.
        self.assertEqual((status["shot"], status["frame"]), (1, 0))
        self.assertEqual(status["frames"], round(2 * clipserver.FPS) + 1)
        release.set()
        self.client.wait_done()

    def test_a_clip_and_a_promo_share_one_job_slot(self):
        self.add_pictures("a.png", "b.png", "still.png")
        promo = promo_body([form_shot("a.png"), form_shot("b.png")])
        # A clip is blocked in the renderer; a promo is refused while it runs.
        gate = threading.Event()
        self.studio.renderer = gated_renderer(gate)
        self.assertEqual(self.client.post("/api/render", good_render())[0], 202)
        self.assertEqual(self.client.post("/api/promo/render", promo)[0], 409)
        gate.set()
        self.client.wait_done()
        # Now a promo is blocked; a clip is refused while it runs.
        gate = threading.Event()
        self.studio.renderer = gated_renderer(gate)
        self.assertEqual(self.client.post("/api/promo/render", promo)[0], 202)
        self.assertEqual(self.client.post("/api/render", good_render())[0], 409)
        gate.set()
        self.client.wait_done()

    def test_a_promo_render_failure_reports_the_last_stderr_line(self):
        self.add_pictures("a.png", "b.png")
        self.studio.renderer = fake_renderer(fail="shot 2: no picture")
        self.client.post("/api/promo/render", promo_body([form_shot("a.png"), form_shot("b.png")]))
        done = self.client.wait_done()
        self.assertEqual((done["state"], done["message"]), ("error", "shot 2: no picture"))

    def test_a_render_is_allowed_again_after_a_promo_finishes(self):
        self.add_pictures("a.png", "b.png", "still.png")
        self.client.post("/api/promo/render", promo_body([form_shot("a.png"), form_shot("b.png")]))
        self.client.wait_done()
        self.assertEqual(self.client.post("/api/render", good_render())[0], 202)
        self.client.wait_done()


class PromoGuardTests(PromoCase):
    def test_every_new_post_without_the_header_is_403(self):
        self.add_pictures("a.png", "b.png")
        for path, body in (("/api/promo/load", {"path": "x.json"}),
                           ("/api/promo/render", promo_body([form_shot("a.png"), form_shot("b.png")]))):
            with self.subTest(path=path):
                status, _, _ = self.client.request("POST", path, body=body, headers={"X-Requested-With": ""})
                self.assertEqual(status, 403)

    def test_a_foreign_origin_on_a_new_post_is_403(self):
        status, _, _ = self.client.post("/api/promo/load", {"path": "x.json"},
                                        headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)

    def test_a_foreign_host_on_a_new_get_is_403(self):
        status, _, _ = self.client.get("/api/promo/plans", host="evil.example:8820")
        self.assertEqual(status, 403)

    def test_a_new_post_with_a_form_content_type_is_415(self):
        status, _, _ = self.client.request("POST", "/api/promo/load", raw=b"path=x",
                                           headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 415)

    def test_the_plans_listing_needs_no_header(self):
        status, _, _ = self.client.get("/api/promo/plans")
        self.assertEqual(status, 200)


class KeptShotTests(PromoCase):
    """A loaded plan's shot the form cannot change is kept as the plan has it and rendered verbatim."""

    def two_segment_plan(self):
        return {"id": "kept", "name": "Kept", "fps": 24, "width": 1080, "height": 1080, "shots": [
            {"id": "shot_1", "image": "one.jpg", "sequence": {"segments": [
                {"preset": "dolly_in", "duration_s": 1, "params": {"distance": 0.4}},
                {"preset": "orbit_left", "duration_s": 1, "params": {"angle": -6}}]},
             "duration": 2, "transition": {"crossfade": 0.5}},
            {"id": "shot_2", "image": "two.jpg", "sequence": {"segments": [
                {"preset": "crane_up", "duration_s": 2, "params": {"distance": 0.25}}]},
             "duration": 2, "transition": "cut"},
        ]}

    def load_kept(self):
        self.write_plan("kept/plan.json", self.two_segment_plan(), pictures=["one.jpg", "two.jpg"])
        _, loaded, _ = self.client.post("/api/promo/load", {"path": "kept/plan.json"})
        return loaded

    def test_a_two_segment_shot_loads_read_only_with_its_sequence_kept(self):
        loaded = self.load_kept()
        first = loaded["shots"][0]
        self.assertFalse(first["editable"])
        self.assertEqual(first["raw"]["sequence"], self.two_segment_plan()["shots"][0]["sequence"])
        self.assertEqual(first["raw"]["duration"], 2)
        self.assertEqual(first["raw"]["transition"], {"crossfade": 0.5})
        self.assertTrue(loaded["shots"][1]["editable"])

    def test_a_kept_shot_renders_its_sequence_verbatim(self):
        loaded = self.load_kept()
        status, job, _ = self.client.post("/api/promo/render", promo_body([
            {"image": "one.jpg", "raw": loaded["shots"][0]["raw"]},
            form_shot("two.jpg", "crane_up", 0.25, 2, "cut"),
        ], source="kept/plan.json"))
        self.assertEqual(status, 202, job)
        done = self.client.wait_done()
        self.assertEqual(done["state"], "done", done)
        with open(os.path.join(self.clip_folder(done), "plan.json"), encoding="utf-8") as f:
            plan = json.load(f)
        self.assertEqual(plan["shots"][0]["sequence"], self.two_segment_plan()["shots"][0]["sequence"])
        self.assertEqual(plan["shots"][0]["transition"], {"crossfade": 0.5})
        self.assertEqual(plan["shots"][0]["image"], "images/shot-1.jpg")
        with open(os.path.join(self.clip_folder(done), "images", "shot-1.jpg"), "rb") as f:
            self.assertEqual(f.read(), JPG)

    def test_a_kept_shot_with_a_bad_sequence_is_refused_by_shotplan(self):
        loaded = self.load_kept()
        raw = loaded["shots"][0]["raw"]
        raw["sequence"]["segments"][1]["preset"] = "dolly_sideways"
        status, body, _ = self.client.post("/api/promo/render", promo_body([
            {"image": "one.jpg", "raw": raw},
            form_shot("two.jpg", "crane_up", 0.25, 2, "cut"),
        ], source="kept/plan.json"))
        self.assertEqual(status, 400)
        self.assertIn("unknown preset", body["error"])
        leftover = [name for name in os.listdir(self.outputs) if name.startswith("promo-")]
        self.assertEqual(leftover, [], "a refused promo must leave no folder behind")

    def test_a_kept_shot_needs_the_plan_it_came_from(self):
        status, body, _ = self.client.post("/api/promo/render", promo_body([
            {"image": "one.jpg", "raw": {"sequence": {"segments": []}, "duration": 2, "transition": "cut"}},
            form_shot("two.jpg"),
        ]))
        self.assertEqual(status, 400)
        self.assertIn("load that plan first", body["error"])

    def test_a_kept_shot_without_a_sequence_is_refused(self):
        self.load_kept()
        status, body, _ = self.client.post("/api/promo/render", promo_body([
            {"image": "one.jpg", "raw": {"duration": 2, "transition": "cut"}},
            form_shot("two.jpg"),
        ], source="kept/plan.json"))
        self.assertEqual(status, 400)
        self.assertIn("sequence is missing", body["error"])

    def test_a_sequence_file_in_the_plan_folder_is_read_for_the_kept_shot(self):
        plan = self.two_segment_plan()
        plan["shots"][0]["sequence"] = "walk.json"
        self.write_plan("kept/plan.json", plan, pictures=["one.jpg", "two.jpg"])
        with open(os.path.join(self.plans, "kept", "walk.json"), "w", encoding="utf-8") as f:
            json.dump({"id": "walk", "name": "Walk", "description": "x", "fps": 24,
                       "segments": [{"preset": "dolly_in", "duration_s": 1, "params": {"distance": 0.3}},
                                    {"preset": "crane_up", "duration_s": 1, "params": {"distance": 0.2}}]}, f)
        _, loaded, _ = self.client.post("/api/promo/load", {"path": "kept/plan.json"})
        raw = loaded["shots"][0]["raw"]
        self.assertEqual(raw["sequence"], {"segments": [
            {"preset": "dolly_in", "duration_s": 1, "params": {"distance": 0.3}},
            {"preset": "crane_up", "duration_s": 1, "params": {"distance": 0.2}}]})

    def test_a_sequence_file_outside_the_plans_root_is_not_read(self):
        plan = self.two_segment_plan()
        plan["shots"][0]["sequence"] = "../outside.json"
        self.write_plan("kept/plan.json", plan, pictures=["one.jpg", "two.jpg"])
        with open(os.path.join(self.outside, "outside.json"), "w", encoding="utf-8") as f:
            json.dump({"segments": [{"preset": "dolly_in", "duration_s": 2}]}, f)
        _, loaded, _ = self.client.post("/api/promo/load", {"path": "kept/plan.json"})
        self.assertIsNone(loaded["shots"][0]["raw"])


class PlanImagePathTests(PromoCase):
    """A plan's picture is found wherever its real path lies inside the plans root."""

    def test_a_sibling_picture_inside_the_plans_root_is_found(self):
        os.makedirs(os.path.join(self.plans, "sibling"), exist_ok=True)
        with open(os.path.join(self.plans, "sibling", "x.png"), "wb") as f:
            f.write(PNG)
        plan = self.sample_shop_shape()
        plan["shots"][0]["image"] = "../sibling/x.png"
        self.write_plan("shop/plan.json", plan, pictures=["shot-2.jpg"])
        _, loaded, _ = self.client.post("/api/promo/load", {"path": "shop/plan.json"})
        self.assertTrue(loaded["shots"][0]["found"])
        self.assertNotIn("../sibling/x.png", loaded["missing"])

    def test_a_sibling_picture_is_copied_into_the_clip_when_rendered(self):
        os.makedirs(os.path.join(self.plans, "sibling"), exist_ok=True)
        with open(os.path.join(self.plans, "sibling", "x.png"), "wb") as f:
            f.write(PNG)
        self.write_plan("shop/plan.json", self.sample_shop_shape(), pictures=["shot-2.jpg"])
        status, job, _ = self.client.post("/api/promo/render", promo_body([
            form_shot("../sibling/x.png"), form_shot("shot-2.jpg", "crane_up", 0.25, 2, "cut"),
        ], source="shop/plan.json"))
        self.assertEqual(status, 202, job)
        done = self.client.wait_done()
        self.assertEqual(done["state"], "done", done)
        with open(os.path.join(self.clip_folder(done), "images", "shot-1.png"), "rb") as f:
            self.assertEqual(f.read(), PNG)

    def test_a_picture_outside_the_plans_root_is_missing(self):
        outside = os.path.normpath(os.path.join(self.plans, "shop", "..", "..", "outside.png"))
        with open(outside, "wb") as f:
            f.write(PNG)
        plan = self.sample_shop_shape()
        plan["shots"][0]["image"] = "../../outside.png"
        self.write_plan("shop/plan.json", plan, pictures=["shot-2.jpg"])
        _, loaded, _ = self.client.post("/api/promo/load", {"path": "shop/plan.json"})
        self.assertFalse(loaded["shots"][0]["found"])
        self.assertIn("../../outside.png", loaded["missing"])

    def test_a_symlink_out_of_the_plans_root_is_still_refused(self):
        secret = os.path.join(self.outside, "secret.png")
        with open(secret, "wb") as f:
            f.write(PNG)
        plan = self.sample_shop_shape()
        plan["shots"][0]["image"] = "../steal.png"
        self.write_plan("shop/plan.json", plan, pictures=["shot-2.jpg"])
        os.symlink(secret, os.path.join(self.plans, "steal.png"))
        _, loaded, _ = self.client.post("/api/promo/load", {"path": "shop/plan.json"})
        self.assertFalse(loaded["shots"][0]["found"])


class FitNoteTests(PromoCase):
    """A finished render's keep-in-frame notes come from its manifest: one per clip, one per promo shot."""

    NOTE = "move shortened to 50% to keep the subject 6% inside the frame"

    def folder(self, name, manifest=None):
        folder = os.path.join(self.outputs, name)
        os.makedirs(folder)
        if manifest is not None:
            with open(os.path.join(folder, "manifest.json"), "w", encoding="utf-8") as f:
                json.dump(manifest, f)
        return folder

    def test_a_clip_reports_its_note(self):
        folder = self.folder("clip-a", {"fit": {"scale": 0.5, "margin": 0.06, "note": self.NOTE}})
        self.assertEqual(clipserver.fit_notes(folder), [self.NOTE])

    def test_no_note_or_no_manifest_gives_no_notes(self):
        self.assertEqual(clipserver.fit_notes(self.folder("a", {"fit": {"scale": 1.0, "margin": 0.06, "note": ""}})), [])
        self.assertEqual(clipserver.fit_notes(self.folder("b", {"fit": None})), [])
        self.assertEqual(clipserver.fit_notes(self.folder("c")), [])

    def test_a_promo_reports_each_shots_note_in_order(self):
        manifest = {"shots": [{"id": "a", "fit": {"scale": 0.5, "margin": 0.06, "note": "first"}},
                              {"id": "b", "fit": {"scale": 1.0, "margin": 0.06, "note": ""}},
                              {"id": "c", "fit": {"scale": 0.8, "margin": 0.06, "note": "third"}}]}
        self.assertEqual(clipserver.fit_notes(self.folder("promo-a", manifest)), ["first", "third"])

    def test_the_clip_status_carries_the_notes_only_when_done(self):
        folder = self.folder("clip-s", {"fit": {"scale": 0.5, "margin": 0.06, "note": self.NOTE}})
        done = self.studio._clip_status({"state": "done", "frames": 3, "clip_dir": folder, "clip": "clip-s",
                                         "message": "clip ready"})
        self.assertEqual(done["notes"], [self.NOTE])
        running = self.studio._clip_status({"state": "running", "frames": 3, "clip_dir": folder, "clip": "clip-s",
                                            "message": "rendering"})
        self.assertEqual(running["notes"], [])

    def test_the_promo_status_carries_the_notes_when_done(self):
        manifest = {"shots": [{"id": "a", "fit": {"scale": 0.5, "margin": 0.06, "note": self.NOTE}},
                              {"id": "b", "fit": None}]}
        folder = self.folder("promo-s", manifest)
        done = self.studio._promo_status({"state": "done", "frames": 6, "clip_dir": folder, "clip": "promo-s",
                                          "message": "", "kind": "promo", "shot_ids": ["a", "b"],
                                          "per_shot": [3, 3]})
        self.assertEqual(done["notes"], [self.NOTE])


if __name__ == "__main__":
    unittest.main()
