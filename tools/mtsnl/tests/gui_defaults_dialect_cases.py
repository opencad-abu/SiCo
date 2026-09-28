"""gui defaults dialect cases regressions."""

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
from mtsnetlistor.defaults import SourceDefaults  # noqa: E402
from mtsnetlistor.model import ModelEntry, SimulatorOption, SourceDesign  # noqa: E402
from gui_window_fixtures import _view


def test_select_source_view_prefetches_both_simulators_and_switch_uses_cache(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source selection initializes both dialects exactly once.

    The controller is latest-request-only, so the two probes are deliberately
    completed one at a time here.  Once both reports have arrived, changing the
    simulator selector must only restore the per-dialect cached form and must
    not start another isolated OCEAN worker.
    """

    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE source ./source\n", encoding="utf-8")
    spectre_model = tmp_path / "spectre.scs"
    hspice_model = tmp_path / "hspice.lib"
    spectre_model.write_text("// spectre\n", encoding="utf-8")
    hspice_model.write_text("* hspice\n", encoding="utf-8")
    key = ("source", "inv", "schematic")
    window = MtsMainWindow(session=None)
    calls: list[str] = []
    tokens = iter((17, 18))
    try:
        page = window._active_page()
        assert page is not None
        window.source_edit.setText(str(cds))

        def read_defaults(source: SourceDesign, *, dialect: str, **_kwargs) -> int:
            assert (source.library, source.cell, source.view) == key
            calls.append(dialect)
            return next(tokens)

        monkeypatch.setattr(window.controller, "read_source_defaults", read_defaults)
        item = QListWidgetItem("schematic")
        item.setData(Qt.UserRole, key)
        window.source_view_list.addItem(item)
        window.source_view_list.setCurrentItem(item)
        window._select_source_view()

        # Selection starts the first job and leaves the other dialect queued;
        # it does not race two OCEAN workers through the latest-only controller.
        assert calls == ["spectre"]
        assert window.defaults.active == 17
        assert window.defaults.dialects[key] == ["hspiceD"]
        assert page.testAttribute(Qt.WA_SetCursor)
        assert page.cursor().shape() == Qt.BusyCursor

        spectre = SourceDefaults(
            "asi_initialization",
            "spectre",
            SourceDesign(cds, *key),
            (ModelEntry(spectre_model, "tt"),),
            (
                SimulatorOption(
                    "method",
                    "trap",
                    "enum",
                    enum_values=("trap", "gear"),
                ),
            ),
            "27.000",
            "1e-6",
            gmin_text="1e-12",
        )
        window._receive_state(
            ControllerState(
                token=17,
                stage="defaults_ready",
                busy=False,
                defaults=spectre,
            )
        )
        window._poll_state()

        # Completing spectre releases the controller slot and starts the
        # queued hspiceD probe for the same source identity.
        assert calls == ["spectre", "hspiceD"]
        assert window.defaults.active == 18
        assert window.defaults.cache[key]["spectre"] is spectre
        assert page.testAttribute(Qt.WA_SetCursor)
        assert page.cursor().shape() == Qt.BusyCursor

        hspice = SourceDefaults(
            "asi_initialization",
            "hspiceD",
            SourceDesign(cds, *key),
            (ModelEntry(hspice_model, "tt"),),
            (
                SimulatorOption(
                    "parhier",
                    "LOCAL",
                    "enum",
                    enum_values=("LOCAL", "GLOBAL"),
                ),
            ),
            "85",
            "0.9",
            gmin_text="2e-12",
        )
        window._receive_state(
            ControllerState(
                token=18,
                stage="defaults_ready",
                busy=False,
                defaults=hspice,
            )
        )
        window._poll_state()
        assert window.defaults.active is None
        assert window.defaults.dialects[key] == []
        assert window.defaults.cache[key]["hspiceD"] is hspice
        assert not page.testAttribute(Qt.WA_SetCursor)
        assert page.cursor().shape() == Qt.ArrowCursor

        # Switching to hspiceD restores its independent values and options;
        # switching back restores spectre. Neither transition probes again.
        window.simulator.setCurrentText("hspiceD")
        assert calls == ["spectre", "hspiceD"]
        assert window.temp.text() == "85"
        assert window.scale.text() == "0.9"
        assert window.gmin.text() == "2e-12"
        assert _view(window, key).dialect == "hspiceD"
        assert _view(window, key).simulator.models == (
            (True, str(hspice_model), "tt", ""),
        )

        window.simulator.setCurrentText("spectre")
        assert calls == ["spectre", "hspiceD"]
        assert window.temp.text() == "27.000"
        assert window.scale.text() == "1e-6"
        assert window.gmin.text() == "1e-12"
        assert _view(window, key).dialect == "spectre"
        assert _view(window, key).simulator.models == (
            (True, str(spectre_model), "tt", ""),
        )

        # Removing the source item must discard all simulator-specific cache
        # and pending-dialect state, so a late worker cannot resurrect it.
        source_item = window.source_cells_list.currentItem()
        assert source_item is not None
        window._delete_source_cell(source_item)
        assert key not in window.defaults.cache
        assert key not in window.defaults.dialects
        assert key not in window.drafts
    finally:
        window.close()
        window.controller.close()
