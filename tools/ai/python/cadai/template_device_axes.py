"""Geometric device alignment axes from captured terminal positions, without PDK rules."""

from collections import defaultdict

from .circuit_geometry_schema import MATRICES, transform
from .template_coords import instance_position, is_integer_space, is_point


def device_axes(asset, *, gaps=None):
    """Identify the unique line containing most distinct terminal locations.

    An axis needs at least two distinct points. Coincident bulk/source terminals
    count once, and tied candidates remain unknown. These are drawing relations,
    not claims about source wire connectivity or device electrical function.
    """
    reference = asset.get("wire_reference") or {}
    def gap(code, device, **fields):
        if gaps is not None:
            gaps.append(dict(code=code, device=device, **fields))

    if not is_integer_space(asset):
        return []
    geometries = {g["device"]: g for g in reference.get("geometry", [])}
    rows = []
    for instance in asset.get("instances", []):
        source = geometries.get(instance.get("device"))
        origin = instance_position(instance)
        if not source:
            gap("source_terminal_geometry_missing", instance.get("device"))
        if not source or origin is None or instance.get("orient") not in MATRICES:
            continue
        points = {}
        for term in source.get("terminals", []):
            anchors = term.get("anchors") or []
            if len(anchors) == 1 and is_point(anchors[0].get("xy")):
                points[term["name"]] = transform(anchors[0]["xy"], instance["orient"], origin)
            else:
                gap("source_terminal_anchor_unavailable", instance["device"], terminal=term["name"])
        axes = {}
        for axis, name in enumerate(("x", "y")):
            groups = defaultdict(list)
            for term, point in points.items():
                groups[point[axis]].append(term)
            counts = {value: len({tuple(points[t]) for t in terms})
                      for value, terms in groups.items()}
            best = max(counts.values(), default=0)
            winners = [value for value, count in counts.items() if count == best]
            if best >= 2 and len(winners) == 1:
                value = winners[0]
                axes[name] = dict(coordinate=value, terminals=sorted(groups[value]))
            elif best >= 2:
                gap("source_terminal_axis_ambiguous", instance["device"], axis=name,
                    candidates=len(winners))
        if axes:
            rows.append(dict(device=instance["device"], axes=axes))
    return sorted(rows, key=lambda row: row["device"])
