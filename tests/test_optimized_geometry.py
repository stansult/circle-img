"""Movable-center fitting, with independent geometric and pixel checks."""
import importlib.util
import math
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

from PIL import Image
from circle_oracle import exhaustive_circle
from fixture_data import (CIRCLE_CASES, CIRCLE_RANDOM_SEED, CIRCLE_RANDOM_CASES,
                          CIRCLE_RANDOM_POINTS, CIRCLE_COORDINATE_LIMIT)

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('optimized_app', ROOT / 'circle-img.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def corners(image):
    return {(x + dx, y + dy)
            for y in range(image.height) for x in range(image.width)
            if image.getpixel((x, y))[3]
            for dx, dy in ((0, 0), (0, 1), (1, 0), (1, 1))}


class OptimizedGeometryTests(unittest.TestCase):
    def shield(self):
        with Image.open(ROOT / 'tests/fixtures/synthetic-shield.png') as image:
            source = image.convert('RGBA')
        self.assertEqual(source.size, (480, 570))
        self.assertEqual(source.getpixel((150, 150)), (142, 229, 207, 255))
        self.assertEqual(source.getpixel((300, 360)), (255, 147, 127, 255))
        # Establish a feasible tighter circle independently of product helpers.
        points = corners(source)
        self.assertLessEqual(max(math.hypot(x - 240, y - 257) for x, y in points), 314)
        return source

    def test_shield_tighter_fit_in_all_orientations(self):
        source = self.shield()
        for turns, margin in ((0, 0), (1, 0), (2, 12), (3, 3)):
            with self.subTest(turns=turns, margin=margin):
                art = source.rotate(90 * turns, expand=True)
                output, layer, (x, y) = app.render(art, (0, 0, 0, 0), margin)
                self.assertLessEqual(output.width, 628 + 2 * margin)
                self.assertGreaterEqual(output.width, max(art.size))
                crop = art.crop(art.getbbox())
                self.assertEqual(layer.crop((x, y, x + crop.width, y + crop.height)).tobytes(), crop.tobytes())
                self.assertEqual(output.getchannel('A').tobytes(), layer.getchannel('A').tobytes())
                self.assertEqual([pixel for pixel in output.get_flattened_data() if pixel[3]],
                                 [pixel for pixel in layer.get_flattened_data() if pixel[3]])
                half = output.width / 2
                for px, py in corners(crop):
                    self.assertLessEqual(math.hypot(px + x - half, py + y - half) + margin, half)

    def test_saved_shield_uses_optimized_fit(self):
        self.shield()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'shield.png'
            result = subprocess.run([sys.executable, str(ROOT / 'circle-img.py'),
                                     '-i', str(ROOT / 'tests/fixtures/synthetic-shield.png'),
                                     '-o', str(output)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            with Image.open(output) as image:
                self.assertLessEqual(image.width, 628)
                self.assertEqual(image.width, image.height)

    def test_analytic_enclosing_circles(self):
        # Independently known solutions exercise diameter, circumcircle, and
        # collinear support, including an interior point and translated inputs.
        for points, expected in CIRCLE_CASES:
            for dx, dy in ((0, 0), (10000, -20000)):
                with self.subTest(points=points, translation=(dx, dy)):
                    shifted = [(x + dx, y + dy) for x, y in points]
                    actual = app.enclosing_circle(shifted)
                    for value, target in zip(actual, (expected[0] + dx, expected[1] + dy, expected[2])):
                        self.assertAlmostEqual(value, target, places=7)
                    self.assertEqual(actual, app.enclosing_circle(list(reversed(shifted))))

    def test_exhaustive_oracle_known_solutions(self):
        # Validate the supporting oracle against known answers, not the product.
        for points, expected in CIRCLE_CASES:
            for dx, dy in ((0, 0), (10000, -20000)):
                with self.subTest(points=points, translation=(dx, dy)):
                    actual = exhaustive_circle([(x + dx, y + dy) for x, y in points])
                    self.assertEqual(actual, (expected[0] + dx, expected[1] + dy, expected[2]))

    def test_enclosing_circle_against_500_exhaustive_solutions(self):
        rng = random.Random(CIRCLE_RANDOM_SEED)
        limit = CIRCLE_COORDINATE_LIMIT
        for trial in range(CIRCLE_RANDOM_CASES):
            points = sorted({(rng.randrange(-limit, limit + 1), rng.randrange(-limit, limit + 1))
                             for _ in range(CIRCLE_RANDOM_POINTS)})
            with self.subTest(trial=trial, points=points):
                self.assertGreaterEqual(len(points), 3)
                self.assertLessEqual(len(points), CIRCLE_RANDOM_POINTS)
                self.assertTrue(all(-limit <= coordinate <= limit for point in points for coordinate in point))
                expected = exhaustive_circle(points)
                cx, cy, radius = app.enclosing_circle(points)
                self.assertTrue(all(math.isfinite(value) for value in (cx, cy, radius)))
                self.assertGreaterEqual(radius, 0)
                self.assertAlmostEqual(radius, expected[2], delta=1e-7)
                for point in points:
                    self.assertLessEqual(math.dist((cx, cy), point), radius + 1e-7)

    def test_shield_generator_matches_fixture(self):
        spec = importlib.util.spec_from_file_location('shield_generator', ROOT / 'tests/fixtures/generate_shield.py')
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        with Image.open(ROOT / 'tests/fixtures/synthetic-shield.png') as stored:
            self.assertEqual(generator.make_shield().tobytes(), stored.tobytes())


if __name__ == '__main__':
    unittest.main()
