"""Bounded integrity checks for one DSPF resistance network."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Iterable

from .integrity_models import (
    _DisjointSet,
    ResistanceComponent,
    ResistanceIntegrity,
    ShortCandidate,
)

if TYPE_CHECKING:
    from .repository import DspfRepository, NetSelector


_MAX_TERMINALS = 1_000_000
_COMPONENT_NAME_LIMIT = 32
_INCIDENT_CTE = (
    "WITH incident_resistor(id) AS ("
    "SELECT r.id FROM node AS n INDEXED BY idx_node_net "
    "JOIN resistor AS r INDEXED BY idx_resistor_node1 ON r.node1_id=n.id "
    "WHERE n.net_id=? UNION "
    "SELECT r.id FROM node AS n INDEXED BY idx_node_net "
    "JOIN resistor AS r INDEXED BY idx_resistor_node2 ON r.node2_id=n.id "
    "WHERE n.net_id=?"
    ") "
)


def analyze_resistance_integrity(
    repository: DspfRepository,
    net: NetSelector,
    *,
    short_threshold_ohm: float = 0.0,
    max_resistors: int = 1_000_000,
    detail_limit: int = 200,
) -> ResistanceIntegrity:
    """Classify terminal opens and cross-net/ground resistive shorts."""
    threshold = _threshold(short_threshold_ohm)
    _integer_limit("max_resistors", max_resistors, allow_zero=False)
    _integer_limit("detail_limit", detail_limit, allow_zero=True)
    item = repository.get_net(net)
    net_id, name = int(item["id"]), str(item["name"])
    db = repository._connection
    incident_count = int(db.execute(
        _INCIDENT_CTE
        + "SELECT count(*) FROM (SELECT id FROM incident_resistor LIMIT ?)",
        (net_id, net_id, max_resistors + 1),
    ).fetchone()[0])
    base = dict(
        net_id=net_id, name=name, short_threshold_ohm=threshold,
        max_resistors=max_resistors, detail_limit=detail_limit,
        incident_resistor_count=incident_count,
    )
    if incident_count > max_resistors:
        return ResistanceIntegrity(
            status="limit_exceeded",
            message=(
                f"Net has more than {max_resistors:,} incident resistors; "
                "integrity check was not computed"
            ),
            open_status="limit_exceeded", short_status="limit_exceeded", **base,
        )

    terminal_count = int(db.execute(
        "SELECT count(*) FROM node INDEXED BY idx_node_net "
        "WHERE net_id=? AND kind IN ('port','instance_pin')", (net_id,),
    ).fetchone()[0])
    if terminal_count > _MAX_TERMINALS:
        return ResistanceIntegrity(
            status="limit_exceeded",
            message=(
                f"Net has more than {_MAX_TERMINALS:,} terminals; "
                "integrity check was not computed"
            ),
            terminal_count=terminal_count, open_status="limit_exceeded",
            short_status="limit_exceeded", **base,
        )

    rows = db.execute(
        _INCIDENT_CTE
        + "SELECT r.id,r.name,r.declared_net_id,r.node1_id,r.node2_id,r.value,"
        "r.source_line,a.name AS node1_name,a.net_id AS node1_net_id,"
        "a.kind AS node1_kind,na.name AS node1_net_name,b.name AS node2_name,"
        "b.net_id AS node2_net_id,b.kind AS node2_kind,nb.name AS node2_net_name "
        "FROM incident_resistor AS i JOIN resistor AS r ON r.id=i.id "
        "JOIN node AS a ON a.id=r.node1_id JOIN node AS b ON b.id=r.node2_id "
        "LEFT JOIN net AS na ON na.id=a.net_id LEFT JOIN net AS nb ON nb.id=b.net_id "
        "ORDER BY r.id", (net_id, net_id),
    )
    terminals = list(db.execute(
        "SELECT id,name FROM node INDEXED BY idx_node_net "
        "WHERE net_id=? AND kind IN ('port','instance_pin') ORDER BY id", (net_id,),
    ))
    return _classify(
        rows, terminals, repository, base, net_id, threshold, detail_limit,
    )


def _classify(
    rows: Iterable[Any], terminals: list[Any], repository: DspfRepository,
    base: dict[str, Any], net_id: int, threshold: float, detail_limit: int,
) -> ResistanceIntegrity:
    graph = _DisjointSet()
    valid_nodes: set[int] = set()
    internal_count = negative = nonfinite = zero = self_loops = 0
    boundary_count = inconclusive = candidate_count = 0
    candidates: list[ShortCandidate] = []
    for row in rows:
        value = float(row["value"])
        finite = math.isfinite(value)
        negative += int(finite and value < 0)
        nonfinite += int(not finite)
        zero += int(finite and value == 0)
        loop = int(row["node1_id"]) == int(row["node2_id"])
        self_loops += int(loop)
        internal = (
            row["declared_net_id"] == net_id
            and row["node1_net_id"] == net_id
            and row["node2_net_id"] == net_id
        )
        if internal:
            internal_count += 1
            if finite and value >= 0 and not loop:
                left, right = int(row["node1_id"]), int(row["node2_id"])
                graph.union(left, right)
                valid_nodes.update((left, right))
            continue
        boundary = _boundary_reason(row, net_id)
        if boundary == "inconclusive":
            inconclusive += int(not loop)
        elif boundary is not None and not loop:
            boundary_count += 1
            if finite and 0 <= value <= threshold:
                candidate_count += 1
                if len(candidates) < detail_limit:
                    candidates.append(_candidate(row, value, boundary))

    terminal_names = {int(row["id"]): str(row["name"]) for row in terminals}
    for node_id in terminal_names:
        graph.add(node_id)
    groups: dict[int, list[int]] = {}
    for node_id in terminal_names:
        groups.setdefault(graph.find(node_id), []).append(node_id)
    terminal_roots = set(groups)
    island_count = len({graph.find(node) for node in valid_nodes} - terminal_roots)
    nonterminal_count = int(repository._connection.execute(
        "SELECT count(*) FROM node INDEXED BY idx_node_net WHERE net_id=? "
        "AND kind NOT IN ('ground','port','instance_pin')", (net_id,),
    ).fetchone()[0])
    orphan_count = max(
        0, nonterminal_count - len(valid_nodes.difference(terminal_names)),
    )
    component_values, names_truncated = _components(
        graph, groups, terminal_names, detail_limit,
    )
    terminal_count, component_count = len(terminals), len(groups)
    open_status = (
        "not_applicable" if terminal_count < 2
        else "open" if component_count > 1 else "pass"
    )
    short_status = (
        "short" if candidate_count else "inconclusive" if inconclusive else "pass"
    )
    truncated = (
        candidate_count > len(candidates)
        or component_count > len(component_values)
        or names_truncated
    )
    return ResistanceIntegrity(
        status="ok", message=None, internal_resistor_count=internal_count,
        terminal_count=terminal_count, terminal_component_count=component_count,
        open_status=open_status, open_components=component_values,
        island_count=island_count, orphan_node_count=orphan_count,
        short_status=short_status, cross_net_resistor_count=boundary_count,
        short_candidate_count=candidate_count, short_candidates=tuple(candidates),
        inconclusive_boundary_count=inconclusive,
        negative_resistor_count=negative, nonfinite_resistor_count=nonfinite,
        zero_resistor_count=zero, self_loop_count=self_loops,
        details_truncated=truncated, **base,
    )


def _components(
    graph: _DisjointSet, groups: dict[int, list[int]], names: dict[int, str],
    detail_limit: int,
) -> tuple[tuple[ResistanceComponent, ...], bool]:
    result: list[ResistanceComponent] = []
    names_truncated = False
    ordered = sorted(groups.items(), key=lambda item: tuple(names[node] for node in item[1]))
    for root, node_ids in ordered[:detail_limit]:
        shown = node_ids[:_COMPONENT_NAME_LIMIT]
        names_truncated |= len(shown) < len(node_ids)
        result.append(ResistanceComponent(
            node_count=graph.size[graph.find(root)], terminal_count=len(node_ids),
            terminal_node_ids=tuple(shown),
            terminal_names=tuple(names[node] for node in shown),
        ))
    return tuple(result), names_truncated


def _boundary_reason(row: Any, net_id: int) -> str | None:
    for prefix, other in (("node1", "node2"), ("node2", "node1")):
        if row[f"{prefix}_net_id"] != net_id:
            continue
        if row[f"{other}_kind"] == "ground":
            return "ground_bridge"
        owner = row[f"{other}_net_id"]
        if owner == net_id:
            return None
        return "inconclusive" if owner is None else "cross_net_bridge"
    return None


def _candidate(row: Any, value: float, reason: str) -> ShortCandidate:
    def owner(prefix: str) -> str | None:
        if row[f"{prefix}_kind"] == "ground":
            return "ground"
        value = row[f"{prefix}_net_name"]
        return None if value is None else str(value)

    return ShortCandidate(
        resistor_id=int(row["id"]), name=str(row["name"]), value=value,
        node1_id=int(row["node1_id"]), node1_name=str(row["node1_name"]),
        node1_net_name=owner("node1"), node1_kind=str(row["node1_kind"]),
        node2_id=int(row["node2_id"]), node2_name=str(row["node2_name"]),
        node2_net_name=owner("node2"), node2_kind=str(row["node2_kind"]),
        source_line=int(row["source_line"]), reason=reason,
    )


def _threshold(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("short_threshold_ohm must be a finite non-negative number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError("short_threshold_ohm must be a finite non-negative number")
    return result


def _integer_limit(name: str, value: int, *, allow_zero: bool) -> None:
    minimum = 0 if allow_zero else 1
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{name} must be a {qualifier} integer")


__all__ = [
    "ResistanceComponent", "ResistanceIntegrity", "ShortCandidate",
    "analyze_resistance_integrity",
]
