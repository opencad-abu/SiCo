from __future__ import annotations

from dataclasses import dataclass
import math

from rcepy.dspf.network import RcNetwork, RcNetworkNode, RcNetworkResistor
from rcepy.dspf.network_layout import layout_rc_network


@dataclass(frozen=True)
class Node:
    id: int
    name: str
    kind: str = "internal"
    x: float | None = None
    y: float | None = None
    external: bool = False


@dataclass(frozen=True)
class Resistor:
    id: int
    node1_id: int
    node2_id: int


@dataclass(frozen=True)
class Capacitor:
    id: int
    node1_id: int
    node2_id: int
    kind: str


@dataclass(frozen=True)
class Network:
    nodes: tuple[Node, ...]
    resistors: tuple[Resistor, ...] = ()
    capacitors: tuple[Capacitor, ...] = ()


def _finite(layout) -> bool:
    return all(math.isfinite(value) for point in layout.positions.values() for value in point)


def test_empty_network_has_stable_topology_result() -> None:
    layout = layout_rc_network(Network(()))

    assert layout.positions == {}
    assert layout.physical_ids == frozenset()
    assert layout.mode == "topology"
    assert layout.bounds == (0.0, 0.0, 0.0, 0.0)


def test_layout_accepts_rc_network_contract() -> None:
    nodes = (
        RcNetworkNode(1, "A", 7, "N", "port", 2.0, 3.0, None, False, 0.0, 0, 0.0, 0),
        RcNetworkNode(2, "B", 7, "N", "subnode", None, None, None, False, 0.0, 0, 0.0, 0),
    )
    resistor = RcNetworkResistor(
        1, "R1", 1, 2, 10.0, "10", None, None, None, 8, False,
    )
    network = RcNetwork(
        "ok", None, 7, "N", "TOP", 2, 2, 1, 0, 0, 1, 1,
        (2.0, 3.0, 2.0, 3.0), 20_000, 20_000, nodes, (resistor,), (),
    )

    layout = layout_rc_network(network)

    assert layout.positions[1] == (2.0, 3.0)
    assert set(layout.positions) == {1, 2}
    assert layout.mode == "mixed"


def test_physical_coordinates_are_exact_for_collinear_and_coincident_nodes() -> None:
    network = Network(
        (
            Node(1, "P", "port", -2.5, 4.25),
            Node(2, "A", x=7.0, y=4.25),
            Node(3, "B", x=7.0, y=4.25),
        ),
        (
            Resistor(1, 1, 2),
            Resistor(2, 1, 2),
            Resistor(3, 2, 2),
            Resistor(4, 2, 3),
        ),
    )

    layout = layout_rc_network(network)

    assert layout.positions == {1: (-2.5, 4.25), 2: (7.0, 4.25), 3: (7.0, 4.25)}
    assert layout.physical_ids == frozenset({1, 2, 3})
    assert layout.mode == "physical"
    assert layout.bounds[1] < 4.25 < layout.bounds[3]


def test_mixed_layout_preserves_anchors_and_is_repeatable() -> None:
    network = Network(
        (
            Node(1, "A", "port", -3.125, 7.75),
            Node(2, "B"),
            Node(3, "C"),
            Node(4, "D", x=9.5, y=-2.25),
        ),
        (Resistor(1, 1, 2), Resistor(2, 2, 3), Resistor(3, 3, 4)),
    )

    first = layout_rc_network(network)
    second = layout_rc_network(network)

    assert first == second
    assert first.positions[1] == (-3.125, 7.75)
    assert first.positions[4] == (9.5, -2.25)
    assert first.physical_ids == frozenset({1, 4})
    assert first.mode == "mixed"
    assert _finite(first)


def test_topology_layout_is_order_independent_for_complex_components() -> None:
    nodes = tuple(Node(index, f"N{index}") for index in range(1, 9))
    resistors = (
        Resistor(1, 1, 2),
        Resistor(2, 1, 3),
        Resistor(3, 1, 4),
        Resistor(4, 2, 3),
        Resistor(5, 2, 3),
        Resistor(6, 4, 4),
        Resistor(7, 5, 6),
        Resistor(8, 6, 7),
    )

    forward = layout_rc_network(Network(nodes, resistors))
    reverse = layout_rc_network(Network(tuple(reversed(nodes)), tuple(reversed(resistors))))

    assert forward == reverse
    assert set(forward.positions) == set(range(1, 9))
    assert len(set(forward.positions.values())) == 8
    assert forward.mode == "topology"
    assert _finite(forward)
    assert forward.bounds[0] < forward.bounds[2]
    assert forward.bounds[1] < forward.bounds[3]


def test_nearby_external_coordinate_is_preserved_as_physical() -> None:
    network = Network(
        (
            Node(1, "LOCAL1", "port", 10.0, 20.0),
            Node(2, "LOCAL2", x=12.0, y=20.0),
            Node(3, "NEAR", x=12.25, y=20.5, external=True),
        ),
        (Resistor(1, 1, 2),),
        (Capacitor(1, 2, 3, "coupling"),),
    )

    layout = layout_rc_network(network)

    assert layout.positions[3] == (12.25, 20.5)
    assert layout.physical_ids == frozenset({1, 2, 3})
    assert layout.mode == "physical"


def test_external_outlier_and_missing_coordinate_use_adaptive_stubs() -> None:
    network = Network(
        (
            Node(1, "LOCAL", "port", 10.0, 20.0),
            Node(2, "EXT1", x=1e9, y=-1e9, external=True),
            Node(3, "EXT2", external=True),
            Node(4, "0", "ground", external=True),
        ),
        capacitors=(
            Capacitor(1, 1, 2, "coupling"),
            Capacitor(2, 2, 3, "coupling"),
            Capacitor(3, 1, 4, "ground"),
        ),
    )

    layout = layout_rc_network(network)

    assert layout.positions[1] == (10.0, 20.0)
    assert layout.positions[2] == (10.35, 20.0)
    assert layout.positions[3] == (10.7, 20.0)
    assert 4 not in layout.positions
    assert layout.physical_ids == frozenset({1})
    assert layout.mode == "physical"
    assert layout.bounds[2] < 11.0


def test_duplicate_physical_coordinates_get_nonzero_bounded_stub() -> None:
    network = Network(
        (
            Node(1, "A", "port", 5.0, 5.0),
            Node(2, "B", x=5.0, y=5.0),
            Node(3, "EXT", external=True),
        ),
        (Resistor(1, 1, 2),),
        (Capacitor(1, 1, 3, "coupling"),),
    )

    layout = layout_rc_network(network)

    assert layout.positions[1] == layout.positions[2] == (5.0, 5.0)
    assert layout.positions[3] == (5.35, 5.0)
    assert layout.physical_ids == frozenset({1, 2})
    assert layout.bounds[2] < 6.0


def test_ground_resistor_gets_bounded_stub_without_positioning_ground_node() -> None:
    network = Network(
        (
            Node(1, "A", "port", 10.0, 20.0),
            Node(2, "VSS", "ground", external=True),
        ),
        (Resistor(11, 1, 2),),
    )

    layout = layout_rc_network(network)

    assert layout.positions == {1: (10.0, 20.0)}
    assert 11 in layout.ground_positions
    assert layout.ground_positions[11] == (10.0, 19.65)
    assert math.dist(layout.positions[1], layout.ground_positions[11]) < 1.0
    assert layout.bounds[1] <= layout.ground_positions[11][1]
    assert layout.mode == "physical"


def test_unlocated_unlinked_net_node_does_not_expand_physical_fit() -> None:
    nodes = (
        Node(1, "A", "port", 1.0, 2.0),
        Node(2, "B", x=3.0, y=2.0),
    )
    resistors = (Resistor(1, 1, 2),)
    baseline = layout_rc_network(Network(nodes, resistors))
    synthetic = Node(3, "DECLARED_NET", "net")
    network = Network((*nodes, synthetic), resistors)

    layout = layout_rc_network(network)

    assert layout == baseline
    assert synthetic in network.nodes
    assert 3 not in layout.positions


def test_unlocated_unlinked_subnode_is_still_drawable() -> None:
    network = Network(
        (
            Node(1, "A", "port", 1.0, 2.0),
            Node(2, "B", x=3.0, y=2.0),
            Node(3, "ISOLATED", "subnode"),
        ),
        (Resistor(1, 1, 2),),
    )

    layout = layout_rc_network(network)

    assert 3 in layout.positions
    assert layout.mode == "mixed"


def test_invalid_coordinates_fall_back_to_finite_topology() -> None:
    network = Network(
        (
            Node(1, "A", x=float("nan"), y=0.0),
            Node(2, "B", x=1.0, y=float("inf")),
            Node(3, "C"),
        ),
        (Resistor(1, 1, 2), Resistor(2, 2, 3)),
    )

    layout = layout_rc_network(network)

    assert layout.physical_ids == frozenset()
    assert layout.mode == "topology"
    assert _finite(layout)


def test_large_chain_is_iterative_and_complete() -> None:
    count = 5000
    network = Network(
        tuple(Node(index, f"N{index:05d}") for index in range(count)),
        tuple(Resistor(index, index, index + 1) for index in range(count - 1)),
    )

    layout = layout_rc_network(network)

    assert len(layout.positions) == count
    assert _finite(layout)
