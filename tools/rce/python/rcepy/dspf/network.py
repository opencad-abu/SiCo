"""Bounded data contract for displaying one DSPF RC network."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import TYPE_CHECKING, Any, Iterable

from ._sqlite import sqlite3

if TYPE_CHECKING:
    from .repository import DspfRepository, NetSelector


_VISIBLE_CAPACITOR_CTE = (
    "WITH visible_capacitor(id) AS ("
    "SELECT c.id FROM capacitor AS c INDEXED BY idx_capacitor_net "
    "WHERE c.declared_net_id=? "
    "UNION ALL SELECT c.id FROM node AS a INDEXED BY idx_node_net "
    "JOIN capacitor AS c INDEXED BY idx_capacitor_node1 ON c.node1_id=a.id "
    "WHERE a.net_id=? AND c.declared_net_id<>? "
    "UNION ALL SELECT c.id FROM node AS b INDEXED BY idx_node_net "
    "JOIN capacitor AS c INDEXED BY idx_capacitor_node2 ON c.node2_id=b.id "
    "JOIN node AS a ON a.id=c.node1_id "
    "WHERE b.net_id=? AND c.declared_net_id<>? AND a.net_id IS NOT ?"
    ") "
)
_NODE_COLUMNS = (
    "n.id,n.name,n.net_id,o.name AS owner_net_name,n.kind,n.x,n.y,n.layer"
)
_FINITE_COORDINATE = (
    "n.kind!='ground' "
    "AND n.x BETWEEN -1.7976931348623157e308 AND 1.7976931348623157e308 "
    "AND n.y BETWEEN -1.7976931348623157e308 AND 1.7976931348623157e308"
)


@dataclass(frozen=True)
class RcNetworkNode:
    id: int
    name: str
    net_id: int | None
    owner_net_name: str | None
    kind: str
    x: float | None
    y: float | None
    layer: str | None
    external: bool
    ground_capacitance: float
    ground_capacitor_count: int
    coupling_capacitance: float
    coupling_capacitor_count: int


@dataclass(frozen=True)
class RcNetworkResistor:
    id: int
    name: str
    node1_id: int
    node2_id: int
    value: float
    raw_value: str
    layer: str | None
    length: float | None
    width: float | None
    source_line: int
    cross_net: bool


@dataclass(frozen=True)
class RcNetworkCapacitor:
    id: int
    name: str
    node1_id: int
    node2_id: int
    value: float
    raw_value: str
    layer: str | None
    source_line: int
    kind: str
    local_node_id: int | None


@dataclass(frozen=True)
class RcNetwork:
    status: str
    message: str | None
    net_id: int
    name: str
    subcircuit: str
    owned_node_count: int
    visible_node_count: int | None
    resistor_count: int
    ground_capacitor_count: int
    coupling_capacitor_count: int
    element_count: int
    coordinate_node_count: int
    bbox: tuple[float, float, float, float] | None
    max_nodes: int
    max_elements: int
    nodes: tuple[RcNetworkNode, ...]
    resistors: tuple[RcNetworkResistor, ...]
    capacitors: tuple[RcNetworkCapacitor, ...]


def load_rc_network(
    repository: DspfRepository,
    net: NetSelector,
    *,
    max_nodes: int = 20_000,
    max_elements: int = 20_000,
    include_capacitors: bool = True,
) -> RcNetwork:
    """Load one complete network, or return counts without a partial graph."""
    _positive_limit("max_nodes", max_nodes)
    _positive_limit("max_elements", max_elements)
    item = repository.get_net(net)
    net_id = int(item["id"])
    db = repository._connection
    owned_count, coordinate_count, bbox = _node_statistics(db, net_id)
    resistor_count = int(db.execute(
        "SELECT count(*) FROM resistor INDEXED BY idx_resistor_net "
        "WHERE declared_net_id=?", (net_id,),
    ).fetchone()[0])
    if include_capacitors:
        cap_count = db.execute(
            _VISIBLE_CAPACITOR_CTE
            + "SELECT count(*) AS total,"
            "COALESCE(sum(CASE WHEN a.kind='ground' OR b.kind='ground' "
            "THEN 1 ELSE 0 END),0) AS ground_count "
            "FROM visible_capacitor AS visible "
            "JOIN capacitor AS c ON c.id=visible.id "
            "JOIN node AS a ON a.id=c.node1_id JOIN node AS b ON b.id=c.node2_id",
            _cap_parameters(net_id),
        ).fetchone()
        capacitor_count = int(cap_count["total"])
        ground_count = int(cap_count["ground_count"])
        coupling_count = capacitor_count - ground_count
    else:
        capacitor_count = ground_count = coupling_count = 0
    element_count = resistor_count + capacitor_count
    result = dict(
        net_id=net_id, name=str(item["name"]), subcircuit=str(item["subcircuit"]),
        owned_node_count=owned_count, resistor_count=resistor_count,
        ground_capacitor_count=ground_count,
        coupling_capacitor_count=coupling_count, element_count=element_count,
        coordinate_node_count=coordinate_count, bbox=bbox,
        max_nodes=max_nodes, max_elements=max_elements,
    )
    if element_count > max_elements:
        element_kind = "R/C" if include_capacitors else "R"
        return _limited(
            result, None,
            f"Net has {element_count:,} {element_kind} elements; "
            f"viewer limit is {max_elements:,}",
        )

    resistor_rows = _resistor_rows(db, net_id)
    capacitor_rows = _capacitor_rows(db, net_id) if include_capacitors else []
    visible_ids = _visible_node_ids(
        db, net_id, resistor_rows, capacitor_rows,
        include_all_owned=include_capacitors,
    )
    visible_count = len(visible_ids)
    if visible_count > max_nodes:
        return _limited(
            result, visible_count,
            f"Net has {visible_count:,} visible nodes; viewer limit is {max_nodes:,}",
        )

    capacitors, capacitance = _capacitors(capacitor_rows, net_id)
    node_rows = _node_rows(db, visible_ids)
    nodes = tuple(
        _node(row, net_id, capacitance.get(int(row["id"])))
        for row in sorted(node_rows, key=lambda value: int(value["id"]))
    )
    resistors = tuple(_resistor(row, net_id) for row in resistor_rows)
    return RcNetwork(
        status="ok", message=None, visible_node_count=visible_count,
        nodes=nodes, resistors=resistors, capacitors=capacitors, **result,
    )


def _positive_limit(name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _node_statistics(
    db: sqlite3.Connection, net_id: int,
) -> tuple[int, int, tuple[float, float, float, float] | None]:
    row = db.execute(
        "SELECT count(*) AS owned,"
        f"COALESCE(sum(CASE WHEN {_FINITE_COORDINATE} THEN 1 ELSE 0 END),0) AS located,"
        f"min(CASE WHEN {_FINITE_COORDINATE} THEN n.x END) AS min_x,"
        f"min(CASE WHEN {_FINITE_COORDINATE} THEN n.y END) AS min_y,"
        f"max(CASE WHEN {_FINITE_COORDINATE} THEN n.x END) AS max_x,"
        f"max(CASE WHEN {_FINITE_COORDINATE} THEN n.y END) AS max_y "
        "FROM node AS n INDEXED BY idx_node_net WHERE n.net_id=?", (net_id,),
    ).fetchone()
    located = int(row["located"])
    bbox = None if not located else tuple(
        float(row[key]) for key in ("min_x", "min_y", "max_x", "max_y")
    )
    return int(row["owned"]), located, bbox  # type: ignore[return-value]


def _resistor_rows(db: sqlite3.Connection, net_id: int) -> list[sqlite3.Row]:
    return list(db.execute(
        "SELECT r.id,r.name,r.node1_id,r.node2_id,r.value,r.raw_value,r.layer,"
        "r.length,r.width,r.source_line,a.net_id AS node1_net_id,"
        "b.net_id AS node2_net_id FROM resistor AS r INDEXED BY idx_resistor_net "
        "JOIN node AS a ON a.id=r.node1_id JOIN node AS b ON b.id=r.node2_id "
        "WHERE r.declared_net_id=? ORDER BY r.id", (net_id,),
    ))


def _capacitor_rows(db: sqlite3.Connection, net_id: int) -> list[sqlite3.Row]:
    return list(db.execute(
        _VISIBLE_CAPACITOR_CTE
        + "SELECT c.id,c.name,c.node1_id,c.node2_id,c.value,c.raw_value,c.layer,"
        "c.source_line,a.net_id AS node1_net_id,b.net_id AS node2_net_id,"
        "a.kind AS node1_kind,b.kind AS node2_kind "
        "FROM visible_capacitor AS visible JOIN capacitor AS c ON c.id=visible.id "
        "JOIN node AS a ON a.id=c.node1_id JOIN node AS b ON b.id=c.node2_id "
        "ORDER BY c.id", _cap_parameters(net_id),
    ))


def _cap_parameters(net_id: int) -> tuple[int, ...]:
    return (net_id,) * 6


def _visible_node_ids(
    db: sqlite3.Connection, net_id: int,
    resistor_rows: Iterable[sqlite3.Row],
    capacitor_rows: Iterable[sqlite3.Row], *, include_all_owned: bool,
) -> set[int]:
    rows = (*resistor_rows, *capacitor_rows)
    result = {
        int(row[f"node{side}_id"])
        for row in rows for side in (1, 2)
    }
    if include_all_owned:
        result.update(
            int(row[0]) for row in db.execute(
                "SELECT id FROM node INDEXED BY idx_node_net WHERE net_id=?",
                (net_id,),
            )
        )
        return result
    for table in ("port", "instance_pin"):
        result.update(
            int(row[0]) for row in db.execute(
                f"SELECT node_id FROM {table} WHERE net_id=?", (net_id,),
            )
        )
    return result


def _node_rows(
    db: sqlite3.Connection, identifiers: set[int],
) -> list[sqlite3.Row]:
    rows: list[sqlite3.Row] = []
    identifiers = sorted(identifiers)
    for start in range(0, len(identifiers), 900):
        chunk = identifiers[start:start + 900]
        placeholders = ",".join("?" for _ in chunk)
        rows.extend(db.execute(
            f"SELECT {_NODE_COLUMNS} FROM node AS n "
            f"LEFT JOIN net AS o ON o.id=n.net_id WHERE n.id IN ({placeholders}) "
            "ORDER BY n.id", tuple(chunk),
        ))
    return rows


def _capacitors(
    rows: Iterable[sqlite3.Row], net_id: int,
) -> tuple[tuple[RcNetworkCapacitor, ...], dict[int, list[float | int]]]:
    result = []
    totals: dict[int, list[float | int]] = {}
    for row in rows:
        grounded = row["node1_kind"] == "ground" or row["node2_kind"] == "ground"
        kind = "ground" if grounded else "coupling"
        local = _local_node(row, net_id, allow_external=grounded)
        value = float(row["value"])
        targets = ({local} - {None}) if grounded else {
            int(row["node1_id"]), int(row["node2_id"]),
        }
        slot = 0 if grounded else 2
        for node_id in targets:
            aggregate = totals.setdefault(int(node_id), [0.0, 0, 0.0, 0])
            aggregate[slot] = float(aggregate[slot]) + value
            aggregate[slot + 1] = int(aggregate[slot + 1]) + 1
        result.append(RcNetworkCapacitor(
            id=int(row["id"]), name=str(row["name"]),
            node1_id=int(row["node1_id"]), node2_id=int(row["node2_id"]),
            value=value, raw_value=str(row["raw_value"]),
            layer=_optional_text(row["layer"]), source_line=int(row["source_line"]),
            kind=kind, local_node_id=local,
        ))
    return tuple(result), totals


def _local_node(
    row: sqlite3.Row, net_id: int, *, allow_external: bool,
) -> int | None:
    endpoints = tuple(
        (int(row[f"node{side}_id"]), row[f"node{side}_net_id"], row[f"node{side}_kind"])
        for side in (1, 2)
    )
    for node_id, owner, kind in endpoints:
        if owner == net_id and kind != "ground":
            return node_id
    if allow_external:
        return next((node_id for node_id, _owner, kind in endpoints if kind != "ground"), None)
    return None


def _node(
    row: sqlite3.Row, net_id: int, aggregate: list[float | int] | None,
) -> RcNetworkNode:
    values = aggregate or [0.0, 0, 0.0, 0]
    return RcNetworkNode(
        id=int(row["id"]), name=str(row["name"]),
        net_id=None if row["net_id"] is None else int(row["net_id"]),
        owner_net_name=_optional_text(row["owner_net_name"]), kind=str(row["kind"]),
        x=_coordinate(row["x"]), y=_coordinate(row["y"]),
        layer=_optional_text(row["layer"]), external=row["net_id"] != net_id,
        ground_capacitance=float(values[0]), ground_capacitor_count=int(values[1]),
        coupling_capacitance=float(values[2]), coupling_capacitor_count=int(values[3]),
    )


def _resistor(row: sqlite3.Row, net_id: int) -> RcNetworkResistor:
    return RcNetworkResistor(
        id=int(row["id"]), name=str(row["name"]),
        node1_id=int(row["node1_id"]), node2_id=int(row["node2_id"]),
        value=float(row["value"]), raw_value=str(row["raw_value"]),
        layer=_optional_text(row["layer"]),
        length=_optional_float(row["length"]), width=_optional_float(row["width"]),
        source_line=int(row["source_line"]),
        cross_net=(row["node1_net_id"] != net_id or row["node2_net_id"] != net_id),
    )


def _limited(
    values: dict[str, Any], visible_count: int | None, message: str,
) -> RcNetwork:
    return RcNetwork(
        status="limit_exceeded", message=message, visible_node_count=visible_count,
        nodes=(), resistors=(), capacitors=(), **values,
    )


def _coordinate(value: Any) -> float | None:
    if value is None:
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _optional_text(value: Any) -> str | None:
    return None if value is None else str(value)


__all__ = [
    "RcNetwork", "RcNetworkCapacitor", "RcNetworkNode", "RcNetworkResistor",
    "load_rc_network",
]
