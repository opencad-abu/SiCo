"""Geometry consumer contract; producers retain their own richer source formats."""

from .circuit_spec_schema import (
    ID,
    NET_NAME,
    REF,
    CircuitSpecError,
    array,
    enum,
    mapping,
    obj,
)

VERSION = "cad.circuit.geometry.v1"
PLAN_VERSION = "cad.circuit.geometry-plan.v1"
NUMBER = {"type": "number", "minimum": -1000000, "maximum": 1000000}
POINT = array(NUMBER, 2, 2)
BOX = array(POINT, 2, 2)
ORIENTATION = enum("R0", "R90", "R180", "R270", "MX", "MY", "MXR90", "MYR90")
DIRECTION = enum("left", "right", "up", "down")
ANCHOR = obj({"id": ID, "xy": POINT, "escape": DIRECTION})
TERMINAL = obj({"name": NET_NAME, "anchors": array(ANCHOR, 8, 1)})
INSTANCE = obj(
    {
        "instance": ID,
        "master_revision": REF,
        "parameters_digest": REF,
        "occupied_bbox": BOX,
        "annotation_bbox": BOX,
        "terminals": array(TERMINAL, 64),
    },
    ("instance", "master_revision", "parameters_digest", "occupied_bbox", "terminals"),
)
GEOMETRY = obj(
    {
        "schema": enum(VERSION),
        "project_ref": REF,
        "binding_snapshot": REF,
        "binding_digest": REF,
        "source_ref": REF,
        "evidence_kind": enum("session_capture", "test_fixture"),
        "coordinate_system": enum("master_local_schematic_uu"),
        "instances": array(INSTANCE, 64, 1),
    }
)
LAYOUT = obj(
    {
        "instances": mapping(obj({"xy": POINT, "orientation": ORIENTATION})),
        "ports": mapping(
            obj(
                {
                    "xy": POINT,
                    "escape": DIRECTION,
                    "role": enum("power", "ground", "signal"),
                    "placement_source": enum("template", "default"),
                    "side": enum("left", "right", "top", "bottom"),
                },
                ("xy", "escape"),
            ),
            NET_NAME,
            128,
        ),
        "anchor_choices": mapping(mapping(ID, NET_NAME)),
        "grid": {"type": "number", "minimum": 0.000001, "maximum": 1},
        "stub_length": {"type": "number", "minimum": 0.000001, "maximum": 10},
        "clearance": {"type": "number", "minimum": 0, "maximum": 10},
        "pin_links": array(array(obj({"instance": ID, "terminal": NET_NAME}), 2, 2), 128),
        "routing": obj(
            {
                "mode": enum("stub", "end_to_end"),
                "engine": enum("cadence_route", "planner", "template"),
                "fallback": enum("stub", "reject"),
                "max_bends": {"type": "integer", "minimum": 1, "maximum": 8},
                "pg": enum("stub", "route"),
                "ports": enum("stub", "route"),
                "max_detour": {"type": "number", "minimum": 1, "maximum": 20},
                "decision": obj(
                    {
                        "source": enum("user", "timeout", "explicit"),
                        "chosen": enum("stub", "end_to_end"),
                        "timeout_s": {"type": "integer", "minimum": 1, "maximum": 600},
                    },
                    ("source",),
                ),
            },
            ("mode",),
        ),
    },
    ("instances", "ports", "anchor_choices", "grid", "stub_length", "clearance"),
)

MATRICES = {
    "R0": (1, 0, 0, 1),
    "R90": (0, -1, 1, 0),
    "R180": (-1, 0, 0, -1),
    "R270": (0, 1, -1, 0),
    "MX": (1, 0, 0, -1),
    "MY": (-1, 0, 0, 1),
    "MXR90": (0, 1, 1, 0),
    "MYR90": (0, -1, -1, 0),
}
VECTORS = {"left": (-1, 0), "right": (1, 0), "up": (0, 1), "down": (0, -1)}
EPS = 1e-9


def transform(point, orientation, origin=(0, 0)):
    a, b, c, d = MATRICES[orientation]
    x, y = point
    return [origin[0] + a * x + b * y, origin[1] + c * x + d * y]


def bounds(points):
    return [
        [min(p[k] for p in points) for k in (0, 1)],
        [max(p[k] for p in points) for k in (0, 1)],
    ]


def transform_box(box, orientation, origin):
    if any(box[0][k] >= box[1][k] for k in (0, 1)):
        raise CircuitSpecError("occupied_bbox must have positive width and height")
    corners = [[x, y] for x in (box[0][0], box[1][0]) for y in (box[0][1], box[1][1])]
    return bounds([transform(p, orientation, origin) for p in corners])


def on_grid(point, grid):
    return all(abs(v - round(v / grid) * grid) <= grid * 1e-7 for v in point)


def inside(point, box):
    return all(box[0][k] - EPS <= point[k] <= box[1][k] + EPS for k in (0, 1))


def overlap(first, second, clearance=0):
    return all(
        first[0][k] <= second[1][k] + clearance + EPS
        and second[0][k] <= first[1][k] + clearance + EPS
        for k in (0, 1)
    )
