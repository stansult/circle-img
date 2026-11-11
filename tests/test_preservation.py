"""Preservation invariants that remain valid when circle placement improves."""
import importlib.util
import io
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image, ImageCms
from fixture_data import ARTWORK_ROWS, ARTWORK_COLORS, PADDING_CASES, BORDER_CASES

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('circle_img_preservation', ROOT / 'circle-img.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def artwork():
    image = Image.new('RGBA', (11, 9))
    for y, row in enumerate(ARTWORK_ROWS):
        for x, symbol in enumerate(row):
            if symbol != '.':
                image.putpixel((x, y), ARTWORK_COLORS[symbol])
    return image


def occupied(image):
    return {(x, y): image.getpixel((x, y))
            for y in range(image.height) for x in range(image.width)
            if image.getpixel((x, y))[3]}


def validate_artwork(test, image):
    # Independently verify the fixture's contract before using it as an oracle.
    test.assertEqual(image.size, (11, 9))
    test.assertEqual(len(occupied(image)), 13)
    test.assertEqual(image.getchannel('A').getbbox(), (1, 0, 10, 9))
    test.assertEqual(image.getpixel((1, 0)), (231, 17, 43, 255))
    test.assertEqual(image.getpixel((7, 4)), (25, 65, 219, 255))
    test.assertEqual(image.getpixel((4, 2)), (0, 0, 0, 0))  # Ring's hole.
    test.assertEqual(image.getpixel((9, 8)), (150, 35, 210, 1))
    test.assertEqual(image.getpixel((7, 5)), (80, 160, 240, 128))
    test.assertNotEqual(image.tobytes(), image.transpose(Image.Transpose.FLIP_LEFT_RIGHT).tobytes())


def assert_translation(test, source, result):
    # Locate a unique colored anchor in the result rather than trusting the
    # implementation's returned offset or requiring bounding-box centering.
    expected = occupied(source)
    actual = occupied(result)
    anchor = (231, 17, 43, 255)
    locations = [point for point, value in actual.items() if value == anchor]
    test.assertEqual(len(locations), 1)
    dx, dy = locations[0][0] - 1, locations[0][1]
    test.assertEqual(actual, {(x + dx, y + dy): rgba for (x, y), rgba in expected.items()})
    return dx, dy


def assert_circle(test, image, points, margin):
    test.assertEqual(image.width, image.height)
    center = image.width / 2
    for x, y in points:
        for dx, dy in ((0, 0), (0, 1), (1, 0), (1, 1)):
            test.assertLessEqual(math.hypot(x + dx - center, y + dy - center) + margin, center)


class PreservationTests(unittest.TestCase):
    def test_asymmetric_artwork_is_only_translated(self):
        source = artwork()
        validate_artwork(self, source)
        result, _, _ = app.render(source, (0, 0, 0, 0), 3)
        assert_translation(self, source, result)
        assert_circle(self, result, occupied(result), 3)

    def test_known_tight_canvas_sizes(self):
        for label, size, box, margin, expected_side in PADDING_CASES:
            with self.subTest(case=label):
                source = Image.new('RGBA', size)
                # Build without using the product's bounding/geometry helpers.
                for y in range(box[1], box[3] + 1):
                    for x in range(box[0], box[2] + 1):
                        source.putpixel((x, y), (17, 83, 191, 255))
                count = (box[2] - box[0] + 1) * (box[3] - box[1] + 1)
                self.assertEqual(len(occupied(source)), count)
                self.assertEqual(source.getchannel('A').getbbox(), (box[0], box[1], box[2] + 1, box[3] + 1))
                result, _, _ = app.render(source, (0, 0, 0, 0), margin)
                self.assertEqual(result.size, (expected_side, expected_side))
                self.assertEqual(len(occupied(result)), count)
                assert_circle(self, result, occupied(result), margin)

    def test_background_confidence_boundary(self):
        for label, exceptions, accepted in BORDER_CASES:
            with self.subTest(case=label):
                source = Image.new('RGBA', (11, 11), 'white')
                for x in range(exceptions):
                    source.putpixel((x, 0), (0, 0, 255, 255))
                source.putpixel((5, 5), (255, 0, 0, 255))
                border = {(x, y) for y in range(11) for x in range(11)
                          if x in (0, 10) or y in (0, 10)}
                self.assertEqual(len(border), 40)
                self.assertEqual(sum(source.getpixel(p) == (255, 255, 255, 255) for p in border), 40 - exceptions)
                if not accepted:
                    with self.assertRaisesRegex(ValueError, 'ambiguous background'):
                        app.foreground(source)
                else:
                    result, _ = app.foreground(source)
                    expected = {(x, 0): (0, 0, 255, 255) for x in range(exceptions)}
                    expected[(5, 5)] = (255, 0, 0, 255)
                    self.assertEqual(occupied(result), expected)


class SavedImageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.source = self.directory / 'art.png'
        self.output = self.directory / 'out.png'

    def run_cli(self, *options):
        result = subprocess.run([sys.executable, str(ROOT / 'circle-img.py'), '-i', str(self.source),
                                 '-o', str(self.output), *options], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with Image.open(self.output) as image:
            self.assertEqual(image.format, 'PNG')
            self.assertEqual(image.mode, 'RGBA')
            return image.copy()

    def test_saved_transparent_artwork_pixels_and_clearance(self):
        source = artwork()
        validate_artwork(self, source)
        source.save(self.source)
        with Image.open(self.source) as stored:
            self.assertEqual(stored.tobytes(), source.tobytes())
        result = self.run_cli('--background', 'transparent', '--margin', '3')
        assert_translation(self, source, result)
        assert_circle(self, result, occupied(result), 3)

    def test_saved_canvas_dimensions(self):
        source = Image.new('RGBA', (8, 10))
        source.paste(Image.new('RGBA', (6, 8), (17, 83, 191, 255)), (1, 1))
        self.assertEqual(source.getchannel('A').getbbox(), (1, 1, 7, 9))
        self.assertEqual(source.getpixel((0, 0)), (0, 0, 0, 0))
        self.assertEqual(len(occupied(source)), 48)
        source.save(self.source)
        result = self.run_cli('--background', 'transparent')
        # Diagonal is exactly 10, with integer placement on a 10x10 canvas.
        self.assertEqual(result.size, (10, 10))
        self.assertEqual(len(occupied(result)), 48)
        assert_circle(self, result, occupied(result), 0)

    def test_saved_custom_background_and_soft_edges(self):
        source = artwork()
        validate_artwork(self, source)
        source.save(self.source)
        result = self.run_cli('--background', '#123456')
        anchor = (231, 17, 43, 255)
        locations = [point for point, value in occupied(result).items() if value == anchor]
        self.assertEqual(len(locations), 1)
        dx, dy = locations[0][0] - 1, locations[0][1]
        bg = (18, 52, 86)
        mapped = {(x + dx, y + dy): rgba for (x, y), rgba in occupied(source).items()}
        for y in range(result.height):
            for x in range(result.width):
                fg = mapped.get((x, y), (0, 0, 0, 0))
                alpha = fg[3]
                expected = tuple((fg[c] * alpha + bg[c] * (255 - alpha) + 127) // 255 for c in range(3)) + (255,)
                self.assertEqual(result.getpixel((x, y)), expected, (x, y))
        assert_circle(self, result, mapped, 0)

    def test_saved_icc_profile(self):
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
        self.assertEqual(profile[36:40], b'acsp')
        self.assertEqual(ImageCms.ImageCmsProfile(io.BytesIO(profile)).profile.xcolor_space.strip(), 'RGB')
        artwork().save(self.source, icc_profile=profile)
        with Image.open(self.source) as stored:
            self.assertEqual(stored.info['icc_profile'], profile)
        result = self.run_cli()
        self.assertEqual(result.info.get('icc_profile'), profile)


if __name__ == '__main__':
    unittest.main()
