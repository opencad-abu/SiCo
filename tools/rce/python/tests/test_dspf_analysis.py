from __future__ import annotations

import ast
from pathlib import Path

import pytest

from rcepy.dspf import DspfRepository, build_index


FIXTURES = Path(__file__).with_name("dspf_fixtures")


def _repository(tmp_path: Path, fixture: str) -> DspfRepository:
    result = build_index(FIXTURES / fixture, cache_dir=tmp_path)
    return DspfRepository(result.index_path)


def test_net_summary_counts_coupling_once_per_incident_net(tmp_path: Path) -> None:
    with _repository(tmp_path, "qrc_1_0.dspf") as repository:
        incoming = repository.net_summary("IN")
        outgoing = repository.net_summary("OUT")

    assert incoming.resistor_count == 1
    assert incoming.ground_capacitor_count == 1
    assert incoming.coupling_capacitor_count == 1
    assert incoming.computed_capacitance == pytest.approx(0.3e-12)
    assert incoming.capacitance_difference == pytest.approx(0.0, abs=1e-27)
    assert outgoing.computed_capacitance == pytest.approx(0.25e-12)


def test_repository_exposes_structured_capacitor_summary(tmp_path: Path) -> None:
    with _repository(tmp_path, "qrc_1_0.dspf") as repository:
        incoming = repository.summarize_capacitors("IN")
        outgoing = repository.summarize_capacitors("OUT")

    assert incoming.ground_count == 1
    assert incoming.coupling_count == 1
    assert incoming.ground_total == pytest.approx(0.1e-12)
    assert incoming.coupling_total == pytest.approx(0.2e-12)
    assert incoming.maximum == pytest.approx(0.2e-12)
    assert outgoing.coupling_total == pytest.approx(0.2e-12)


def test_repository_exposes_structured_resistor_summary(tmp_path: Path) -> None:
    with _repository(tmp_path, "qrc_1_0.dspf") as repository:
        summary = repository.summarize_resistors("IN")

    assert summary.count == 1
    assert summary.total == pytest.approx(1000.0)
    assert summary.maximum == pytest.approx(1000.0)


def test_analysis_does_not_import_repository_private_symbols() -> None:
    source = Path(__file__).parents[1] / "rcepy" / "dspf" / "analysis.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))

    private_imports = [
        alias.name
        for node in ast.walk(tree)
        if (
            isinstance(node, ast.ImportFrom)
            and node.level == 1
            and node.module == "repository"
        )
        for alias in node.names
        if alias.name.startswith("_")
    ]
    assert private_imports == []

    assert not any(
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "repository"
        and node.attr == "_connection"
        for node in ast.walk(tree)
    )


def test_net_summary_capacitor_query_uses_visibility_indexes(
    tmp_path: Path,
) -> None:
    with _repository(tmp_path, "qrc_1_0.dspf") as repository:
        statements: list[str] = []
        repository._connection.set_trace_callback(statements.append)
        try:
            repository.net_summary("IN")
        finally:
            repository._connection.set_trace_callback(None)
        query = next(item for item in statements if "visible_capacitor" in item)
        details = [
            str(row["detail"])
            for row in repository._connection.execute("EXPLAIN QUERY PLAN " + query)
        ]

    plan = "\n".join(details)
    assert "idx_capacitor_net" in plan
    assert "idx_capacitor_node1" in plan
    assert "idx_capacitor_node2" in plan
    assert not any(
        detail.startswith("SCAN") and "TABLE capacitor" in detail
        for detail in details
    )


def test_dijkstra_returns_nodes_elements_and_total_resistance(tmp_path: Path) -> None:
    with _repository(tmp_path, "starrc_1_3.spf") as repository:
        path = repository.resistance_path("B1", "B1", "B1:2")

    assert path.status == "ok"
    assert path.total_resistance == 10.0
    assert path.nodes == ["B1", "B1:2"]
    assert path.resistors == ["R_b1"]


def test_path_reports_disconnected_and_ignores_negative_resistance(tmp_path: Path) -> None:
    with _repository(tmp_path, "qrc_1_0.dspf") as repository:
        disconnected = repository.resistance_path("IN", "IN", "XBUF:A")
    with _repository(tmp_path / "cal", "calibre_1_5_errors.dspf") as repository:
        negative = repository.resistance_path("A<0>", "A<0>", "A<0>:1")

    assert disconnected.status == "no_path"
    assert negative.status == "no_path"


def test_dangling_nodes_ignore_negative_and_cross_net_resistors(
    tmp_path: Path,
) -> None:
    with _repository(tmp_path, "calibre_1_5_errors.dspf") as repository:
        summary = repository.net_summary("A<0>")

    assert summary.connected_components == 2
    assert summary.dangling_node_count == 1


def test_dangling_query_is_indexed_and_scales_without_correlated_scan(
    tmp_path: Path,
) -> None:
    node_count = 2_000
    source = tmp_path / "large-net.dspf"
    records = [
        "*|DSPF 1.0", ".SUBCKT top A B 0", "*|GROUND_NET 0",
        "*|NET A 0", "*|P (A B 0 0 0)",
    ]
    records.extend(
        f"*|S (A:{index} {index} 0)" for index in range(1, node_count + 1)
    )
    records.append("R0 A A:1 1")
    records.extend(
        f"R{index} A:{index} A:{index + 1} 1"
        for index in range(1, node_count - 1)
    )
    records.extend([
        "Rcross A:2000 B:1 1",
        "*|NET B 0",
        "*|P (B B 0 0 0)",
        "*|S (B:1 0 0)",
    ])
    records.append(".ENDS top")
    source.write_text("\n".join(records) + "\n", encoding="ascii")
    result = build_index(source, cache_dir=tmp_path / "cache")

    with DspfRepository(result.index_path) as repository:
        statements: list[str] = []
        repository._connection.set_trace_callback(statements.append)
        try:
            summary = repository.net_summary("A")
        finally:
            repository._connection.set_trace_callback(None)
        query = next(item for item in statements if "dangling_node" in item)
        details = [
            str(row["detail"])
            for row in repository._connection.execute("EXPLAIN QUERY PLAN " + query)
        ]

    assert summary.dangling_node_count == 1
    assert summary.connected_components == 2
    plan = "\n".join(details)
    assert "idx_node_net" in plan
    assert "idx_resistor_net" in plan
    assert "CORRELATED" not in plan


def test_cross_net_resistor_is_excluded_from_path_limit_and_graph(tmp_path: Path) -> None:
    with _repository(tmp_path, "calibre_1_5_errors.dspf") as repository:
        path = repository.resistance_path("Z", "Z", "Z:1", max_resistors=1)
        diagnostics = repository.list_diagnostics()

    assert path.status == "ok"
    assert path.total_resistance == 1.0
    assert path.resistors == ["Rz"]
    assert any(item["code"] == "cross_net_resistor" for item in diagnostics)


def test_path_limit_and_missing_node_have_explicit_status(tmp_path: Path) -> None:
    source = tmp_path / "path_limit.dspf"
    source.write_text(
        "*|DSPF 1.0\n.SUBCKT top A 0\n*|GROUND_NET 0\n"
        "*|NET A 0\n*|P (A B 0 0 0)\n*|S (A:1 1 0)\n*|S (A:2 2 0)\n"
        "R1 A A:1 1\nR2 A:1 A:2 2\n.ENDS\n",
        encoding="utf-8",
    )
    result = build_index(source, cache_dir=tmp_path / "cache")
    with DspfRepository(result.index_path) as repository:
        limited = repository.resistance_path("A", "A", "A:2", max_resistors=1)
        missing = repository.resistance_path("A", "A", "missing")
        with pytest.raises(ValueError, match="positive"):
            repository.resistance_path("A", "A", "A:2", max_resistors=0)
        for invalid in (True, 1.0, float("nan"), float("inf"), "1", None):
            with pytest.raises(ValueError, match="positive integer"):
                repository.resistance_path(
                    "A", "A", "A:2", max_resistors=invalid,  # type: ignore[arg-type]
                )

    assert limited.status == "limit_exceeded"
    assert missing.status == "node_not_found"
