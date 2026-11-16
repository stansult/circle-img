"""Independent scalar oracles protect the optimized mask algorithms."""
from collections import deque
import random
import unittest
from PIL import Image
from fixture_data import BORDER_FILL_CASES, MASK_RANDOM_SEED, EROSION_RADII
from test_edge_background import load, ROOT

app = load('optimized_background_app', ROOT/'circle-img.py')


def binary(rows):
    assert rows and all(len(row)==len(rows[0]) for row in rows)
    assert all(value in '01' for row in rows for value in row)
    return Image.frombytes('L',(len(rows[0]),len(rows)),bytes(255 if value=='1' else 0 for row in rows for value in row))


def scalar_region(image, core, candidate, tolerance):
    """Original four-neighbor eligibility and rejection rule, without bulk masks."""
    w,h=image.size
    remaining=bytearray(w*h)
    seen=set()
    queue=deque((x,y) for y in range(h) for x in range(w) if x in (0,w-1) or y in (0,h-1))
    while queue:
        x,y=queue.popleft()
        if (x,y) in seen:
            continue
        seen.add((x,y))
        pixel=image.getpixel((x,y))
        distance=max(abs(pixel[c]-candidate[c]) for c in range(3))
        fringe=not core.getpixel((x,y))
        if pixel[3]==0 or distance<=tolerance or (fringe and (pixel[3]<255 or distance<=max(32,tolerance))):
            remaining[y*w+x]=255
            for nx,ny in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
                if 0<=nx<w and 0<=ny<h:
                    queue.append((nx,ny))
        elif fringe:
            raise ValueError('contrasting boundary')
    return bytes(remaining)


def scalar_fill(mask):
    w,h=mask.size
    seen=set()
    queue=deque((x,y) for y in range(h) for x in range(w) if x in (0,w-1) or y in (0,h-1))
    output=Image.new('L',mask.size)
    while queue:
        x,y=queue.popleft()
        if (x,y) in seen:
            continue
        seen.add((x,y))
        if not mask.getpixel((x,y)):
            continue
        output.putpixel((x,y),255)
        for nx,ny in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
            if 0<=nx<w and 0<=ny<h:
                queue.append((nx,ny))
    return output


def scalar_erosion(mask,radius):
    w,h=mask.size
    output=Image.new('L',mask.size)
    for y in range(radius,h-radius):
        for x in range(radius,w-radius):
            output.putpixel((x,y),min(mask.getpixel((nx,ny))
                for ny in range(y-radius,y+radius+1) for nx in range(x-radius,x+radius+1)))
    return output


class BackgroundOptimizationTests(unittest.TestCase):
    def test_fixture_and_oracle_contracts(self):
        for _,rows in BORDER_FILL_CASES:
            mask=binary(rows)
            self.assertEqual(mask.size,(len(rows[0]),len(rows)))
            self.assertEqual(list(mask.get_flattened_data()),[255 if value=='1' else 0 for row in rows for value in row])
        island=binary(('11111','10001','10101','10001','11111'))
        expected=binary(('11111','10001','10001','10001','11111'))
        self.assertEqual(scalar_fill(island).tobytes(),expected.tobytes())
        diagonal=binary(('10000','01000','00100','00010','00001'))
        self.assertEqual(scalar_fill(diagonal).tobytes(),binary(('10000','00000','00000','00000','00001')).tobytes())
        self.assertEqual(scalar_erosion(Image.new('L',(5,5),255),1).tobytes(),binary(('00000','01110','01110','01110','00000')).tobytes())
        image=Image.new('RGBA',(5,5),(10,20,30,255))
        image.putpixel((2,2),(200,200,200,1))
        expected=bytearray(b'\xff'*25);expected[12]=0
        self.assertEqual(scalar_region(image,Image.new('L',(5,5),255),(10,20,30),8),bytes(expected))
        image.putpixel((0,0),(200,200,200,255))
        with self.assertRaises(ValueError):
            scalar_region(image,Image.new('L',(5,5)),(10,20,30),8)

    def test_span_fill_matches_independent_flood_fill(self):
        cases=[(name,binary(rows)) for name,rows in BORDER_FILL_CASES]
        rng=random.Random(MASK_RANDOM_SEED)
        for index in range(300):
            w,h=rng.randint(1,24),rng.randint(1,24)
            mask=Image.frombytes('L',(w,h),bytes(255 if rng.random()<(.2,.5,.8)[index%3] else 0 for _ in range(w*h)))
            cases.append((f'random {index}',mask))
        for name,mask in cases:
            with self.subTest(case=name):
                before=mask.tobytes()
                self.assertEqual(app.border_connected(mask).tobytes(),scalar_fill(mask).tobytes())
                self.assertEqual(mask.tobytes(),before)

    def test_fast_erosion_matches_square_minimum(self):
        rng=random.Random(MASK_RANDOM_SEED)
        for radius in EROSION_RADII:
            for kind in ('solid','hole','gray'):
                with self.subTest(radius=radius,kind=kind):
                    mask=Image.new('L',(37,35),255)
                    if kind=='hole':
                        mask.putpixel((18,17),0)
                    elif kind=='gray':
                        mask=Image.frombytes('L',mask.size,bytes(rng.randrange(256) for _ in range(37*35)))
                    before=mask.tobytes()
                    self.assertEqual(app.erode_square(mask,radius).tobytes(),scalar_erosion(mask,radius).tobytes())
                    self.assertEqual(mask.tobytes(),before)
        for size in ((1,1),(1,8),(9,1),(3,2)):
            self.assertEqual(app.erode_square(Image.new('L',size,255),16).getbbox(),None)

    def test_bulk_color_and_fringe_rules_match_scalar_reference(self):
        rng=random.Random(MASK_RANDOM_SEED)
        candidate=(80,100,120)
        accepted=rejected=0
        for index in range(240):
            w,h=rng.randint(1,15),rng.randint(1,15)
            image=Image.new('RGBA',(w,h))
            core=Image.new('L',(w,h),255 if index%3==0 else 0)
            tolerance=(0,8,32,64,255)[index%5]
            for y in range(h):
                for x in range(w):
                    rgb=list(candidate)
                    rgb[rng.randrange(3)]+=rng.choice((0,8,9,32,33,60))
                    image.putpixel((x,y),tuple(rgb)+(rng.choice((0,1,128,254,255)),))
                    if index%3==2:
                        core.putpixel((x,y),rng.choice((0,255)))
            before=image.tobytes()
            with self.subTest(case=index,tolerance=tolerance):
                try:
                    expected=scalar_region(image,core,candidate,tolerance)
                except ValueError:
                    rejected+=1
                    with self.assertRaisesRegex(ValueError,'contrasting content touches'):
                        app.inner_background_region(image,core,candidate,tolerance)
                else:
                    accepted+=1
                    self.assertEqual(app.inner_background_region(image,core,candidate,tolerance).tobytes(),expected)
                self.assertEqual(image.tobytes(),before)
        self.assertGreater(accepted,50)
        self.assertGreater(rejected,20)

    def test_rejection_requires_four_neighbor_access(self):
        candidate=(80,100,120)
        image=Image.new('RGBA',(5,5),(200,200,200,255))
        core=Image.new('L',(5,5),255)
        core.putpixel((2,2),0)
        image.putpixel((1,0),candidate+(255,))
        image.putpixel((1,1),candidate+(255,))
        expected=bytearray(25);expected[1]=expected[6]=255
        self.assertEqual(scalar_region(image,core,candidate,0),bytes(expected))
        self.assertEqual(app.inner_background_region(image,core,candidate,0).tobytes(),bytes(expected))
        # Connecting one orthogonal neighbor now makes the contrasting fringe
        # reachable. Diagonal contact alone above must not reject the image.
        image.putpixel((2,1),candidate+(255,))
        with self.assertRaises(ValueError):
            scalar_region(image,core,candidate,0)
        with self.assertRaisesRegex(ValueError,'contrasting content touches'):
            app.inner_background_region(image,core,candidate,0)

    def test_large_region_preserves_enclosed_pixels(self):
        # Check a large mask without imposing a machine-dependent timing limit.
        # A full row between disconnected border components must not fill
        # earlier rows' enclosed pixels when scanning backward.
        mask=Image.new('L',(2048,1024),255)
        mask.paste(0,(1,1,2047,1023))
        mask.putpixel((1024,512),255)
        result=app.border_connected(mask)
        expected=mask.copy();expected.putpixel((1024,512),0)
        self.assertEqual(result.tobytes(),expected.tobytes())


if __name__=='__main__':
    unittest.main()
