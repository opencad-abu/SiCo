"""Pin placement style: pin orientation and stub direction are separate facts.

Two independent plan facts describe every port:

``escape``
    Direction of the short wire (stub) drawn from the port pin.  It is a
    drawing/routing choice and must never rotate a pin master.

``orientation``
    Placement of the ``basic/ipin|opin|iopin`` schematic pin master.  It is
    derived from the terminal direction and the circuit edge the port faces so
    that the arrow shows the signal direction relative to the circuit: inputs
    and bidirectional pins point into the circuit, outputs point away from it.
    Only the four rotations are produced; mirrors would flip the pin name text.

The bundled ``basic`` masters draw their arrow along ``+x`` in ``R0``.  The
module is the single place that maps a body side to a pin rotation, so the
planner, the preview checks and the native readback all agree.
"""

from __future__ import annotations

# Unit vector pointing away from the circuit body for every port side.
OUTWARD = {
    "left": (-1, 0),
    "right": (1, 0),
    "top": (0, 1),
    "bottom": (0, -1),
}
SIDES = tuple(OUTWARD)

# A port stub leaves the pin toward the circuit body.
INWARD_ESCAPE = {
    "left": "right",
    "right": "left",
    "top": "down",
    "bottom": "up",
}

# Rotation of the pinned masters for a requested arrow direction.  Both
# ``basic/ipin`` and ``basic/opin`` draw their arrow along +x in R0.
ARROW_ORIENTATION = {
    (1, 0): "R0",
    (-1, 0): "R180",
    (0, 1): "R90",
    (0, -1): "R270",
}


def default_side(direction):
    """Side implied by the terminal direction when geometry cannot decide.

    Inputs and bidirectional pins belong on the left edge, outputs on the right
    edge.  This mirrors the documented band policy and is only a fallback for
    ports that sit inside the body footprint.
    """
    return "right" if direction == "output" else "left"


def port_side(xy, body_box, direction):
    """Circuit side a port faces, from its position relative to the body.

    ``body_box`` is the union bounding box of the placed instances, or ``None``
    when the plan has no instance geometry.  The horizontal band wins: a port
    left or right of the whole body belongs to that left/right band even when it
    sits below the body corner, which keeps the pin aligned with its band and
    its stub.  A port inside the body's horizontal span uses the vertical edge
    it crosses; a port inside the footprint (for example between two columns)
    falls back to the direction-implied edge.
    """
    if body_box is None:
        return default_side(direction)
    if xy[0] < body_box[0][0]:
        return "left"
    if xy[0] > body_box[1][0]:
        return "right"
    if xy[1] > body_box[1][1]:
        return "top"
    if xy[1] < body_box[0][1]:
        return "bottom"
    return default_side(direction)


def port_pin_orientation(direction, side):
    """Pin master rotation for one direction/side pair (never a mirror)."""
    if side not in OUTWARD:
        raise ValueError("unknown port side: " + str(side))
    arrow = OUTWARD[side]
    if direction != "output":
        arrow = (-arrow[0], -arrow[1])
    return ARROW_ORIENTATION[arrow]


def planned_pin_orientation(xy, body_box, direction):
    """Pin master rotation for a port at ``xy`` facing ``body_box``."""
    return port_pin_orientation(direction, port_side(xy, body_box, direction))


def placed_pin_orientation(position, body_box, direction):
    """A captured template side wins over the direction/body fallback."""
    side = position.get("side") or port_side(position["xy"], body_box, direction)
    return port_pin_orientation(direction, side)
