# circle-img

Prepare images for circular crops without cutting off meaningful content.
Useful for Google Contacts, avatars, and profile pictures. Output is a square
PNG with enough space for a centered, inscribed circular crop.

## Install and run

Requires Python 3.11+ and Pillow 13. From this folder:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
./circle-img.py -i logo.png
```

```bash
./circle-img.py -i logo.png -o avatar.png --margin 12 --preview
./circle-img.py -i logo.png --background '#f5f5f5'
./circle-img.py -i logo.png --background transparent
./circle-img.py -i logo.jpg --source-background white --tolerance 10
./circle-img.py -i photo.png --mask foreground-mask.png
```

Run `./circle-img.py` directly; no environment activation is needed. If the
current Python lacks Pillow, the script automatically uses `.venv/bin/python`
beside the script, even when invoked from another directory or through a symlink.
The setup above is only needed once; nothing is installed automatically.

## Options

| Option | Behavior |
| --- | --- |
| `-i`, `--input FILE` | One raster image; required. |
| `-o`, `--output FILE` | PNG destination, default `<input-stem>-circle.png` beside input. Parent must exist. |
| `-b`, `--background COLOR` | Destination background: `white` by default, a Pillow color name, hex color, or `transparent`. |
| `--margin N` | Nonnegative integer minimum clearance from content to the **circle**, in output pixels; default 0. |
| `--source-background COLOR` | Explicit opaque source background color; overrides alpha-based detection. |
| `--tolerance N` | Maximum difference in any RGB channel from source background, 0–255; default 0. Applies to explicit or inferred source color. Cannot be used with a mask. |
| `--mask FILE` | Single-frame grayscale mask matching oriented input dimensions: white retains, black removes, gray reduces original alpha proportionally. Exclusive with source background. |
| `--preview` | Also save `<output-stem>-preview.png`, a circular-crop preview with transparent corners. |
| `-O`, `--overwrite` | Replace existing output/preview files; input and mask are always protected. |
| `-v`, `--verbose` | Report detection method, dimensions, placement, and margin. |
| `-h`, `--help` | Show CLI help. |

## Content and background rules

1. An explicit mask determines foreground coverage, multiplied by existing alpha.
2. Otherwise, when any input pixel has alpha below 255, existing alpha defines
   foreground, unless `--source-background` was supplied. Every nonzero alpha
   counts, including faint edges and shadows. Transparent gaps and holes receive
   the new background automatically. This assumes alpha already separates the
   intended artwork; a mixed transparent/opaque backdrop may need a mask.
3. For fully opaque input, automatic detection selects the most common RGB color
   on the outer border. At least 95% of border pixels must match it within the
   tolerance. Otherwise processing stops and requests an explicit color or mask.
4. Starting at matching border pixels, four-connected flood fill removes only
   matching pixels. Enclosed areas are preserved, even if they share the source
   background color. Use a mask if enclosed areas should also be removed.

Automatic detection is a heuristic for simple flat backgrounds, not semantic
segmentation. It can mistake artwork touching the border for background.
Explicit colors are interpreted in the input's RGB color space. Tolerance is a
per-channel RGB difference, not a perceptual color distance. The script does not
recover antialiased edges blended into an opaque background: halos may remain,
and increasing tolerance can remove foreground detail. Complex backgrounds,
photos, and gradients generally need a prepared mask. Empty foreground is an error.

## Geometry and quality

The script applies EXIF orientation, locates foreground bounds, centers them on a
square canvas (within half a pixel), and measures the farthest **pixel corner**
from that canvas's center. All foreground pixel areas must fit within the circle
with the requested margin. Integer placement is checked before saving. The
canvas is never smaller than the larger original dimension. Existing background
padding can be redistributed to center the content; the artwork itself is never
resized. Centering uses the bounding box, not visual mass or an optimized minimum
enclosing circle.

For a fixed center, required side length is at least `ceil(2 × (radius + margin))`;
integer placement may require an additional pixel. A filled square needs more
padding than a circular silhouette. Margin zero provides geometric containment;
use a positive margin for extra visual space or crop-rendering differences.

Output uses lossless PNG. Transparent output preserves foreground RGBA values;
colored output deliberately alpha-composites edge pixels over the new color.
ICC profiles, PNG sRGB/gamma markers, and resolution metadata are retained when
present. EXIF is not copied after orientation. Standard 8-bit RGB, RGBA,
grayscale, and palette images are supported. Multi-frame images, 16-bit PNG, and
unsupported modes such as CMYK are rejected rather than silently converted with
precision or color loss. JPEG decoding cannot recover detail already lost in the
source. This is not a universal high-bit-depth archival conversion tool.

Only the optional preview applies an antialiased circular mask. Upload the square
output and use the full centered crop. The script cannot control a service's
resizing, compression, transparency handling, or a manually repositioned crop.

Writes use a temporary file beside each destination and atomic publication.
Existing files require `-O` or `--overwrite`; inputs and masks cannot be destinations,
even through symlinks or hard links. Output and preview are separate writes; if
preview writing fails after output succeeds, the output remains available.

## Development and verification

Run the complete suite:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

GitHub Actions runs this suite on pushes to `main` and pull requests, using
Python 3.11 and 3.15 on Linux and Python 3.15 on macOS. Each job creates the
documented `.venv` and also checks direct CLI execution with the sample image.
The [Tests workflow](.github/workflows/tests.yml) can also be started manually.

Tests use generated, explicitly checked fixtures and independently check every
foreground pixel corner against the output circle. Coverage includes alpha and
pixel preservation, circle geometry, margins, flood-fill boundaries, masks,
metadata, file protection, CLI errors, and preview behavior. Regression checks
should be shown to fail under a narrow temporary fault before being accepted;
restore the implementation and rerun the complete suite afterward.

For visual verification with the example image in this folder:

```bash
./circle-img.py \
  -i ticketnetworkdotcom-mark-fullcolor-10979@4x.png --margin 12 --preview -v
```

Inspect both the square output and circular preview. Generated circle images
and the local virtual environment are ignored by `.gitignore`.
