#!/usr/bin/env python3
"""Prepare raster artwork for circular crops without resampling it."""
import argparse
from collections import Counter, deque
import math
import os
from pathlib import Path
import random
import shlex
import sys
import tempfile

try:
    from PIL import Image, ImageColor, ImageDraw, ImageOps, ImageFilter, PngImagePlugin
except ModuleNotFoundError as exc:
    if exc.name != 'PIL' or __name__ != '__main__':
        raise
    project = Path(__file__).resolve().parent
    environment = project / '.venv'
    python = environment / 'bin' / 'python'
    # Compare prefixes, not executable targets: venv Python is often a symlink
    # to the system interpreter. Never retry an environment already in use.
    if python.is_file() and os.access(python, os.X_OK) and Path(sys.prefix).resolve() != environment.resolve():
        try:
            os.execv(str(python), [str(python), str(Path(__file__).resolve()), *sys.argv[1:]])
        except OSError as launch_error:
            print(f'error: could not start project Python: {launch_error}', file=sys.stderr)
            sys.exit(1)
    print('error: Pillow is not installed. Run the one-time setup:\n'
          f'  cd {shlex.quote(str(project))}\n'
          '  python3 -m venv .venv\n'
          '  .venv/bin/python -m pip install -r requirements.txt\n'
          'Then run ./circle-img.py normally; activation is unnecessary.', file=sys.stderr)
    sys.exit(1)


def color(value):
    if value.lower() == 'transparent':
        return (0, 0, 0, 0)
    try:
        return ImageColor.getcolor(value, 'RGBA')
    except ValueError as exc:
        raise ValueError(f'invalid color: {value}') from exc


def load_image(path):
    with Image.open(path) as source:
        if getattr(source, 'n_frames', 1) != 1:
            raise ValueError('animated or multi-frame inputs are not supported')
        # Avoid silently reducing precision on inputs outside the supported scope.
        if source.format == 'PNG':
            with open(path, 'rb') as stream:
                header = stream.read(25)
            if len(header) == 25 and header[24] == 16:
                raise ValueError('16-bit PNG inputs are not supported without precision loss')
        if source.mode not in ('1', 'L', 'LA', 'P', 'RGB', 'RGBA'):
            raise ValueError(f'unsupported image mode: {source.mode}; use an 8-bit RGB/RGBA image')
        oriented = ImageOps.exif_transpose(source)
        return oriented.convert('RGBA'), dict(source.info)


def foreground(image, source_background=None, tolerance=0, mask=None):
    """Return straight-alpha foreground; infer only a consistent border color."""
    result = image.copy()
    if mask is not None:
        if mask.size != image.size:
            raise ValueError('mask dimensions must match the oriented input')
        from PIL import ImageChops
        result.putalpha(ImageChops.multiply(image.getchannel('A'), mask.convert('L')))
        return result, 'supplied mask'
    if source_background == 'edge':
        artwork, _, method = edge_foreground(image, tolerance)
        return artwork, method
    if source_background is None and image.getchannel('A').getextrema()[0] < 255:
        return result, 'existing alpha'
    w, h = image.size
    border = [(x, 0) for x in range(w)]
    if h > 1:
        border += [(x, h - 1) for x in range(w)]
    border += [(0, y) for y in range(1, h - 1)]
    if w > 1:
        border += [(w - 1, y) for y in range(1, h - 1)]
    pixels = image.load()
    if source_background is None:
        candidate = Counter(pixels[x, y][:3] for x, y in border).most_common(1)[0][0]
    else:
        rgba = color(source_background)
        if rgba[3] != 255:
            raise ValueError('source background must be opaque')
        candidate = rgba[:3]

    def matches(x, y):
        return max(abs(pixels[x, y][c] - candidate[c]) for c in range(3)) <= tolerance

    if source_background is None and sum(matches(x, y) for x, y in border) / len(border) < .95:
        raise ValueError('ambiguous background: supply --source-background or --mask')
    alpha = result.getchannel('A')
    ap = alpha.load()
    seen = bytearray(w * h)
    queue = deque()

    def visit(x, y):
        index = y * w + x
        if not seen[index]:
            seen[index] = 1
            if matches(x, y):
                ap[x, y] = 0
                queue.append((x, y))

    for x, y in border:
        visit(x, y)
    while queue:
        x, y = queue.popleft()
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < w and 0 <= ny < h:
                visit(nx, ny)
    result.putalpha(alpha)
    return result, f'border-connected RGB {candidate}, tolerance {tolerance}'


def edge_foreground(image, tolerance, *, outer_only=False):
    """Infer a flat tile background and remove four matching corner remnants.

    Corner components are examined AFTER the main-color flood fill. They never
    become traversable seeds for that fill, so enclosed same-colored artwork
    cannot be reached through the main background.
    """
    w, h = image.size
    pixels = image.load()

    def reject(reason):
        raise ValueError(f'ambiguous edge background: {reason}; use an explicit color or --mask')

    def distance(a, b):
        return max(abs(a[c] - b[c]) for c in range(3))

    if min(w, h) < 8:
        reject('image is too small to sample four edges')
    sides = [[pixels[x, y] for x in range(w // 4, w - w // 4)] for y in (0, h - 1)]
    sides += [[pixels[x, y] for y in range(h // 4, h - h // 4)] for x in (0, w - 1)]
    samples = [pixel[:3] for side in sides for pixel in side if pixel[3] == 255]
    if not samples:
        reject('no opaque edge samples; use normal alpha detection for transparent artwork')
    seed = Counter(samples).most_common(1)[0][0]
    inliers = [rgb for rgb in samples if distance(rgb, seed) <= tolerance]
    candidate = tuple(sorted(rgb[c] for rgb in inliers)[len(inliers) // 2] for c in range(3))
    for side in sides:
        if sum(pixel[3] == 255 and distance(pixel, candidate) <= tolerance for pixel in side) / len(side) < .95:
            reject('the middle half of each edge must agree on one opaque color')

    hex_color = '#{:02x}{:02x}{:02x}'.format(*candidate)
    artwork, _ = foreground(image, hex_color, tolerance)
    alpha = artwork.getchannel('A')
    ap = alpha.load()
    patches = []
    colors = []
    corners = ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))
    limit = min(w, h) // 4
    for sx, sy in corners:
        if not ap[sx, sy]:
            continue
        corner_color = pixels[sx, sy][:3]
        if pixels[sx, sy][3] != 255 or distance(corner_color, candidate) <= 2 * tolerance + 24:
            reject('corner color is not clearly separated from the background')
        component = {(sx, sy)}
        queue = deque(component)
        while queue:
            x, y = queue.popleft()
            if abs(x - sx) >= limit or abs(y - sy) >= limit:
                reject('corner content extends beyond a small corner region')
            for nx, ny in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)):
                if 0 <= nx < w and 0 <= ny < h and ap[nx, ny] and (nx, ny) not in component:
                    component.add((nx, ny))
                    queue.append((nx, ny))
        # Accept only a corner-colored patch fading into the inferred background.
        vector = tuple(corner_color[c] - candidate[c] for c in range(3))
        denominator = sum(v*v for v in vector)
        for x, y in component:
            rgb = pixels[x, y]
            amount = max(0, min(1, sum((rgb[c]-candidate[c])*vector[c] for c in range(3)) / denominator))
            if max(abs(rgb[c]-candidate[c]-amount*vector[c]) for c in range(3)) > max(24, tolerance):
                reject('corner contains colors inconsistent with a blended background')
        normalized = {(abs(x-sx), abs(y-sy)) for x, y in component}
        # A rounded-corner remnant narrows inward, allowing two pixels of noise.
        previous = limit
        for y in range(max(y for _, y in normalized) + 1):
            row = [x for x, yy in normalized if yy == y]
            if not row or min(row) > 1 or max(row) > previous + 2:
                reject('corner shape is not a rounded-tile remnant')
            previous = max(row)
        patches.append(component)
        colors.append(corner_color)

    if patches:
        if len(patches) != 4:
            reject('corner remnants must appear at all four corners')
        if any(distance(rgb, colors[0]) > max(8, tolerance) for rgb in colors[1:]):
            reject('corner colors disagree')
        widths = [max(x for x, _ in patch)-min(x for x, _ in patch)+1 for patch in patches]
        heights = [max(y for _, y in patch)-min(y for _, y in patch)+1 for patch in patches]
        for lengths in (widths, heights):
            if max(lengths)-min(lengths) > max(3, max(lengths) / 4):
                reject('corner sizes disagree')
        for patch in patches:
            for x, y in patch:
                ap[x, y] = 0

    # Any other border-connected artwork remains ambiguous; do not discard it.
    if any(ap[x, y] for y in (0, h-1) for x in range(w)) or any(ap[x, y] for x in (0, w-1) for y in range(h)):
        reject('unrecognized foreground touches an edge')
    if outer_only:
        if not patches:
            reject('no separate outer corner background')
        artwork = image.copy()
        alpha = artwork.getchannel('A')
        ap = alpha.load()
        for patch in patches:
            for x, y in patch:
                ap[x, y] = 0
        candidate = colors[0]
    artwork.putalpha(alpha)
    return artwork, candidate + (255,), f'edge RGB {candidate}, tolerance {tolerance}, corner patches {len(patches)}'


def inner_background(image, tolerance):
    """Peel a flat background along the visible silhouette, retaining enclosed detail.

    A narrow exterior band tolerates alpha/compression fringes. Color inference
    uses opaque samples inside that band, separately checked on all four sides.
    This is deliberately a flat-background heuristic, not semantic segmentation.
    """
    alpha = image.getchannel('A')
    bbox = alpha.getbbox()
    def reject(reason):
        raise ValueError(f'ambiguous background layer 2: {reason}; use one layer or --mask')
    if bbox is None:
        reject('no content remains after layer 1')
    left, top, right, bottom = bbox
    if min(right-left, bottom-top) < 12:
        reject('visible shape is too small to sample')
    band = max(2, min(16, min(right-left, bottom-top)//64))
    # Pad before erosion so image edges are treated as exterior too.
    opaque = Image.new('L', (image.width+2*band, image.height+2*band))
    opaque.paste(alpha.point(lambda a: 255 if a == 255 else 0), (band, band))
    def erode(mask):
        for _ in range(band):
            mask = mask.filter(ImageFilter.MinFilter(3))
        return mask
    core = erode(opaque).crop(
        (band, band, band+image.width, band+image.height))
    pixels = image.load()
    sides = [[], [], [], []]
    for y, x0, x1 in row_extents(core):
        if top+(bottom-top)//4 <= y < bottom-(bottom-top)//4:
            sides[0].append(pixels[x0,y][:3])
            sides[1].append(pixels[x1,y][:3])
    for x, y0, y1 in row_extents(core.transpose(Image.Transpose.TRANSPOSE)):
        if left+(right-left)//4 <= x < right-(right-left)//4:
            sides[2].append(pixels[x,y0][:3])
            sides[3].append(pixels[x,y1][:3])
    if any(len(side) < 4 for side in sides):
        reject('not enough opaque boundary samples')
    distance = lambda rgb, target: max(abs(rgb[c]-target[c]) for c in range(3))
    samples = [rgb for side in sides for rgb in side]
    seed = Counter(samples).most_common(1)[0][0]
    inliers = [rgb for rgb in samples if distance(rgb, seed) <= tolerance]
    candidate = tuple(sorted(rgb[c] for rgb in inliers)[len(inliers)//2] for c in range(3))
    if any(sum(distance(rgb,candidate) <= tolerance for rgb in side)/len(side) < .95 for side in sides):
        reject('the visible boundary does not agree on one opaque color')

    w, h = image.size
    original_alpha = alpha.tobytes()
    silhouette = Image.new('L', opaque.size)
    silhouette.paste(alpha.point(lambda a: 255 if a else 0), (band,band))
    core_bytes = erode(silhouette).crop(
        (band,band,band+w,band+h)).tobytes()
    remaining = bytearray(original_alpha)
    seen = bytearray(w*h)
    queue = deque()
    def visit(x,y):
        i = y*w+x
        if seen[i]:
            return
        seen[i] = 1
        rgb = pixels[x,y]
        matching = distance(rgb,candidate) <= tolerance
        fringe = not core_bytes[i] and (rgb[3] < 255 or distance(rgb,candidate) <= max(32,tolerance))
        if not original_alpha[i] or matching or fringe:
            remaining[i] = 0
            queue.append((x,y))
        elif not core_bytes[i]:
            reject('contrasting content touches the outer boundary')
    for x in range(w):
        visit(x,0); visit(x,h-1)
    for y in range(h):
        visit(0,y); visit(w-1,y)
    while queue:
        x,y = queue.popleft()
        for nx,ny in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
            if 0 <= nx < w and 0 <= ny < h:
                visit(nx,ny)
    result = image.copy()
    result.putalpha(Image.frombytes('L', image.size, bytes(remaining)))
    if not result.getchannel('A').getbbox():
        reject('no foreground remains after removing the second background')
    return result, candidate+(255,), f'visible-boundary RGB {candidate}, tolerance {tolerance}'


def layered_foreground(image, layers=1, tolerance=8):
    """Exclude one or two outside-in backgrounds and return the output color."""
    if layers not in (1,2):
        raise ValueError('background-layers must be 1 or 2')
    if image.getchannel('A').getextrema()[0] < 255:
        artwork, background, method = image.copy(), (255,255,255,255), 'existing alpha (transparent background becomes white)'
    else:
        try:
            artwork, method = foreground(image, tolerance=tolerance)
        except ValueError:
            try:
                artwork, background, method = edge_foreground(image, tolerance, outer_only=True)
            except ValueError as exc:
                raise ValueError(f'ambiguous background: {exc}') from exc
        else:
            w,h = image.size
            pixels = image.load()
            border = [pixels[x,y][:3] for y in (0,h-1) for x in range(w)]
            border += [pixels[x,y][:3] for x in (0,w-1) for y in range(1,h-1)]
            background = Counter(border).most_common(1)[0][0]+(255,)
    if layers == 2:
        artwork, background, inner_method = inner_background(artwork,tolerance)
        method += '; layer 2: '+inner_method
    return artwork, background, method


def row_extents(alpha):
    """Extremal occupied pixels per row suffice for distance from any center."""
    data = alpha.tobytes()
    w, h = alpha.size
    extents = []
    for y in range(h):
        row = data[y * w:(y + 1) * w]
        occupied = [x for x, a in enumerate(row) if a]
        if occupied:
            extents.append((y, occupied[0], occupied[-1]))
    return extents


def radius(extents, cx, cy):
    return max(math.hypot(max(abs(left - cx), abs(right + 1 - cx)),
                          max(abs(y - cy), abs(y + 1 - cy)))
               for y, left, right in extents)


def convex_hull(points):
    """Discard interior/collinear points; only hull vertices constrain a circle."""
    points = sorted(set(points))

    def cross(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    def half(sequence):
        result = []
        for point in sequence:
            while len(result) > 1 and cross(result[-2], result[-1], point) <= 0:
                result.pop()
            result.append(point)
        return result

    if len(points) <= 1:
        return points
    return half(points)[:-1] + half(reversed(points))[:-1]


def enclosing_circle(points):
    """Smallest continuous circle, using randomized incremental boundary points.

    A local fixed seed gives repeatable results without changing global RNG state.
    Integer pixel corners keep the orientation/determinant calculations exact.
    """
    points = convex_hull(points)
    random.Random(0).shuffle(points)

    def contains(circle, point):
        cx, cy, r = circle
        return math.hypot(point[0] - cx, point[1] - cy) <= r + 1e-9

    def diameter(a, b):
        return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2,
                math.hypot(a[0] - b[0], a[1] - b[1]) / 2)

    def through_three(a, b, c):
        # Translate to a to avoid cancellation from large absolute coordinates.
        bx, by = b[0] - a[0], b[1] - a[1]
        cx, cy = c[0] - a[0], c[1] - a[1]
        determinant = 2 * (bx * cy - by * cx)
        if determinant == 0:
            return max((diameter(a, b), diameter(a, c), diameter(b, c)), key=lambda circle: circle[2])
        bb, cc = bx * bx + by * by, cx * cx + cy * cy
        x = (cy * bb - by * cc) / determinant
        y = (bx * cc - cx * bb) / determinant
        return a[0] + x, a[1] + y, math.hypot(x, y)

    circle = None
    for i, a in enumerate(points):
        if circle is not None and contains(circle, a):
            continue
        circle = (a[0], a[1], 0)
        for j, b in enumerate(points[:i]):
            if contains(circle, b):
                continue
            circle = diameter(a, b)
            for c in points[:j]:
                if not contains(circle, c):
                    circle = through_three(a, b, c)
    return circle


def layout(artwork, margin, min_size=256):
    if margin < 0:
        raise ValueError('margin must be nonnegative')
    if min_size < 0:
        raise ValueError('min-size must be nonnegative')
    box = artwork.getchannel('A').getbbox()
    if box is None:
        raise ValueError('no foreground content remains')
    cropped = artwork.crop(box)
    w, h = cropped.size
    extents = row_extents(cropped.getchannel('A'))
    points = [(x, yy) for y, left, right in extents
              for x in (left, right + 1) for yy in (y, y + 1)]
    cx, cy, _ = enclosing_circle(points)
    # Recompute against every occupied row, without the solver's float tolerance.
    side = max(min_size, math.ceil(2 * (radius(extents, cx, cy) + margin)))
    while True:
        # No fractional translation/resampling: try the four adjacent placements.
        # Nearest integer placement needs at most two extra canvas pixels.
        candidates = {(x, y) for x in (math.floor(side / 2 - cx), math.ceil(side / 2 - cx))
                      for y in (math.floor(side / 2 - cy), math.ceil(side / 2 - cy))}
        ranked = sorted((radius(extents, side / 2 - x, side / 2 - y), x, y)
                        for x, y in candidates if 0 <= x <= side - w and 0 <= y <= side - h)
        for r, x, y in ranked:
            if r + margin <= side / 2:
                return cropped, side, (x, y)
        side += 1


def render(artwork, background, margin, min_size=256):
    cropped, side, offset = layout(artwork, margin, min_size)
    layer = Image.new('RGBA', (side, side))
    layer.paste(cropped, offset)  # No mask: preserve straight-alpha values exactly.
    output = Image.alpha_composite(Image.new('RGBA', layer.size, background), layer)
    return output, layer, offset


def preview(image):
    # Preview-only antialiasing; never resample the deliverable artwork.
    scale = 4
    mask = Image.new('L', (image.width * scale, image.height * scale))
    ImageDraw.Draw(mask).ellipse((0, 0, mask.width - 1, mask.height - 1), fill=255)
    mask = mask.resize(image.size, Image.Resampling.LANCZOS)
    from PIL import ImageChops
    result = image.copy()
    result.putalpha(ImageChops.multiply(result.getchannel('A'), mask))
    return result


def save_png(image, path, info, overwrite):
    metadata = PngImagePlugin.PngInfo()
    if 'srgb' in info:
        metadata.add(b'sRGB', bytes([info['srgb']]))
    if 'gamma' in info:
        metadata.add(b'gAMA', round(info['gamma'] * 100000).to_bytes(4, 'big'))
    fd, temporary = tempfile.mkstemp(prefix='.circle-img-', suffix='.png', dir=path.parent)
    os.close(fd)
    try:
        image.save(temporary, format='PNG', pnginfo=metadata,
                   icc_profile=info.get('icc_profile'), **({'dpi': info['dpi']} if 'dpi' in info else {}))
        if overwrite:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)  # Atomic refusal if another process created it.
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('-i', '--input', type=Path, nargs='+', action='extend', required=True,
                   metavar='FILE', help='one or more images; accepts shell-expanded wildcards; repeatable')
    p.add_argument('-o', '--output', type=Path, metavar='FILE|DIR',
                   help='PNG file or existing directory (keeps input name; non-PNG suffix becomes .png); '
                        'default: INPUT-circle.png')
    p.add_argument('-b', '--background', default='auto', help='color, transparent, or auto (default): reuse excluded background; transparency becomes white')
    p.add_argument('--background-layers', type=int, choices=(1, 2), default=1,
                   help='backgrounds to exclude, outside inward (default: 1); 2 also excludes an inner flat tile or circle')
    p.add_argument('--margin', type=int, default=0, help='minimum clearance from circle in pixels')
    p.add_argument('--min-size', type=int, default=256,
                   help='minimum output side in pixels; 0 disables the floor (default: 256); no artwork resizing')
    group = p.add_mutually_exclusive_group()
    group.add_argument('--source-background', help='opaque source background color, or edge for flat tiles with matching corners')
    group.add_argument('--mask', type=Path, help='grayscale foreground mask; white retains, black removes')
    p.add_argument('--tolerance', type=int, help='maximum per-channel background difference, 0–255; default 8 for automatic/edge detection, otherwise 0')
    p.add_argument('--preview', action='store_true', help='also save OUTPUT-stem-preview.png with circular crop')
    p.add_argument('-O', '--overwrite', action='store_true', help='replace existing outputs (never the input or mask)')
    p.add_argument('-v', '--verbose', action='store_true')
    return p


def same_path(first, second):
    return (first.resolve() == second.resolve() or
            (first.exists() and second.exists() and os.path.samefile(first, second)))


def plan_outputs(args):
    """Reject destructive or ambiguous batch destinations before any writes."""
    if len(args.input) > 1 and args.output is not None and not args.output.is_dir():
        raise ValueError('multiple inputs require an existing output directory when using -o')
    plans = []
    claimed = []
    protected = args.input + ([args.mask] if args.mask else [])
    for source in args.input:
        output = args.output or source.with_name(source.stem + '-circle.png')
        if args.output is not None and args.output.is_dir():
            filename = source.name if source.suffix.lower() == '.png' else source.stem + '.png'
            output = args.output / filename
        if output.suffix.lower() != '.png':
            raise ValueError(f'{source}: output must have a .png extension for lossless encoding')
        destinations = [output]
        if args.preview:
            destinations.append(output.with_name(output.stem + '-preview.png'))
        for dest in destinations:
            if any(same_path(dest, path) for path in protected):
                raise ValueError(f'{source}: output cannot replace the input or mask: {dest}')
            for previous, owner in claimed:
                # Reject case-only differences too, for portable batch naming.
                if str(dest.resolve()).casefold() == str(previous.resolve()).casefold() or same_path(dest, previous):
                    raise ValueError(f'output collision: {source} and {owner} both target {dest}')
            claimed.append((dest, source))
        plans.append((source, destinations))
    return plans


def process_image(source, destinations, args, background):
    output = destinations[0]
    for dest in destinations:
        if dest.exists() and not args.overwrite:
            raise ValueError(f'output exists: {dest}; use --overwrite to replace it')
        if not dest.parent.is_dir():
            raise ValueError(f'output directory does not exist: {dest.parent}')
    image, info = load_image(source)
    mask = None
    if args.mask:
        with Image.open(args.mask) as supplied:
            if supplied.mode not in ('1', 'L') or getattr(supplied, 'n_frames', 1) != 1:
                raise ValueError('mask must be a single-frame grayscale image')
            mask = ImageOps.exif_transpose(supplied).copy()
    if args.source_background == 'edge':
        artwork, detected_background, method = edge_foreground(image, args.tolerance)
    elif args.source_background is not None or mask is not None:
        artwork, method = foreground(image, args.source_background, args.tolerance, mask)
        detected_background = color(args.source_background) if args.source_background else (255,255,255,255)
    else:
        artwork, detected_background, method = layered_foreground(image, args.background_layers, args.tolerance)
    if background is None:
        background = detected_background
    result, layer, offset = render(artwork, background, args.margin, args.min_size)
    save_png(result, output, info, args.overwrite)
    print(f'Saved {output} ({result.width} × {result.height})')
    if args.preview:
        save_png(preview(result), destinations[1], info, args.overwrite)
        print(f'Saved preview {destinations[1]}')
    if args.verbose:
        print(f'{source}: background: {method}; original: {image.width} × {image.height}; '
              f'placement: {offset}; circular margin: {args.margin}px; minimum side: {args.min_size}px; no resampling')


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.min_size < 0:
            raise ValueError('min-size must be nonnegative')
        if args.tolerance is None:
            args.tolerance = 8 if not args.mask and args.source_background in (None, 'edge') else 0
        if args.margin < 0 or not 0 <= args.tolerance <= 255:
            raise ValueError('margin must be nonnegative; tolerance must be between 0 and 255')
        if args.mask and args.tolerance:
            raise ValueError('--tolerance cannot be combined with --mask')
        if args.background_layers != 1 and (args.source_background or args.mask):
            raise ValueError('--background-layers 2 cannot be combined with --source-background or --mask')
        if args.background.lower() == 'auto':
            background = None
        else:
            background = color(args.background)
        plans = plan_outputs(args)
    except (ValueError, OSError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1
    failed = False
    for source, destinations in plans:
        try:
            process_image(source, destinations, args, background)
        except (ValueError, OSError, Image.DecompressionBombError) as exc:
            print(f'error: {source}: {exc}', file=sys.stderr)
            failed = True
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
