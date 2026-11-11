"""Synthetic, non-brand artwork and deliberate cases for preservation tests."""

# A ring with a hole, disconnected asymmetric marks, and faint edge pixels.
ARTWORK_ROWS = (
    '.R.........',
    '...GGG.....',
    '...G.G.....',
    '...GGG.....',
    '.......B...',
    '.......b...',
    '...........',
    '..Y........',
    '.........f.',
)
ARTWORK_COLORS = {
    'R': (231, 17, 43, 255),
    'G': (23, 180, 71, 255),
    'B': (25, 65, 219, 255),
    'b': (80, 160, 240, 128),
    'Y': (243, 194, 20, 64),
    'f': (150, 35, 210, 1),
}
# These even-sized symmetric cases have independently known tight solutions:
# 6x8 rectangle: diagonal 10. 8x8 square: diagonal sqrt(128), rounded to 12.
# With margin 3, the latter needs side 18. Large existing canvas stays at 40.
PADDING_CASES = (
    ('pythagorean rectangle', (6, 8), (0, 0, 5, 7), 0, 10),
    ('filled square', (8, 8), (0, 0, 7, 7), 0, 12),
    ('square with radial margin', (8, 8), (0, 0, 7, 7), 3, 18),
    ('existing large canvas', (40, 32), (2, 3, 7, 10), 0, 40),
)
# An 11x11 image has exactly 40 unique border pixels.
BORDER_CASES = (
    ('below threshold', 3, False),   # 37/40 = 92.5%
    ('at threshold', 2, True),       # 38/40 = 95%
    ('above threshold', 1, True),    # 39/40 = 97.5%
)
