from __future__ import annotations

import os
from pathlib import Path
from threading import Event
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dspf_gui_test_support import application, wait_until, write_small_dspf
from PyQt5.QtWidgets import QToolBar
from rcepy.dspf.indexer import build_index
from rcepy.dspf_gui.main_window import DspfMainWindow
from rcepy.dspf_gui.widgets import SummaryPane


def test_summary_pane_shows_component_limit_message() -> None:
    app = application()
    pane = SummaryPane()
    pane.set_summary({
        "connected_components": None,
        "connected_components_message": "Not computed: graph limit exceeded",
    })

    assert pane.labels["connected_components"].text() == (
        "Not computed: graph limit exceeded"
    )
    pane.close()
    app.processEvents()


def test_gui_indexes_fixture_and_selects_net(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("LOGO", raising=False)
    monkeypatch.delenv("COMPANY", raising=False)
    app = application()
    source = write_small_dspf(tmp_path / "small.dspf")
    window = DspfMainWindow(cache_dir=str(tmp_path / "cache"))
    assert window.windowTitle() == "SiCo::RCE DSPF Analyzer"
    assert window.windowIcon().isNull() is False
    loaded = []
    window.indexLoaded.connect(loaded.append)
    window.show()
    window.open_path(source)

    assert wait_until(lambda: bool(loaded))
    assert window.windowTitle() == (
        f"SiCo::RCE DSPF Analyzer--{window.index_path.name}"
    )
    assert window.models["nets"].total_rows == 2
    assert window.select_net_by_name("A")
    assert window.current_net_name == "A"
    assert window.models["resistors"].total_rows == 1
    assert window.models["capacitors"].total_rows == 2
    assert wait_until(
        lambda: window.ui.summary.labels["ground_capacitor_count"].text() != "-"
    )
    for key in (
        "ground_capacitor_count",
        "coupling_capacitor_count",
        "max_resistance",
        "max_capacitance",
        "computed_capacitance",
        "declared_capacitance",
        "capacitance_difference",
        "dangling_node_count",
        "connected_components",
    ):
        assert window.ui.summary.labels[key].text() != "-"

    window.close()
    app.processEvents()


def test_summary_and_path_queries_do_not_block_gui(tmp_path: Path, monkeypatch) -> None:
    from rcepy.dspf.repository import DspfRepository

    app = application()
    source = write_small_dspf(tmp_path / "slow.dspf")
    index = build_index(source, cache_dir=tmp_path / "cache").index_path
    window = DspfMainWindow()
    window.open_path(index)
    summary_started, summary_release = Event(), Event()
    original_summary = DspfRepository.net_summary

    def slow_summary(repository, net):
        summary_started.set()
        summary_release.wait(2)
        return original_summary(repository, net)

    monkeypatch.setattr(DspfRepository, "net_summary", slow_summary)
    started = time.monotonic()
    assert window.select_net_by_name("A")
    assert time.monotonic() - started < 0.2
    assert wait_until(summary_started.is_set)
    assert window.ui.summary.labels["node_count"].text() == "Loading..."
    summary_release.set()
    assert wait_until(lambda: window.ui.summary.labels["node_count"].text() != "Loading...")

    path_started, path_release = Event(), Event()
    original_path = DspfRepository.resistance_path

    def slow_path(repository, net, start, end, **kwargs):
        path_started.set()
        path_release.wait(2)
        return original_path(repository, net, start, end, **kwargs)

    monkeypatch.setattr(DspfRepository, "resistance_path", slow_path)
    started = time.monotonic()
    window._analyze_path("A", "A:1")
    assert time.monotonic() - started < 0.2
    assert wait_until(path_started.is_set)
    assert window.ui.path_pane.run_button.isEnabled() is False
    path_release.set()
    assert wait_until(window.ui.path_pane.run_button.isEnabled)
    window.close()
    app.processEvents()


def test_net_switch_discards_running_path_result(tmp_path: Path, monkeypatch) -> None:
    from rcepy.dspf.repository import DspfRepository

    app = application()
    source = write_small_dspf(tmp_path / "stale-path.dspf")
    index = build_index(source, cache_dir=tmp_path / "cache").index_path
    window = DspfMainWindow()
    window.open_path(index)
    assert window.select_net_by_name("A")

    path_started, path_release = Event(), Event()

    def slow_path(_repository, _net, _start, _end, **_kwargs):
        path_started.set()
        path_release.wait(2)
        return {"net": "A", "resistance": 10.0}

    monkeypatch.setattr(DspfRepository, "resistance_path", slow_path)
    window._analyze_path("A", "A:1")
    assert wait_until(path_started.is_set)
    assert "Analyzing" in window.ui.path_pane.output.toPlainText()

    assert window.select_net_by_name("B")
    assert window.current_net_name == "B"
    assert window.ui.path_pane.output.toPlainText() == ""
    assert window.ui.path_pane.run_button.isEnabled() is True

    path_release.set()
    assert wait_until(lambda: not window.path_queries._workers)
    assert window.ui.path_pane.output.toPlainText() == ""
    window.close()
    app.processEvents()


def test_bridge_mode_locks_launch_source_and_allows_force_rebuild(
    tmp_path: Path, monkeypatch
) -> None:
    class FakeBridge:
        def close(self) -> None:
            pass

    monkeypatch.setattr(
        DspfMainWindow,
        "_enable_bridge",
        lambda window: setattr(window, "bridge", FakeBridge()),
    )
    app = application()
    source = write_small_dspf(tmp_path / "launch.dspf")
    other_source = write_small_dspf(tmp_path / "other.dspf")
    other_index = build_index(
        other_source, cache_dir=tmp_path / "other-cache"
    ).index_path
    window = DspfMainWindow(
        cache_dir=str(tmp_path / "launch-cache"), bridge_enabled=True
    )
    errors: list[str] = []
    results = []
    window._show_error = errors.append
    window.indexer.completed.connect(results.append)
    loaded = []
    window.indexLoaded.connect(loaded.append)

    assert window.open_action.isEnabled() is False
    window.open_path(source)
    assert wait_until(lambda: len(loaded) == 1)
    original_index = window.index_path
    assert window.open_action.isEnabled() is False

    window.open_path(other_source)
    window.open_path(other_index)
    assert len(errors) == 2
    assert all("locked to its launch source" in error for error in errors)
    assert window.source_path == source.resolve()
    assert window.index_path == original_index

    window.open_path(source, force=True)
    assert wait_until(lambda: len(loaded) == 2)
    assert results[-1].reused is False
    assert len(errors) == 2
    assert window.open_action.isEnabled() is False
    window.close()
    app.processEvents()


def test_oa_exact_net_selection_is_not_limited_to_first_page(tmp_path: Path) -> None:
    lines = [".SUBCKT top"]
    lines.extend(f"*|NET A{number:03d}_ZZTARGET 0" for number in range(250))
    lines.extend(("*|NET ZZTARGET 0", ".ENDS top"))
    source = tmp_path / "many-nets.dspf"
    source.write_text("\n".join(lines) + "\n", encoding="ascii")
    index = build_index(source, cache_dir=tmp_path / "cache").index_path
    app = application()
    window = DspfMainWindow()
    window.open_path(index)
    window.ui.search.setText("A")
    assert window._search_timer.isActive()
    assert window.select_net_by_name("ZZTARGET")
    assert window._search_timer.isActive() is False
    assert window.models["nets"].total_rows == 1
    assert window.current_net_name == "ZZTARGET"
    window.close()
    app.processEvents()


def test_oa_actions_require_a_complete_layout_context(monkeypatch) -> None:
    names = (
        "RCE_DSPF_LAYOUT_LIB", "RCE_DSPF_LAYOUT_CELL", "RCE_DSPF_LAYOUT_VIEW"
    )
    for name in names:
        monkeypatch.delenv(name, raising=False)
    app = application()
    window = DspfMainWindow()
    window.bridge = object()
    window.index_path = Path("placeholder.sqlite3")
    window.current_net_name = "A"
    window._update_actions()
    assert window.highlight_action.isEnabled() is False
    assert window.oa_selection_action.isEnabled() is False
    window.bridge = None
    window.close()

    for name in names:
        monkeypatch.setenv(name, "present")
    window = DspfMainWindow()
    window.bridge = object()
    window.index_path = Path("placeholder.sqlite3")
    window.current_net_name = "A"
    window._update_actions()
    assert window.highlight_action.isEnabled() is True
    assert window.oa_selection_action.isEnabled() is True
    window.bridge = None
    window.close()
    app.processEvents()


def test_net_context_menu_targets_clicked_row_and_owns_highlight(
    tmp_path: Path, monkeypatch,
) -> None:
    import rcepy.dspf_gui.main_window as main_window_module

    app = application()
    source = write_small_dspf(tmp_path / "context-menu.dspf")
    index = build_index(source, cache_dir=tmp_path / "cache").index_path
    window = DspfMainWindow()
    window.open_path(index)
    window.show()
    app.processEvents()
    table = window.ui.net_pane.table
    target_index = window.models["nets"].index(1, 0)
    position = table.visualRect(target_index).center()
    clicked_index = table.indexAt(position)
    assert clicked_index.isValid()
    target_name = window.models["nets"].row_at(clicked_index.row())["name"]
    table.setCurrentIndex(window.models["nets"].index(0, 0))
    assert window.current_net_name != target_name
    menus = []

    class FakeMenu:
        def __init__(self, parent):
            self.parent = parent
            self.actions = []
            self.position = None
            menus.append(self)

        def addAction(self, action):
            self.actions.append(action)

        def exec_(self, position):
            self.position = position

    monkeypatch.setattr(main_window_module, "QMenu", FakeMenu)
    window._show_net_context_menu(position)

    assert window.current_net_name == target_name
    assert menus[0].actions == [window.highlight_action]
    assert menus[0].position == table.viewport().mapToGlobal(position)
    assert all(
        window.highlight_action not in toolbar.actions()
        for toolbar in window.findChildren(QToolBar)
    )
    window.close()
    app.processEvents()
