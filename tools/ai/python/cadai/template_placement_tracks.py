"""Solve shared source rows/columns with target geometry and grid-safe separation."""

import math
from itertools import combinations

from .circuit_geometry_schema import EPS, transform, transform_box
from .circuit_spec_schema import CircuitSpecError


def _offset(source, axis, target, geometry, orientation, terminal_map, gaps):
    evidence = source.get("axes", {}).get("xy"[axis])
    if not evidence:
        return source["xy"][axis], 0.0
    terms = {t["name"]: t for t in geometry["terminals"]}
    values = []
    for name in evidence["terminals"]:
        mapped = terminal_map.get(name) if terminal_map is not None else name
        anchors = terms.get(mapped, {}).get("anchors", [])
        if len(anchors) != 1:
            break
        values.append(transform(anchors[0]["xy"], orientation)[axis])
    else:
        if values and max(values) - min(values) <= EPS:
            return evidence["coordinate"], values[0]
    gaps.append(dict(code="terminal_axis_unavailable", instance=target, axis="xy"[axis],
                     terminals=evidence["terminals"]))
    return source["xy"][axis], 0.0


def _tracks(rows, axis, edges, grid, dbu):
    grouped = {}
    for key, row in rows.items():
        grouped.setdefault(row["source"][axis], []).append(key)
    levels = sorted(grouped)
    locations = {}
    for index, level in enumerate(levels):
        members = grouped[level]
        # Origins stay on grid. Shared terminal axes may have a nonzero common
        # phase, but incompatible phases cannot be aligned by rounding pins.
        phase = rows[members[0]]["offset"][axis] % grid
        if any(abs((rows[k]["offset"][axis] - phase) / grid
                   - round((rows[k]["offset"][axis] - phase) / grid)) > EPS for k in members):
            raise CircuitSpecError("template terminal axis cannot align on target grid")
        lower = max(-rows[k]["box"][0][axis] for k in members) if not index else (
            locations[levels[index - 1]] + max(grid, (level - levels[index - 1]) / dbu))
        for (start, end), distance in edges.items():
            if end == level:
                lower = max(lower, locations[start] + distance)
        locations[level] = phase + math.ceil((lower - phase - EPS) / grid) * grid
    return locations, [dict(axis="xy"[axis], source_coordinate=v, coordinate=locations[v],
                            members=sorted(grouped[v])) for v in levels]


def place_tracks(geometry, orientation, reference, device_map, grid, gaps,
                 column_gap, row_gap, *, terminal_map=None):
    """Preserve shared tracks; solve only necessary pair separation constraints.

    The source's vertical overlap chooses horizontal separation. Otherwise the
    pair keeps its vertical order. Equality of a source track always wins over
    spacing on that axis. Distances expand for target text/body extents; ordered
    tracks preserve gaps and empty columns across rows instead of restarting x.
    """
    source_rows = {r["device"]: r for r in reference.get("instances", [])}
    axes = {r["device"]: r["axes"] for r in reference.get("device_axes", [])}
    rows = {}
    for source_id, key in sorted(device_map.items()):
        if key not in geometry or source_id not in source_rows:
            continue
        source = {**source_rows[source_id], "axes": axes.get(source_id, {})}
        if source.get("xy") is None:
            continue
        mapping = terminal_map.get(source_id, {}) if terminal_map is not None else None
        resolved = [_offset(source, a, key, geometry[key], orientation[key], mapping, gaps)
                    for a in (0, 1)]
        offset = [p[1] for p in resolved]
        box = transform_box(geometry[key].get("annotation_bbox", geometry[key]["occupied_bbox"]),
                            orientation[key], [-v for v in offset])
        rows[key] = dict(source=[p[0] for p in resolved], offset=offset, box=box,
                         source_box=source.get("bbox"), source_id=source_id)
    edges = [{}, {}]
    for left, right in combinations(sorted(rows), 2):
        a, b = rows[left], rows[right]
        equal = [abs(a["source"][k] - b["source"][k]) <= EPS for k in (0, 1)]
        if all(equal):
            raise CircuitSpecError("distinct template devices share both placement axes")
        if any(equal):
            axis = 1 if equal[0] else 0
        else:
            old_a, old_b = a["source_box"], b["source_box"]
            y_overlap = old_a and old_b and (
                max(old_a[0][1], old_b[0][1]) <= min(old_a[1][1], old_b[1][1]))
            axis = 0 if y_overlap else 1
        if a["source"][axis] > b["source"][axis]:
            a, b = b, a
        distance = a["box"][1][axis] - b["box"][0][axis] + (column_gap, row_gap)[axis]
        pair = (a["source"][axis], b["source"][axis])
        edges[axis][pair] = max(edges[axis].get(pair, 0), distance)
    dbu = reference.get("dbu_per_uu") or 1
    locations, tracks = [], []
    for axis in (0, 1):
        values, report = _tracks(rows, axis, edges[axis], grid, dbu)
        locations.append(values)
        tracks.extend(report)
    layout = {k: dict(xy=[round((locations[a][r["source"][a]] - r["offset"][a]) / grid) * grid
                               for a in (0, 1)], orientation=orientation[k])
              for k, r in rows.items()}
    # Keep a stable local frame: annotation left edge near zero, top edge near zero.
    if rows:
        shift = [math.floor(min(layout[k]["xy"][0] + r["box"][0][0] + r["offset"][0]
                                for k, r in rows.items()) / grid) * grid,
                 math.ceil(max(layout[k]["xy"][1] + r["box"][1][1] + r["offset"][1]
                               for k, r in rows.items()) / grid) * grid]
        for position in layout.values():
            position["xy"] = [position["xy"][a] - shift[a] for a in (0, 1)]
        for track in tracks:
            track["coordinate"] -= shift["xy".index(track["axis"])]
    return layout, tracks
