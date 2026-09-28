"""gui table cases regressions."""

from __future__ import annotations
from pathlib import Path
import pytest
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication,
    QComboBox,
)
from mtsnetlistor.gui.main_window import (  # noqa: E402
    MtsMainWindow,
)
from mtsnetlistor.gui.generation_coordinator import AcceptedGeneration


def test_gui_collects_process_and_advanced_options(
    application: QApplication, tmp_path: Path
) -> None:
    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE source ./source\n", encoding="utf-8")
    source_lib = tmp_path / "source"
    source_lib.mkdir()
    model_a = tmp_path / "model-a.lib"
    model_b = tmp_path / "model-b.lib"
    model_a.write_text("model a\n", encoding="utf-8")
    model_b.write_text("model b\n", encoding="utf-8")

    window = MtsMainWindow(session=None)
    try:
        window.source_edit.setText(str(cds))
        window.source_library.setText("source")
        window.source_cell.setText("top")
        window.source_view.setText("schematic")
        window.temp.setValue(27.0)
        window.tnom.setValue(25.0)
        window.scale.setValue(1.0)
        window.scalem.setValue(2.0)
        window.reltol.setValue(0.001)

        window._add_model()
        window._set_model_row(0, enabled=True, file=str(model_a), section="tt", label="core")
        window._add_model()
        window._set_model_row(1, enabled=False, file=str(model_b), section="ss", label="io")
        window.models_table.selectRow(1)
        window._move_model(-1)

        window._add_option()
        window.options_table.item(0, 1).setText("reltol")
        window.options_table.item(0, 2).setText("real")
        window.options_table.item(0, 3).setText("1e-3")
        window._add_option()
        window.options_table.item(1, 1).setText("method")
        window.options_table.item(1, 2).setText("enum")
        window.options_table.item(1, 3).setText("gear")
        window.options_table.item(1, 4).setText("trap,gear")
        window.options_table.selectRow(1)
        window._move_option(-1)

        request = window._request()
        assert request.process_options.temp == 27.0
        assert request.process_options.tnom == 25.0
        assert request.process_options.scale == 1.0
        assert request.process_options.scalem == 2.0
        assert request.process_options.reltol == 0.001
        assert [entry.file for entry in request.models] == [model_b.resolve(), model_a.resolve()]
        assert [entry.label for entry in request.models] == ["io", "core"]
        assert [option.name for option in request.simulator_options] == ["method", "reltol"]
        assert request.simulator_options[0].enum_values == ("trap", "gear")
    finally:
        window.close()
        window.controller.close()


def test_gui_rejects_invalid_advanced_option_via_shared_validator(
    application: QApplication, tmp_path: Path
) -> None:
    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE source ./source\n", encoding="utf-8")
    (tmp_path / "source").mkdir()
    window = MtsMainWindow(session=None)
    try:
        window.source_edit.setText(str(cds))
        window.source_library.setText("source")
        window.source_cell.setText("top")
        window._add_option()
        window.options_table.item(0, 1).setText("bad-name")
        window.options_table.item(0, 3).setText("1")
        with pytest.raises(Exception, match="invalid simulator option name"):
            window._request()
    finally:
        window.close()
        window.controller.close()


def test_temperature_and_scale_keep_user_text_until_request_validation(
    application: QApplication, tmp_path: Path
) -> None:
    cds = tmp_path / "cds.lib"
    source = tmp_path / "source"
    source.mkdir()
    cds.write_text(f"DEFINE source {source}\n", encoding="utf-8")
    window = MtsMainWindow(session=None)
    try:
        window.source_edit.setText(str(cds))
        window.source_library.setText("source")
        window.source_cell.setText("top")
        window.source_view.setText("schematic")
        window.temp.setText("27.125")
        window.scale.setText("1e-6")
        window.gmin.setText("1.00e-12")

        assert window.temp.text() == "27.125"
        assert window.scale.text() == "1e-6"
        assert window.gmin.text() == "1.00e-12"
        request = window._request()
        assert request.process_options.temp == 27.125
        assert request.process_options.scale == 1e-6
        assert request.process_options.gmin == "1.00e-12"
        assert window.temp.text() == "27.125"
        assert window.scale.text() == "1e-6"
        assert window.gmin.text() == "1.00e-12"

        window.temp.setText("not-a-number")
        with pytest.raises(ValueError, match="temp must be a real number"):
            window._request()
    finally:
        window.close()
        window.controller.close()


def test_model_file_corner_combo_parses_and_maps_to_section(
    application: QApplication, tmp_path: Path
) -> None:
    cds = tmp_path / "cds.lib"
    source = tmp_path / "source"
    source.mkdir()
    cds.write_text(f"DEFINE source {source}\n", encoding="utf-8")
    model = tmp_path / "models.lib"
    model.write_text(".lib tt\n.endl tt\n.lib ss\n.endl ss\n", encoding="utf-8")

    window = MtsMainWindow(session=None)
    try:
        window.source_edit.setText(str(cds))
        window.source_library.setText("source")
        window.source_cell.setText("top")
        window.source_view.setText("schematic")
        window._add_model()
        window._set_model_row(0, enabled=True, file=str(model), section="ss")
        corner = window.models_table.cellWidget(0, 2)
        assert isinstance(corner, QComboBox)
        assert [corner.itemText(index) for index in range(corner.count())] == ["tt", "ss"]
        assert corner.currentText() == "ss"
        assert window.models_table.horizontalHeaderItem(2).text() == "Corner"
        assert window._request().models[0].section == "ss"
    finally:
        window.close()
        window.controller.close()


def test_model_file_double_click_scans_selected_file_once(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = tmp_path / "models.lib"
    model.write_text(".lib tt\n", encoding="utf-8")
    window = MtsMainWindow(session=None)
    try:
        window._add_model()
        scans = []
        real_model_corners = window._model_corners
        monkeypatch.setattr(
            window._model_editor,
            "corners",
            lambda path: scans.append(path) or real_model_corners(path),
        )
        monkeypatch.setattr(
            "mtsnetlistor.gui.model_table.ask_file",
            lambda *_args, **_kwargs: (str(model), True),
        )
        window._model_cell_double_clicked(0, 1)
        assert scans == [str(model)]
        assert window.models_table.item(0, 1).text() == str(model)
    finally:
        window.close()
        window.controller.close()


def test_removing_model_or_advanced_option_invalidates_generation(
    application: QApplication,
) -> None:
    window = MtsMainWindow(session=None)
    try:
        window._add_model()
        window.models_table.selectRow(0)
        window.generation.accepted = AcceptedGeneration(object(), None)
        window._remove_model()
        assert window.generation.result is None

        window._add_option()
        window.options_table.selectRow(0)
        window.generation.accepted = AcceptedGeneration(object(), None)
        window._remove_option()
        assert window.generation.result is None
    finally:
        window.close()
        window.controller.close()


def test_hidden_legacy_process_controls_invalidate_generation(
    application: QApplication,
):
    window = MtsMainWindow(session=None)
    try:
        for spin in (window.tnom, window.scalem, window.reltol):
            window.generation.accepted = AcceptedGeneration(object(), None)
            value = spin.value()
            spin.setValue(spin.minimum() if value != spin.minimum() else value + 1.0)
            assert window.generation.result is None
    finally:
        window.close()
        window.controller.close()
