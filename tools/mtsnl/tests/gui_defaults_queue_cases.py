"""gui defaults queue cases regressions."""

from __future__ import annotations
from pathlib import Path
import pytest
from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication,
)
from mtsnetlistor.gui.controller import ControllerState  # noqa: E402
from mtsnetlistor.gui.main_window import (  # noqa: E402
    MtsMainWindow,
)
from mtsnetlistor.defaults import SourceDefaults  # noqa: E402
from mtsnetlistor.model import ModelEntry, SimulatorOption, SourceDesign  # noqa: E402
from gui_window_fixtures import _draft, _view, _text, _append_source_cell


def test_select_source_view_queues_defaults_probe_and_preserves_advanced_options(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE source ./source\n", encoding="utf-8")
    model = tmp_path / "pdk.scs"
    model.write_text("// model\n", encoding="utf-8")
    window = MtsMainWindow(session=None)
    calls: list[tuple[SourceDesign, str]] = []
    try:
        window.source_edit.setText(str(cds))

        def read_defaults(source: SourceDesign, *, dialect: str, **_kwargs) -> int:
            calls.append((source, dialect))
            return 17

        monkeypatch.setattr(window.controller, "read_source_defaults", read_defaults)
        window.source_view_list.addItem("schematic")
        candidate = window.source_view_list.item(0)
        candidate.setData(Qt.UserRole, ("source", "inv", "schematic"))
        window.source_view_list.setCurrentItem(candidate)

        window._select_source_view()

        key = ("source", "inv", "schematic")
        assert [(call[0].library, call[0].cell, call[1]) for call in calls] == [
            ("source", "inv", "spectre")
        ]
        assert window.defaults.active == 17
        assert window.defaults.pending[17].cell_key == key

        # Selecting the same browser item again only focuses the existing
        # queue entry; it must not launch a second Cadence worker.
        window._select_source_view()
        assert len(calls) == 1

        # Simulate a row the user created while the probe was running. The
        # report contains another typed option, but automatic defaults loading
        # does not publish report options into this table.
        window._loading_cell_state = True
        window._set_option_rows(((True, "userOpt", "string", "keep", ""),))
        window._loading_cell_state = False
        window._save_active_cell_state()
        result = SourceDefaults(
            "asi_initialization",
            "spectre",
            SourceDesign(cds, "source", "inv"),
            (ModelEntry(model, "tt"),),
            (SimulatorOption("reltol", "1e-3", "real"),),
            "27.000",
            "1e-6",
        )
        window._apply_defaults_report(result, requested_key=key)
        applied = _view(window, key)
        assert applied.simulator.models == ((True, str(model), "tt", ""),)
        assert _text(window, key) == ("27.000", "1e-6", "")
        assert applied.simulator.options == ((True, "userOpt", "string", "keep", ""),)
    finally:
        window.close()
        window.controller.close()


def test_automatic_defaults_waits_behind_controller_operation(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE source ./source\n", encoding="utf-8")
    window = MtsMainWindow(session=None)
    calls: list[str] = []
    try:
        page = window._active_page()
        assert page is not None
        window.source_edit.setText(str(cds))
        state = _draft("source", "inv", "schematic")
        _append_source_cell(window, state)
        window.controller._state = ControllerState(token=4, stage="cataloging_source", busy=True)
        monkeypatch.setattr(
            window.controller,
            "read_source_defaults",
            lambda source, **_kwargs: calls.append(source.cell) or 9,
        )
        window._enqueue_defaults_probe(state.key)
        assert calls == []
        assert window.defaults.queue == [state.key]
        assert page.testAttribute(Qt.WA_SetCursor)
        assert page.cursor().shape() == Qt.BusyCursor

        window.controller._state = ControllerState(
            token=4, stage="source_catalog_ready", busy=False
        )
        window._receive_state(ControllerState(token=4, stage="source_catalog_ready", busy=False))
        window._poll_state()
        assert calls == ["inv"]
        assert window.defaults.active == 9
        assert page.testAttribute(Qt.WA_SetCursor)
        assert page.cursor().shape() == Qt.BusyCursor
    finally:
        window.close()
        window.controller.close()


def test_cancel_defaults_probe_restores_process_cursor(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE source ./source\n", encoding="utf-8")
    state = _draft("source", "inv", "schematic")
    window = MtsMainWindow(session=None)
    try:
        page = window._active_page()
        assert page is not None
        window.source_edit.setText(str(cds))
        _append_source_cell(window, state)
        monkeypatch.setattr(
            window.controller,
            "read_source_defaults",
            lambda *_args, **_kwargs: 73,
        )
        window._enqueue_defaults_probe(state.key)
        assert page.testAttribute(Qt.WA_SetCursor)
        assert page.cursor().shape() == Qt.BusyCursor

        window._cancel_defaults_probes()

        assert window.defaults.active is None
        assert window.defaults.queue == []
        assert not page.testAttribute(Qt.WA_SetCursor)
        assert page.cursor().shape() == Qt.ArrowCursor
    finally:
        window.close()
        window.controller.close()


def test_defaults_busy_cursor_is_scoped_to_its_process_tab(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE source ./source\n", encoding="utf-8")
    first_state = _draft("source", "first", "schematic")
    second_state = _draft("source", "second", "schematic")
    window = MtsMainWindow(session=None)
    try:
        first_page = window._active_page()
        assert first_page is not None
        second_page = window._add_process_page()
        first_page.source_edit.setText(str(cds))
        second_page.source_edit.setText(str(cds))
        _append_source_cell(first_page, first_state)
        _append_source_cell(second_page, second_state)
        monkeypatch.setattr(
            first_page.controller,
            "read_source_defaults",
            lambda *_args, **_kwargs: 81,
        )

        first_page._enqueue_defaults_probe(first_state.key)

        assert first_page.testAttribute(Qt.WA_SetCursor)
        assert first_page.cursor().shape() == Qt.BusyCursor
        assert not second_page.testAttribute(Qt.WA_SetCursor)
        assert second_page.cursor().shape() == Qt.ArrowCursor

        first_page._cancel_defaults_probes()
        assert not first_page.testAttribute(Qt.WA_SetCursor)
        assert not second_page.testAttribute(Qt.WA_SetCursor)
    finally:
        window.close()


def test_defaults_probes_selected_cells_serially(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE source ./source\n", encoding="utf-8")
    window = MtsMainWindow(session=None)
    calls: list[str] = []
    tokens = iter((21,))
    try:
        window.source_edit.setText(str(cds))

        def read_defaults(source: SourceDesign, *, dialect: str, **_kwargs) -> int:
            calls.append(source.cell)
            return next(tokens)

        monkeypatch.setattr(window.controller, "read_source_defaults", read_defaults)
        first = _draft("source", "first", "schematic")
        second = _draft("source", "second", "schematic")
        _append_source_cell(window, first)
        _append_source_cell(window, second)

        window._enqueue_defaults_probe(first.key)
        window._enqueue_defaults_probe(second.key)
        assert calls == ["first"]
        assert window.defaults.active == 21
        assert window.defaults.queue == [second.key]

        first_result = SourceDefaults(
            "asi_initialization",
            "spectre",
            SourceDesign(cds, "source", "first"),
            (),
            (),
            "27",
            "1",
        )
        window._receive_state(
            ControllerState(token=21, stage="defaults_ready", defaults=first_result)
        )
        window._poll_state()

        # The first successful probe establishes the Project/source-context
        # baseline. The queued second cell receives an adapted copy instead
        # of starting another OCEAN process.
        assert calls == ["first"]
        assert window.defaults.active is None
        assert window.defaults.queue == []
        assert window.defaults.cache[second.key]["spectre"].source_key == second.key
        assert window.defaults.baselines["spectre"] is first_result
        assert _view(window, second.key).simulator.temp == "27"
    finally:
        window.close()
        window.controller.close()


def test_deleting_active_defaults_cell_cancels_only_that_probe(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE source ./source\n", encoding="utf-8")
    window = MtsMainWindow(session=None)
    canceled: list[bool] = []
    try:
        page = window._active_page()
        assert page is not None
        window.source_edit.setText(str(cds))
        first = _draft("source", "first", "schematic")
        second = _draft("source", "second", "schematic")
        first_item = _append_source_cell(window, first)
        _append_source_cell(window, second)
        window.source_cells_list.setCurrentItem(first_item)

        monkeypatch.setattr(
            window.controller,
            "read_source_defaults",
            lambda *_args, **_kwargs: 31,
        )
        cancel = window.controller.cancel

        def record_cancel():
            canceled.append(True)
            cancel()

        monkeypatch.setattr(window.controller, "cancel", record_cancel)
        window._enqueue_defaults_probe(first.key)
        window._enqueue_defaults_probe(second.key)
        window.controller._state = ControllerState(
            token=31, stage="probing_defaults", busy=True
        )
        assert page.testAttribute(Qt.WA_SetCursor)
        assert page.cursor().shape() == Qt.BusyCursor
        window._delete_source_cell(first_item)
        assert canceled == [True]
        assert window.defaults.active == 31
        assert first.key not in window.drafts
        assert window.run_button.isEnabled() is False  # second remains queued
        assert page.testAttribute(Qt.WA_SetCursor)
        assert page.cursor().shape() == Qt.BusyCursor
        second_item = window.source_cells_list.currentItem()
        window._delete_source_cell(second_item)
        assert window.run_button.isEnabled()
        assert not page.testAttribute(Qt.WA_SetCursor)
        assert page.cursor().shape() == Qt.ArrowCursor
    finally:
        window.close()
        window.controller.close()
