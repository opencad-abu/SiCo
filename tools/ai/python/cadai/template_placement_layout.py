"""Relative placement algorithm with complete target and residual coverage."""

import math

from .circuit_geometry_schema import transform_box
from .circuit_spec_schema import CircuitSpecError
from .template_coords import is_point
from .template_placement_tracks import place_tracks
from .template_port_placement import plan_ports
from .template_relations import MIRROR_VERTICAL

PLACEMENT_SCHEMA = "cad.template.placement.v1"
MAX_INSTANCES = 64
LOOSE_MIN_GAP = 1.0


def _relation_devices(relation_view, device_map):
    """Relations label rows/pairs with source names; map them to target instances."""
    name_to_device = {
        row.get("name"): row.get("device")
        for row in relation_view.get("instances") or []
        if row.get("name")
    }

    def target(member):
        device = name_to_device.get(member, member)
        return device_map.get(device)

    return name_to_device, target


def _body(geometry_row, orientation):
    # Reserve source text space for placement, while routing still uses the body.
    box = transform_box(geometry_row.get("annotation_bbox", geometry_row["occupied_bbox"]),
                        orientation, [0, 0])
    return {
        "left": box[0][0],
        "bottom": box[0][1],
        "right": box[1][0],
        "top": box[1][1],
        "width": box[1][0] - box[0][0],
        "height": box[1][1] - box[0][1],
    }


def _snap(value, grid):
    return round(value / grid) * grid


def plan_layout(spec, bindings, geometry, relation_view, device_map, options=None,
                *, port_map=None, terminal_map=None):
    """Return ``(layout, report)`` for the mapped target instances."""
    options = dict(options or {})
    scale = relation_view.get("dbu_per_uu")
    if relation_view.get("unit") == "source_dbu" and not (
        type(scale) in (int, float) and math.isfinite(scale) and scale > 0
    ):
        raise CircuitSpecError("template coordinate scale missing; source DBU is not user units")
    grid = options.get("grid", 0.0625)
    clearance = options.get("clearance", 0.25)
    stub_length = options.get("stub_length", 0.5)
    instances = {row["id"]: row for row in spec["instances"]}
    if len(instances) > MAX_INSTANCES:
        raise CircuitSpecError("placement supports at most 64 instances")
    geometry_rows = {row["instance"]: row for row in geometry["instances"]}
    masters = {row["id"]: row for row in bindings["masters"]}
    for key in instances:
        if key not in geometry_rows:
            raise CircuitSpecError("geometry is missing instance " + key)
        if instances[key]["master"] not in masters:
            raise CircuitSpecError("bindings are missing master for " + key)

    sources = {r["device"] for r in relation_view.get("instances", [])}
    sources.update(g["device"] for g in relation_view.get("gaps", [])
                   if g["code"] == "placement_incomplete" and g.get("device"))
    if set(device_map) - sources or set(device_map.values()) - set(instances):
        raise CircuitSpecError("placement device_map references unknown source or target")
    if len(set(device_map.values())) != len(device_map):
        raise CircuitSpecError("placement device_map must be one-to-one")
    if terminal_map is not None:
        if set(terminal_map) - set(device_map):
            raise CircuitSpecError("placement terminal_map references unmapped source")
        for source, names in terminal_map.items():
            target_names = {t["name"] for t in geometry_rows[device_map[source]]["terminals"]}
            if set(names.values()) - target_names or len(set(names.values())) != len(names):
                raise CircuitSpecError("placement terminal_map has unknown or duplicate target")

    # 1. orientations: reuse the source orientation (the template's drawing style);
    #    mirrored pairs are cross-checked against the left-right mirror table
    source_rows = {
        row.get("name"): row for row in relation_view.get("instances") or []
    }
    orientation = {key: "R0" for key in instances}
    if options.get("orientations", "template") == "template":
        for row in source_rows.values():
            target = device_map.get(row.get("device"))
            if target in orientation and row.get("orient") in MIRROR_VERTICAL:
                orientation[target] = row["orient"]
    report_pairs, orientation_gaps = [], []
    name_to_device, target_of = _relation_devices(relation_view, device_map)
    for pair in relation_view.get("mirrored_pairs") or []:
        left, right = target_of(pair["a"]), target_of(pair["b"])
        if not left or not right or left == right:
            continue
        if left not in instances or right not in instances:
            continue
        if orientation.get(right) != MIRROR_VERTICAL.get(orientation[left]):
            orientation_gaps.append(
                {"code": "mirrored_pair_orientation_mismatch", "pair": [left, right]}
            )
        report_pairs.append(
            {
                "a": left,
                "b": right,
                "a_orientation": orientation[left],
                "b_orientation": orientation[right],
            }
        )

    # 2. rows from the template (top row first); leftovers appended as singletons
    source_index = {
        row["device"]: index
        for index, row in enumerate(relation_view.get("instances") or [])
    }
    source_of = {target: source for source, target in device_map.items()}

    def order_key(instance):
        row = next((r for r in source_rows.values()
                    if r.get("device") == source_of.get(instance)), {})
        return ((row.get("xy") or [float("inf")])[0],
                source_index.get(source_of.get(instance, instance), 10**6), instance)

    mapped_rows, covered = [], set()
    for row in relation_view.get("rows") or []:
        members = list(dict.fromkeys(
            mapped
            for mapped in (target_of(member) for member in (row.get("members") or []))
            if mapped in instances
        ))
        if len(members) < 2:
            continue
        mapped_rows.append(sorted(members, key=order_key))
        covered.update(members)
    gaps = list(relation_view.get("gaps", [])) + [
        {"code": "source_position_unavailable" if key in source_of else "residual_placement",
         "instance": key}
        for key in sorted(instances)
        if not any(r.get("device") == source_of.get(key) and is_point(r.get("xy"))
                   for r in source_rows.values())
    ]
    for key in sorted(instances):
        if key not in covered:
            mapped_rows.append([key])

    # 3. spacing: reproduce the template's own pitch (converted to target units)
    #    and subtract the target body extent; never copy source coordinates.
    dbu = relation_view.get("dbu_per_uu") or 1
    positions = {
        row.get("device"): row.get("xy")
        for row in relation_view.get("instances") or []
        if row.get("device") and isinstance(row.get("xy"), list) and len(row.get("xy")) == 2
    }
    # Singleton rows carry the same vertical evidence as multi-device rows.
    # Sorting only named groups put an inverter's NMOS above its PMOS when the
    # remaining instances happened to be appended in lexical name order.
    def row_key(members):
        levels = [positions[source_of[key]][1] for key in members
                  if source_of.get(key) in positions]
        return (0, -max(levels), order_key(members[0])) if levels else (
            1, 0, order_key(members[0]))

    mapped_rows.sort(key=row_key)
    source_pitches = []
    for members in mapped_rows:
        xs = [
            positions.get(source_of.get(key, key), [None, None])[0]
            for key in members
        ]
        xs = sorted(value for value in xs if value is not None)
        source_pitches.extend(b - a for a, b in zip(xs, xs[1:]) if b > a)
    row_levels = sorted(
        {
            positions.get(source_of.get(member, member), [None, None])[1]
            for members in mapped_rows
            for member in members
        } - {None}, reverse=True
    )
    row_pitches = [
        a - b for a, b in zip(row_levels, row_levels[1:]) if a > b
    ]
    column_pitch = source_pitches[len(source_pitches) // 2] if source_pitches else None
    row_pitch = (
        row_pitches[len(row_pitches) // 2] if row_pitches else column_pitch
    )
    mean_width = sum(
        _body(geometry_rows[key], orientation[key])["width"] for key in instances
    ) / len(instances)
    mean_height = sum(
        _body(geometry_rows[key], orientation[key])["height"] for key in instances
    ) / len(instances)
    spacing_mode = options.get("spacing", "loose")
    if options.get("column_gap") is not None:
        column_gap = options["column_gap"]
    elif column_pitch:
        column_gap = max(4 * grid, column_pitch / dbu - mean_width)
    else:
        column_gap = max(4 * grid, 0.5 * mean_width)
    if spacing_mode == "loose":
        # Industry convention over density: even, generous channels between parts.
        column_gap = max(column_gap, LOOSE_MIN_GAP)
    elif spacing_mode == "compact":
        column_gap = max(4 * grid, column_gap / 2)
    if options.get("row_gap") is not None:
        row_gap = options["row_gap"]
    elif row_pitch:
        row_gap = max(4 * grid, row_pitch / dbu - mean_height)
    else:
        row_gap = max(column_gap, 2 * clearance)
    if spacing_mode == "loose":
        row_gap = max(row_gap, LOOSE_MIN_GAP)
    elif spacing_mode == "compact":
        row_gap = max(4 * grid, row_gap / 2)

    # 4. Shared template axes carry across every row; residuals get separate rows.
    layout_instances, tracks = place_tracks(
        geometry_rows, orientation, relation_view, device_map, grid, gaps,
        column_gap, row_gap, terminal_map=terminal_map)
    report_rows = []
    y_cursor = min((p["xy"][1] + _body(geometry_rows[k], p["orientation"])["bottom"]
                    for k, p in layout_instances.items()), default=row_gap) - row_gap
    for members in mapped_rows:
        existing = [key for key in members if key in layout_instances]
        if existing:
            report_rows.append(dict(members=existing, bottom=_snap(min(
                layout_instances[k]["xy"][1] + _body(geometry_rows[k], orientation[k])["bottom"]
                for k in existing), grid)))
        members = [key for key in members if key not in layout_instances]
        if not members:
            continue
        bodies = {key: _body(geometry_rows[key], orientation[key]) for key in members}
        row_height = max(body["height"] for body in bodies.values())
        bottom = y_cursor - row_height
        x_cursor = 0.0
        for key in members:
            body = bodies[key]
            xy = [_snap(x_cursor - body["left"], grid), _snap(bottom - body["bottom"], grid)]
            layout_instances[key] = {"xy": xy, "orientation": orientation[key]}
            x_cursor = xy[0] + body["right"] + column_gap
        report_rows.append({"members": members, "bottom": _snap(bottom, grid)})
        y_cursor = bottom - row_gap

    # 5. Template port positions win; absent evidence uses the default bands.
    layout_ports, port_report = plan_ports(
        spec, geometry_rows, layout_instances, relation_view, device_map, options,
        port_map=port_map,
    )

    layout = {
        "instances": layout_instances,
        "ports": layout_ports,
        "anchor_choices": {},
        "grid": grid,
        "stub_length": stub_length,
        "clearance": clearance,
    }
    if options.get("routing"):
        layout["routing"] = options["routing"]
    geometry_review = []
    for key in sorted(instances):
        body = _body(geometry_rows[key], orientation[key])
        anchors = [
            anchor["xy"]
            for terminal in geometry_rows[key]["terminals"]
            for anchor in terminal["anchors"]
        ]
        if len(anchors) < 2:
            continue
        span_x = max(point[0] for point in anchors) - min(point[0] for point in anchors)
        span_y = max(point[1] for point in anchors) - min(point[1] for point in anchors)
        warnings = []
        if span_x <= grid and body["width"] > 2 * grid:
            warnings.append("pins_on_vertical_centerline; use up/down escapes")
        if span_y <= grid and body["height"] > 2 * grid:
            warnings.append("pins_on_horizontal_centerline; use left/right escapes")
        if warnings:
            geometry_review.append({"instance": key, "warnings": warnings})
    report = {
        "schema": PLACEMENT_SCHEMA,
        "template_ref": relation_view.get("template_ref"),
        "rows": report_rows,
        "tracks": tracks,
        "mirrored_pairs": report_pairs,
        **port_report,
        "spacing": {
            "mode": spacing_mode,
            "column_gap": column_gap,
            "row_gap": row_gap,
            "source_column_pitch": column_pitch,
            "source_row_pitch": row_pitch,
            "dbu_per_uu": dbu,
            "mean_body_width": mean_width,
            "mean_body_height": mean_height,
        },
        "gaps": gaps + orientation_gaps,
        "geometry_review": geometry_review,
        "use": "deterministic relations placement; verify with preview_circuit_geometry",
    }
    return layout, report
