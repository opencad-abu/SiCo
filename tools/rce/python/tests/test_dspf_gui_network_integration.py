from __future__ import annotations

from dataclasses import replace
import io
import json
import os
from pathlib import Path
from threading import Event, Timer

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dspf_gui_test_support import (
    SMALL_DSPF,
    application,
    wait_until,
    write_small_dspf,
)
from rcepy.dspf.indexer import build_index
from rcepy.dspf_gui.bridge import StdioBridge
from rcepy.dspf_gui.main_window import DspfMainWindow


def _index(source: Path, cache: Path) -> Path:
    return build_index(source, cache_dir=cache).index_path


def test_network_query_is_lazy_and_reuses_loaded_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from rcepy.dspf.repository import DspfRepository

    source = write_small_dspf(tmp_path / "lazy.dspf")
    index = _index(source, tmp_path / "cache")
    original = DspfRepository.rc_network
    calls: list[int | str] = []
    options: list[dict[str, object]] = []

    def counted(repository, net, **kwargs):
        calls.append(net)
        options.append(kwargs)
        return original(repository, net, **kwargs)

    monkeypatch.setattr(DspfRepository, "rc_network", counted)
    app = application()
    window = DspfMainWindow()
    window.open_path(index)
    assert window.status_text.text() == f"Index loaded: {index}"
    assert window.select_net_by_name("A")
    assert wait_until(lambda: not window.summary_queries._workers)
    assert calls == []
    assert window.ui.network_pane.network is None

    window.ui.tabs.setCurrentWidget(window.ui.network_pane)
    assert wait_until(lambda: window.ui.network_pane.network is not None)
    assert window.ui.network_pane.network.name == "A"
    assert window.ui.network_pane.network.capacitors == ()
    assert len(calls) == 1
    assert options == [{"include_capacitors": False}]
    assert window.ui.tabs.tabText(
        window.ui.tabs.indexOf(window.ui.network_pane)
    ) == "R Network"

    window.ui.tabs.setCurrentWidget(window.ui.summary)
    window.ui.tabs.setCurrentWidget(window.ui.network_pane)
    app.processEvents()
    assert len(calls) == 1
    window.close()
    app.processEvents()


def test_net_switch_discards_running_network_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from rcepy.dspf.repository import DspfRepository

    source = write_small_dspf(tmp_path / "stale-net.dspf")
    index = _index(source, tmp_path / "cache")
    original = DspfRepository.rc_network
    started, release = Event(), Event()

    def delayed(repository, net, **kwargs):
        if repository.get_net(net)["name"] == "A":
            started.set()
            release.wait(2)
        return original(repository, net, **kwargs)

    monkeypatch.setattr(DspfRepository, "rc_network", delayed)
    app = application()
    window = DspfMainWindow()
    delivered: list[tuple[object, object]] = []
    window.network_queries.resultReady.connect(delivered.append)
    window.open_path(index)
    assert window.select_net_by_name("A")
    window.ui.tabs.setCurrentWidget(window.ui.network_pane)
    assert wait_until(started.is_set)

    try:
        assert window.select_net_by_name("B")
        assert window.ui.network_pane.network is None
        release.set()
        assert wait_until(
            lambda: (
                window.ui.network_pane.network is not None
                and window.ui.network_pane.network.name == "B"
            )
        )
        assert [result[0].name for result in delivered] == ["B"]
    finally:
        release.set()
        window.close()
        app.processEvents()


def test_index_switch_discards_running_network_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from rcepy.dspf.repository import DspfRepository

    first_source = write_small_dspf(tmp_path / "first.dspf")
    second_source = tmp_path / "second.dspf"
    second_source.write_text(
        SMALL_DSPF.replace(" B", " C").replace("B:", "C:"), encoding="ascii"
    )
    first_index = _index(first_source, tmp_path / "first-cache")
    second_index = _index(second_source, tmp_path / "second-cache")
    original = DspfRepository.rc_network
    started, release = Event(), Event()

    def delayed(repository, net, **kwargs):
        if repository.index_path == first_index.resolve():
            started.set()
            release.wait(2)
        return original(repository, net, **kwargs)

    monkeypatch.setattr(DspfRepository, "rc_network", delayed)
    app = application()
    window = DspfMainWindow()
    delivered: list[tuple[object, object]] = []
    window.network_queries.resultReady.connect(delivered.append)
    window.open_path(first_index)
    assert window.select_net_by_name("A")
    window.ui.tabs.setCurrentWidget(window.ui.network_pane)
    assert wait_until(started.is_set)

    try:
        window.open_path(second_index)
        assert window.index_path == second_index
        assert window.ui.network_pane.network is None
        assert window.select_net_by_name("C")
        release.set()
        assert wait_until(
            lambda: (
                window.ui.network_pane.network is not None
                and window.ui.network_pane.network.name == "C"
            )
        )
        assert [result[0].name for result in delivered] == ["C"]
    finally:
        release.set()
        window.close()
        app.processEvents()


def test_net_switch_discards_running_highlight_and_sends_current_r_regions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import rcepy.dspf.highlight as highlight_module

    source = write_small_dspf(tmp_path / "stale-highlight-net.dspf")
    index = _index(source, tmp_path / "cache")
    started, release = Event(), Event()

    def delayed(repository, net, **_kwargs):
        name = repository.get_net(net)["name"]
        if name == "A":
            started.set()
            release.wait(2)
        return ((1.0, 2.0, 3.0, 4.0),) if name == "A" else (
            (10.0, 20.0, 30.0, 40.0),
        )

    monkeypatch.setattr(
        highlight_module, "resistance_highlight_regions", delayed
    )
    app = application()
    output = io.StringIO()
    window = DspfMainWindow()
    window.bridge = StdioBridge(
        reader=io.BytesIO(), writer=output, install_notifier=False, parent=window
    )
    window.open_path(index)
    assert window.select_net_by_name("A")
    window._highlight_current_net()
    assert wait_until(started.is_set)

    try:
        assert window.select_net_by_name("B")
        release.set()
        assert wait_until(lambda: not window.highlight_queries._workers)
        assert output.getvalue() == ""

        window._highlight_current_net()
        assert wait_until(lambda: bool(output.getvalue()))
        request = json.loads(output.getvalue())
        assert request == {
            "id": 1,
            "method": "oa.highlight_net",
            "params": {
                "name": "B",
                "regions": [[10.0, 20.0, 30.0, 40.0]],
            },
        }
    finally:
        release.set()
        window.close()
        app.processEvents()


def test_index_switch_discards_same_name_highlight_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import rcepy.dspf.highlight as highlight_module

    first_source = write_small_dspf(tmp_path / "first-highlight.dspf")
    second_source = write_small_dspf(tmp_path / "second-highlight.dspf")
    first_index = _index(first_source, tmp_path / "first-cache")
    second_index = _index(second_source, tmp_path / "second-cache")
    started, release = Event(), Event()

    def delayed(repository, _net, **_kwargs):
        if repository.index_path == first_index.resolve():
            started.set()
            release.wait(2)
            return ((1.0, 1.0, 2.0, 2.0),)
        return ((50.0, 60.0, 70.0, 80.0),)

    monkeypatch.setattr(
        highlight_module, "resistance_highlight_regions", delayed
    )
    app = application()
    output = io.StringIO()
    window = DspfMainWindow()
    window.bridge = StdioBridge(
        reader=io.BytesIO(), writer=output, install_notifier=False, parent=window
    )
    window.open_path(first_index)
    assert window.select_net_by_name("A")
    window._highlight_current_net()
    assert wait_until(started.is_set)

    try:
        window.open_path(second_index)
        assert window.select_net_by_name("A")
        window._highlight_current_net()
        release.set()
        assert wait_until(lambda: bool(output.getvalue()))
        requests = [json.loads(line) for line in output.getvalue().splitlines()]
        assert requests == [{
            "id": 1,
            "method": "oa.highlight_net",
            "params": {
                "name": "A",
                "regions": [[50.0, 60.0, 70.0, 80.0]],
            },
        }]
    finally:
        release.set()
        window.close()
        app.processEvents()


def test_limit_result_is_forwarded_without_layout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from rcepy.dspf.repository import DspfRepository

    source = write_small_dspf(tmp_path / "limited.dspf")
    index = _index(source, tmp_path / "cache")
    original = DspfRepository.rc_network

    def limited(repository, net, **kwargs):
        network = original(repository, net, **kwargs)
        return replace(
            network,
            status="limit_exceeded",
            message="viewer limit reached",
            visible_node_count=None,
            nodes=(),
            resistors=(),
            capacitors=(),
        )

    monkeypatch.setattr(DspfRepository, "rc_network", limited)
    app = application()
    window = DspfMainWindow()
    window.open_path(index)
    assert window.select_net_by_name("A")
    window.ui.tabs.setCurrentWidget(window.ui.network_pane)

    assert wait_until(lambda: window.ui.network_pane.network is not None)
    assert window.ui.network_pane.network.status == "limit_exceeded"
    assert window.ui.network_pane.layout is None
    assert window.ui.network_pane.state_text.text() == "viewer limit reached"
    window.close()
    app.processEvents()


def test_net_switch_discards_running_integrity_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from rcepy.dspf.repository import DspfRepository

    source = write_small_dspf(tmp_path / "stale-integrity.dspf")
    index = _index(source, tmp_path / "cache")
    original = DspfRepository.resistance_integrity
    started, release = Event(), Event()

    def delayed(repository, net, **kwargs):
        if repository.get_net(net)["name"] == "A":
            started.set()
            release.wait(2)
        return original(repository, net, **kwargs)

    monkeypatch.setattr(DspfRepository, "resistance_integrity", delayed)
    app = application()
    window = DspfMainWindow()
    delivered: list[object] = []
    window.integrity_queries.resultReady.connect(delivered.append)
    window.open_path(index)
    assert window.select_net_by_name("A")
    window._check_network_integrity(0.1)
    assert wait_until(started.is_set)

    try:
        assert window.select_net_by_name("B")
        window._check_network_integrity(0.1)
        release.set()
        assert wait_until(lambda: len(delivered) == 1)
        assert delivered[0].name == "B"
    finally:
        release.set()
        window.close()
        app.processEvents()


def test_close_discards_results_and_drains_analysis_workers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from rcepy.dspf.repository import DspfRepository

    source = write_small_dspf(tmp_path / "close.dspf")
    index = _index(source, tmp_path / "cache")
    summary_operation = DspfRepository.net_summary
    network_operation = DspfRepository.rc_network
    path_operation = DspfRepository.resistance_path
    integrity_operation = DspfRepository.resistance_integrity
    summary_started, network_started = Event(), Event()
    path_started, integrity_started = Event(), Event()
    release = Event()

    def delayed_summary(repository, net):
        summary_started.set()
        release.wait(2)
        return summary_operation(repository, net)

    def delayed_network(repository, net, **kwargs):
        network_started.set()
        release.wait(2)
        return network_operation(repository, net, **kwargs)

    def delayed_path(repository, net, start, end, **kwargs):
        path_started.set()
        release.wait(2)
        return path_operation(repository, net, start, end, **kwargs)

    def delayed_integrity(repository, net, **kwargs):
        integrity_started.set()
        release.wait(2)
        return integrity_operation(repository, net, **kwargs)

    monkeypatch.setattr(DspfRepository, "net_summary", delayed_summary)
    monkeypatch.setattr(DspfRepository, "rc_network", delayed_network)
    monkeypatch.setattr(DspfRepository, "resistance_path", delayed_path)
    monkeypatch.setattr(DspfRepository, "resistance_integrity", delayed_integrity)
    app = application()
    window = DspfMainWindow()
    results: list[object] = []
    for controller in (
        window.summary_queries,
        window.network_queries,
        window.integrity_queries,
        window.path_queries,
    ):
        controller.resultReady.connect(results.append)
    window.open_path(index)
    assert window.select_net_by_name("A")
    window.ui.tabs.setCurrentWidget(window.ui.network_pane)
    window._analyze_path("A", "A:1")
    window._check_network_integrity(0.1)
    assert wait_until(
        lambda: (
            summary_started.is_set()
            and network_started.is_set()
            and path_started.is_set()
            and integrity_started.is_set()
        )
    )

    timer = Timer(0.05, release.set)
    timer.start()
    try:
        window.close()
    finally:
        release.set()
        timer.join()
    assert wait_until(
        lambda: all(
            not controller._workers and controller.pool.activeThreadCount() == 0
            for controller in (
                window.summary_queries,
                window.network_queries,
                window.integrity_queries,
                window.path_queries,
            )
        )
    )
    assert results == []
    app.processEvents()
