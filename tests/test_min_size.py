"""The output floor is configurable and independent of source canvas padding."""
import importlib.util
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from PIL import Image
from fixture_data import MIN_SIZE_CASES

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('min_size_app', ROOT / 'circle-img.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def fixture(size, box):
    source = Image.new('RGBA', size)
    art = Image.new('RGBA', (box[2]-box[0], box[3]-box[1]), (20, 140, 90, 255))
    art.putpixel((0, 0), (240, 10, 70, 128))
    source.paste(art, box[:2])
    return source, art


class MinimumSizeTests(unittest.TestCase):
    def test_default_and_custom_sizes_preserve_artwork(self):
        for label, size, box, margin, minimum, side in MIN_SIZE_CASES:
            with self.subTest(case=label):
                source, art = fixture(size, box)
                self.assertEqual(source.getchannel('A').getbbox(), box)
                self.assertEqual(source.getpixel(box[:2]), (240,10,70,128))
                self.assertEqual(source.getpixel((box[2]-1,box[3]-1)), (20,140,90,255))
                self.assertEqual(sum(a>0 for a in source.getchannel('A').get_flattened_data()), art.width*art.height)
                options = {} if minimum is None else {'min_size':minimum}
                result, layer, _ = app.render(source, (0,0,0,0), margin, **options)
                self.assertEqual(result.size, (side, side))
                target_box = result.getchannel('A').getbbox()
                self.assertEqual(result.crop(target_box).tobytes(), art.tobytes())
                self.assertEqual((target_box[2]-target_box[0],target_box[3]-target_box[1]),art.size)
                self.assertEqual(result.tobytes(),layer.tobytes())
                # Every occupied pixel lies in this filled rectangle: its four
                # corners independently bound all pixel areas, including alpha 128.
                for x in (target_box[0],target_box[2]):
                    for y in (target_box[1],target_box[3]):
                        self.assertLessEqual(math.hypot(x-side/2,y-side/2)+margin,side/2)

    def test_cli_default_and_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            source = directory/'padded.png'
            fixture((1200,900),(900,600,906,608))[0].save(source)
            for value, expected_side in ((None,256),(0,10),(512,512)):
                with self.subTest(minimum=value):
                    output = directory/f'out-{value}.png'
                    args = [] if value is None else ['--min-size',str(value)]
                    run = subprocess.run([sys.executable,str(ROOT/'circle-img.py'),'-i',str(source),
                                          '-o',str(output),'--background','transparent','--preview',*args],
                                         capture_output=True,text=True)
                    self.assertEqual(run.returncode,0,run.stderr)
                    for path in (output,output.with_stem(output.stem+'-preview')):
                        with Image.open(path) as image:
                            self.assertEqual(image.size,(expected_side,expected_side))
                    with Image.open(output) as image:
                        box=image.getchannel('A').getbbox()
                        self.assertEqual(image.crop(box).tobytes(),fixture((20,20),(4,5,10,13))[1].tobytes())

    def test_negative_minimum_rejected(self):
        source,_=fixture((20,20),(4,5,10,13))
        with self.assertRaisesRegex(ValueError,'min-size must be nonnegative'):
            app.render(source,(0,0,0,0),0,min_size=-1)
        with tempfile.TemporaryDirectory() as directory:
            source_path=Path(directory)/'source.png'; source.save(source_path)
            output=Path(directory)/'out.png'
            run=subprocess.run([sys.executable,str(ROOT/'circle-img.py'),'-i',str(source_path),
                                '-o',str(output),'--min-size','-1'],capture_output=True,text=True)
            self.assertEqual(run.returncode,1)
            self.assertIn('min-size must be nonnegative',run.stderr)
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
