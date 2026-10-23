"""Family symbol layout helpers shared by planning and native geometry."""

from .template_symbol_contacts import GRID


def bootstrap_rows(rows):
    """Make a left/right-only bootstrap layout for the native rectangle builder."""
    left = [row for row in rows if not row[7]]
    right = [row for row in rows if row[7]]
    height = max(1.0, 0.25 * (max(len(left), len(right)) + 2))

    def place(items, x, right_side):
        return [
            [
                row[0],
                row[1],
                row[2],
                row[3],
                row[4],
                x,
                round(-height + 0.125 + index * 0.25, 8),
                right_side,
            ]
            for index, row in enumerate(items)
        ]

    return place(left, 0.125, False) + place(right, 0.875, True), height


def drawn_selection_box(body, stubs, pins, annotations):
    """Match the native builder's body/pin/annotation selection extent."""
    points = []
    for _, geometry, _ in body + stubs:
        points.extend(geometry if geometry and isinstance(geometry[0], list) else [geometry])
    points.extend(pin["origin"] for pin in pins)
    for annotation in annotations:
        points.extend(annotation["bbox"])
    if not points:
        return None
    return [
        [round(min(point[0] for point in points), 8), round(min(point[1] for point in points), 8)],
        [round(max(point[0] for point in points), 8), round(max(point[1] for point in points), 8)],
    ]


def center_pg_slots(family, anchors, sides):
    """Place power/ground slots on the body top/bottom center lanes."""
    body_box = family["body_box"]
    center = (body_box[0][0] + body_box[1][0]) / 2
    groups = {"top": [], "bottom": []}
    for slot in family["slots"]:
        role = slot.get("layout_role")
        if role in {"power", "ground"}:
            groups["top" if role == "power" else "bottom"].append(slot)
    for side, slots in groups.items():
        for index, slot in enumerate(slots):
            x = round((center + (index - (len(slots) - 1) / 2) * 0.375) / GRID) * GRID
            anchors[slot["name"]] = [x, body_box[1][1] if side == "top" else body_box[0][1]]
            sides[slot["name"]] = side
