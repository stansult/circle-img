"""Slow, independent circle oracle for small integer point sets in tests only."""
from fractions import Fraction
from itertools import combinations
import math


def exhaustive_circle(points):
    """Enumerate all 1/2/3-point support circles and select the smallest enclosure.

    Rational arithmetic makes candidate construction and containment exact.
    This deliberately uses neither the product's hull nor incremental solver.
    """
    candidates = [(Fraction(x), Fraction(y), Fraction(0)) for x, y in points]
    for a, b in combinations(points, 2):
        x, y = Fraction(a[0] + b[0], 2), Fraction(a[1] + b[1], 2)
        candidates.append((x, y, (x - a[0])**2 + (y - a[1])**2))

    for a, b, c in combinations(points, 3):
        # Solve the two perpendicular-bisector equations in absolute coordinates.
        aa, bb, cc = (px*px + py*py for px, py in (a, b, c))
        u, v = 2 * (b[0] - a[0]), 2 * (b[1] - a[1])
        s, t = 2 * (c[0] - a[0]), 2 * (c[1] - a[1])
        determinant = u * t - v * s
        if determinant == 0:
            continue
        x = Fraction((bb - aa) * t - v * (cc - aa), determinant)
        y = Fraction(u * (cc - aa) - (bb - aa) * s, determinant)
        candidates.append((x, y, (x - a[0])**2 + (y - a[1])**2))

    for x, y, squared_radius in sorted(candidates, key=lambda circle: circle[2]):
        if all((x - px)**2 + (y - py)**2 <= squared_radius for px, py in points):
            return float(x), float(y), math.sqrt(squared_radius)
    raise ValueError('expected a nonempty integer point set')
