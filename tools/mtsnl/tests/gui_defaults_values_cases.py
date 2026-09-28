"""gui defaults values cases regressions."""

from __future__ import annotations
from pathlib import Path
import pytest
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication,
)
from mtsnetlistor.gui.main_window import (  # noqa: E402
    MtsMainWindow,
)
from mtsnetlistor.defaults import DefaultsReport, SourceDefaults  # noqa: E402
from mtsnetlistor.model import ModelEntry, SimulatorOption, SourceDesign  # noqa: E402
from gui_window_fixtures import _draft, _view, _text, _append_source_cell


def test_gui_applies_typed_defaults_without_changing_publication_state(
    application: QApplication, tmp_path: Path
) -> None:
    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE source ./source\n", encoding="utf-8")
    model = tmp_path / "models.scs"
    model.write_text("// model\n", encoding="utf-8")
    window = MtsMainWindow(session=None)
    try:
        window.source_edit.setText(str(cds))
        state = _draft(
            "source",
            "inv",
            "schematic",
            target_library="target",
            target_cell="inv_mts",
            publish_symbol=True,
            overwrite_symbol=True,
            publish_text=True,
            overwrite_text=True,
        )
        item = _append_source_cell(window, state)
        window.source_cells_list.setCurrentItem(item)
        result = SourceDefaults(
            "asi_initialization",
            "spectre",
            SourceDesign(cds, "source", "inv"),
            (ModelEntry(model, "tt", enabled=False),),
            (SimulatorOption("reltol", "1e-3", "real"),),
            "27.000",
            "1e-6",
            gmin_text="1.00e-12",
        )

        window._apply_defaults_report(result)

        applied = _view(window, state.key)
        assert window.defaults.last_report is result
        assert applied.simulator.models == ((False, str(model), "tt", ""),)
        # Runtime defaults expose models and the dedicated process fields;
        # the complete simulator-option snapshot remains in defaults.json.
        assert applied.simulator.options == ()
        assert window.temp.text() == "27.000"
        assert window.scale.text() == "1e-6"
        assert window.gmin.text() == "1.00e-12"
        assert applied.simulator.gmin == "1.00e-12"
        assert applied.publication.target_library == "target"
        assert applied.publication.target_cell == "inv_mts"
        assert applied.publication.publish_symbol and applied.publication.overwrite_symbol
        assert applied.publication.publish_text and applied.publication.overwrite_text
    finally:
        window.close()
        window.controller.close()


def test_gui_normalizes_nested_gmin_from_raw_defaults_report(
    application: QApplication, tmp_path: Path
) -> None:
    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE source ./source\n", encoding="utf-8")
    source = SourceDesign(cds, "source", "inv", "schematic").validate()
    state = _draft("source", "inv", "schematic")
    report = DefaultsReport(
        status="succeeded",
        dialect="spectre",
        tool_name="spectre",
        source={
            "cds_lib": str(source.cds_lib),
            "library": source.library,
            "cell": source.cell,
            "view": source.view,
        },
        baseline={},
        after_design={
            "model_files": [],
            "environment_options": {},
            "simulator_options": {
                "gmin": [["value", "1.00e-12"], ["choices", None]]
            },
        },
        after_startup_simrc={},
    )
    window = MtsMainWindow(session=None)
    try:
        window.source_edit.setText(str(cds))
        item = _append_source_cell(window, state)
        window.source_cells_list.setCurrentItem(item)

        window._apply_defaults_report(report, requested_key=state.key)

        assert window.gmin.text() == "1.00e-12"
        assert _view(window, state.key).simulator.gmin == "1.00e-12"
        assert window.defaults.last_report is report
    finally:
        window.close()
        window.controller.close()


def test_defaults_report_missing_fields_preserves_existing_process_values(
    application: QApplication, tmp_path: Path
) -> None:
    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE source ./source\n", encoding="utf-8")
    window = MtsMainWindow(session=None)
    try:
        key = ("source", "inv", "schematic")
        state = _draft(
            *key,
            temp="27.000",
            scale="1e-6",
            gmin="1.00e-12",
            models=(),
        )
        window.source_edit.setText(str(cds))
        item = _append_source_cell(window, state)
        window.source_cells_list.setCurrentItem(item)
        report = SourceDefaults(
            "asi_initialization",
            "spectre",
            SourceDesign(cds, "source", "inv"),
            (),
            (),
        )
        window._apply_defaults_report(report, requested_key=key)
        applied = _view(window, key)
        assert applied.simulator.temp == "27.000"
        assert applied.simulator.scale == "1e-6"
        assert applied.simulator.gmin == "1.00e-12"
        assert _text(window, key) == ("27.000", "1e-6", "1.00e-12")
    finally:
        window.close()
        window.controller.close()


def test_legacy_defaults_probe_helper_uses_selected_cell_dialect(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Direct helper callers retain the selected hspiceD dialect contract."""

    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE source ./source\n", encoding="utf-8")
    key = ("source", "inv", "schematic")
    window = MtsMainWindow(session=None)
    try:
        window.source_edit.setText(str(cds))
        window.drafts.add(key, dialect="hspiceD")
        window._active_cell_key = key
        window.simulator.setCurrentText("hspiceD")
        calls: list[str] = []
        monkeypatch.setattr(
            window.controller,
            "read_source_defaults",
            lambda _source, *, dialect, **_kwargs: calls.append(dialect) or 71,
        )
        window._enqueue_defaults_probe(key)
        assert calls == ["hspiceD"]
    finally:
        window.close()
        window.controller.close()
