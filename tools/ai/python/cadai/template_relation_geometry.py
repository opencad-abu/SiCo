"""Shared deterministic geometric facts, unchanged across relation view versions."""

import math

MIRROR_VERTICAL = {
    "R0": "MY",
    "MY": "R0",
    "R90": "MXR90",
    "MXR90": "R90",
    "R180": "MX",
    "MX": "R180",
    "R270": "MYR90",
    "MYR90": "R270",
}


def _net_names(record):
    """Topology net id -> source name; wire shapes already carry source names."""
    return {
        net["id"]: net.get("source_name", net["id"])
        for net in record.get("topology", {}).get("nets", [])
    }


def _grouped(instances, axis):
    grouped = {}
    for row in instances.values():
        grouped.setdefault(row["xy"][axis], []).append(row["name"])
    return [
        {"x" if axis == 0 else "y": key, "members": sorted(members)}
        for key, members in sorted(grouped.items())
        if len(members) > 1
    ]


def _spacing(instances):
    names = sorted(instances)
    gaps = {}
    for name in names:
        distances = [
            math.dist(instances[name]["xy"], instances[other]["xy"])
            for other in names
            if other != name
        ]
        if distances:
            gaps[name] = min(distances)
    values = sorted(gaps.values())
    classes = None
    if len(values) >= 3:
        third = max(1, len(values) // 3)
        tight, medium = values[third - 1], values[min(len(values) - 1, 2 * third - 1)]
        classes = {
            "tight": tight,
            "medium": medium,
            "members": {
                "tight": [n for n in names if gaps.get(n, math.inf) <= tight],
                "medium": [n for n in names if tight < gaps.get(n, math.inf) <= medium],
                "wide": [n for n in names if gaps.get(n, math.inf) > medium],
            },
        }
    return {"nearest": {n: gaps[n] for n in names if n in gaps}, "classes": classes}
