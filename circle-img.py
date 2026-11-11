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
    from PIL import Image, ImageColor, ImageDraw, ImageOps, PngImagePlugin
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


def layout(artwork, margin):
    if margin < 0:
        raise ValueError('margin must be nonnegative')
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
    side = max(*artwork.size, math.ceil(2 * (radius(extents, cx, cy) + margin)))
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


def render(artwork, background, margin):
    cropped, side, offset = layout(artwork, margin)
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
    p.add_argument('-b', '--background', default='white', help='color or transparent (default: white)')
    p.add_argument('--margin', type=int, default=0, help='minimum clearance from circle in pixels')
    group = p.add_mutually_exclusive_group()
    group.add_argument('--source-background', help='opaque source background color to remove')
    group.add_argument('--mask', type=Path, help='grayscale foreground mask; white retains, black removes')
    p.add_argument('--tolerance', type=int, default=0, help='maximum per-channel background difference, 0–255')
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
    artwork, method = foreground(image, args.source_background, args.tolerance, mask)
    result, layer, offset = render(artwork, background, args.margin)
    save_png(result, output, info, args.overwrite)
    print(f'Saved {output} ({result.width} × {result.height})')
    if args.preview:
        save_png(preview(result), destinations[1], info, args.overwrite)
        print(f'Saved preview {destinations[1]}')
    if args.verbose:
        print(f'{source}: background: {method}; original: {image.width} × {image.height}; '
              f'placement: {offset}; circular margin: {args.margin}px; no resampling')


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.margin < 0 or not 0 <= args.tolerance <= 255:
            raise ValueError('margin must be nonnegative; tolerance must be between 0 and 255')
        if args.mask and args.tolerance:
            raise ValueError('--tolerance cannot be combined with --mask')
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
