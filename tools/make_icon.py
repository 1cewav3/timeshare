#!/usr/bin/env python3
"""Draw the app icon: the module lamp panel, in the game's own palette.

    python3 tools/make_icon.py        # needs Pillow; writes assets/icon.png
"""
import os

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BG, PANEL, RULE = (11, 13, 9), (18, 21, 14), (58, 66, 48)
LAMP, GLOW, OFF, FAULT = (168, 235, 154), (60, 110, 52), (36, 42, 30), (220, 74, 60)
SIZE = 512

img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((16, 16, SIZE - 16, SIZE - 16), radius=72, fill=BG)
d.rounded_rectangle((56, 56, SIZE - 56, SIZE - 56), radius=36, fill=PANEL, outline=RULE, width=6)
for x, y in ((40, 40), (SIZE - 40, 40), (40, SIZE - 40), (SIZE - 40, SIZE - 40)):
    d.ellipse((x - 9, y - 9, x + 9, y + 9), fill=BG, outline=RULE, width=3)

# four rows of four lamps: most lit, two dark, one at fault
states = ["on", "on", "on", "on",
          "on", "on", "on", "off",
          "on", "fault", "on", "on",
          "on", "on", "off", "on"]
glow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
g = ImageDraw.Draw(glow)
step, r = 84, 26
for i, state in enumerate(states):
    cx, cy = 130 + (i % 4) * step, 130 + (i // 4) * step
    if state != "off":
        g.ellipse((cx - r - 16, cy - r - 16, cx + r + 16, cy + r + 16),
                  fill=(GLOW if state == "on" else (120, 40, 32)) + (200,))
    d.ellipse((cx - r, cy - r, cx + r, cy + r),
              fill={"on": LAMP, "off": OFF, "fault": FAULT}[state])
glow = glow.filter(ImageFilter.GaussianBlur(14))
out = Image.alpha_composite(img, glow)    # the glow spills over the panel
# lamps go back on top of their glow
d2 = ImageDraw.Draw(out)
for i, state in enumerate(states):
    cx, cy = 130 + (i % 4) * step, 130 + (i // 4) * step
    d2.ellipse((cx - r, cy - r, cx + r, cy + r),
               fill={"on": LAMP, "off": OFF, "fault": FAULT}[state])
    if state != "off":                     # a highlight, like a glass lamp cap
        d2.ellipse((cx - r + 9, cy - r + 7, cx - 4, cy - 6),
                   fill=(255, 255, 255, 70))
os.makedirs(os.path.join(HERE, "assets"), exist_ok=True)
out.save(os.path.join(HERE, "assets", "icon.png"))
print("wrote assets/icon.png")
