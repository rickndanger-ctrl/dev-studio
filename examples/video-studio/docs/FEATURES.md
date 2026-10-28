# Video studio: feature map

Clean-room notes from general knowledge. No competitor sites, apps or code were opened, and no
network calls were made. Every model name, license and hardware figure that is not firmly known
is marked **(verify)**. Treat all licenses as unconfirmed until checked against the project page.

How to read each section: what the user does; inputs -> outputs; open models or libraries that can
do it locally; rough hardware need; and how `camera-motion` plugs in.

Hardware tiers used below: **CPU ok**, **Apple Silicon 16 GB**, **24+ GB GPU**.

---

## 1. Camera-motion control (ours, done)

- **User does:** picks a camera move (dolly, orbit, crane, pan, tilt, dolly zoom) or a chained
  sequence, previews it, and exports it.
- **Inputs -> outputs:** preset id or sequence file -> frames (JSON, RE10K text, Blender script,
  glTF) and an animated SVG preview.
- **Open models / libraries:** none needed; stdlib Python. See [camera-motion](../../camera-motion).
- **Hardware:** CPU ok.
- **How it plugs in:** this is the source of truth for camera paths. Its RE10K and JSON
  trajectories are the conditioning input for camera-conditioned image-to-video (section 3).
  Its sequences drive shot planning (section 10).

---

## 2. Motion presets on images

- **User does:** takes a still image and applies a named motion (slow zoom, drift, parallax, a
  subtle breathing motion) without a text prompt.
- **Inputs -> outputs:** one image + motion preset + duration -> short clip (MP4 or frame folder).
- **Open models / libraries:**
  - AnimateDiff, with motion adapters and LoRA-style motion modules; Apache-2.0 for code (**verify**
    for each adapter's weights).
  - Stable Video Diffusion image-to-video; Stability AI Community License (**verify**; it has
    commercial-use thresholds).
  - Classic 2D warp tools (homography / parallax from a depth map) need no model at all; the depth
    model is the only learned part (for example Depth Anything, **verify** license).
- **Hardware:** warp-only is CPU ok. AnimateDiff on SD 1.5 is roughly Apple Silicon 16 GB
  (**verify**). SVD is roughly 24+ GB GPU at full quality (**verify**).
- **How it plugs in:** the preset names map to `camera-motion` presets, so a "dolly in" choice on
  a still uses the same timing and easing. A warp renderer can take the RE10K pose per frame
  directly.

---

## 3. Image-to-video (camera-conditioned)

- **User does:** supplies a start image and a prompt, plus a camera move, and gets a clip that
  follows that move.
- **Inputs -> outputs:** image + prompt + trajectory -> video clip.
- **Open models / libraries:**
  - CogVideoX image-to-video, with camera-control variants; code Apache-2.0, weights under a
    separate license (**verify**).
  - Wan 2.1 image-to-video; Apache-2.0 for the open variants (**verify** size and license).
  - LTX-Video; open weights under a RAIL-style license (**verify**).
  - Camera control for these usually uses a pose-conditioning adapter (for example a
    Plücker-ray or RE10K-style pose embedding). The exact adapter names are **verify**.
- **Hardware:** the small variants of CogVideoX / Wan run on a 24+ GB GPU (**verify**); the
  larger ones need more. Apple Silicon 16 GB is not realistic for full-length clips (**verify**).
- **How it plugs in:** this is the main consumer of `camera-motion` output. Export RE10K or JSON
  and read the per-frame `position` and `rotation` as the conditioning signal. The frame count
  and fps come from the export, so a 3 s clip at 24 fps gives 72 frames plus the start frame
  (**verify** the model's expected frame count).

---

## 4. Video upscaling

- **User does:** raises a clip's resolution (e.g. 720p to 4K) and cleans up detail.
- **Inputs -> outputs:** video -> higher-resolution video.
- **Open models / libraries:**
  - Real-ESRGAN (video mode via per-frame use); BSD-3-Clause (**verify** the model weight terms).
  - Video-aware restorers (for example SeedVR-type diffusion upscalers) (**verify** names and
    licenses).
- **Hardware:** Real-ESRGAN per-frame is CPU ok but slow; Apple Silicon 16 GB is workable for
  small clips (**verify**); diffusion upscalers need a 24+ GB GPU (**verify**).
- **How it plugs in:** not part of camera motion. It runs on the rendered output, after section 3.

---

## 5. Frame interpolation

- **User does:** raises fps (e.g. 24 to 60) for smoother motion, or fills a slow-motion effect.
- **Inputs -> outputs:** video at N fps -> video at higher fps, or slow-motion output.
- **Open models / libraries:**
  - RIFE; MIT (**verify** the current version).
  - FILM (frame interpolation for large motion); Apache-2.0 (**verify**).
- **Hardware:** RIFE is Apple Silicon 16 GB or better at 1080p (**verify**); CPU ok at low
  resolution.
- **How it plugs in:** the camera path's fps is the input rate. Interpolating a 24 fps export to
  48 fps should keep the same start and end poses, so check the time mapping after interpolation.

---

## 6. Relighting

- **User does:** changes the light on a subject (new direction, colour, or mood) while keeping the
  pose.
- **Inputs -> outputs:** image (and optional background or light prompt) -> relit image.
- **Open models / libraries:**
  - IC-Light; license (**verify**, it may be non-commercial).
  - Diffusion inpainting with a lighting prompt (generic; base-model license applies).
- **Hardware:** Apple Silicon 16 GB for images (**verify**); video needs the per-frame cost on top.
- **How it plugs in:** the relit frames keep the camera path as-is, so the camera move is applied
  before relighting. Relighting per frame can flicker; a temporal method is needed for video
  (not from `camera-motion`).

---

## 7. Style transfer

- **User does:** restyles a clip (painterly, sketch, a given look) while keeping the motion.
- **Inputs -> outputs:** video + style reference or prompt -> restyled video.
- **Open models / libraries:**
  - ControlNet with depth or edge conditioning on an image model; Apache-2.0 (**verify** per
    checkpoint).
  - AdaIN-style feature transfer (per frame, lower quality); MIT-licensed code is common (**verify**).
- **Hardware:** per-frame ControlNet is Apple Silicon 16 GB or 24+ GB GPU (**verify**).
- **How it plugs in:** the camera path is untouched. Depth conditioning can be taken from the
  same camera pose, so the style stays aligned with the geometry.

---

## 8. Lip-sync

- **User does:** makes a face in a clip speak a supplied audio track.
- **Inputs -> outputs:** face video or image + audio -> video with matching mouth motion.
- **Open models / libraries:**
  - Wav2Lip; research license, non-commercial (**verify**).
  - SadTalker and MuseTalk; check each repo's license before use (**verify**).
- **Hardware:** Apple Silicon 16 GB for short clips (**verify**); 24+ GB GPU for quality or length
  (**verify**).
- **How it plugs in:** not a camera task. It sits after the camera move, on the face shot only.
  Keep the camera still for the lip-sync shot; a moving camera makes mouth alignment harder.

---

## 9. Background removal

- **User does:** cuts the subject out of a frame, for compositing or a new background.
- **Inputs -> outputs:** image or video -> alpha matte (PNG sequence or alpha video).
- **Open models / libraries:**
  - rembg (wraps several models); MIT for the wrapper (**verify** each model's weight license).
  - U2-Net; Apache-2.0 (**verify**).
  - BiRefNet (**verify** license).
  - Video matting models (for example RobustVideoMatting; **verify** license).
- **Hardware:** CPU ok for images; Apple Silicon 16 GB for video (**verify**).
- **How it plugs in:** the matte lets a camera move run on the subject and background separately,
  so a parallax move over a fixed background is possible. Depth from section 2 can help.

---

## 10. Keyframe and shot planning

- **User does:** lays out a video as shots: each shot with a camera move, a duration and a
  transition, then renders them in order.
- **Inputs -> outputs:** shot list (or prompt) -> ordered shot plan, camera paths and render jobs.
- **Open models / libraries:** no model needed for the plan itself; this is data and a scheduler.
  Planning from a text brief can use any LLM (provider and license are the user's choice).
- **Hardware:** CPU ok.
- **How it plugs in:** this is what `camera-motion` sequences were built for. A sequence file
  already holds ordered segments of presets with durations; the planner writes these, and the
  export and preview steps from `camera-motion` render each shot.

---

## Summary

| # | Feature | Typical input -> output | Local open option (license) | Hardware | camera-motion role |
|---|---|---|---|---|---|
| 1 | Camera-motion control | preset / sequence -> trajectory, preview | ours (stdlib) | CPU ok | source of truth |
| 2 | Motion presets on images | image -> short clip | AnimateDiff (Apache-2.0, verify); SVD (Stability CL, verify); warp + depth | CPU ok (warp) to 16 GB | preset names and timing |
| 3 | Image-to-video (camera) | image + prompt + trajectory -> clip | CogVideoX, Wan 2.1, LTX-Video (licenses vary, verify) | 24+ GB GPU (verify) | conditioning input (RE10K / JSON) |
| 4 | Video upscaling | clip -> higher resolution | Real-ESRGAN (BSD-3, verify) | CPU ok to 24+ GB (verify) | none (post-render) |
| 5 | Frame interpolation | clip -> higher fps | RIFE (MIT, verify); FILM (Apache-2.0, verify) | 16 GB (verify) | fps and time mapping |
| 6 | Relighting | frames -> relit frames | IC-Light (license verify) | 16 GB (verify) | none (applied after the move) |
| 7 | Style transfer | clip -> restyled clip | ControlNet (Apache-2.0, verify per checkpoint) | 16 GB to 24+ GB (verify) | shares depth from the pose |
| 8 | Lip-sync | face + audio -> speaking face | Wav2Lip (non-commercial, verify); SadTalker, MuseTalk (verify) | 16 GB to 24+ GB (verify) | keep camera still on the shot |
| 9 | Background removal | frame -> alpha matte | rembg (MIT wrapper, verify); U2-Net (Apache-2.0, verify) | CPU ok (images) | enables separate subject/background moves |
| 10 | Keyframe / shot planning | shot list -> ordered render jobs | none needed (data + scheduler) | CPU ok | sequences drive shots |
