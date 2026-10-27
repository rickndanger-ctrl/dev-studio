# camera-motion tutorial

This walks through installing camera-motion, looking at a preset, previewing it, running the
viewer, and exporting a camera move for Blender or any glTF viewer. You need Python 3.9 or
newer. Nothing needs a network connection once the project is on disk.

## 1. Get the project and install it into a fresh virtual environment

Clone the repository and go to the project folder (use the real repository URL in place of
`<repo-url>`):

```text
git clone <repo-url>
cd dev-studio/examples/camera-motion
```

Then create a virtual environment and install:

```sh
python3 -m venv ~/venvs/camera-motion
source ~/venvs/camera-motion/bin/activate
pip install .
```

The `camera-motion` command is now on your path while the environment is active.

Check the install. Each line should start with PASS:

```sh
camera-motion doctor
```

## 2. List the bundled presets

```sh
camera-motion list
```

Each line is a preset id, a tab, and a name. The catalog has 14 presets, such as `dolly_in`,
`crane_up`, `orbit_left` and `tilt_down`.

## 3. Print the path for one preset

```sh
camera-motion path dolly_in
```

This prints JSON with the first and last frame. Each frame has a time `t`, a `position`
`[x, y, z]`, a `rotation` `[yaw, pitch, roll]` in degrees, and a field of view `fov`.

## 4. Preview it as an SVG

A static top-down preview:

```sh
camera-motion preview dolly_in -o dolly_in.svg
```

The same preview with the camera animated along the path. Open the file in a browser to watch it:

```sh
camera-motion preview orbit_left -o orbit_left.svg --animate
```

## 5. Run the web viewer

```sh
camera-motion serve --port 8790
```

Open http://127.0.0.1:8790/ in a browser. The viewer lists the presets, plays them, and lets you
change the overrides. Press Ctrl+C in the terminal to stop the server. It only listens on
127.0.0.1, so other computers cannot reach it.

## 6. Export a move for Blender or a glTF viewer

Create a folder for the output, then export the same preset in both formats:

```sh
mkdir -p ~/projects/camera-shots
camera-motion export dolly_in --format blender -o ~/projects/camera-shots/dolly_in.py
camera-motion export dolly_in --format gltf -o ~/projects/camera-shots/dolly_in.gltf
```

The `.py` file is a Blender script. The `.gltf` file is a glTF 2.0 document with the camera
animated. Both use 24 frames per second over 3 seconds unless you pass `--fps` or `--seconds`.

### Load the Blender script

1. Open Blender. Click the **Scripting** tab at the top of the window.
2. Choose **Text > Open**, then pick `~/projects/camera-shots/dolly_in.py`.
3. Choose **Run Script** (Alt+P on Windows and Linux, Option+P on macOS). Blender creates a camera
   named after the preset (here, "Dolly in") with one keyframe per frame.
4. Hover over the 3D viewport and press **Numpad 0** to look through the camera.
5. Press **Space** to play the move.

### Load the glTF file

1. Open Blender and choose **File > Import > glTF 2.0**.
2. Pick `~/projects/camera-shots/dolly_in.gltf` and confirm the import.
3. Press **Space** to play the animation on the timeline.

Other glTF 2.0 viewers can open the same file.

## Troubleshooting

- **`command not found: camera-motion`**: the virtual environment is not active. Run
  `source ~/venvs/camera-motion/bin/activate` again in the same terminal.
- **`error: port N is in use`**: another program holds that port. Start the server on another port,
  for example `camera-motion serve --port 8791`, and open that port in the browser.
- **Check you reached the right page**: the browser tab is titled 'Camera motion'.
- **An unknown preset id**: run `camera-motion list` to see the valid ids.
