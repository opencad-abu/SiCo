"""Bounded DSPF resistance regions for OA layout highlighting."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from .repository import DspfRepository, NetSelector


MAX_HIGHLIGHT_REGIONS = 256
MAX_HIGHLIGHT_RESISTORS = 20_000


def resistance_highlight_regions(
    repository: DspfRepository,
    net: NetSelector,
    *,
    max_regions: int = MAX_HIGHLIGHT_REGIONS,
    max_resistors: int = MAX_HIGHLIGHT_RESISTORS,
) -> tuple[tuple[float, float, float, float], ...]:
    """Return regions covering all located R edges without an unbounded payload."""
    _positive_limit("max_regions", max_regions)
    _positive_limit("max_resistors", max_resistors)
    network = repository.rc_network(
        net,
        max_nodes=max_resistors * 2 + 4096,
        max_elements=max_resistors,
        include_capacitors=False,
    )
    if network.status != "ok":
        return ()
    nodes = {node.id: node for node in network.nodes}
    points = [
        (float(node.x), float(node.y))
        for node in network.nodes
        if _located(node)
    ]
    span = _span(points)
    regions = []
    for resistor in network.resistors:
        first = _point(nodes.get(resistor.node1_id))
        second = _point(nodes.get(resistor.node2_id))
        if first is None and second is None:
            continue
        if first is None:
            first = second
        if second is None:
            second = first
        assert first is not None and second is not None
        padding = _padding(resistor.width, resistor.length, first, second, span)
        regions.append((
            min(first[0], second[0]) - padding,
            min(first[1], second[1]) - padding,
            max(first[0], second[0]) + padding,
            max(first[1], second[1]) + padding,
        ))
    return tuple(_merge_regions(regions, max_regions))


def _located(node: object) -> bool:
    return _point(node) is not None and getattr(node, "kind", "") != "ground"


def _point(node: object | None) -> tuple[float, float] | None:
    if node is None:
        return None
    try:
        x, y = float(getattr(node, "x")), float(getattr(node, "y"))
    except (TypeError, ValueError):
        return None
    return (x, y) if math.isfinite(x) and math.isfinite(y) else None


def _span(points: Iterable[tuple[float, float]]) -> float:
    values = list(points)
    if not values:
        return 1.0
    xs, ys = zip(*values)
    return max(max(xs) - min(xs), max(ys) - min(ys), 1.0e-3)


def _layout_dimension(value: float | None) -> float | None:
    if value is None or not math.isfinite(value) or value == 0:
        return None
    magnitude = abs(value)
    # Extractors commonly write suffixed L/W in meters and bare values in um.
    return magnitude * 1.0e6 if magnitude <= 1.0e-3 else magnitude


def _padding(
    width: float | None,
    length: float | None,
    first: tuple[float, float],
    second: tuple[float, float],
    network_span: float,
) -> float:
    edge_length = math.hypot(second[0] - first[0], second[1] - first[1])
    width_uu = _layout_dimension(width)
    length_uu = _layout_dimension(length) if edge_length == 0 else None
    dimension = max((item for item in (width_uu, length_uu) if item), default=0.0)
    floor = max(network_span * 1.0e-5, 1.0e-4)
    ceiling = max(network_span * 0.02, 0.02)
    return max(floor, min(dimension * 0.5, ceiling))


def _merge_regions(
    regions: list[tuple[float, float, float, float]], limit: int,
) -> list[tuple[float, float, float, float]]:
    if len(regions) <= limit:
        return regions
    groups = [regions]
    while len(groups) < limit:
        index = max(range(len(groups)), key=lambda item: len(groups[item]))
        group = groups[index]
        if len(group) < 2:
            break
        x_span = max(item[2] for item in group) - min(item[0] for item in group)
        y_span = max(item[3] for item in group) - min(item[1] for item in group)
        axis = 0 if x_span >= y_span else 1
        ordered = sorted(group, key=lambda item: (item[axis] + item[axis + 2], item))
        middle = len(ordered) // 2
        groups[index:index + 1] = (ordered[:middle], ordered[middle:])
    return [_union(group) for group in groups]


def _union(
    regions: list[tuple[float, float, float, float]],
) -> tuple[float, float, float, float]:
    return (
        min(item[0] for item in regions),
        min(item[1] for item in regions),
        max(item[2] for item in regions),
        max(item[3] for item in regions),
    )


def _positive_limit(name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


__all__ = [
    "MAX_HIGHLIGHT_REGIONS",
    "MAX_HIGHLIGHT_RESISTORS",
    "resistance_highlight_regions",
]
