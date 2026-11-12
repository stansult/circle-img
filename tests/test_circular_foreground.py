"""A circular foreground needs no extra padding beyond its radius and margin."""
import importlib.util
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image
from fixture_data import DISK_CASES

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


app = load_module('disk_app', ROOT / 'circle-img.py')
generator = load_module('disk_generator', ROOT / 'tests/fixtures/generate_disk.py')
FIXTURE = ROOT / 'tests/fixtures/synthetic-disk.png'


class CircularForegroundTests(unittest.TestCase):
    def disk(self):
        with Image.open(FIXTURE) as image:
            return image.convert('RGBA')

    def assert_contained(self, image, margin):
        center = image.width / 2
        for y in range(image.height):
            for x in range(image.width):
                if image.getpixel((x, y))[3]:
                    for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1)):
                        self.assertLessEqual(math.hypot(x + dx - center, y + dy - center) + margin, center)

    def test_fixture_contract_and_reproducibility(self):
        disk = self.disk()
        self.assertEqual(disk.size, (120, 120))
        self.assertEqual(disk.getbbox(), (1, 1, 119, 119))
        self.assertEqual(disk.tobytes(), generator.make_disk().tobytes())
        self.assertEqual(disk.getpixel((10, 60)), (28, 145, 160, 255))
        self.assertEqual(disk.getpixel((100, 60)), (130, 92, 190, 255))
        self.assertEqual(disk.getpixel((60, 50)), (255, 165, 130, 255))
        self.assertEqual(disk.getpixel((0, 0)), (0, 0, 0, 0))
        self.assert_contained(disk, 0)
        # Opposite occupied corners (24,12) and (96,108) are 120 apart:
        # 72**2 + 96**2 = 120**2. Thus no smaller circle can contain it.
        self.assertEqual(disk.getpixel((24, 12))[3], 255)
        self.assertEqual(disk.getpixel((95, 107))[3], 255)
        # The support is a disk, not just a few extremal points or a ring.
        for y in range(120):
            for x in range(120):
                inside = all((x + dx - 60)**2 + (y + dy - 60)**2 <= 3600
                             for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1)))
                self.assertEqual(disk.getpixel((x, y))[3], 255 if inside else 0)

    def test_tight_fit_margin_and_off_center_input(self):
        disk = self.disk()
        for label, size, origin, margin, side in DISK_CASES:
            with self.subTest(case=label):
                source = Image.new('RGBA', size)
                source.paste(disk, origin)
                self.assertEqual(source.getbbox(), (origin[0]+1, origin[1]+1, origin[0]+119, origin[1]+119))
                result, _, _ = app.render(source, (0, 0, 0, 0), margin)
                expected = Image.new('RGBA', (side, side))
                expected.paste(disk, ((side-120)//2, (side-120)//2))
                self.assertEqual(result.size, expected.size)
                self.assertEqual(result.tobytes(), expected.tobytes())
                self.assert_contained(result, margin)

    def test_faint_circular_edge_is_preserved(self):
        disk = self.disk()
        for y in range(120):
            for x in range(120):
                rgba = disk.getpixel((x, y))
                if rgba[3] and math.hypot(x + .5 - 60, y + .5 - 60) > 58:
                    disk.putpixel((x, y), rgba[:3] + (1,))
        self.assertEqual(disk.getpixel((95, 107))[3], 1)
        result, _, _ = app.render(disk, (0, 0, 0, 0), 0)
        self.assertEqual(result.size, (120, 120))
        self.assertEqual(result.tobytes(), disk.tobytes())
        self.assert_contained(result, 0)

    def test_saved_circular_foreground(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'disk.png'
            run = subprocess.run([sys.executable, str(ROOT / 'circle-img.py'), '-i', str(FIXTURE),
                                  '-o', str(target), '-b', 'transparent', '--margin', '7'],
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            expected = Image.new('RGBA', (134, 134))
            expected.paste(self.disk(), (7, 7))
            with Image.open(target) as result:
                self.assertEqual(result.size, expected.size)
                self.assertEqual(result.tobytes(), expected.tobytes())
                self.assert_contained(result, 7)


if __name__ == '__main__':
    unittest.main()
