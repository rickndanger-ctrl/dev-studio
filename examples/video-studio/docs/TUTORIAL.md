# video-studio tutorial

This walks through the four steps of making a short move from stills: plan, check, render and
watch. You need Python 3.9 or newer. Nothing needs a network connection and no models are
downloaded.

Run the commands from the `video-studio` folder, the one that holds this file's parent `docs/`
folder. Every output goes to `~/video-tutorial`, outside the project, so the project folder stays
clean.

## 1. Install into a virtual environment outside the folder

`stillmove` uses `camera-motion`, which is a pip package in the sibling folder. Keep the two folders
side by side, then install both into the environment. Run these from the `video-studio` folder (from
the repository root, `cd video-studio` first), because `../camera-motion` is relative to it:

```sh
python3 -m venv ~/venvs/video-studio
source ~/venvs/video-studio/bin/activate
pip install numpy Pillow
pip install ../camera-motion
pip install .
```

After `pip install .` you have two commands, `stillmove` and `shotplan`. The steps below use the
`python3 -m` form, which runs from this folder without an install: `python3 -m stillmove.cli` and
`python3 -m shotplan`. Both forms do the same thing, so use whichever you like.

Check the install. Each line should start with PASS or INFO; a WARN is allowed. A fresh install
typically shows WARN lines for the optional depth model, onnxruntime and matte model, which only the
learned depth and the `isnet` matte need, and for ffmpeg if it is missing:

    WARN ffmpeg: ffmpeg not found on PATH; mp4 output is off

The README's "Messages you may see" quotes every WARN and says what to do about each.

```sh
python3 -m stillmove.cli doctor
```

## 2. Plan: a shot plan is a list of shots

A shot plan (see `shotplan/schema.json`) lists shots in order. Each shot is one still image moved
by one camera sequence, and each shot can crossfade into the next one. Open
`shotplan/samples/product_reveal.json` in an editor to read one. Its three shots are an inline
`dolly_in`, an `orbit_left` with a crossfade, and a `crane_up` then `tilt_up` on a logo card.

Check the plan. The check prints the timeline (start, end, shot, moves and transition) and exits 0
when every rule holds. A plan with a mistake lists each error on stderr and exits 1:

```sh
python3 -m shotplan check shotplan/samples/product_reveal.json
```

Draw the same timeline as an SVG file, one lane per shot, one block per move, with the crossfades
shaded. The first command makes the output folder for every later step:

```sh
mkdir -p ~/video-tutorial
python3 -m shotplan timeline shotplan/samples/product_reveal.json --svg ~/video-tutorial/product-reveal-timeline.svg
```

## 3. Render: move the stills

Render the plan. Each shot is rendered with the fake depth backend (the default, `--depth fake`),
then the shots are joined into one preview GIF. `--scale 0.1` renders at a tenth of the plan's size,
which is quick for a first run. The output folder holds `manifest.json`, `preview.gif`, the joined
`frames/` and one `shots/<id>/` folder per shot:

```sh
python3 -m shotplan render shotplan/samples/product_reveal.json ~/video-tutorial/product-reveal --scale 0.1
```

Render one still on its own with `stillmove`. This uses `docs/example/input.png`, a 320x180 test
picture drawn in code by `tools/make_example.py`, and the gradient depth backend, which is also a
fake:

```sh
python3 -m stillmove.cli render docs/example/input.png --preset dolly_in --backend gradient -o ~/video-tutorial/dolly-in
```

The same move can make all three shapes in one render: square, wide and vertical, each in its own folder,
with a zip of the three. This uses the fake plane depth:

```sh
python3 -m stillmove.cli render docs/example/input.png --preset dolly_in --seconds 1 --fps 12 --sizes square,wide,vertical --backend plane -o ~/video-tutorial/every-size
```

### Mesh for Blender (optional)

`mesh` turns the same still into a textured grid at its depth, and writes a Blender scene that moves
the camera along the same path. This command uses the fake plane depth, which is drawn at a fixed
distance, so no model is needed:

```sh
python3 -m stillmove.cli mesh docs/example/input.png --preset dolly_in --depth fake --grid 32x18 -o ~/video-tutorial/mesh
```

The output folder holds `mesh.obj`, `mesh.mtl`, the texture and `scene.py`. To open it in Blender,
switch to the Scripting tab, choose Text > Open and pick `scene.py` from that folder. Then choose
Run Script (Alt+P) and press Numpad 0 for the camera view. The script loads `mesh.obj` from the path
the mesh was written to, so keep the folder where it is.

## 4. Watch: open the result

Open the two previews in any image viewer or browser:

- `~/video-tutorial/product-reveal/preview.gif`
- `~/video-tutorial/dolly-in/preview.gif`

The same shot plan can be watched on a page. This starts a server on 127.0.0.1 only. Open
http://127.0.0.1:8810/ in a browser, and stop the server with Ctrl-C:

```sh
python3 -m shotplan serve --port 8810
```

The stillmove page works the same way. Upload any image and pick a move; the page renders it before
it replies. Open http://127.0.0.1:8800/, and stop with Ctrl-C:

```sh
python3 -m stillmove.web.server 8800
```

## 5. Make a clip: the page for making videos

The owner's page makes one clip from one picture, or a promo from two to six pictures. Start it from
`video-studio/` and open the page on 127.0.0.1 only:

```sh
python3 -m makeclip --port 8820
```

Open http://127.0.0.1:8820/ and stop with Ctrl-C. After `pip install .`, the same page starts with
`makeclip --port 8820`.

The Make a clip size picker has an "Every size" choice: one render makes the square, wide and vertical
versions, each with its own download, and a zip with all three.

The page has two tabs:

- **Make a clip** takes one picture (pick one from the input folder or drop a file), a camera move
  with a safe-range slider, a length, a size, and whether to split the foreground with Layers. It
  renders the clip and shows the MP4 and the GIF with download links.
- **Make a promo** takes two to six pictures, each with its own move, length and transition (a cut
  or a crossfade). A plan file can be loaded to fill the form; any missing picture is listed by name.
  The render reports which shot it is on, then the joining step.

Pictures are read from `~/Pictures/stillmove-input/`, and clips are written to a new folder under
`~/Pictures/stillmove-output/`. Nothing else is written, and the server refuses requests from other
hosts and other origins.
