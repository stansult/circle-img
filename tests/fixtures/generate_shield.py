"""Rebuild synthetic-shield.png without any external artwork or dependencies beyond Pillow."""
from pathlib import Path

from PIL import Image, ImageDraw


def make_shield():
    # Broad shoulders, shallow domed top, straight sides, tapered lower half.
    # Coordinates are independently drawn; no logo pixels or paths are used.
    scale = 4
    points = [(4, 57)]

    def curve(a, b, end):
        start = points[-1]
        for step in range(1, 201):
            t = step / 200
            u = 1 - t
            points.append(tuple(u**3 * start[i] + 3*u*u*t*a[i]
                                + 3*u*t*t*b[i] + t**3*end[i] for i in (0, 1)))

    curve((145, -12), (335, -12), (476, 57))
    points.append((476, 340))
    curve((476, 465), (400, 485), (240, 565))
    curve((80, 485), (4, 465), (4, 340))
    mask = Image.new('L', (480 * scale, 570 * scale))
    ImageDraw.Draw(mask).polygon([(round(x*scale), round(y*scale)) for x, y in points], fill=255)
    art = Image.new('RGBA', mask.size, '#157f89')
    draw = ImageDraw.Draw(art)
    draw.polygon([(0, 260*scale), (480*scale, 100*scale),
                  (480*scale, 310*scale), (0, 470*scale)], fill='#7958bc')
    draw.ellipse((105*scale, 100*scale, 205*scale, 200*scale), fill='#8ee5cf')
    draw.rectangle((270*scale, 330*scale, 345*scale, 405*scale), fill='#ff937f')
    art.putalpha(mask)
    return art.resize((480, 570), Image.Resampling.LANCZOS)


if __name__ == '__main__':
    make_shield().save(Path(__file__).with_name('synthetic-shield.png'))
