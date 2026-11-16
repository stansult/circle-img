"""Outside-in backgrounds: independent fixture, pixel, geometry and CLI checks."""
import math
from pathlib import Path
import tempfile
import unittest
from PIL import Image, ImageDraw
from test_edge_background import app, fixture, load, ROOT
import test_edge_background as edge_tests

circle = load('layered_circle_generator', ROOT/'tests/fixtures/generate_layered_circle.py')


class BackgroundLayersTests(unittest.TestCase):
    cli = edge_tests.EdgeBackgroundTests.cli

    def assert_art(self, source, art, box, extra=()):
        expected = Image.new('L',source.size)
        expected.paste(source.getchannel('A').crop(box),box)
        for point in extra:
            expected.putpixel(point,source.getpixel(point)[3])
        self.assertEqual(art.getchannel('A').tobytes(),expected.tobytes())
        for y in range(source.height):
            for x in range(source.width):
                if expected.getpixel((x,y)):
                    self.assertEqual(art.getpixel((x,y)),source.getpixel((x,y)))

    def test_circle_fixture_contract(self):
        source=circle.make_circle()
        self.assertEqual(source.size,(128,128))
        for y in range(128):
            for x in range(128):
                pixel=source.getpixel((x,y))
                radius=math.hypot(x+.5-64,y+.5-64)
                self.assertEqual(pixel[3]>0,radius<60)
                if 48<=x<80 and 48<=y<80:
                    rgb=((220,90,150) if 59<=x<66 and 59<=y<69 else
                         (250,250,250) if min(x-48,79-x,y-48,79-y)<4 else circle.BACKGROUND)
                    self.assertEqual(pixel,rgb+(255,))
                elif (x,y)==(85,64):
                    self.assertEqual(pixel,(220,90,150,1))
                elif radius<=59:
                    self.assertEqual(pixel,circle.BACKGROUND+(255,))
                elif radius<60:
                    self.assertTrue(0<pixel[3]<255)
                    self.assertEqual(pixel[:3],tuple(c+15 for c in circle.BACKGROUND))
        with Image.open(ROOT/'tests/fixtures/layered-background.png') as stored:
            self.assertEqual(source.tobytes(),stored.tobytes())

    def test_one_layer_keeps_circle_and_uses_white(self):
        source=circle.make_circle()
        art,bg,_=app.layered_foreground(source)
        self.assertEqual(art.tobytes(),source.tobytes())
        self.assertEqual(bg,(255,255,255,255))
        self.assertEqual(app.render(art,bg,0)[0].size,(256,256))

    def test_two_layers_remove_circle_preserve_enclosed_and_faint_pixels(self):
        for noise in (False,True):
            with self.subTest(noise=noise):
                source=circle.make_circle(noise=noise)
                art,bg,_=app.layered_foreground(source,2)
                self.assertEqual(bg,circle.BACKGROUND+(255,))
                self.assert_art(source,art,(48,48,80,80),((85,64),))
                output,layer,_=app.render(art,(0,0,0,0),7,min_size=0)
                self.assertLess(output.width,70)
                self.assertEqual(sum(a>0 for a in layer.getchannel('A').get_flattened_data()),1025)
                for y in range(layer.height):
                    for x in range(layer.width):
                        if layer.getpixel((x,y))[3]:
                            for dx,dy in ((0,0),(0,1),(1,0),(1,1)):
                                self.assertLessEqual(math.hypot(x+dx-output.width/2,y+dy-output.height/2)+7,output.width/2+1e-9)

    def test_rounded_corners_are_outer_layer(self):
        for noise in (False,True):
            source=fixture.make_tile(noise=noise)
            art,bg,_=app.layered_foreground(source,1)
            self.assertEqual(bg,source.getpixel((0,0)))
            self.assertEqual(art.getpixel((0,0))[3],0)
            self.assertEqual(art.getpixel((40,0)),source.getpixel((40,0)))
            self.assertEqual(art.crop((12,12,68,68)).tobytes(),source.crop((12,12,68,68)).tobytes())
            inner,bg,_=app.layered_foreground(source,2)
            # Noisy boundary samples can shift the median by their ±2 noise;
            # there is no hidden original color available to the detector.
            self.assertEqual(bg[3],255)
            self.assertLessEqual(max(abs(bg[c]-fixture.BACKGROUND[c]) for c in range(3)),2 if noise else 0)
            self.assert_art(source,inner,(24,24,56,56))

    def test_flat_background_reused_and_no_second_layer_rejected(self):
        source=Image.new('RGBA',(80,80),(30,130,150,255))
        ImageDraw.Draw(source).rectangle((24,24,55,55),fill='white')
        art,bg,_=app.layered_foreground(source)
        self.assertEqual(bg,(30,130,150,255))
        self.assert_art(source,art,(24,24,56,56))
        with self.assertRaisesRegex(ValueError,'no foreground remains'):
            app.layered_foreground(source,2)

    def test_conflicting_boundary_and_touching_art_rejected(self):
        source=circle.make_circle()
        ImageDraw.Draw(source).rectangle((4,60,20,68),fill='white')
        with self.assertRaisesRegex(ValueError,'ambiguous background layer 2'):
            app.layered_foreground(source,2)
        split=circle.make_circle()
        for y in range(128):
            for x in range(64,128):
                if split.getpixel((x,y))[3]:
                    split.putpixel((x,y),(150,20,60,255))
        with self.assertRaisesRegex(ValueError,'visible boundary'):
            app.layered_foreground(split,2)
        with self.assertRaisesRegex(ValueError,'visible boundary'):
            app.layered_foreground(circle.make_circle(noise=True),2,tolerance=0)

    def test_cli_defaults_batch_overrides_and_saved_pixels(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory); dest=folder/'out'; dest.mkdir()
            sources=[]
            colors=((40,130,170),(130,60,150))
            for index,bg in enumerate(colors):
                path=folder/f'{index}.png'; circle.make_circle(background=bg).save(path); sources.append(path)
            run=self.cli('-i',*sources,'-o',dest,'--background-layers','2','--preview')
            self.assertEqual(run.returncode,0,run.stderr)
            for source,bg in zip(sources,colors):
                with Image.open(dest/source.name) as output:
                    self.assertEqual(output.size,(256,256))
                    self.assertEqual(output.getpixel((0,0)),bg+(255,))
                self.assertTrue((dest/(source.stem+'-preview.png')).exists())
            for options,background in (([],(255,255,255,255)),(['--background','transparent'],(0,0,0,0)),(['--background','#123456'],(18,52,86,255))):
                run=self.cli('-i',sources[0],'-o',dest,'-O',*options)
                self.assertEqual(run.returncode,0,run.stderr)
                with Image.open(dest/sources[0].name) as output:
                    self.assertEqual(output.getpixel((0,0)),background)
            run=self.cli('-i',sources[0],'-o',dest,'-O','--background-layers','2','-b','transparent','--min-size','0')
            self.assertEqual(run.returncode,0,run.stderr)
            with Image.open(dest/sources[0].name) as output:
                pixels=[p for p in output.get_flattened_data() if p[3]]
                original=circle.make_circle()
                expected=[original.getpixel((x,y)) for y in range(48,80) for x in range(48,80)]+[(220,90,150,1)]
                self.assertCountEqual(pixels,expected)
                self.assertLess(output.width,56)

    def test_removed_background_choices_rejected_before_batch_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory); source=folder/'source.png'
            fixture.make_tile().save(source)
            for option,value in (('--background','auto'),('--source-background','edge')):
                with self.subTest(option=option):
                    destination=folder/value; destination.mkdir()
                    run=self.cli('-i',source,'-o',destination,option,value)
                    self.assertNotEqual(run.returncode,0)
                    self.assertIn('invalid color: '+value,run.stderr)
                    self.assertNotIn('processing:',run.stdout)
                    self.assertEqual(list(destination.iterdir()),[])

    def test_advanced_options_and_invalid_combinations(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory); source=folder/'source.png'; output=folder/'out.png'; mask=folder/'mask.png'
            fixture.make_tile(rounded=False).save(source)
            Image.new('L',(80,80),255).save(mask)
            for options in (['--mask',mask],['--source-background','#1e8296']):
                run=self.cli('-i',source,'-o',output,'--background-layers','2',*options)
                self.assertNotEqual(run.returncode,0)
                self.assertIn('cannot be combined',run.stderr)
                self.assertFalse(output.exists())
            for options,bg in ((['--mask',mask],(255,255,255,255)),(['--source-background','#1e8296'],(30,130,150,255))):
                run=self.cli('-i',source,'-o',output,'-O',*options)
                self.assertEqual(run.returncode,0,run.stderr)
                with Image.open(output) as saved:
                    self.assertEqual(saved.getpixel((0,0)),bg)
            for value in ('0','3','-1'):
                run=self.cli('-i',source,'-o',folder/'invalid.png','--background-layers',value)
                self.assertNotEqual(run.returncode,0)
                self.assertFalse((folder/'invalid.png').exists())


if __name__=='__main__':
    unittest.main()
