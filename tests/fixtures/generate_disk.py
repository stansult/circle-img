"""Rebuild a circular foreground with an exact 60-pixel enclosing radius."""
from pathlib import Path
from PIL import Image


def make_disk():
    image = Image.new('RGBA', (120, 120))
    for y in range(120):
        for x in range(120):
            far_x = max(abs(x - 60), abs(x + 1 - 60))
            far_y = max(abs(y - 60), abs(y + 1 - 60))
            if far_x * far_x + far_y * far_y <= 60 * 60:
                color = (28, 145, 160, 255) if x < 60 else (130, 92, 190, 255)
                if 45 <= y < 60:
                    color = (255, 165, 130, 255)
                image.putpixel((x, y), color)
    return image


if __name__ == '__main__':
    make_disk().save(Path(__file__).with_name('synthetic-disk.png'))
