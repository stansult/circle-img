"""Edge-background mode must isolate artwork without flooding through it."""
import importlib.util
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw
from fixture_data import EDGE_TILE_CASES

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


app = load('edge_app', ROOT / 'circle-img.py')
fixture = load('tile_generator', ROOT / 'tests/fixtures/generate_rounded_tile.py')



class EdgeBackgroundTests(unittest.TestCase):
    def expected_artwork(self, source):
        expected = Image.new('RGBA', source.size)
        expected.paste(source.crop((24, 24, 56, 56)), (24, 24))
        return expected

    def test_fixture_contract(self):
        source = fixture.make_tile()
        self.assertEqual(source.size, (80, 80))
        self.assertEqual(source.getpixel((0, 0)), (250, 250, 250, 255))
        self.assertEqual(source.getpixel((40, 0)), (30, 130, 150, 255))
        self.assertEqual(source.getpixel((24, 24)), (250, 250, 250, 255))
        self.assertEqual(source.getpixel((30, 30)), (30, 130, 150, 255))
        self.assertEqual(source.getpixel((35, 35)), (70, 220, 190, 255))
        self.assertTrue(any(source.getpixel((x, y))[:3] not in (fixture.BACKGROUND, fixture.CORNER)
                            for x in range(12) for y in range(12)))
        for y in range(24, 56):
            for x in range(24, 56):
                expected = ((70, 220, 190) if 35 <= x < 42 and 35 <= y < 45 else
                            (250, 250, 250) if min(x-24, 55-x, y-24, 55-y) < 4 else (30, 130, 150))
                self.assertEqual(source.getpixel((x, y)), expected + (255,))
        for _, options in EDGE_TILE_CASES:
            variant = fixture.make_tile(**options)
            self.assertEqual(variant.crop((24,24,56,56)).tobytes(), source.crop((24,24,56,56)).tobytes())
            self.assertEqual(variant.getpixel((0,0))[3], 0 if options.get('transparent') else 255)
            for x, y in ((40,0),(40,79),(0,40),(79,40)):
                pixel = variant.getpixel((x,y))
                self.assertEqual(pixel[3],255)
                self.assertLessEqual(max(abs(pixel[c]-fixture.BACKGROUND[c]) for c in range(3)),2)
        with Image.open(ROOT / 'tests/fixtures/rounded-tile.png') as stored:
            self.assertEqual(stored.tobytes(), source.tobytes())

    def test_exact_foreground_and_enclosed_details(self):
        for label, options in EDGE_TILE_CASES:
            with self.subTest(case=label):
                source = fixture.make_tile(**options)
                art, _ = app.foreground(source, 'edge', 8)
                expected = self.expected_artwork(source)
                self.assertEqual(art.getchannel('A').tobytes(), expected.getchannel('A').tobytes())
                self.assertEqual(art.crop((24,24,56,56)).tobytes(), expected.crop((24,24,56,56)).tobytes())
                # Artwork, not the tile, determines padding; retain input-size floor.
                self.assertEqual(app.render(art, fixture.BACKGROUND+(255,), 0)[0].size, (80,80))

    def test_uncertain_edges_or_corners_rejected(self):
        conflicting = fixture.make_tile()
        ImageDraw.Draw(conflicting).line((20,0,59,0), fill='blue')
        one_corner = fixture.make_tile(rounded=False)
        ImageDraw.Draw(one_corner).rectangle((0,0,5,5), fill='white')
        connected = fixture.make_tile()
        ImageDraw.Draw(connected).line((0,0,24,24), fill=fixture.CORNER, width=3)
        edge_mark = fixture.make_tile(rounded=False)
        edge_mark.putpixel((40,0),(255,255,255,255))
        different_corner = fixture.make_tile()
        different_corner.putpixel((0,0),(255,0,0,255))
        transparent = Image.new('RGBA',(80,80))
        transparent.paste(fixture.make_tile().crop((24,24,56,56)),(24,24))
        for label, source in (('inconsistent sides',conflicting),('corner artwork',one_corner),
                              ('corner connected to artwork',connected),('no opaque edges',transparent),
                              ('small edge artwork',edge_mark),('different corner color',different_corner)):
            with self.subTest(case=label):
                with self.assertRaisesRegex(ValueError, 'ambiguous edge background'):
                    app.foreground(source,'edge',8)

    def test_radial_margin_and_pixel_preservation(self):
        source = fixture.make_tile()
        artwork, _ = app.foreground(source,'edge',8)
        result, layer, _ = app.render(artwork,(0,0,0,0),30)
        # 32x32 square support: ceil(sqrt(32**2+32**2) + 2*30) = 106.
        self.assertEqual(result.size,(106,106))
        self.assertEqual(result.crop((37,37,69,69)).tobytes(),source.crop((24,24,56,56)).tobytes())
        self.assertEqual(sum(a>0 for a in result.getchannel('A').get_flattened_data()),1024)
        for y in range(37,69):
            for x in range(37,69):
                for dx,dy in ((0,0),(0,1),(1,0),(1,1)):
                    self.assertLessEqual(math.hypot(x+dx-53,y+dy-53)+30,53)

    def test_explicit_tolerance_is_honored(self):
        source = fixture.make_tile(noise=True)
        with self.assertRaisesRegex(ValueError,'ambiguous edge background'):
            app.foreground(source,'edge',0)
        art,_ = app.foreground(source,'edge',8)
        self.assertEqual(art.getchannel('A').tobytes(),self.expected_artwork(source).getchannel('A').tobytes())

    def test_default_detection_unchanged(self):
        with self.assertRaisesRegex(ValueError,'ambiguous background'):
            app.foreground(fixture.make_tile())
        alpha = fixture.make_tile(transparent=True)
        art, method = app.foreground(alpha)
        self.assertEqual(method,'existing alpha')
        self.assertEqual(art.tobytes(),alpha.tobytes())

    def cli(self, *args):
        return subprocess.run([sys.executable,str(ROOT/'circle-img.py'),*map(str,args)],capture_output=True,text=True)

    def test_cli_auto_color_explicit_color_and_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            destination = folder/'out'; destination.mkdir()
            sources=[]
            for name, bg in (('teal',(30,130,150)),('purple',(120,60,140))):
                source = folder/(name+'.png')
                fixture.make_tile(background=bg).save(source)
                sources.append(source)
            run = self.cli('-i',*sources,'-o',destination,'--source-background','edge','-b','auto','--preview')
            self.assertEqual(run.returncode,0,run.stderr)
            for source,bg in zip(sources,((30,130,150),(120,60,140))):
                with Image.open(destination/source.name) as image:
                    self.assertEqual(image.size,(80,80))
                    self.assertEqual(image.getpixel((0,0)),bg+(255,))
                    self.assertEqual(image.crop((24,24,56,56)).tobytes(),fixture.make_tile(background=bg).crop((24,24,56,56)).tobytes())
                self.assertTrue((destination/(source.stem+'-preview.png')).exists())
            for bg in ('transparent','#123456'):
                run=self.cli('-i',sources[0],'-o',destination,'--source-background','edge','-b',bg,'-O')
                self.assertEqual(run.returncode,0,run.stderr)
                with Image.open(destination/sources[0].name) as image:
                    self.assertEqual(image.getpixel((0,0)),app.color(bg))

    def test_auto_requires_edge_mode_and_rejects_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'out.png'
            run=self.cli('-i',ROOT/'tests/fixtures/rounded-tile.png','-o',target,'-b','auto')
            self.assertNotEqual(run.returncode,0)
            self.assertIn('--background auto requires --source-background edge',run.stderr)
            self.assertFalse(target.exists())


if __name__ == '__main__':
    unittest.main()
