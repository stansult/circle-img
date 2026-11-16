"""Unbranded flat circle with an alpha fringe and enclosed multicolor artwork."""
import math
from pathlib import Path
from PIL import Image

BACKGROUND = (40, 130, 170)


def make_circle(background=BACKGROUND, noise=False):
    image = Image.new('RGBA', (128, 128))
    for y in range(128):
        for x in range(128):
            radius = math.hypot(x+.5-64, y+.5-64)
            if radius >= 60:
                continue
            delta = (x*3+y*5) % 5-2 if noise else 0
            rgb = tuple(c+delta for c in background)
            alpha = 255
            if radius > 59:
                rgb = tuple(c+15 for c in background)
                alpha = max(1, round(255*(60-radius)))
            image.putpixel((x,y), rgb+(alpha,))
    for y in range(48,80):
        for x in range(48,80):
            rgb = (250,250,250) if min(x-48,79-x,y-48,79-y)<4 else background
            if 59 <= x < 66 and 59 <= y < 69:
                rgb = (220,90,150)
            image.putpixel((x,y),rgb+(255,))
    # A disconnected, almost invisible foreground mark must survive layer 2.
    image.putpixel((85,64),(220,90,150,1))
    return image


if __name__ == '__main__':
    make_circle().save(Path(__file__).with_name('layered-background.png'))
