"""Integer template coordinates: source DBU offsets relative to one anchor.

Schema v2 normalizes every placement coordinate into integers scaled by the
captured ``dbu_per_uu`` and expressed relative to a single documented anchor.
The values record relative position relations only: the absolute source
coordinates stay in the private raw capture, target placement must recompute
with target master geometry, and no coordinate is a binding or sizing claim.
Symbol drawing geometry keeps the captured user units until its own migration.
"""

from __future__ import annotations

import math

from .template_schema import COORDINATE_SPACE, LEGACY_COORDINATE_SPACE

POINT_KEYS = ("xy", "origin", "beginPt", "endPt")
BOX_KEYS = ("bbox", "bBox", "ellipseBBox")
POINT_LIST_KEYS = ("points",)
LENGTH_KEYS = ("width", "height", "rowSpacing", "columnSpacing")
RESIDUAL_EPSILON = 1e-9


def is_point(value):
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(type(n) in (int, float) and math.isfinite(n) for n in value)
    )


def valid_box(value):
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(is_point(p) for p in value)
        and all(value[0][i] <= value[1][i] for i in (0, 1))
    )


def union_box(boxes):
    boxes = [box for box in boxes if valid_box(box)]
    if not boxes:
        return None
    return [
        [min(b[0][i] for b in boxes) for i in (0, 1)],
        [max(b[1][i] for b in boxes) for i in (0, 1)],
    ]


def asset_space(asset):
    """Coordinate space marker of a stored asset record."""
    if isinstance(asset, dict) and asset.get("coordinate_space"):
        return asset["coordinate_space"]
    return None if asset is None else LEGACY_COORDINATE_SPACE


def is_integer_space(asset):
    return asset_space(asset) == COORDINATE_SPACE


def instance_position(instance):
    """Agent-facing position of one placement/instance record."""
    for key in ("relative_xy", "xy"):
        value = instance.get(key)
        if is_point(value):
            return list(value)
    return None


class Coordinates:
    """Scale capture coordinates to integers offset by one anchor in DBU."""

    def __init__(self, dbu, anchor=None, gaps=None):
        self.dbu = dbu if type(dbu) in (int, float) and math.isfinite(dbu) and dbu > 0 else None
        self.anchor = [int(n) for n in anchor] if anchor else [0, 0]
        self.values = 0
        self.rounded_values = 0
        self.max_residual = 0.0
        self.gaps = gaps if gaps is not None else []

    @property
    def scale(self):
        return self.dbu if self.dbu is not None else 1

    def scaled(self, value):
        scaled = value * self.scale
        rounded = int(math.floor(scaled + 0.5))
        residual = abs(scaled - rounded)
        self.values += 1
        if residual > RESIDUAL_EPSILON:
            self.rounded_values += 1
            self.max_residual = max(self.max_residual, residual)
        return rounded

    def point(self, value):
        if not is_point(value):
            return None
        return [self.scaled(value[i]) - self.anchor[i] for i in (0, 1)]

    def box(self, value):
        if not isinstance(value, list) or len(value) != 2 or not all(is_point(p) for p in value):
            return None
        return [self.point(p) for p in value]

    def points(self, value):
        if not isinstance(value, list) or not all(is_point(p) for p in value):
            return None
        return [self.point(p) for p in value]

    def length(self, value):
        return self.scaled(value)

    def convert(self, value, key=None):
        if value is None:
            return None
        if key in POINT_KEYS:
            return self.point(value)
        if key in BOX_KEYS:
            return self.box(value)
        if key in POINT_LIST_KEYS:
            return self.points(value)
        if key in LENGTH_KEYS:
            return self.length(value)
        if isinstance(value, dict):
            return {k: self.convert(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [self.convert(v, None) for v in value]
        return value

    def report(self):
        return {
            "dbu_per_uu": self.dbu,
            "scaled_values": self.values,
            "rounded_values": self.rounded_values,
            "max_residual_dbu": round(self.max_residual, 6),
        }

    def rounded_gap(self):
        if not self.rounded_values:
            return None
        return {
            "code": "coordinate_rounded_to_dbu",
            "values": self.rounded_values,
            "max_residual_dbu": round(self.max_residual, 6),
        }


def instance_anchor(instance_rows, dbu):
    """Component-wise minimum of the captured instance positions in DBU."""
    coords = Coordinates(dbu)
    points = [
        coords.point(row.get("xy"))
        for row in instance_rows
        if isinstance(row, dict) and is_point(row.get("xy"))
    ]
    if not points:
        return [0, 0]
    return [min(p[i] for p in points) for i in (0, 1)]


def asset_anchor(rows, dbu):
    """Anchor and its provenance kind for one captured view's rows.

    Views with placed instances anchor on their component-wise minimum, exactly
    like schematic and layout placement. Flat symbol views keep their own origin,
    so captured symbol geometry stays relative to the symbol, not to a shifted
    synthetic anchor.
    """
    coords = Coordinates(dbu)
    points = [
        coords.point(row.get("xy"))
        for row in rows
        if isinstance(row, dict) and row.get("kind") == "instance" and is_point(row.get("xy"))
    ]
    if not points:
        return {"kind": "view_origin", "source_xy_dbu": [0, 0]}
    return {
        "kind": "instance_min",
        "source_xy_dbu": [min(p[i] for p in points) for i in (0, 1)],
    }
