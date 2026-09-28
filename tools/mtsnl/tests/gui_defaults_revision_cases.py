"""gui defaults revision cases regressions."""

from __future__ import annotations
from pathlib import Path
import pytest
from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication,
    QListWidgetItem,
)
from mtsnetlistor.gui.controller import ControllerState  # noqa: E402
from mtsnetlistor.gui.main_window import (  # noqa: E402
    MtsMainWindow,
)
from mtsnetlistor.gui.defaults_coordinator import DefaultsRequestContext
from mtsnetlistor.defaults import SourceDefaults  # noqa: E402
from mtsnetlistor.model import ModelEntry, SimulatorOption, SourceDesign  # noqa: E402
from gui_window_fixtures import _draft, _view, _text, _append_source_cell


def test_gui_discards_defaults_after_requesting_cell_changes(
    application: QApplication, tmp_path: Path
) -> None:
    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE source ./source\n", encoding="utf-8")
    window = MtsMainWindow(session=None)
    try:
        window.source_edit.setText(str(cds))
        state = _draft("source", "inv", "schematic")
        item = _append_source_cell(window, state)
        window.source_cells_list.setCurrentItem(item)
        result = SourceDefaults(
            "asi_initialization",
            "spectre",
            SourceDesign(cds, "source", "inv", "schematic"),
            (),
            (),
            "27",
            "1.0",
        )
        # The request was made against this cell's revision 0. A later edit
        # to the same cell must win even though the OCEAN worker returns a
        # valid source/dialect report.
        window.defaults.pending[17] = DefaultsRequestContext(
            SourceDesign(cds, "source", "inv", "schematic"),
            state.key,
            "spectre",
            window.drafts.revision(state.key, "spectre"),
        )
        window.temp.setText("28")
        assert window.drafts.revision(state.key, "spectre").cell_revision == 1
        window._receive_state(
            ControllerState(token=17, stage="defaults_ready", defaults=result)
        )
        window._poll_state()
        assert window.defaults.last_report is None
        assert window.temp.text() == "28"
        assert "Discarded stale PDK defaults" in window.log.toPlainText()
    finally:
        window.close()
        window.controller.close()


def test_gui_applies_defaults_to_requesting_cell_after_user_browses_another_cell(
    application: QApplication, tmp_path: Path
) -> None:
    """An isolated worker result must keep its original Source Cell identity."""

    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE source ./source\n", encoding="utf-8")
    model = tmp_path / "models.scs"
    model.write_text("// model\n", encoding="utf-8")
    window = MtsMainWindow(session=None)
    try:
        window.source_edit.setText(str(cds))
        first = _draft("source", "first", "schematic", temp="11", scale="1")
        second = _draft("source", "second", "schematic", temp="22", scale="2")
        first_item = _append_source_cell(window, first)
        second_item = _append_source_cell(window, second)
        window.source_cells_list.setCurrentItem(first_item)
        window.source_cells_list.setCurrentItem(second_item)
        assert window._active_cell_key == second.key

        result = SourceDefaults(
            "asi_initialization",
            "spectre",
            SourceDesign(cds, "source", "first", "schematic"),
            (ModelEntry(model, "tt"),),
            (SimulatorOption("reltol", "1e-3", "real"),),
            "27.000",
            "1e-6",
            gmin_text="1.00e-12",
        )
        window.defaults.pending[17] = DefaultsRequestContext(
            SourceDesign(cds, "source", "first", "schematic"),
            first.key,
            "spectre",
            window.drafts.revision(first.key, "spectre"),
        )
        # This edit raises the broad generation revision, but only B's cell
        # revision. It must not make A's isolated defaults result stale.
        window.temp.setText("33")
        assert window.drafts.revision(first.key, "spectre").cell_revision == 0
        assert window.drafts.revision(second.key, "spectre").cell_revision == 1
        window._receive_state(
            ControllerState(token=17, stage="defaults_ready", defaults=result)
        )
        window._poll_state()

        # The visible second cell was not overwritten by a result for first.
        assert window._active_cell_key == second.key
        assert window.temp.text() == "33"
        assert window.scale.text() == "2"

        # The result remains with first and loads when that Source Cell is
        # selected later, rather than being lost during the browse action.
        applied = _view(window, first.key)
        assert applied.simulator.models == ((True, str(model), "tt", ""),)
        assert applied.simulator.options == ()
        assert _text(window, first.key) == (
            "27.000",
            "1e-6",
            "1.00e-12",
        )
        window.source_cells_list.setCurrentItem(first_item)
        assert _view(window, second.key).simulator.temp == "33"
        assert window.temp.text() == "27.000"
        assert window.scale.text() == "1e-6"
        assert window.gmin.text() == "1.00e-12"
        assert window._request().cell_specs[0].process_options.gmin == "1.00e-12"
        assert window.models_table.rowCount() == 1
    finally:
        window.close()
        window.controller.close()


def test_gui_preserves_requesting_cell_edits_when_defaults_return_late(
    application: QApplication, tmp_path: Path
) -> None:
    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE source ./source\n", encoding="utf-8")
    original = tmp_path / "original.scs"
    discovered = tmp_path / "discovered.scs"
    original.write_text("// original\n", encoding="utf-8")
    discovered.write_text("// discovered\n", encoding="utf-8")
    window = MtsMainWindow(session=None)
    try:
        window.source_edit.setText(str(cds))
        first = _draft(
            "source", "first", "schematic", models=((True, str(original), "", ""),)
        )
        second = _draft("source", "second", "schematic")
        first_item = _append_source_cell(window, first)
        second_item = _append_source_cell(window, second)
        window.source_cells_list.setCurrentItem(first_item)
        window.drafts.edited(first.key, "spectre")
        window.source_cells_list.setCurrentItem(second_item)

        result = SourceDefaults(
            "asi_initialization",
            "spectre",
            SourceDesign(cds, "source", "first", "schematic"),
            (ModelEntry(discovered, "tt"),),
            (),
            "27",
            "1",
        )
        window.defaults.pending[23] = DefaultsRequestContext(
            SourceDesign(cds, "source", "first", "schematic"),
            first.key,
            "spectre",
            window.drafts.revision(first.key, "spectre"),
        )
        window._receive_state(
            ControllerState(token=23, stage="defaults_ready", defaults=result)
        )
        window._poll_state()

        assert _view(window, first.key).simulator.models == first.simulator.models
        assert window.drafts.dirty(first.key)
        assert "user edits preserved" in window.log.toPlainText()
    finally:
        window.close()
        window.controller.close()


def test_automatic_defaults_use_dialect_revision_and_not_other_dialect_edit(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Editing hspiceD must not discard an in-flight spectre probe."""

    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE source ./source\n", encoding="utf-8")
    spectre_model = tmp_path / "spectre.scs"
    spectre_model.write_text("// spectre\n", encoding="utf-8")
    key = ("source", "inv", "schematic")
    window = MtsMainWindow(session=None)
    try:
        window.source_edit.setText(str(cds))
        calls: list[str] = []
        monkeypatch.setattr(
            window.controller,
            "read_source_defaults",
            lambda _source, *, dialect, **_kwargs: calls.append(dialect) or 41,
        )
        item = QListWidgetItem("schematic")
        item.setData(Qt.UserRole, key)
        window.source_view_list.addItem(item)
        window.source_view_list.setCurrentItem(item)
        window._select_source_view()
        assert calls == ["spectre"]

        # Switch to the other dialect and edit it while spectre is still
        # running. The spectre context must remain valid.
        window.simulator.setCurrentText("hspiceD")
        window.temp.setText("99")
        window._save_active_cell_state()
        assert window.drafts.dirty(key, "hspiceD")
        result = SourceDefaults(
            "asi_initialization",
            "spectre",
            SourceDesign(cds, *key),
            (ModelEntry(spectre_model, "tt"),),
            (),
            "27",
            "1",
        )
        window._receive_state(
            ControllerState(token=41, stage="defaults_ready", defaults=result)
        )
        window._poll_state()
        assert window.defaults.cache[key]["spectre"] is result
        assert _view(window, key, "spectre").simulator.models == (
            (True, str(spectre_model), "tt", ""),
        )
        # hspiceD is still dirty and retains the user's text.
        assert _view(window, key, "hspiceD").simulator.temp == "99"
    finally:
        window.close()
        window.controller.close()
