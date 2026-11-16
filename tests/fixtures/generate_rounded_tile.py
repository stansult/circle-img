"""Synthetic rounded tile: flat teal background, pale corners, unrelated artwork."""
import math
from pathlib import Path
from PIL import Image

BACKGROUND = (30, 130, 150)
CORNER = (250, 250, 250)
SIZE = 80


def make_tile(rounded=True, noise=False, transparent=False, background=BACKGROUND):
    image = Image.new('RGBA', (SIZE, SIZE), background + (255,))
    for y in range(SIZE):
        for x in range(SIZE):
            # Rounded corners of radius 12, with a one-pixel blended transition.
            dx, dy = max(12 - x - .5, x + .5 - 68, 0), max(12 - y - .5, y + .5 - 68, 0)
            coverage = max(0, min(1, 12.5 - math.hypot(dx, dy))) if rounded else 1
            rgb = tuple(round(background[c]*coverage + CORNER[c]*(1-coverage)) for c in range(3))
            if transparent:
                rgb = background
            if noise:
                delta = (x * 3 + y * 5) % 5 - 2
                rgb = tuple(max(0, min(255, value + delta)) for value in rgb)
            image.putpixel((x, y), rgb + ((round(255*coverage) if transparent else 255),))
    # A pale square frame encloses background-colored detail and a cyan mark.
    for y in range(24, 56):
        for x in range(24, 56):
            rgb = CORNER if x < 28 or x >= 52 or y < 28 or y >= 52 else background
            if 35 <= x < 42 and 35 <= y < 45:
                rgb = (70, 220, 190)
            image.putpixel((x, y), rgb + (255,))
    return image


if __name__ == '__main__':
    make_tile().save(Path(__file__).with_name('rounded-tile.png'))
