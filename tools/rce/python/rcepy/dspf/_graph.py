"""Compact graph algorithms for large DSPF resistance networks."""

from __future__ import annotations

import math
from array import array
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .repository import DspfRepository


_UNSEEN = -1
_SETTLED = -2
_MAX_DENSE_NODES = (1 << 31) - 1


@dataclass(frozen=True)
class CompactPath:
    total_resistance: float | None
    node_ids: list[int]
    resistor_ids: list[int]
    visited_nodes: int


@dataclass(frozen=True)
class _CsrGraph:
    node_ids: array
    offsets: array
    neighbors: array
    weights: array
    resistor_ids: array


class _IndexedMinHeap:
    """A decrease-key heap whose storage remains bounded by vertex count."""

    def __init__(self, distances: array, node_ids: array) -> None:
        self._distances = distances
        self._node_ids = node_ids
        self._nodes = array("I")
        self._positions = array("i", [_UNSEEN]) * len(node_ids)

    def __bool__(self) -> bool:
        return bool(self._nodes)

    def is_settled(self, node: int) -> bool:
        return self._positions[node] == _SETTLED

    def decrease(self, node: int) -> None:
        position = self._positions[node]
        if position == _SETTLED:
            return
        if position == _UNSEEN:
            position = len(self._nodes)
            self._nodes.append(node)
        self._sift_up(position, node)

    def pop(self) -> int:
        root = self._nodes[0]
        last = self._nodes.pop()
        if self._nodes:
            self._sift_down(0, last)
        self._positions[root] = _SETTLED
        return root

    def _less(self, left: int, right: int) -> bool:
        left_distance = self._distances[left]
        right_distance = self._distances[right]
        return left_distance < right_distance or (
            left_distance == right_distance
            and self._node_ids[left] < self._node_ids[right]
        )

    def _sift_up(self, position: int, node: int) -> None:
        while position:
            parent_position = (position - 1) >> 1
            parent = self._nodes[parent_position]
            if not self._less(node, parent):
                break
            self._nodes[position] = parent
            self._positions[parent] = position
            position = parent_position
        self._nodes[position] = node
        self._positions[node] = position

    def _sift_down(self, position: int, node: int) -> None:
        size = len(self._nodes)
        half = size >> 1
        while position < half:
            child_position = (position << 1) + 1
            child = self._nodes[child_position]
            right_position = child_position + 1
            if right_position < size:
                right = self._nodes[right_position]
                if self._less(right, child):
                    child_position, child = right_position, right
            if not self._less(child, node):
                break
            self._nodes[position] = child
            self._positions[child] = position
            position = child_position
        self._nodes[position] = node
        self._positions[node] = position


def shortest_resistance_path(
    repository: DspfRepository,
    net_id: int,
    start_id: int,
    end_id: int,
) -> CompactPath:
    graph, start, end = _build_csr(repository, net_id, start_id, end_id)
    if graph is None or start is None:
        return CompactPath(None, [], [], 1)

    vertex_count = len(graph.node_ids)
    distances = array("d", [math.inf]) * vertex_count
    previous_node = array("i", [_UNSEEN]) * vertex_count
    previous_resistor = array("q", [_UNSEEN]) * vertex_count
    heap = _IndexedMinHeap(distances, graph.node_ids)
    distances[start] = 0.0
    heap.decrease(start)
    visited = 0
    found = False

    while heap:
        node = heap.pop()
        visited += 1
        if end is not None and node == end:
            found = True
            break
        distance = distances[node]
        for arc in range(graph.offsets[node], graph.offsets[node + 1]):
            other = graph.neighbors[arc]
            if heap.is_settled(other):
                continue
            candidate = distance + graph.weights[arc]
            if candidate < distances[other]:
                distances[other] = candidate
                previous_node[other] = node
                previous_resistor[other] = graph.resistor_ids[arc]
                heap.decrease(other)

    if not found or end is None:
        return CompactPath(None, [], [], visited)

    dense_path = array("I", [end])
    resistor_path = array("q")
    cursor = end
    while cursor != start:
        resistor_path.append(previous_resistor[cursor])
        cursor = previous_node[cursor]
        dense_path.append(cursor)
    dense_path.reverse()
    resistor_path.reverse()
    return CompactPath(
        distances[end],
        [int(graph.node_ids[node]) for node in dense_path],
        [int(resistor_id) for resistor_id in resistor_path],
        visited,
    )


def connected_component_count(
    repository: DspfRepository,
    net_id: int,
    owned_node_count: int,
) -> int:
    if owned_node_count == 0:
        return 0
    dense_by_node: dict[int, int] = {}
    parent = array("I")
    rank = bytearray()
    components = owned_node_count

    def index(node_id: int) -> int:
        dense = dense_by_node.get(node_id)
        if dense is not None:
            return dense
        dense = len(parent)
        _check_dense_size(dense)
        dense_by_node[node_id] = dense
        parent.append(dense)
        rank.append(0)
        return dense

    for edge in repository._component_edges(net_id):
        left = _find(parent, index(int(edge[0])))
        right = _find(parent, index(int(edge[1])))
        if left == right:
            continue
        if rank[left] < rank[right]:
            left, right = right, left
        parent[right] = left
        if rank[left] == rank[right]:
            rank[left] += 1
        components -= 1
    return components


def _build_csr(
    repository: DspfRepository,
    net_id: int,
    start_id: int,
    end_id: int,
) -> tuple[_CsrGraph | None, int | None, int | None]:
    dense_by_node: dict[int, int] = {}
    node_ids = array("q")
    degrees = array("I")

    def index(node_id: int) -> int:
        dense = dense_by_node.get(node_id)
        if dense is not None:
            return dense
        dense = len(node_ids)
        _check_dense_size(dense)
        dense_by_node[node_id] = dense
        node_ids.append(node_id)
        degrees.append(0)
        return dense

    for edge in repository._path_edges(net_id):
        left = index(int(edge["node1_id"]))
        right = index(int(edge["node2_id"]))
        degrees[left] += 1
        degrees[right] += 1

    start = dense_by_node.get(start_id)
    end = dense_by_node.get(end_id)
    if start is None:
        return None, None, end

    offsets = array("Q", [0])
    arc_count = 0
    for degree in degrees:
        arc_count += degree
        offsets.append(arc_count)
    write_at = offsets[:-1]
    neighbors = array("I", [0]) * arc_count
    weights = array("d", [0.0]) * arc_count
    resistor_ids = array("q", [0]) * arc_count

    for edge in repository._path_edges(net_id):
        left = dense_by_node[int(edge["node1_id"])]
        right = dense_by_node[int(edge["node2_id"])]
        value = float(edge["value"])
        resistor_id = int(edge["id"])
        left_arc = write_at[left]
        write_at[left] += 1
        right_arc = write_at[right]
        write_at[right] += 1
        neighbors[left_arc], neighbors[right_arc] = right, left
        weights[left_arc] = weights[right_arc] = value
        resistor_ids[left_arc] = resistor_ids[right_arc] = resistor_id

    dense_by_node.clear()
    return _CsrGraph(node_ids, offsets, neighbors, weights, resistor_ids), start, end


def _find(parent: array, node: int) -> int:
    while parent[node] != node:
        parent[node] = parent[parent[node]]
        node = parent[node]
    return node


def _check_dense_size(dense: int) -> None:
    if dense >= _MAX_DENSE_NODES:
        raise OverflowError("DSPF graph has too many nodes for compact analysis")
