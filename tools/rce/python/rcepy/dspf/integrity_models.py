"""Result models and compact union-find state for integrity analysis."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ResistanceComponent:
    node_count: int
    terminal_count: int
    terminal_node_ids: tuple[int, ...]
    terminal_names: tuple[str, ...]


@dataclass(frozen=True)
class ShortCandidate:
    resistor_id: int
    name: str
    value: float
    node1_id: int
    node1_name: str
    node1_net_name: str | None
    node1_kind: str
    node2_id: int
    node2_name: str
    node2_net_name: str | None
    node2_kind: str
    source_line: int
    reason: str


@dataclass(frozen=True)
class ResistanceIntegrity:
    status: str
    message: str | None
    net_id: int
    name: str
    short_threshold_ohm: float
    max_resistors: int
    detail_limit: int
    incident_resistor_count: int = 0
    internal_resistor_count: int = 0
    terminal_count: int = 0
    terminal_component_count: int = 0
    open_status: str = "not_applicable"
    open_components: tuple[ResistanceComponent, ...] = ()
    island_count: int = 0
    orphan_node_count: int = 0
    short_status: str = "pass"
    cross_net_resistor_count: int = 0
    short_candidate_count: int = 0
    short_candidates: tuple[ShortCandidate, ...] = ()
    inconclusive_boundary_count: int = 0
    negative_resistor_count: int = 0
    nonfinite_resistor_count: int = 0
    zero_resistor_count: int = 0
    self_loop_count: int = 0
    details_truncated: bool = False

    @property
    def components(self) -> tuple[ResistanceComponent, ...]:
        return self.open_components

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _DisjointSet:
    def __init__(self) -> None:
        self.parent: dict[int, int] = {}
        self.size: dict[int, int] = {}

    def add(self, node: int) -> None:
        if node not in self.parent:
            self.parent[node] = node
            self.size[node] = 1

    def find(self, node: int) -> int:
        root = node
        while self.parent[root] != root:
            root = self.parent[root]
        while node != root:
            parent = self.parent[node]
            self.parent[node] = root
            node = parent
        return root

    def union(self, left: int, right: int) -> None:
        self.add(left)
        self.add(right)
        left, right = self.find(left), self.find(right)
        if left == right:
            return
        if self.size[left] < self.size[right]:
            left, right = right, left
        self.parent[right] = left
        self.size[left] += self.size.pop(right)


__all__ = [
    "ResistanceComponent", "ResistanceIntegrity", "ShortCandidate",
]
