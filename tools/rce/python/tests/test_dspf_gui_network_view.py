from __future__ import annotations

from dataclasses import replace
import math
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QPointF
from PyQt5.QtGui import QColor, QImage, QPainter

from dspf_gui_test_support import application, wait_until
from rcepy.dspf.network import (
    RcNetwork,
    RcNetworkCapacitor,
    RcNetworkNode,
    RcNetworkResistor,
)
from rcepy.dspf.network_layout import RcNetworkLayout
from rcepy.dspf_gui.network_canvas import format_node_label, format_tooltip
from rcepy.dspf_gui.network_pane import RcNetworkPane


def _node(
    identifier: int,
    name: str,
    x: float | None,
    y: float | None,
    *,
    external: bool = False,
    kind: str = "subnode",
    ground_cap: float = 0.0,
    ground_count: int = 0,
    coupling_cap: float = 0.0,
    coupling_count: int = 0,
) -> RcNetworkNode:
    return RcNetworkNode(
        identifier,
        name,
        None if external else 1,
        "OTHER" if external else "A",
        kind,
        x,
        y,
        "M1",
        external,
        ground_cap,
        ground_count,
        coupling_cap,
        coupling_count,
    )


def _resistor(
    identifier: int,
    name: str,
    first: int,
    second: int,
    value: float,
    *,
    cross_net: bool = False,
) -> RcNetworkResistor:
    return RcNetworkResistor(
        identifier,
        name,
        first,
        second,
        value,
        str(value),
        "M1",
        1.0,
        0.1,
        10 + identifier,
        cross_net,
    )


def _capacitor(
    identifier: int,
    name: str,
    first: int,
    second: int,
    value: float,
    kind: str,
    local: int | None,
) -> RcNetworkCapacitor:
    return RcNetworkCapacitor(
        identifier,
        name,
        first,
        second,
        value,
        str(value),
        "M1",
        30 + identifier,
        kind,
        local,
    )


def _network() -> tuple[RcNetwork, RcNetworkLayout]:
    nodes = (
        _node(1, "A", 0.0, 0.0, kind="port", ground_cap=1e-15, ground_count=1),
        _node(
            2,
            "A:1",
            100.0,
            0.0,
            ground_cap=1e-12,
            ground_count=1,
            coupling_cap=5e-15,
            coupling_count=1,
        ),
        _node(3, "A:2", 100.0, 100.0, ground_cap=-1e-15, ground_count=1),
        _node(4, "A:3", 0.0, 100.0, ground_cap=0.0, ground_count=1),
        _node(5, "XB:1", 10_000.0, 10_000.0, external=True, kind="instance_pin"),
        _node(6, "0", None, None, external=True, kind="ground"),
    )
    resistors = (
        _resistor(1, "Rlow", 1, 2, 1.0),
        _resistor(2, "Rhigh", 2, 3, 100.0),
        _resistor(3, "Rzero", 3, 4, 0.0),
        _resistor(4, "Rnegative", 4, 1, -2.0),
        _resistor(5, "Rloop", 2, 2, 10.0),
        _resistor(6, "Rexternal", 2, 5, 5.0, cross_net=True),
        _resistor(7, "Rground", 3, 6, 20.0, cross_net=True),
    )
    capacitors = (
        _capacitor(1, "Cg1", 1, 6, 1e-15, "ground", 1),
        _capacitor(2, "Cg2", 2, 6, 1e-12, "ground", 2),
        _capacitor(3, "Cc", 2, 5, 5e-15, "coupling", 2),
        _capacitor(4, "Czero", 3, 4, 0.0, "coupling", 3),
        _capacitor(5, "Cnegative", 1, 4, -1e-15, "coupling", 1),
    )
    network = RcNetwork(
        "ok",
        None,
        1,
        "A",
        "top",
        4,
        6,
        len(resistors),
        2,
        3,
        len(resistors) + len(capacitors),
        4,
        (0.0, 0.0, 100.0, 100.0),
        20_000,
        20_000,
        nodes,
        resistors,
        capacitors,
    )
    layout = RcNetworkLayout(
        {
            1: (0.0, 0.0),
            2: (100.0, 0.0),
            3: (100.0, 100.0),
            4: (0.0, 100.0),
            5: (106.0, 0.0),
        },
        frozenset({1, 2, 3, 4}),
        "physical",
        (0.0, 0.0, 106.0, 100.0),
        {7: (100.0, 120.0)},
    )
    return network, layout


def _shown_pane() -> RcNetworkPane:
    application()
    pane = RcNetworkPane()
    pane.resize(720, 520)
    pane.show()
    network, layout = _network()
    pane.set_network(network, layout)
    assert wait_until(lambda: pane.canvas.transform().m11() > 0)
    pane.canvas.fit_network()
    application().processEvents()
    return pane


def _render(pane: RcNetworkPane) -> QImage:
    viewport = pane.canvas.viewport()
    image = QImage(viewport.size(), QImage.Format_ARGB32_Premultiplied)
    image.fill(QColor("#ffffff"))
    painter = QPainter(image)
    viewport.render(painter)
    painter.end()
    return image


def _pixel_counts(image: QImage) -> tuple[int, set[int]]:
    white = QColor("#ffffff").rgb()
    colors = {
        image.pixel(x, y) for y in range(image.height()) for x in range(image.width())
    }
    ink = sum(
        image.pixel(x, y) != white
        for y in range(image.height())
        for x in range(image.width())
    )
    return ink, colors


def test_canvas_uses_one_r_only_item_and_keeps_external_stub_bounded() -> None:
    pane = _shown_pane()
    item = pane.canvas.item

    assert len(pane.canvas.scene().items()) == 1
    assert item.boundingRect().right() < 200
    assert len(item.node_hits) == 5
    assert [node.name for node, _point in item.node_hits if node.external] == ["XB:1"]
    assert item.zero_path.elementCount() > 0
    assert item.warning_path.elementCount() > 0
    assert {kind for kind, *_rest in item.hits} == {"resistor", "capacitor"}
    assert [(node.name, point) for node, point in item.ground_hits] == [
        ("0", QPointF(100.0, -120.0))
    ]
    assert any(color not in {"#6b7280", "#b42318"} for _point, color in item.rings)
    pane.close()


def test_r_only_controls_keep_transform_and_remain_functional() -> None:
    pane = _shown_pane()
    before = pane.canvas.transform()

    pane.zoom_in.click()
    assert pane.canvas.transform().m11() > before.m11()
    pane.fit_button.click()
    assert math.isclose(pane.canvas.transform().m11(), before.m11(), rel_tol=1e-6)
    pane.labels.click()
    assert pane.canvas.item.labels is True
    assert "ohm" in pane.legend.description
    assert "external R endpoints" in pane.legend.description
    assert " C" not in pane.legend.description
    pane.close()


def test_integrity_controls_results_and_external_labels() -> None:
    pane = _shown_pane()
    requested: list[float] = []
    pane.integrityRequested.connect(requested.append)
    pane.short_threshold.setValue(0.25)
    pane.check_integrity.click()

    assert requested == [0.25]
    external = next(node for node in pane.network.nodes if node.external)
    assert format_node_label(external) == "XB:1 [net OTHER]"
    assert "squares: external R endpoints" in pane.legend.description

    component = SimpleNamespace(node_count=2, terminal_names=("A", "XA:d"))
    candidate = SimpleNamespace(
        name="Rbridge",
        value=0.05,
        node1_name="A:1",
        node1_net_name="A",
        node2_name="B:1",
        node2_net_name="B",
        source_line=42,
    )
    pane.set_integrity_result(SimpleNamespace(
        status="ok",
        message=None,
        short_threshold_ohm=0.25,
        open_status="open",
        terminal_count=3,
        terminal_component_count=2,
        open_components=(component,),
        island_count=1,
        orphan_node_count=1,
        short_status="short",
        short_candidate_count=1,
        short_candidates=(candidate,),
        inconclusive_boundary_count=0,
        negative_resistor_count=1,
        zero_resistor_count=0,
        self_loop_count=0,
    ))

    assert "Open: OPEN" in pane.integrity_status.text()
    assert "Short: SHORT" in pane.integrity_status.text()
    assert "1 candidates, R <= 250 mohm" in pane.integrity_status.text()
    assert pane.integrity_issues.rowCount() == 3
    assert pane.integrity_issues.item(0, 0).text() == "Open"
    assert pane.integrity_issues.item(1, 1).text() == "Rbridge"
    assert "A:1 [A]" in pane.integrity_issues.item(1, 3).text()
    pane.set_integrity_busy(True)
    assert not pane.check_integrity.isEnabled()
    pane.set_integrity_error("query stopped")
    assert "query stopped" in pane.integrity_status.text()
    pane.close()


def test_tooltips_cover_only_nodes_resistors_and_external_stubs() -> None:
    pane = _shown_pane()
    node = pane.canvas.tooltip_at(QPointF(0.0, 0.0), 3)
    assert node.startswith("Node A\n")
    assert "Ground C:" not in node and "Coupling C:" not in node
    resistor = pane.canvas.tooltip_at(QPointF(50.0, 0.0), 3)
    assert "R Rlow" in resistor and "1 ohm" in resistor
    external_node, external_point = next(
        (node, point) for node, point in pane.canvas.item.node_hits if node.external
    )
    assert external_node.name == "XB:1"
    assert pane.canvas.tooltip_at(external_point, 3).startswith("Node XB:1\n")
    assert "L/W: 1.0 / 0.1" in format_tooltip(
        "resistor",
        pane.network.resistors[0],
        pane.canvas.item.nodes,
    )
    ground_node, ground_point = pane.canvas.item.ground_hits[0]
    assert ground_node.name == "0"
    ground = pane.canvas.tooltip_at(ground_point, 3)
    assert ground.startswith("Node 0\nKind: ground")
    assert "Ground C:" not in ground and "Coupling C:" not in ground
    assert {kind for kind, *_rest in pane.canvas.item.hits} == {
        "resistor", "capacitor",
    }
    pane.close()


def test_limit_error_clear_and_collinear_fit_states() -> None:
    app = application()
    pane = RcNetworkPane()
    network, layout = _network()
    limited = replace(
        network,
        status="limit_exceeded",
        message="viewer limit reached",
        visible_node_count=None,
        nodes=(),
        resistors=(),
        capacitors=(),
    )
    pane.set_network(limited, None)
    assert pane.stack.currentIndex() == 1
    assert pane.state_text.text() == "viewer limit reached"
    assert "not counted" in pane.footer.text()
    assert "not computed layout" in pane.footer.text()
    assert not pane.canvas.scene().items()

    pane.set_error("database unavailable")
    assert pane.network is None
    assert pane.state_text.text() == "database unavailable"
    collinear = replace(
        layout,
        positions={1: (0, 0), 2: (100, 0), 3: (200, 0), 4: (300, 0)},
        bounds=(0, 0, 300, 0),
    )
    pane.set_network(network, collinear)
    pane.resize(600, 400)
    pane.show()
    pane.canvas.fit_network()
    app.processEvents()
    assert math.isfinite(pane.canvas.transform().m11())
    assert pane.canvas.transform().m11() > 0

    stub_network = replace(
        network,
        nodes=(network.nodes[0], network.nodes[4]),
        resistors=(replace(network.resistors[-2], node1_id=1),),
        capacitors=(),
    )
    stub_layout = RcNetworkLayout(
        {1: (0, 0), 5: (80, 0)},
        frozenset({1}),
        "physical",
        (0, 0, 80, 0),
    )
    pane.set_network(stub_network, stub_layout)
    pane.canvas.fit_network()
    app.processEvents()
    mapped = [pane.canvas.mapFromScene(QPointF(x, 0)).x() for x in (0, 80)]
    assert all(0 <= position < pane.canvas.viewport().width() for position in mapped)

    ground_only = replace(
        network,
        nodes=(network.nodes[-1],),
        resistors=(),
        capacitors=(),
    )
    ground_layout = RcNetworkLayout(
        {6: (0, 0)},
        frozenset(),
        "topology",
        (0, 0, 0, 0),
    )
    pane.set_network(ground_only, ground_layout)
    assert len(pane.canvas.scene().items()) == 1
    pane.clear()
    assert pane.network is None and not pane.canvas.scene().items()
    pane.close()


def test_offscreen_canvas_pixels_are_nonblank_and_ignore_capacitors() -> None:
    pane = _shown_pane()
    application().processEvents()
    with_capacitors = _render(pane)
    network, layout = _network()
    pane.set_network(replace(network, capacitors=()), layout)
    pane.canvas.fit_network()
    application().processEvents()
    without_capacitors = _render(pane)

    r_ink, r_colors = _pixel_counts(with_capacitors)
    changed = sum(
        with_capacitors.pixel(x, y) != without_capacitors.pixel(x, y)
        for y in range(with_capacitors.height())
        for x in range(with_capacitors.width())
    )
    assert r_ink > 500
    assert len(r_colors) > 5
    assert changed == 0
    pane.close()
