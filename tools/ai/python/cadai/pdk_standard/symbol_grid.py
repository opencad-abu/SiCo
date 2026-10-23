"""Symbol connection grid: decimal lattice capture and exact on-grid checks.

The grid is the captured/confirmed connection-and-placement reference of one
symbol. DBU, manufacturing grids and editor settings are never its source, and
coordinates are compared in decimal semantics so a confirmed decimal step
admits its true decimal multiples without binary-float rounding.
"""

from decimal import Decimal, InvalidOperation
from fractions import Fraction
from math import gcd

from .jsonio import fail


def decimal(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal, str)):
        fail('Grid coordinate must be numeric')
    try:
        return Decimal(str(value))
    except InvalidOperation:
        fail('Grid coordinate must be numeric')


def _lcm(left, right):
    return left * right // gcd(left, right)


def step(values):
    """Minimal positive decimal step dividing every observed coordinate."""
    fractions = [Fraction(decimal(v)) for v in values]
    fractions = [f for f in fractions if f != 0]
    if not fractions:
        return None
    denominator = 1
    for fraction in fractions:
        denominator = _lcm(denominator, fraction.denominator)
    divisor = 0
    for fraction in fractions:
        divisor = gcd(divisor, abs(fraction.numerator) * (denominator // fraction.denominator))
    return None if divisor == 0 else Fraction(divisor, denominator)


def lattice(points):
    """Per-axis minimal step of observed coordinates, or None without spread."""
    if not points or any(len(p) != 2 for p in points):
        fail('Grid lattice requires two-dimensional points')
    steps = []
    for k in (0, 1):
        values = {decimal(p[k]) for p in points}
        if len(values) < 2:
            return None
        steps.append(step(values))
    return [float(s) for s in steps]


def off_grid(value, grid):
    """Confirmed anchors outside the declared decimal grid, in document order."""
    steps = [decimal(v) for v in grid['step']]
    origin = [decimal(v) for v in grid['origin']]
    if any(s <= 0 for s in steps):
        fail('Symbol grid must be positive')
    offenders = []
    for name, row in (value.get('terminals') or {}).items():
        anchors = row.get('anchors')
        if not isinstance(anchors, list):
            continue
        for anchor in anchors:
            point = [decimal(v) for v in anchor.get('xy') or []]
            if len(point) != 2 or any((point[k] - origin[k]) % steps[k] != 0 for k in (0, 1)):
                offenders.append({'terminal': name, 'id': anchor.get('id'), 'xy': anchor.get('xy')})
    return offenders
