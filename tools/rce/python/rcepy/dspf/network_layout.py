"""Deterministic, dependency-free layout for bounded RC networks."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
import math
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    from .network import RcNetwork


_X_SPACING, _Y_SPACING, _TOPOLOGY_STUB_SPACING = 120.0, 50.0, 80.0
_EXTERNAL_BBOX_MARGIN_SPANS = 8.0
_PHYSICAL_STUB_SPAN_FRACTION = 0.35
_MIN_PHYSICAL_STUB_SPACING = 1e-6
_SMOOTHING_PASSES = 8
_KIND_PRIORITY = {"port": 0, "net": 1, "instance_pin": 2, "subnode": 3}


@dataclass(frozen=True)
class RcNetworkLayout:
    positions: dict[int, tuple[float, float]]
    physical_ids: frozenset[int]
    mode: str
    bounds: tuple[float, float, float, float]
    ground_positions: dict[int, tuple[float, float]] = field(default_factory=dict)


def layout_rc_network(network: RcNetwork) -> RcNetworkLayout:
    """Lay out an RC network without changing its source-coordinate system."""
    nodes = {int(node.id): node for node in network.nodes}
    primary = {
        node_id for node_id, node in nodes.items()
        if not node.external and str(node.kind) != "ground"
    }
    pairs = _layout_pairs(network, nodes)
    primary.difference_update(_synthetic_net_ids(primary, pairs, nodes))
    external = {
        node_id for pair in pairs for node_id in pair
        if node_id in nodes and node_id not in primary
        and str(nodes[node_id].kind) != "ground"
    }
    drawable = primary | external
    if not drawable:
        return RcNetworkLayout({}, frozenset(), "topology", (0.0, 0.0, 0.0, 0.0))

    physical = {
        node_id: point for node_id in primary
        if (point := _physical_point(nodes[node_id])) is not None
    }
    external_physical = _nearby_external_physical(external, nodes, physical.values())
    adjacency = _primary_adjacency(primary, pairs, nodes)
    components = _components(primary, adjacency, nodes)
    positions: dict[int, tuple[float, float]] = {}
    unanchored: list[tuple[list[int], dict[int, tuple[float, float]]]] = []

    for component in components:
        anchors = {node_id: physical[node_id] for node_id in component if node_id in physical}
        if len(anchors) == len(component):
            positions.update(anchors)
            continue
        topology = _layer_component(component, adjacency, nodes)
        if not anchors:
            unanchored.append((component, topology))
            continue
        initial = _align_to_anchors(topology, anchors)
        positions.update(_smooth_missing(component, adjacency, initial, anchors))

    _pack_components(unanchored, positions)
    positions.update(external_physical)
    stub_spacing = (
        _physical_stub_spacing(physical.values())
        if physical else _TOPOLOGY_STUB_SPACING
    )
    _place_external(external, positions, pairs, nodes, stub_spacing)
    ground_positions = _place_ground_stubs(network, nodes, positions, stub_spacing)

    physical_ids = frozenset(physical) | frozenset(external_physical)
    mode = "physical" if primary and len(physical) == len(primary) else "mixed" if physical else "topology"
    bounds = _bounds((*positions.values(), *ground_positions.values()))
    return RcNetworkLayout(positions, physical_ids, mode, bounds, ground_positions)


def _layout_pairs(network: Any, nodes: dict[int, Any]) -> list[tuple[int, int]]:
    pairs = [
        (int(item.node1_id), int(item.node2_id))
        for item in network.resistors
        if int(item.node1_id) in nodes and int(item.node2_id) in nodes
    ]
    pairs.extend(
        (int(item.node1_id), int(item.node2_id))
        for item in network.capacitors
        if item.kind == "coupling" and int(item.node1_id) in nodes
        and int(item.node2_id) in nodes
    )
    return pairs


def _synthetic_net_ids(
    primary: set[int], pairs: Iterable[tuple[int, int]], nodes: dict[int, Any],
) -> set[int]:
    linked = {node_id for pair in pairs for node_id in pair}
    return {
        node_id for node_id in primary
        if node_id not in linked and str(nodes[node_id].kind) == "net"
        and _physical_point(nodes[node_id]) is None
    }


def _nearby_external_physical(
    external: set[int], nodes: dict[int, Any],
    primary_points: Iterable[tuple[float, float]],
) -> dict[int, tuple[float, float]]:
    points = list(primary_points)
    if not points:
        return {}
    left, bottom, right, top = _raw_bounds(points)
    span = max(right - left, top - bottom, 1.0)
    margin = span * _EXTERNAL_BBOX_MARGIN_SPANS
    result: dict[int, tuple[float, float]] = {}
    for node_id in external:
        point = _physical_point(nodes[node_id])
        if point is not None and (
            left - margin <= point[0] <= right + margin
            and bottom - margin <= point[1] <= top + margin
        ):
            result[node_id] = point
    return result


def _physical_stub_spacing(
    points: Iterable[tuple[float, float]],
) -> float:
    left, bottom, right, top = _raw_bounds(points)
    span = max(right - left, top - bottom)
    if span <= 0.0:
        span = 1.0
    return max(span * _PHYSICAL_STUB_SPAN_FRACTION, _MIN_PHYSICAL_STUB_SPACING)


def _primary_adjacency(
    primary: set[int], pairs: Iterable[tuple[int, int]], nodes: dict[int, Any],
) -> dict[int, tuple[int, ...]]:
    neighbors: dict[int, set[int]] = {node_id: set() for node_id in primary}
    for first, second in pairs:
        if first in primary and second in primary and first != second:
            neighbors[first].add(second)
            neighbors[second].add(first)
    return {
        node_id: tuple(sorted(items, key=lambda item: _node_key(nodes, item)))
        for node_id, items in neighbors.items()
    }


def _components(
    node_ids: set[int], adjacency: dict[int, tuple[int, ...]], nodes: dict[int, Any],
) -> list[list[int]]:
    remaining = set(node_ids)
    result: list[list[int]] = []
    for start in sorted(node_ids, key=lambda item: _node_key(nodes, item)):
        if start not in remaining:
            continue
        remaining.remove(start)
        queue = deque([start])
        component: list[int] = []
        while queue:
            current = queue.popleft()
            component.append(current)
            for neighbor in adjacency[current]:
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    queue.append(neighbor)
        component.sort(key=lambda item: _node_key(nodes, item))
        result.append(component)
    return result


def _layer_component(
    component: list[int], adjacency: dict[int, tuple[int, ...]], nodes: dict[int, Any],
) -> dict[int, tuple[float, float]]:
    root = min(component, key=lambda item: (
        _KIND_PRIORITY.get(str(nodes[item].kind), 9), -len(adjacency[item]),
        *_node_key(nodes, item),
    ))
    distance = {root: 0}
    queue = deque([root])
    while queue:
        current = queue.popleft()
        for neighbor in adjacency[current]:
            if neighbor not in distance:
                distance[neighbor] = distance[current] + 1
                queue.append(neighbor)
    depth_count = max(distance.values(), default=0) + 1
    layers: list[list[int]] = [[] for _ in range(depth_count)]
    for node_id in component:
        layers[distance[node_id]].append(node_id)
    for layer in layers:
        layer.sort(key=lambda item: _node_key(nodes, item))

    result: dict[int, tuple[float, float]] = {}
    for depth, layer in enumerate(layers):
        center = (len(layer) - 1) / 2.0
        for rank, node_id in enumerate(layer):
            result[node_id] = (depth * _X_SPACING, (rank - center) * _Y_SPACING)
    return result
def _align_to_anchors(
    topology: dict[int, tuple[float, float]],
    anchors: dict[int, tuple[float, float]],
) -> dict[int, tuple[float, float]]:
    identifiers = sorted(anchors)
    topo_center = _centroid(topology[node_id] for node_id in identifiers)
    physical_center = _centroid(anchors.values())
    denominator = numerator_a = numerator_b = 0.0
    for node_id in identifiers:
        u = topology[node_id][0] - topo_center[0]
        v = topology[node_id][1] - topo_center[1]
        x = anchors[node_id][0] - physical_center[0]
        y = anchors[node_id][1] - physical_center[1]
        denominator += u * u + v * v
        numerator_a += u * x + v * y
        numerator_b += u * y - v * x
    a = numerator_a / denominator if denominator else 0.0
    b = numerator_b / denominator if denominator else 0.0
    if math.hypot(a, b) <= 1e-15:
        a, b = 1.0 / _X_SPACING, 0.0
    tx = physical_center[0] - (a * topo_center[0] - b * topo_center[1])
    ty = physical_center[1] - (b * topo_center[0] + a * topo_center[1])
    return {
        node_id: (a * u - b * v + tx, b * u + a * v + ty)
        for node_id, (u, v) in topology.items()
    }


def _smooth_missing(
    component: list[int], adjacency: dict[int, tuple[int, ...]],
    initial: dict[int, tuple[float, float]],
    anchors: dict[int, tuple[float, float]],
) -> dict[int, tuple[float, float]]:
    positions = dict(initial)
    positions.update(anchors)
    missing = [node_id for node_id in component if node_id not in anchors]
    for _ in range(_SMOOTHING_PASSES):
        updated: dict[int, tuple[float, float]] = {}
        for node_id in missing:
            neighbors = adjacency[node_id]
            if not neighbors:
                updated[node_id] = initial[node_id]
                continue
            x = initial[node_id][0] + sum(positions[item][0] for item in neighbors)
            y = initial[node_id][1] + sum(positions[item][1] for item in neighbors)
            weight = len(neighbors) + 1
            updated[node_id] = (x / weight, y / weight)
        positions.update(updated)
    positions.update(anchors)
    return positions


def _pack_components(
    components: list[tuple[list[int], dict[int, tuple[float, float]]]],
    positions: dict[int, tuple[float, float]],
) -> None:
    if not components:
        return
    gap = _X_SPACING
    boxes = [(_raw_bounds(local.values()), local) for _, local in components]
    cell_width = max(max(box[2] - box[0], gap) for box, _ in boxes) + gap
    cell_height = max(max(box[3] - box[1], gap) for box, _ in boxes) + gap
    columns = math.ceil(math.sqrt(len(boxes)))
    if positions:
        existing = _raw_bounds(positions.values())
        origin_x, origin_y = existing[2] + gap, existing[1]
    else:
        origin_x = origin_y = 0.0
    for index, (box, local) in enumerate(boxes):
        row, column = divmod(index, columns)
        shift_x = origin_x + column * cell_width - box[0]
        shift_y = origin_y + row * cell_height - box[1]
        positions.update({node_id: (px + shift_x, py + shift_y) for node_id, (px, py) in local.items()})


def _place_external(
    external: set[int], positions: dict[int, tuple[float, float]],
    pairs: Iterable[tuple[int, int]], nodes: dict[int, Any], stub_spacing: float,
) -> None:
    links: dict[int, set[int]] = defaultdict(set)
    for first, second in pairs:
        if first != second:
            links[first].add(second)
            links[second].add(first)
    pending = external.difference(positions)
    queue = deque(sorted(positions, key=lambda item: _node_key(nodes, item)))
    slots: dict[int, int] = defaultdict(int)
    while queue:
        parent = queue.popleft()
        for child in sorted(links[parent], key=lambda item: _node_key(nodes, item)):
            if child not in pending:
                continue
            slot = slots[parent]
            slots[parent] += 1
            ring, direction = divmod(slot, 8)
            angle = direction * math.pi / 4.0
            radius = (ring + 1) * stub_spacing
            base = positions[parent]
            positions[child] = (
                base[0] + radius * math.cos(angle),
                base[1] + radius * math.sin(angle),
            )
            pending.remove(child)
            queue.append(child)
    if pending:
        box = _raw_bounds(positions.values()) if positions else (0.0, 0.0, 0.0, 0.0)
        for index, node_id in enumerate(sorted(pending, key=lambda item: _node_key(nodes, item))):
            positions[node_id] = (
                box[2] + _X_SPACING * (1 + index % 8),
                box[1] + _Y_SPACING * (index // 8),
            )


def _place_ground_stubs(
    network: Any, nodes: dict[int, Any], positions: dict[int, tuple[float, float]],
    spacing: float,
) -> dict[int, tuple[float, float]]:
    grouped: dict[int, list[int]] = defaultdict(list)
    for resistor in network.resistors:
        first, second = int(resistor.node1_id), int(resistor.node2_id)
        first_ground = first in nodes and str(nodes[first].kind) == "ground"
        second_ground = second in nodes and str(nodes[second].kind) == "ground"
        if first_ground != second_ground:
            local = second if first_ground else first
            if local in positions:
                grouped[local].append(int(resistor.id))

    result: dict[int, tuple[float, float]] = {}
    for local in sorted(grouped, key=lambda item: _node_key(nodes, item)):
        identifiers = sorted(grouped[local])
        spread = min(math.pi * 2.0 / 3.0, (len(identifiers) - 1) * math.pi / 12.0)
        step = spread / max(len(identifiers) - 1, 1)
        for rank, resistor_id in enumerate(identifiers):
            angle = -math.pi / 2.0 - spread / 2.0 + rank * step
            base = positions[local]
            result[resistor_id] = (
                base[0] + spacing * math.cos(angle),
                base[1] + spacing * math.sin(angle),
            )
    return result


def _physical_point(node: Any) -> tuple[float, float] | None:
    try:
        x, y = float(node.x), float(node.y)
    except (TypeError, ValueError):
        return None
    return (x, y) if math.isfinite(x) and math.isfinite(y) else None


def _node_key(nodes: dict[int, Any], node_id: int) -> tuple[str, int]:
    return str(nodes[node_id].name), node_id


def _centroid(points: Iterable[tuple[float, float]]) -> tuple[float, float]:
    values = list(points)
    x = sum(point[0] for point in values) / len(values)
    y = sum(point[1] for point in values) / len(values)
    return x, y


def _raw_bounds(points: Iterable[tuple[float, float]]) -> tuple[float, float, float, float]:
    values = list(points)
    if not values:
        return 0.0, 0.0, 0.0, 0.0
    xs, ys = zip(*values)
    return min(xs), min(ys), max(xs), max(ys)


def _bounds(points: Iterable[tuple[float, float]]) -> tuple[float, float, float, float]:
    left, bottom, right, top = _raw_bounds(points)
    span = max(right - left, top - bottom, 1.0)
    padding = span * 0.05
    if left == right:
        left, right = left - padding, right + padding
    if bottom == top:
        bottom, top = bottom - padding, top + padding
    return left, bottom, right, top


__all__ = ["RcNetworkLayout", "layout_rc_network"]
