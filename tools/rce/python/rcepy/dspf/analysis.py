"""Net summaries and bounded resistance-path analysis."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from ._graph import connected_component_count, shortest_resistance_path
from .repository import DspfRepository, NetSelector


_MAX_COMPONENT_RESISTORS = 1_000_000


@dataclass(frozen=True)
class NetSummary:
    net_id: int
    name: str
    node_count: int
    resistor_count: int
    ground_capacitor_count: int
    coupling_capacitor_count: int
    total_resistance: float
    ground_capacitance: float
    coupling_capacitance: float
    max_resistance: float | None
    max_capacitance: float | None
    dangling_node_count: int
    connected_components: int | None
    connected_components_status: str
    connected_components_message: str | None
    diagnostic_count: int
    declared_capacitance: float | None
    computed_capacitance: float
    capacitance_difference: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResistancePath:
    status: str
    total_resistance: float | None
    node_ids: list[int]
    nodes: list[str]
    resistor_ids: list[int]
    resistors: list[str]
    visited_nodes: int
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "total_resistance": self.total_resistance,
            "node_ids": self.node_ids,
            "nodes": self.nodes,
            "resistor_ids": self.resistor_ids,
            "resistors": self.resistors,
            "visited_nodes": self.visited_nodes,
            "message": self.message,
        }


def summarize_net(repository: DspfRepository, net: NetSelector) -> NetSummary:
    item = repository.get_net(net)
    net_id = int(item["id"])
    resistor = repository.summarize_resistors(net_id)
    capacitor = repository.summarize_capacitors(net_id)
    node_count = repository.count_nodes(net_id)
    components, component_status, component_message = _component_summary(
        repository, net_id, node_count,
    )
    dangling = repository.count_dangling_nodes(net_id)
    ground_cap = capacitor.ground_total
    coupling_cap = capacitor.coupling_total
    computed = ground_cap + coupling_cap
    declared = item["declared_cap"]
    return NetSummary(
        net_id=net_id,
        name=str(item["name"]),
        node_count=node_count,
        resistor_count=resistor.count,
        ground_capacitor_count=capacitor.ground_count,
        coupling_capacitor_count=capacitor.coupling_count,
        total_resistance=resistor.total,
        ground_capacitance=ground_cap,
        coupling_capacitance=coupling_cap,
        max_resistance=resistor.maximum,
        max_capacitance=capacitor.maximum,
        dangling_node_count=dangling,
        connected_components=components,
        connected_components_status=component_status,
        connected_components_message=component_message,
        diagnostic_count=repository.count_diagnostics(net_id),
        declared_capacitance=None if declared is None else float(declared),
        computed_capacitance=computed,
        capacitance_difference=None if declared is None else computed - float(declared),
    )


def find_resistance_path(
    repository: DspfRepository,
    net: NetSelector,
    start: NetSelector,
    end: NetSelector,
    *,
    max_resistors: int = 1_000_000,
) -> ResistancePath:
    if (
        not isinstance(max_resistors, int)
        or isinstance(max_resistors, bool)
        or max_resistors < 1
    ):
        raise ValueError("max_resistors must be a positive integer")
    net_id = int(repository.get_net(net)["id"])
    resistor_count = repository._path_resistor_count(net_id)
    if resistor_count > max_resistors:
        return _empty_path(
            "limit_exceeded",
            f"Net has {resistor_count} resistors; path limit is {max_resistors}",
        )
    try:
        start_node = repository.get_node(net_id, start)
        end_node = repository.get_node(net_id, end)
    except KeyError as error:
        return _empty_path("node_not_found", str(error))
    start_id = int(start_node["id"])
    end_id = int(end_node["id"])
    if start_id == end_id:
        return ResistancePath(
            "ok", 0.0, [start_id], [str(start_node["name"])], [], [], 1,
        )
    path = shortest_resistance_path(repository, net_id, start_id, end_id)
    if path.total_resistance is None:
        return _empty_path(
            "no_path", "No non-negative resistance path connects the nodes",
            path.visited_nodes,
        )
    return ResistancePath(
        "ok", path.total_resistance, path.node_ids,
        repository._node_names(path.node_ids), path.resistor_ids,
        repository._resistor_names(path.resistor_ids), path.visited_nodes,
    )


def _component_summary(
    repository: DspfRepository, net_id: int, node_count: int,
) -> tuple[int | None, str, str | None]:
    edge_count = repository._path_resistor_count(
        net_id, stop_after=_MAX_COMPONENT_RESISTORS, non_negative=True,
    )
    if edge_count > _MAX_COMPONENT_RESISTORS:
        return (
            None,
            "limit_exceeded",
            f"Not computed: net exceeds {_MAX_COMPONENT_RESISTORS:,} graph resistors",
        )
    return connected_component_count(repository, net_id, node_count), "ok", None


def _empty_path(status: str, message: str, visited: int = 0) -> ResistancePath:
    return ResistancePath(status, None, [], [], [], [], visited, message)
