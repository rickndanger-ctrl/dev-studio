"""Draws the two Dock icons (1024x1024 PNG). Run: python3 assets/make_icons.py"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).parent
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
S = 1024
BOX = (100, 100, 924, 924)  # Apple's icon grid leaves a 100px margin
RADIUS = 185


def gradient(top, bottom):
    img = Image.new("RGB", (S, S))
    px = img.load()
    for y in range(S):
        t = y / (S - 1)
        c = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        for x in range(S):
            px[x, y] = c
    return img


def icon(name, label, top, bottom, accent, panes):
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle(BOX, RADIUS, fill=255)

    # Soft drop shadow under the squircle.
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((100, 118, 924, 942), RADIUS, fill=(0, 0, 0, 120))
    out = shadow.filter(ImageFilter.GaussianBlur(18))

    body = gradient(top, bottom).convert("RGBA")
    d = ImageDraw.Draw(body)

    # A little terminal window with the team's panes inside.
    win = (190, 220, 834, 700)
    d.rounded_rectangle(win, 48, fill=(16, 14, 28, 235))
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse((240 + i * 58, 258, 278 + i * 58, 296), fill=c)
    x0, y0, x1, y1 = 228, 330, 796, 664
    for (px0, py0, px1, py1), glow in panes:
        r = (x0 + (x1 - x0) * px0 + 8, y0 + (y1 - y0) * py0 + 8,
             x0 + (x1 - x0) * px1 - 8, y0 + (y1 - y0) * py1 - 8)
        d.rounded_rectangle(r, 18, fill=accent if glow else (58, 52, 92))
        # Prompt cursor in each pane.
        d.rectangle((r[0] + 22, r[1] + 26, r[0] + 52, r[1] + 40), fill=(255, 255, 255, 200))

    font = ImageFont.truetype(FONT, 150)
    w = d.textlength(label, font=font)
    d.text(((S - w) / 2 + 4, 722), label, font=font, fill=(0, 0, 0, 90))
    d.text(((S - w) / 2, 716), label, font=font, fill=(255, 255, 255))

    out.paste(body, (0, 0), mask)
    out.save(HERE / f"{name}.png")


# HQ: architect pane glows on the left, studio + messenger on the right.
icon("icon-hq", "HQ", (255, 79, 154), (110, 46, 214), (255, 120, 200),
     [((0, 0, 0.6, 1), True), ((0.6, 0, 1, 0.5), False), ((0.6, 0.5, 1, 1), False)])
# Workshop: a row of builders glowing on top, messenger + studio below.
icon("icon-workshop", "SHOP", (255, 196, 0), (240, 90, 30), (255, 220, 90),
     [((0, 0, 0.34, 0.66), True), ((0.34, 0, 0.67, 0.66), True), ((0.67, 0, 1, 0.66), True),
      ((0, 0.66, 0.5, 1), False), ((0.5, 0.66, 1, 1), False)])
# Solo: architect on the left, a column of builders on the right, studio strip below.
icon("icon-solo", "STUDIO", (0, 214, 201), (37, 99, 235), (120, 255, 230),
     [((0, 0, 0.55, 0.72), True), ((0.55, 0, 1, 0.36), False), ((0.55, 0.36, 1, 0.72), False),
      ((0, 0.72, 1, 1), False)])
print("icons written")
