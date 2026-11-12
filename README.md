# circle-img

Prepare images for circular crops without cutting off meaningful content.
Useful for Google Contacts, avatars, and profile pictures. Output is a square
PNG with enough space for a centered, inscribed circular crop.

![Square artwork gains enough padding to fit entirely inside a circular crop.](docs/images/circle-safe.svg)

## Install and run

Requires Python 3.11+ and Pillow 13. From this folder:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
./circle-img.py -i logo.png
```

```bash
./circle-img.py -i logo.png -o avatar.png --margin 12 --preview
./circle-img.py -i logo.png -o exports/  # existing folder: exports/logo.png
./circle-img.py -i ~/Desktop/*.png -o ~/Desktop/circle -O  # folder must exist
./circle-img.py -i first.png second.png --margin 12
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
| `-i`, `--input FILE [FILE ...]` | One or more raster images; required. Accepts shell-expanded wildcards and repeated `-i`. |
| `-o`, `--output FILE\|DIR` | PNG file or existing directory. A directory keeps the input filename for PNG inputs; other extensions become `.png`. Default: `<input-stem>-circle.png` beside each input. Multiple inputs require an existing directory when `-o` is supplied. Parent must exist. |
| `-b`, `--background COLOR` | Destination background: `white` by default, a Pillow color name, hex color, or `transparent`. |
| `--margin N` | Nonnegative integer minimum clearance from content to the **circle**, in output pixels; default 0. |
| `--source-background COLOR` | Explicit opaque source background color; overrides alpha-based detection. |
| `--tolerance N` | Maximum difference in any RGB channel from source background, 0–255; default 0. Applies to explicit or inferred source color. Cannot be used with a mask. |
| `--mask FILE` | Single-frame grayscale mask matching oriented input dimensions: white retains, black removes, gray reduces original alpha proportionally. The same mask is applied to every input. Exclusive with source background. |
| `--preview` | Also save `<output-stem>-preview.png`, a circular-crop preview with transparent corners. |
| `-O`, `--overwrite` | Replace existing output/preview files; input and mask are always protected. |
| `-v`, `--verbose` | Report detection method, dimensions, placement, and margin. |
| `-h`, `--help` | Show CLI help. |

## Multiple images

The shell expands unquoted wildcards before running the script:

```bash
mkdir -p ~/Desktop/circle
./circle-img.py -i ~/Desktop/*.png -o ~/Desktop/circle -O
```

Quote individual filenames containing spaces, but do not quote the wildcard.
The script does not expand literal wildcard patterns. You can also list files
explicitly or repeat `-i`. Inputs are processed in the supplied order.

With multiple inputs, `-o` must be an existing directory or omitted. Without
`-o`, each result is saved beside its input with the `-circle.png` suffix.
All processing options apply to every input; a supplied mask must match each
image's oriented dimensions. Errors identify the affected file, processing
continues with the remaining files, and the command exits with status 1 if any
image failed (0 when all succeed). Successful outputs are retained.

Before processing starts, conflicting destination paths abort the whole batch,
even with `-O`. This includes two inputs mapping to the same PNG name, duplicate
inputs, preview/output collisions, and names differing only by case. No output
may replace any input or the mask. Keep output files in a separate directory
when using wildcards so later runs do not include previous results as inputs.

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

The script applies EXIF orientation and finds the smallest enclosing circle of
the foreground's **pixel corners**. It centers that circle on a square canvas,
allowing asymmetric artwork to shift away from bounding-box centering for a
tighter fit. This is geometric fitting, not centering by visual mass.

All foreground pixel areas must fit within the circle with the requested margin.
The artwork itself is never resized. Placement uses whole pixels: the script
checks the four placements surrounding the ideal position and enlarges the
canvas if needed. This rounding can add up to two pixels to the ideal canvas
side; it does not search every possible whole-pixel placement. The canvas is
never smaller than the larger original dimension. Existing background padding
can be redistributed.

For a fixed center, required side length is at least `ceil(2 × (radius + margin))`;
integer placement may require extra pixels. A filled square needs more
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
With `-o exports/`, an existing directory receives `logo.png` for input `logo.png`,
or `photo.png` for input `photo.jpg`. PNG filename capitalization is preserved.
The optional preview is saved beside the result as `<output-stem>-preview.png`.
Directories are not created automatically; a destination that is not an existing
directory is treated as an explicit PNG filename. Selecting the input's own
directory for a PNG input is rejected because it would replace the source,
even with `-O`.

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
metadata, file protection, CLI errors, and preview behavior.

`tests/test_preservation.py` adds asymmetric multicolor artwork, mixed alpha,
holes and disconnected marks, independently known tight canvas sizes, and
end-to-end checks of saved PNG pixels, radial clearance, custom backgrounds,
and ICC profiles. Reusable inputs and explicit boundary cases live in
`tests/fixture_data.py`. These checks allow artwork placement to change while
requiring its pixels to remain intact. Background-confidence cases cover below,
at, and above the documented 95% threshold.

`tests/test_optimized_geometry.py` checks a synthetic shield in four orientations,
saved output, independently known enclosing circles, and fixture reproducibility.
It also compares 500 repeatable point sets against an exhaustive test-only solver
that checks every possible one-, two-, or three-point support circle using exact
rational arithmetic. Known geometric solutions independently validate that solver.
These checks run with the complete suite, including in GitHub Actions.
The shield's broad shoulders and tapered base exercise tighter fitting without
using company artwork. Rebuild it with:

```bash
.venv/bin/python tests/fixtures/generate_shield.py
```

`tests/test_circular_foreground.py` covers circular foreground content: tight
fitting, radial margins, off-center input on a larger canvas, faint edge pixels,
and saved PNG preservation. Its multicolor disk fixture has an independently
verified 60-pixel enclosing radius. Rebuild it with:

```bash
.venv/bin/python tests/fixtures/generate_disk.py
```

Regression checks should be shown to fail under a narrow temporary fault before
being accepted; restore the implementation and rerun the complete suite afterward.

For visual verification with the AI-generated blue ribbon fixture under
`tests/fixtures/` (also used by the CI direct-execution check):

```bash
./circle-img.py \
  -i tests/fixtures/blue-ribbons.png --margin 12 --preview -v
```

Inspect both the square output and circular preview. Generated circle images
and the local virtual environment are ignored by `.gitignore`.
