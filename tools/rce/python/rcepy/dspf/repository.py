"""Read-only, paged access to a completed DSPF SQLite index."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence, Union

from ._sqlite import sqlite3
from .schema import connect_database, get_metadata, validate_database


NetSelector = Union[int, str]
_NET_SORTS = {"id", "name", "declared_cap", "line_start"}
_DETAIL_SORTS = {"id", "name", "value", "source_line"}
_NODE_SORTS = {"id", "name", "kind", "source_line"}
_DIAGNOSTIC_SORTS = {"id", "severity", "code", "source_line"}
_CAPACITOR_VISIBILITY_CTE = (
    "WITH visible_capacitor(id) AS ("
    "SELECT declared_capacitor.id FROM capacitor AS declared_capacitor "
    "INDEXED BY idx_capacitor_net WHERE declared_capacitor.declared_net_id=? "
    "UNION SELECT node1_capacitor.id FROM node AS node1 "
    "INDEXED BY idx_node_net JOIN capacitor AS node1_capacitor "
    "INDEXED BY idx_capacitor_node1 ON node1_capacitor.node1_id=node1.id "
    "WHERE node1.net_id=? "
    "UNION SELECT node2_capacitor.id FROM node AS node2 "
    "INDEXED BY idx_node_net JOIN capacitor AS node2_capacitor "
    "INDEXED BY idx_capacitor_node2 ON node2_capacitor.node2_id=node2.id "
    "WHERE node2.net_id=?"
    ") "
)


@dataclass(frozen=True)
class CapacitorSummary:
    """Aggregated capacitors visible from one declared net."""

    ground_count: int
    coupling_count: int
    ground_total: float
    coupling_total: float
    maximum: float | None


@dataclass(frozen=True)
class ResistorSummary:
    """Aggregated resistors declared on one net."""

    count: int
    total: float
    maximum: float | None


class DspfRepository:
    """Own a read-only connection and expose bounded SQL-level queries."""

    def __init__(self, index_path: str | Path) -> None:
        self.index_path = Path(index_path).expanduser().resolve()
        connection = connect_database(self.index_path, read_only=True)
        try:
            validate_database(connection)
        except BaseException:
            connection.close()
            raise
        self._connection = connection

    def __enter__(self) -> "DspfRepository":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    def info(self) -> dict[str, Any]:
        result = get_metadata(self._connection)
        result["index_path"] = str(self.index_path)
        return result

    def count_nets(
        self, search: str | None = None, *, exact_name: str | None = None,
    ) -> int:
        where, parameters = _net_filter(search, exact_name, "n.name")
        return self._scalar(f"SELECT count(*) FROM net n{where}", parameters)

    def list_nets(
        self, *, offset: int = 0, limit: int = 200, search: str | None = None,
        exact_name: str | None = None, sort_by: str = "name",
        descending: bool = False,
    ) -> list[dict[str, Any]]:
        _page(offset, limit)
        sort = _sort(sort_by, _NET_SORTS, descending, "n")
        where, parameters = _net_filter(search, exact_name, "n.name")
        rows = self._connection.execute(
            "SELECT n.id,n.name,n.declared_cap,n.raw_cap,n.line_start,n.line_end,"
            "s.name AS subcircuit FROM net n JOIN subcircuit s ON s.id=n.subcircuit_id"
            f"{where} ORDER BY {sort},n.id LIMIT ? OFFSET ?",
            (*parameters, limit, offset),
        )
        return _dicts(rows)

    def get_net(self, net: NetSelector) -> dict[str, Any]:
        rows = self._net_rows(net)
        if not rows:
            raise KeyError(f"DSPF net not found: {net!r}")
        if len(rows) > 1:
            raise ValueError(f"DSPF net name is ambiguous: {net!r}")
        return dict(rows[0])

    def count_nodes(self, net: NetSelector) -> int:
        net_id = self._net_id(net)
        return self._scalar("SELECT count(*) FROM node WHERE net_id=?", (net_id,))

    def list_nodes(
        self, net: NetSelector, *, offset: int = 0, limit: int = 200,
        sort_by: str = "name", descending: bool = False,
    ) -> list[dict[str, Any]]:
        _page(offset, limit)
        net_id = self._net_id(net)
        sort = _sort(sort_by, _NODE_SORTS, descending, "n")
        rows = self._connection.execute(
            "SELECT n.id,n.name,n.kind,n.x,n.y,n.layer,n.source_line "
            f"FROM node n WHERE n.net_id=? ORDER BY {sort},n.id LIMIT ? OFFSET ?",
            (net_id, limit, offset),
        )
        return _dicts(rows)

    def get_node(self, net: NetSelector, node: NetSelector) -> dict[str, Any]:
        net_id = self._net_id(net)
        column = "id" if isinstance(node, int) else "name"
        rows = list(self._connection.execute(
            f"SELECT id,name,kind,net_id FROM node WHERE net_id=? AND {column}=?",
            (net_id, node),
        ))
        if not rows:
            raise KeyError(f"DSPF node not found on net: {node!r}")
        return dict(rows[0])

    def count_resistors(self, net: NetSelector) -> int:
        net_id = self._net_id(net)
        return self._scalar(
            "SELECT count(*) FROM resistor INDEXED BY idx_resistor_net "
            "WHERE declared_net_id=?",
            (net_id,),
        )

    def summarize_resistors(self, net: NetSelector) -> ResistorSummary:
        """Return resistance count and totals for one declared net."""
        net_id = self._net_id(net)
        row = self._connection.execute(
            "SELECT count(*) AS count,COALESCE(sum(value),0) AS total,max(value) AS maximum "
            "FROM resistor INDEXED BY idx_resistor_net WHERE declared_net_id=?",
            (net_id,),
        ).fetchone()
        return ResistorSummary(
            count=int(row["count"]),
            total=float(row["total"]),
            maximum=None if row["maximum"] is None else float(row["maximum"]),
        )

    def list_resistors(
        self, net: NetSelector, *, offset: int = 0, limit: int = 200,
        sort_by: str = "id", descending: bool = False,
    ) -> list[dict[str, Any]]:
        _page(offset, limit)
        net_id = self._net_id(net)
        sort = _sort(sort_by, _DETAIL_SORTS, descending, "r")
        rows = self._connection.execute(
            "SELECT r.id,r.name,a.name AS node1,b.name AS node2,r.value,r.raw_value,"
            "r.layer,r.length,r.width,r.source_line,r.declared_net_id "
            "FROM resistor AS r INDEXED BY idx_resistor_net "
            "JOIN node a ON a.id=r.node1_id JOIN node b ON b.id=r.node2_id "
            f"WHERE r.declared_net_id=? ORDER BY {sort},r.id LIMIT ? OFFSET ?",
            (net_id, limit, offset),
        )
        return _dicts(rows)

    def count_capacitors(self, net: NetSelector) -> int:
        net_id = self._net_id(net)
        return self._scalar(
            _CAPACITOR_VISIBILITY_CTE
            + "SELECT count(*) FROM visible_capacitor",
            (net_id, net_id, net_id),
        )

    def summarize_capacitors(self, net: NetSelector) -> CapacitorSummary:
        """Return the ground/coupling capacitance totals visible from ``net``."""
        net_id = self._net_id(net)
        row = self._connection.execute(
            _CAPACITOR_VISIBILITY_CTE
            + "SELECT "
            "COALESCE(sum(CASE WHEN a.kind='ground' OR b.kind='ground' "
            "THEN 1 ELSE 0 END),0) AS ground_count,"
            "COALESCE(sum(CASE WHEN a.kind!='ground' AND b.kind!='ground' "
            "THEN 1 ELSE 0 END),0) AS coupling_count,"
            "COALESCE(sum(CASE WHEN a.kind='ground' OR b.kind='ground' "
            "THEN c.value ELSE 0 END),0) AS ground_total,"
            "COALESCE(sum(CASE WHEN a.kind!='ground' AND b.kind!='ground' "
            "THEN c.value ELSE 0 END),0) AS coupling_total,"
            "max(c.value) AS maximum FROM visible_capacitor AS visible "
            "JOIN capacitor AS c ON c.id=visible.id "
            "JOIN node a ON a.id=c.node1_id JOIN node b ON b.id=c.node2_id",
            (net_id, net_id, net_id),
        ).fetchone()
        return CapacitorSummary(
            ground_count=int(row["ground_count"]),
            coupling_count=int(row["coupling_count"]),
            ground_total=float(row["ground_total"]),
            coupling_total=float(row["coupling_total"]),
            maximum=None if row["maximum"] is None else float(row["maximum"]),
        )

    def list_capacitors(
        self, net: NetSelector, *, offset: int = 0, limit: int = 200,
        sort_by: str = "id", descending: bool = False,
    ) -> list[dict[str, Any]]:
        _page(offset, limit)
        net_id = self._net_id(net)
        sort = _sort(sort_by, _DETAIL_SORTS, descending, "c")
        rows = self._connection.execute(
            _CAPACITOR_VISIBILITY_CTE
            + "SELECT c.id,c.name,a.name AS node1,b.name AS node2,a.net_id AS node1_net_id,"
            "b.net_id AS node2_net_id,c.value,c.raw_value,c.layer,c.source_line,c.declared_net_id "
            "FROM visible_capacitor AS visible JOIN capacitor AS c ON c.id=visible.id "
            "JOIN node a ON a.id=c.node1_id JOIN node b ON b.id=c.node2_id "
            f"ORDER BY {sort},c.id LIMIT ? OFFSET ?",
            (net_id, net_id, net_id, limit, offset),
        )
        return _dicts(rows)

    def count_diagnostics(self, net: NetSelector | None = None) -> int:
        if net is None:
            return self._scalar("SELECT count(*) FROM diagnostic", ())
        return self._scalar("SELECT count(*) FROM diagnostic WHERE net_id=?", (self._net_id(net),))

    def count_dangling_nodes(self, net: NetSelector) -> int:
        """Count non-terminal nodes without a valid in-net resistor edge."""
        net_id = self._net_id(net)
        return self._scalar(
            "WITH valid_resistor(node1_id,node2_id) AS ("
            "SELECT r.node1_id,r.node2_id FROM resistor AS r "
            "INDEXED BY idx_resistor_net "
            "JOIN node AS a ON a.id=r.node1_id JOIN node AS b ON b.id=r.node2_id "
            "WHERE r.declared_net_id=? AND r.value>=0 AND a.net_id=? AND b.net_id=?"
            "),dangling_node(id) AS ("
            "SELECT n.id FROM node AS n INDEXED BY idx_node_net "
            "WHERE n.net_id=? AND n.kind NOT IN ('ground','port','instance_pin') "
            "EXCEPT SELECT node1_id FROM valid_resistor "
            "EXCEPT SELECT node2_id FROM valid_resistor"
            ") SELECT count(*) FROM dangling_node",
            (net_id, net_id, net_id, net_id),
        )

    def list_diagnostics(
        self, net: NetSelector | None = None, *, offset: int = 0, limit: int = 200,
        sort_by: str = "source_line", descending: bool = False,
    ) -> list[dict[str, Any]]:
        _page(offset, limit)
        sort = _sort(sort_by, _DIAGNOSTIC_SORTS, descending, "d")
        where = "" if net is None else " WHERE d.net_id=?"
        parameters: tuple[Any, ...] = () if net is None else (self._net_id(net),)
        rows = self._connection.execute(
            "SELECT d.id,d.severity,d.code,d.message,d.source_line,d.line_end,d.net_id,d.raw_summary "
            f"FROM diagnostic d{where} ORDER BY {sort},d.id LIMIT ? OFFSET ?",
            (*parameters, limit, offset),
        )
        return _dicts(rows)

    def net_summary(self, net: NetSelector) -> Any:
        from .analysis import summarize_net

        return summarize_net(self, net)

    def net_detail(self, net: NetSelector) -> dict[str, Any]:
        return self.net_summary(net).to_dict()

    def resistance_path(
        self, net: NetSelector, start: NetSelector, end: NetSelector,
        *, max_resistors: int = 1_000_000,
    ) -> Any:
        from .analysis import find_resistance_path

        return find_resistance_path(self, net, start, end, max_resistors=max_resistors)

    def rc_network(
        self, net: NetSelector, *, max_nodes: int = 20_000,
        max_elements: int = 20_000, include_capacitors: bool = True,
    ) -> Any:
        from .network import load_rc_network

        return load_rc_network(
            self, net, max_nodes=max_nodes, max_elements=max_elements,
            include_capacitors=include_capacitors,
        )

    def resistance_integrity(
        self, net: NetSelector, *, short_threshold_ohm: float = 0.0,
        max_resistors: int = 1_000_000, detail_limit: int = 200,
    ) -> Any:
        from .integrity import analyze_resistance_integrity

        return analyze_resistance_integrity(
            self, net, short_threshold_ohm=short_threshold_ohm,
            max_resistors=max_resistors, detail_limit=detail_limit,
        )

    def _net_rows(self, net: NetSelector) -> list[sqlite3.Row]:
        column = "n.id" if isinstance(net, int) else "n.name"
        return list(self._connection.execute(
            "SELECT n.id,n.name,n.subcircuit_id,n.declared_cap,n.raw_cap,n.line_start,n.line_end,"
            "s.name AS subcircuit FROM net n JOIN subcircuit s ON s.id=n.subcircuit_id "
            f"WHERE {column}=? ORDER BY n.id LIMIT 2", (net,),
        ))

    def _net_id(self, net: NetSelector) -> int:
        return int(self.get_net(net)["id"])

    def _scalar(self, statement: str, parameters: tuple[Any, ...]) -> int:
        return int(self._connection.execute(statement, parameters).fetchone()[0])

    def _path_resistor_count(
        self, net_id: int, *, stop_after: int | None = None,
        non_negative: bool = False,
    ) -> int:
        value_filter = " AND r.value>=0" if non_negative else ""
        if stop_after is not None:
            return self._scalar(
                "SELECT count(*) FROM (SELECT 1 FROM resistor r "
                "JOIN node a ON a.id=r.node1_id JOIN node b ON b.id=r.node2_id "
                "WHERE r.declared_net_id=? AND a.net_id=? AND b.net_id=?"
                f"{value_filter} LIMIT ?)",
                (net_id, net_id, net_id, stop_after + 1),
            )
        return self._scalar(
            "SELECT count(*) FROM resistor r JOIN node a ON a.id=r.node1_id "
            "JOIN node b ON b.id=r.node2_id WHERE r.declared_net_id=? "
            f"AND a.net_id=? AND b.net_id=?{value_filter}",
            (net_id, net_id, net_id),
        )

    def _path_edges(self, net_id: int) -> Iterator[sqlite3.Row]:
        return iter(self._connection.execute(
            "SELECT r.id,r.node1_id,r.node2_id,r.value FROM resistor r "
            "JOIN node a ON a.id=r.node1_id "
            "JOIN node b ON b.id=r.node2_id WHERE r.declared_net_id=? AND r.value>=0 "
            "AND a.net_id=? AND b.net_id=? ORDER BY r.id", (net_id, net_id, net_id),
        ))

    def _component_edges(self, net_id: int) -> Iterator[sqlite3.Row]:
        cursor = self._connection.execute(
            "SELECT r.node1_id,r.node2_id FROM resistor r "
            "JOIN node a ON a.id=r.node1_id JOIN node b ON b.id=r.node2_id "
            "WHERE r.declared_net_id=? AND r.value>=0 AND a.net_id=? AND b.net_id=?",
            (net_id, net_id, net_id),
        )
        yield from cursor

    def _node_names(self, node_ids: Sequence[int]) -> list[str]:
        return self._ordered_names("node", node_ids)

    def _resistor_names(self, resistor_ids: Sequence[int]) -> list[str]:
        return self._ordered_names("resistor", resistor_ids)

    def _ordered_names(self, table: str, identifiers: Sequence[int]) -> list[str]:
        result: list[str] = []
        for start in range(0, len(identifiers), 900):
            chunk = identifiers[start:start + 900]
            placeholders = ",".join("?" for _ in chunk)
            names = {
                int(row["id"]): str(row["name"])
                for row in self._connection.execute(
                    f"SELECT id,name FROM {table} WHERE id IN ({placeholders})",
                    tuple(chunk),
                )
            }
            result.extend(names[identifier] for identifier in chunk)
        return result


def _page(offset: int, limit: int) -> None:
    integers = (
        isinstance(offset, int) and not isinstance(offset, bool)
        and isinstance(limit, int) and not isinstance(limit, bool)
    )
    if not integers or offset < 0 or limit < 1 or limit > 10_000:
        raise ValueError(
            "Pagination requires integer offset >= 0 and 1 <= limit <= 10000"
        )


def _sort(name: str, allowed: set[str], descending: bool, alias: str) -> str:
    if name not in allowed:
        raise ValueError(f"Unsupported DSPF sort column: {name}")
    return f"{alias}.{name} {'DESC' if descending else 'ASC'}"


def _search_clause(search: str | None, column: str) -> tuple[str, tuple[str, ...]]:
    if not search:
        return "", ()
    escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f" WHERE {column} LIKE ? ESCAPE '\\'", (f"%{escaped}%",)


def _net_filter(
    search: str | None, exact_name: str | None, column: str,
) -> tuple[str, tuple[str, ...]]:
    if search and exact_name is not None:
        raise ValueError("search and exact_name are mutually exclusive")
    if exact_name is not None:
        return f" WHERE {column}=?", (exact_name,)
    return _search_clause(search, column)


def _dicts(rows: Iterator[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]
