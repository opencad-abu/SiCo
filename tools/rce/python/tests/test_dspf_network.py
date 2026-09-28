from __future__ import annotations

from pathlib import Path

import pytest

from rcepy.dspf import (
    DspfRepository,
    RcNetwork,
    RcNetworkCapacitor,
    RcNetworkNode,
    RcNetworkResistor,
    build_index,
)


FIXTURES = Path(__file__).with_name("dspf_fixtures")


@pytest.fixture
def qrc_repository(tmp_path: Path):
    result = build_index(FIXTURES / "qrc_1_0.dspf", cache_dir=tmp_path)
    with DspfRepository(result.index_path) as repository:
        yield repository


def test_network_returns_complete_typed_qrc_graph(
    qrc_repository: DspfRepository,
) -> None:
    network = qrc_repository.rc_network("IN")

    assert isinstance(network, RcNetwork)
    assert network.status == "ok"
    assert network.message is None
    assert (network.name, network.subcircuit) == ("IN", "qrc_top")
    assert (
        network.owned_node_count,
        network.visible_node_count,
        network.resistor_count,
        network.ground_capacitor_count,
        network.coupling_capacitor_count,
        network.element_count,
    ) == (3, 5, 1, 1, 1, 3)
    assert network.coordinate_node_count == 3
    assert network.bbox == (0.0, 0.0, 2.0, 0.0)
    assert isinstance(network.nodes, tuple)
    assert isinstance(network.resistors, tuple)
    assert isinstance(network.capacitors, tuple)
    assert all(isinstance(item, RcNetworkNode) for item in network.nodes)
    assert all(isinstance(item, RcNetworkResistor) for item in network.resistors)
    assert all(isinstance(item, RcNetworkCapacitor) for item in network.capacitors)
    assert [item.id for item in network.nodes] == sorted(item.id for item in network.nodes)
    assert [item.id for item in network.capacitors] == sorted(
        item.id for item in network.capacitors
    )

    nodes = {item.name: item for item in network.nodes}
    assert nodes["VSS"].external is True
    assert nodes["VSS"].net_id is None
    assert nodes["OUT"].external is True
    assert nodes["OUT"].owner_net_name == "OUT"
    assert nodes["IN:1"].ground_capacitance == pytest.approx(0.2e-12)
    assert nodes["IN:1"].ground_capacitor_count == 1
    assert nodes["IN:1"].coupling_capacitance == pytest.approx(0.1e-12)
    assert nodes["OUT"].coupling_capacitor_count == 1

    resistor = network.resistors[0]
    assert resistor.name == "Rin"
    assert resistor.cross_net is False
    capacitors = {item.name: item for item in network.capacitors}
    assert capacitors["Cin"].kind == "ground"
    assert capacitors["Cin"].local_node_id == nodes["IN:1"].id
    assert capacitors["Cc_io"].kind == "coupling"
    assert capacitors["Cc_io"].local_node_id == nodes["IN:1"].id


def test_network_deduplicates_visibility_and_preserves_edge_cases(
    tmp_path: Path,
) -> None:
    source = tmp_path / "network-semantics.dspf"
    source.write_text(
        "*|DSPF 1.0\n.SUBCKT top A B 0\n*|GROUND_NET 0\n"
        "*|NET B 0\n*|P (B B 0 4 0)\nCdecl A:1 A:2 2P\n"
        "*|NET A 0\n*|S (A:1 -1 0)\n*|S (A:2 0 -2)\n"
        "*|S (A:3 inf 1)\nCleft 0 A:1 1P\nCright A:2 0 3P\n"
        "Czero A:1 0 0\nCnegative A:2 0 -4P\n"
        "Rzero A:1 A:1 0\nRnegative A:1 A:2 -1\n"
        "Rparallel A:1 A:2 2\nRcross A:2 B 4\n.ENDS top\n",
        encoding="ascii",
    )
    result = build_index(source, cache_dir=tmp_path / "cache")

    with DspfRepository(result.index_path) as repository:
        network = repository.rc_network("A")

    assert network.status == "ok"
    assert network.owned_node_count == 4
    assert network.visible_node_count == 6
    assert network.resistor_count == 4
    assert network.ground_capacitor_count == 4
    assert network.coupling_capacitor_count == 1
    assert network.element_count == 9
    assert network.coordinate_node_count == 2
    assert network.bbox == (-1.0, -2.0, 0.0, 0.0)
    assert [item.name for item in network.capacitors].count("Cdecl") == 1

    nodes = {item.name: item for item in network.nodes}
    assert nodes["A:3"].x is None
    assert nodes["A:3"].y == 1.0
    assert nodes["A:1"].ground_capacitance == pytest.approx(1e-12)
    assert nodes["A:1"].ground_capacitor_count == 2
    assert nodes["A:2"].ground_capacitance == pytest.approx(-1e-12)
    assert nodes["A:2"].ground_capacitor_count == 2
    assert nodes["A:1"].coupling_capacitance == pytest.approx(2e-12)
    assert nodes["A:2"].coupling_capacitor_count == 1
    assert nodes["B"].external is True
    assert nodes["B"].owner_net_name == "B"

    capacitors = {item.name: item for item in network.capacitors}
    assert capacitors["Cleft"].local_node_id == nodes["A:1"].id
    assert capacitors["Cright"].local_node_id == nodes["A:2"].id
    assert capacitors["Czero"].value == 0.0
    assert capacitors["Cnegative"].value == pytest.approx(-4e-12)
    assert capacitors["Cdecl"].local_node_id == nodes["A:1"].id
    resistors = {item.name: item for item in network.resistors}
    assert resistors["Rzero"].node1_id == resistors["Rzero"].node2_id
    assert resistors["Rnegative"].value == -1.0
    assert resistors["Rcross"].cross_net is True


def test_network_keeps_missing_coordinates_for_topology_fallback(
    tmp_path: Path,
) -> None:
    result = build_index(FIXTURES / "starrc_1_3.spf", cache_dir=tmp_path)
    with DspfRepository(result.index_path) as repository:
        network = repository.rc_network("B1")

    nodes = {item.name: item for item in network.nodes}
    assert network.status == "ok"
    assert network.owned_node_count == 2
    assert network.coordinate_node_count == 1
    assert network.bbox == (1.0, 0.0, 1.0, 0.0)
    assert nodes["B1:2"].external is False
    assert (nodes["B1:2"].x, nodes["B1:2"].y) == (None, None)


def test_network_limits_return_counts_without_partial_graphs(
    qrc_repository: DspfRepository,
) -> None:
    element_limited = qrc_repository.rc_network("IN", max_elements=2)
    node_limited = qrc_repository.rc_network("IN", max_elements=3, max_nodes=4)
    exact_limit = qrc_repository.rc_network("IN", max_elements=3, max_nodes=5)

    assert element_limited.status == "limit_exceeded"
    assert element_limited.element_count == 3
    assert element_limited.visible_node_count is None
    assert "3 R/C elements" in str(element_limited.message)
    assert element_limited.nodes == element_limited.resistors == element_limited.capacitors == ()

    assert node_limited.status == "limit_exceeded"
    assert node_limited.visible_node_count == 5
    assert "5 visible nodes" in str(node_limited.message)
    assert node_limited.nodes == node_limited.resistors == node_limited.capacitors == ()
    assert exact_limit.status == "ok"


def test_resistance_only_network_excludes_capacitors_from_data_and_limits(
    qrc_repository: DspfRepository,
) -> None:
    network = qrc_repository.rc_network(
        "IN", max_nodes=3, max_elements=1, include_capacitors=False,
    )

    assert network.status == "ok"
    assert network.visible_node_count == 3
    assert network.resistor_count == network.element_count == 1
    assert network.ground_capacitor_count == 0
    assert network.coupling_capacitor_count == 0
    assert network.capacitors == ()
    assert all(node.external is False for node in network.nodes)
    assert all(node.ground_capacitor_count == 0 for node in network.nodes)
    assert all(node.coupling_capacitor_count == 0 for node in network.nodes)


def test_resistance_only_nodes_are_r_endpoints_and_terminals(tmp_path: Path) -> None:
    source = tmp_path / "r-only-nodes.dspf"
    source.write_text(
        "*|DSPF 1.0\n.SUBCKT top A VSS\n*|GROUND_NET VSS\n"
        "*|NET A 0\n*|P (A I 0 0 0)\n*|I (XA:p XA p I 0 1 0)\n"
        "*|S (A:r 2 0)\n*|S (A:c1 3 0)\n*|S (A:c2 4 0)\n"
        "*|S (A:isolated 5 0)\nRpath A A:r 1\nRground A:r VSS 2\n"
        "Conly1 A:c1 VSS 1P\nConly2 A:c2 VSS 2P\n.ENDS top\n",
        encoding="ascii",
    )
    result = build_index(source, cache_dir=tmp_path / "cache")

    with DspfRepository(result.index_path) as repository:
        network = repository.rc_network(
            "A", max_nodes=4, max_elements=2, include_capacitors=False,
        )

    assert network.status == "ok"
    assert network.owned_node_count > network.visible_node_count == 4
    assert {node.name for node in network.nodes} == {"A", "XA:p", "A:r", "VSS"}
    assert next(node for node in network.nodes if node.name == "VSS").kind == "ground"
    assert network.resistor_count == network.element_count == 2
    assert network.capacitors == ()


def test_network_enforces_default_20000_element_and_node_limits(
    tmp_path: Path,
) -> None:
    element_source = tmp_path / "element-limit.dspf"
    element_source.write_text(
        "*|DSPF 1.0\n.SUBCKT top A\n*|NET A 0\n"
        + "\n".join(
            f"R{index} A:{index} A:{index + 1} 1" for index in range(20_001)
        )
        + "\n.ENDS\n",
        encoding="ascii",
    )
    node_source = tmp_path / "node-limit.dspf"
    node_source.write_text(
        "*|DSPF 1.0\n.SUBCKT top A\n*|NET A 0\n"
        + "\n".join(f"*|S (A:{index})" for index in range(20_000))
        + "\n.ENDS\n",
        encoding="ascii",
    )
    element_index = build_index(element_source, cache_dir=tmp_path / "element-cache")
    node_index = build_index(node_source, cache_dir=tmp_path / "node-cache")

    with DspfRepository(element_index.index_path) as repository:
        element_limited = repository.rc_network("A")
    with DspfRepository(node_index.index_path) as repository:
        node_limited = repository.rc_network("A")

    assert element_limited.element_count == 20_001
    assert element_limited.visible_node_count is None
    assert element_limited.status == "limit_exceeded"
    assert node_limited.element_count == 0
    assert node_limited.visible_node_count == 20_001
    assert node_limited.status == "limit_exceeded"
    for result in (element_limited, node_limited):
        assert result.nodes == result.resistors == result.capacitors == ()


@pytest.mark.parametrize("value", [True, False, 0, -1, 1.0, "1", None])
@pytest.mark.parametrize("field", ["max_nodes", "max_elements"])
def test_network_limits_require_positive_integers(
    qrc_repository: DspfRepository, field: str, value: object,
) -> None:
    with pytest.raises(ValueError, match=rf"{field} must be a positive integer"):
        qrc_repository.rc_network("IN", **{field: value})  # type: ignore[arg-type]


def test_network_visible_capacitor_queries_use_all_endpoint_indexes(
    qrc_repository: DspfRepository,
) -> None:
    statements: list[str] = []
    qrc_repository._connection.set_trace_callback(statements.append)
    try:
        assert qrc_repository.rc_network("OUT").status == "ok"
    finally:
        qrc_repository._connection.set_trace_callback(None)

    queries = [item for item in statements if "visible_capacitor" in item]
    assert len(queries) == 2
    for query in queries:
        assert "UNION ALL" in query
        details = [
            str(row["detail"])
            for row in qrc_repository._connection.execute("EXPLAIN QUERY PLAN " + query)
        ]
        plan = "\n".join(details)
        assert "idx_capacitor_net" in plan
        assert "idx_capacitor_node1" in plan
        assert "idx_capacitor_node2" in plan
        assert "UNION USING TEMP B-TREE" not in plan


def test_network_propagates_missing_and_ambiguous_net_errors(tmp_path: Path) -> None:
    source = tmp_path / "ambiguous.dspf"
    source.write_text(
        ".SUBCKT first A\n*|NET A 0\n.ENDS\n"
        ".SUBCKT second A\n*|NET A 0\n.ENDS\n",
        encoding="ascii",
    )
    result = build_index(source, cache_dir=tmp_path / "cache")
    with DspfRepository(result.index_path) as repository:
        with pytest.raises(KeyError, match="not found"):
            repository.rc_network("missing")
        with pytest.raises(ValueError, match="ambiguous"):
            repository.rc_network("A")
