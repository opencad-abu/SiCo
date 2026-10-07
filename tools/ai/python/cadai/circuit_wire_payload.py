"""Canonical native draw points without changing planned electrical geometry."""

from .circuit_geometry_schema import EPS


def draw_points(points):
    """Remove duplicate points and monotonic collinear interior vertices only.

    schCreateWire may extend an existing wire but return nil when a redundant
    interior vertex revisits its newly merged segment. A single straight run
    avoids that native ambiguity. Reversals, corners and endpoints are retained;
    the covered grid edges, junction locations and label coordinates stay equal.
    """
    result = []
    for point in points:
        if result and all(abs(point[k] - result[-1][k]) <= EPS for k in (0, 1)):
            continue
        result.append(list(point))
        while len(result) >= 3:
            a, b, c = result[-3:]
            straight = any(
                abs(a[k] - b[k]) <= EPS and abs(b[k] - c[k]) <= EPS
                and (b[1-k] - a[1-k]) * (c[1-k] - b[1-k]) > 0
                for k in (0, 1)
            )
            if not straight:
                break
            result.pop(-2)
    return result
