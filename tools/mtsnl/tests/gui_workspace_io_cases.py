"""gui workspace io cases regressions."""

from __future__ import annotations
from pathlib import Path
import pytest
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication,
)
from mtsnetlistor.gui.main_window import (  # noqa: E402
    MtsMainWindow,
)
from mtsnetlistor.gui.generation_coordinator import AcceptedGeneration
from mtsnetlistor.model import SourceDesign  # noqa: E402
from gui_window_fixtures import _view


def test_gui_config_round_trip_preserves_startup_and_simrc(
    application: QApplication, tmp_path: Path
) -> None:
    from mtsnetlistor.config import request_to_dict
    from mtsnetlistor.model import CellNetlistSpec, NetlistRequest

    cds = tmp_path / "cds.lib"
    source = tmp_path / "source"
    source.mkdir()
    cds.write_text(f"DEFINE source {source}\n", encoding="utf-8")
    startup = tmp_path / "startup.il"
    startup.write_text("t\n", encoding="utf-8")
    simrc = tmp_path / ".simrc"
    simrc.write_text("simrc\n", encoding="utf-8")
    request = NetlistRequest(
        SourceDesign(cds, "source", "first", "schematic", startup, simrc),
        cell_specs=(
            CellNetlistSpec("source", "first"),
            CellNetlistSpec("source", "second"),
        ),
    ).validate()

    window = MtsMainWindow(session=None)
    try:
        window._refresh_source = lambda: None
        window._apply_request_config(request)
        assert request_to_dict(window._request()) == request_to_dict(request)
    finally:
        window.close()
        window.controller.close()


def test_gui_workspace_config_round_trip_replaces_all_process_tabs(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mtsnetlistor.config import (
        WorkspaceConfig,
        WorkspaceProcess,
        request_to_dict,
        save_workspace,
    )
    from mtsnetlistor.model import CellNetlistSpec, NetlistRequest

    requests = []
    for index in (1, 2):
        root = tmp_path / f"process{index}"
        root.mkdir()
        cds = root / "cds.lib"
        library = f"source{index}"
        (root / library).mkdir()
        cds.write_text(f"DEFINE {library} ./{library}\n", encoding="utf-8")
        requests.append(
            NetlistRequest(
                SourceDesign(cds, library, f"cell{index}"),
                cell_specs=(
                    CellNetlistSpec(library, f"cell{index}"),
                    CellNetlistSpec(library, f"aux{index}"),
                ),
            ).validate()
        )
    config = save_workspace(
        WorkspaceConfig(
            tuple(
                WorkspaceProcess(f"Process{index}", request)
                for index, request in enumerate(requests, start=1)
            )
        ),
        tmp_path / "workspace.toml",
    )

    window = MtsMainWindow(session=None)
    try:
        refreshes = []
        monkeypatch.setattr(
            "mtsnetlistor.gui.main_window.ProcessPage._refresh_source",
            lambda page: refreshes.append(page.source_edit.text()),
        )
        monkeypatch.setattr(
            "mtsnetlistor.gui.workspace_actions.ask_file",
            lambda *_args, **_kwargs: (str(config), True),
        )
        window._load_workspace_config()

        assert [window.process_tabs.tabText(i) for i in range(window.process_tabs.count())] == [
            "Process1",
            "Process2",
            "New Process",
        ]
        assert len(window._pages) == 2
        assert [request_to_dict(page._request()) for page in window._pages] == [
            request_to_dict(request) for request in requests
        ]
        assert refreshes == [str(request.source.cds_lib) for request in requests]
    finally:
        window.close()
        window.controller.close()


def test_host_save_config_writes_all_process_tabs_to_one_workspace_file(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mtsnetlistor.config import load_workspace
    from mtsnetlistor.model import NetlistRequest

    requests = []
    for index in (1, 2):
        root = tmp_path / f"process{index}"
        root.mkdir()
        cds = root / "cds.lib"
        library = f"source{index}"
        cds.write_text(f"DEFINE {library} ./{library}\n", encoding="utf-8")
        requests.append(
            NetlistRequest(SourceDesign(cds, library, f"cell{index}")).validate()
        )

    destination = tmp_path / "workspace.toml"
    window = MtsMainWindow(session=None)
    try:
        window._tab_clicked(1)
        for page, request in zip(window._pages, requests):
            page._apply_request_config(request, refresh_catalog=False)
        monkeypatch.setattr(
            "mtsnetlistor.gui.workspace_actions.ask_save_file",
            lambda *_args, **_kwargs: (str(destination), True),
        )
        window._save_workspace_config()

        loaded = load_workspace(destination)
        assert [process.name for process in loaded.processes] == [
            "Process1",
            "Process2",
        ]
        assert [
            "/".join((process.request.source.library, process.request.source.cell, process.request.source.view))
            for process in loaded.processes
        ] == ["source1/cell1/schematic", "source2/cell2/schematic"]
        assert "Configuration saved:" in window._pages[-1].log.toPlainText()
    finally:
        window.close()
        window.controller.close()


def test_workspace_save_load_preserves_raw_process_field_text(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mtsnetlistor.config import load_workspace
    from mtsnetlistor.model import NetlistRequest, ProcessOptions

    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE source ./source\n", encoding="utf-8")
    request = NetlistRequest(
        SourceDesign(cds, "source", "top"),
        process_options=ProcessOptions(temp=27, scale=1e-6, gmin="1.00e-12"),
    ).validate()
    destination = tmp_path / "workspace.toml"
    window = MtsMainWindow(session=None)
    try:
        page = window._pages[0]
        page._apply_request_config(request, refresh_catalog=False)
        page.temp.setText("27.000")
        page.scale.setText("1e-6")
        page.gmin.setText("1.00e-12")
        monkeypatch.setattr(
            "mtsnetlistor.gui.workspace_actions.ask_save_file",
            lambda *_args, **_kwargs: (str(destination), True),
        )
        window._save_workspace_config()
        loaded = load_workspace(destination)
        presentation = loaded.processes[0].presentation[0]
        assert presentation.temperature_text == "27.000"
        assert presentation.scale_text == "1e-6"
        assert presentation.gmin_text == "1.00e-12"
    finally:
        window.close()
        window.controller.close()


def test_gui_workspace_invalid_load_keeps_existing_tabs(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invalid = tmp_path / "invalid.toml"
    invalid.write_text(
        'format = "sico-mts-netlistor-request"\nschema_version = 1\n',
        encoding="utf-8",
    )
    window = MtsMainWindow(session=None)
    try:
        page = window._pages[0]
        page.source_edit.setText("/keep/current/cds.lib")
        monkeypatch.setattr(
            "mtsnetlistor.gui.workspace_actions.ask_file",
            lambda *_args, **_kwargs: (str(invalid), True),
        )
        window._load_workspace_config()
        assert window._pages == [page]
        assert window.source_edit.text() == "/keep/current/cds.lib"
        assert "Error:" in window.log.toPlainText()
    finally:
        window.close()
        window.controller.close()


def test_loading_config_preserves_unknown_target_and_invalidates_old_generation(
    application: QApplication, tmp_path: Path
) -> None:
    from mtsnetlistor.model import CellNetlistSpec, NetlistRequest, TargetSelection

    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE source ./source\n", encoding="utf-8")
    (tmp_path / "source").mkdir()
    request = NetlistRequest(
        SourceDesign(cds, "source", "top"),
        cell_specs=(
            CellNetlistSpec(
                "source",
                "top",
                target=TargetSelection(
                    library="configuredTarget",
                    generate_netlist_view=True,
                ),
            ),
        ),
    ).validate()
    window = MtsMainWindow(session=None)
    try:
        window.generation.accepted = AcceptedGeneration(object(), None)
        window.target_library.addItem("firstWritable")
        window._apply_request_config(request)
        assert window.target_library.currentText() == "configuredTarget"
        assert _view(window, ("source", "top", "schematic")).publication.target_library == "configuredTarget"
        assert window.generation.result is None
        assert not window.publish_button.isEnabled()
    finally:
        window.close()
        window.controller.close()
