#!/usr/bin/env python3
"""Prepare raster artwork for circular crops without resampling it."""
import argparse
from collections import Counter, deque
import math
import os
from pathlib import Path
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


def layout(artwork, margin):
    if margin < 0:
        raise ValueError('margin must be nonnegative')
    box = artwork.getchannel('A').getbbox()
    if box is None:
        raise ValueError('no foreground content remains')
    cropped = artwork.crop(box)
    w, h = cropped.size
    extents = row_extents(cropped.getchannel('A'))
    side = max(*artwork.size, math.ceil(2 * (radius(extents, w / 2, h / 2) + margin)))
    while True:
        x, y = (side - w) // 2, (side - h) // 2
        if radius(extents, side / 2 - x, side / 2 - y) + margin <= side / 2:
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
    p.add_argument('-i', '--input', type=Path, required=True)
    p.add_argument('-o', '--output', type=Path, help='default: INPUT-circle.png')
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


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    try:
        if args.margin < 0 or not 0 <= args.tolerance <= 255:
            raise ValueError('margin must be nonnegative; tolerance must be between 0 and 255')
        if args.mask and args.tolerance:
            raise ValueError('--tolerance cannot be combined with --mask')
        background = color(args.background)
        output = args.output or args.input.with_name(args.input.stem + '-circle.png')
        if output.suffix.lower() != '.png':
            raise ValueError('output must have a .png extension for lossless encoding')
        destinations = [output]
        if args.preview:
            destinations.append(output.with_name(output.stem + '-preview.png'))
        protected = [args.input] + ([args.mask] if args.mask else [])
        for dest in destinations:
            if any(dest.resolve() == path.resolve() or
                   (dest.exists() and path.exists() and os.path.samefile(dest, path)) for path in protected):
                raise ValueError('output cannot replace the input or mask')
            if dest.exists() and not args.overwrite:
                raise ValueError(f'output exists: {dest}; use --overwrite to replace it')
            if not dest.parent.is_dir():
                raise ValueError(f'output directory does not exist: {dest.parent}')
        image, info = load_image(args.input)
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
            print(f'Background: {method}; original: {image.width} × {image.height}; '
                  f'placement: {offset}; circular margin: {args.margin}px; no resampling')
        return 0
    except (ValueError, OSError, Image.DecompressionBombError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
