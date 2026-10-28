# video-studio tutorial

This walks through the four steps of making a short move from stills: plan, check, render and
watch. You need Python 3.9 or newer. Nothing needs a network connection and no models are
downloaded.

Run the commands from the `video-studio` folder, the one that holds this file's parent `docs/`
folder. Every output goes to `~/video-tutorial`, outside the project, so the project folder stays
clean.

## 1. Install into a virtual environment outside the folder

`stillmove` uses `camera-motion`, which is a pip package in the sibling folder. Keep the two folders
side by side, then install both into the environment:

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

Check the install. Each line should start with PASS; a WARN is allowed (for example, ffmpeg is
missing, so mp4 output is off):

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
