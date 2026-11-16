"""SVG rasterization contracts, independent fixture checks, and real CLI output."""
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image
import resvg_py
from test_edge_background import load, ROOT

app = load('svg_app', ROOT/'circle-img.py')
FIXTURE = ROOT/'tests/fixtures/layered-vector.svg'
SIZE_CASES = (
    ('small landscape', 'width="200" height="100"', (1024,512)),
    ('small portrait', 'width="100" height="200"', (512,1024)),
    ('viewBox only', 'viewBox="0 0 80 40"', (1024,512)),
    ('physical units', 'width="2in" height="1in"', (1024,512)),
    ('large', 'width="1600" height="800"', (1600,800)),
    ('at minimum', 'width="1024" height="512"', (1024,512)),
)

class SVGTests(unittest.TestCase):
    def test_fixture_renderer_contract(self):
        data=resvg_py.svg_to_bytes(svg_path=str(FIXTURE),dpi=96)
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual(image.size,(128,128))
            self.assertEqual(image.convert('RGBA').getpixel((0,0))[3],0)
            self.assertEqual(image.getpixel((64,20)),(40,130,170,255))
            self.assertEqual(image.getpixel((50,50)),(240,200,80,255))
            self.assertEqual(image.getpixel((64,64)),(40,130,170,255))

    def test_sizes_and_case_insensitive_extension(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'shape.SVG'
            for name,attributes,size in SIZE_CASES:
                with self.subTest(case=name):
                    source.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" {attributes}><rect width="20" height="20" fill="red"/></svg>')
                    image,_=app.load_image(source)
                    self.assertEqual(image.size,size)
                    self.assertEqual(image.mode,'RGBA')
                    self.assertEqual(image.getpixel((0,0)),(255,0,0,255))
                    self.assertEqual(image.getpixel((size[0]-1,size[1]-1))[3],0)

    def test_renders_vectors_at_target_resolution(self):
        actual,_=app.load_image(FIXTURE)
        expected=resvg_py.svg_to_bytes(svg_path=str(FIXTURE),width=1024,dpi=96)
        with Image.open(io.BytesIO(expected)) as image:
            self.assertEqual(actual.tobytes(),image.convert('RGBA').tobytes())
        self.assertEqual(actual.size,(1024,1024))
        self.assertTrue(any(0<a<255 for a in actual.getchannel('A').get_flattened_data()))

    def test_layers_keep_enclosed_detail(self):
        image,_=app.load_image(FIXTURE)
        outer,color,_=app.layered_foreground(image,1,8)
        self.assertEqual(outer.tobytes(),image.tobytes())
        self.assertEqual(color,(255,255,255,255))
        inner,color,_=app.layered_foreground(image,2,8)
        self.assertEqual(color,(40,130,170,255))
        self.assertEqual(inner.getchannel('A').getbbox(),(384,384,640,640))
        self.assertEqual(inner.getpixel((512,512)),(40,130,170,255))
        self.assertEqual(inner.getpixel((400,400)),(240,200,80,255))

    def test_opaque_background_and_partial_opacity(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'opaque.svg'
            source.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128">'
                              '<rect width="128" height="128" fill="#2882aa"/>'
                              '<rect x="48" y="48" width="32" height="32" fill="#f0c850"/></svg>')
            image,_=app.load_image(source)
            self.assertEqual(image.getchannel('A').getextrema(),(255,255))
            artwork,color,_=app.layered_foreground(image,1,8)
            self.assertEqual(color,(40,130,170,255))
            self.assertEqual(artwork.getchannel('A').getbbox(),(384,384,640,640))
            source.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128">'
                              '<rect width="64" height="64" fill="red" opacity="0.5"/></svg>')
            image,_=app.load_image(source)
            self.assertEqual(image.getpixel((100,100)),(255,0,0,128))
            self.assertEqual(image.getpixel((900,900))[3],0)

    def test_missing_svg_dependency_and_environment_fallback(self):
        # Block only the renderer: Pillow remains available. Re-exec into the
        # project environment removes this process-local simulated import fault.
        blocker = """import sys, runpy
class BlockRenderer:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'resvg_py':
            raise ModuleNotFoundError("No module named 'resvg_py'", name='resvg_py')
sys.meta_path.insert(0, BlockRenderer())
try:
    import resvg_py
except ModuleNotFoundError:
    pass
else:
    raise AssertionError('renderer import fault was not installed')
sys.argv = [sys.argv[1], '--help']
runpy.run_path(sys.argv[0], run_name='__main__')
"""
        with tempfile.TemporaryDirectory() as directory:
            copied=Path(directory)/'circle-img.py'
            copied.write_bytes((ROOT/'circle-img.py').read_bytes())
            run=subprocess.run([sys.executable,'-c',blocker,str(copied)],capture_output=True,text=True,timeout=15)
            self.assertEqual(run.returncode,1)
            self.assertIn('resvg_py (SVG renderer) is not installed',run.stderr)
            self.assertIn('pip install -r requirements.txt',run.stderr)
            self.assertNotIn('Traceback',run.stderr)
            # Make the simulated starting environment distinct from .venv.
            fallback=blocker.replace('sys.argv = [sys.argv[1]',
                                     "sys.prefix = '/simulated/external/python'\nsys.argv = [sys.argv[1]")
            run=subprocess.run([sys.executable,'-c',fallback,str(ROOT/'circle-img.py')],capture_output=True,text=True,timeout=15)
            self.assertEqual(run.returncode,0,run.stderr)
            self.assertIn('1024px',run.stdout)

    def test_invalid_and_missing_svg(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'bad.svg'
            for content in ('not XML','<svg', '<svg xmlns="http://www.w3.org/2000/svg" width="0" height="0"/>'):
                source.write_text(content)
                with self.assertRaisesRegex(ValueError,'SVG'):
                    app.load_image(source)
            with self.assertRaises(OSError):
                app.load_image(Path(directory)/'missing.svg')

    def test_cli_batch_preview_background_and_verbose(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            raster=folder/'raster.png'
            Image.new('RGBA',(10,10),(255,0,0,255)).save(raster)
            # A transparent border makes this raster's foreground unambiguous.
            with Image.open(raster) as im:
                padded=Image.new('RGBA',(12,12));padded.paste(im,(1,1));padded.save(raster)
            output=folder/'output';output.mkdir()
            command=[sys.executable,str(ROOT/'circle-img.py'),'-i',str(FIXTURE),str(raster),'-o',str(output),'-b','transparent','--preview','-v']
            run=subprocess.run(command,capture_output=True,text=True,timeout=20)
            self.assertEqual(run.returncode,0,run.stderr)
            self.assertIn('SVG rendered: 1024 × 1024',run.stderr)
            for stem in ('layered-vector','raster'):
                with Image.open(output/(stem+'.png')) as image:
                    self.assertEqual(image.width,image.height)
                    self.assertEqual(image.getpixel((0,0))[3],0)
                self.assertTrue((output/(stem+'-preview.png')).exists())
            with Image.open(output/'raster.png') as image:
                self.assertEqual(image.size,(256,256))
            # Errors remain per-file and must not abort the following valid SVG.
            bad=folder/'bad.svg';bad.write_text('<svg')
            run=subprocess.run([sys.executable,str(ROOT/'circle-img.py'),'-i',str(bad),str(FIXTURE),'-o',str(output),'-O'],capture_output=True,text=True,timeout=20)
            self.assertEqual(run.returncode,1)
            self.assertIn('invalid SVG',run.stderr)
            self.assertNotIn('Traceback',run.stderr)
            self.assertIn('Saved ',run.stdout)

if __name__=='__main__':
    unittest.main()
