"""config workspace cases regressions."""

from __future__ import annotations
from pathlib import Path
import pytest
from mtsnetlistor.config import (
    WorkspaceCellPresentation,
    WorkspaceConfig,
    WorkspaceProcess,
    load_workspace,
    request_to_dict,
    save_workspace,
)
from mtsnetlistor.errors import RequestValidationError
from mtsnetlistor.model import (
    CellNetlistSpec,
    NetlistRequest,
    ProcessOptions,
    SourceDesign,
    TargetSelection,
)
from config_fixtures import (
    _write_request,
)


def test_workspace_v2_round_trips_multiple_processes_in_order(tmp_path: Path) -> None:
    cds_a = tmp_path / "a.cds.lib"
    cds_b = tmp_path / "b.cds.lib"
    cds_a.write_text("DEFINE workA ./workA\n", encoding="utf-8")
    cds_b.write_text("DEFINE workB ./workB\n", encoding="utf-8")
    (tmp_path / "workA").mkdir()
    (tmp_path / "workB").mkdir()
    request_a = NetlistRequest(
        SourceDesign(cds_a, "workA", "first"),
        cell_specs=(
            CellNetlistSpec(
                "workA",
                "first",
                process_options=ProcessOptions(
                    temp=27, scale=1e-6, gmin="1.00e-12"
                ),
                target=TargetSelection(
                    library="targetA", generate_netlist_view=True
                ),
            ),
            CellNetlistSpec(
                "workA",
                "second",
                process_options=ProcessOptions(temp=85, gmin="2.50e-13"),
            ),
        ),
    ).validate()
    request_b = NetlistRequest(
        SourceDesign(cds_b, "workB", "inv"),
        dialect="hspiceD",
        process_options=ProcessOptions(scale=0.9),
    ).validate()
    workspace = WorkspaceConfig(
        (
            WorkspaceProcess("Process1", request_a),
            WorkspaceProcess("Memory Process", request_b),
        )
    )

    destination = save_workspace(workspace, tmp_path / "workspace.toml")
    loaded = load_workspace(destination)

    assert destination.read_text(encoding="utf-8").startswith(
        'format = "sico-mts-netlistor-workspace"\nschema_version = 2\n'
    )
    assert [process.name for process in loaded.processes] == [
        "Process1",
        "Memory Process",
    ]
    assert [request_to_dict(process.request) for process in loaded.processes] == [
        request_to_dict(request_a),
        request_to_dict(request_b),
    ]
    rendered = destination.read_text(encoding="utf-8")
    assert 'gmin = "1.00e-12"' in rendered
    assert 'gmin = "2.50e-13"' in rendered
    assert [
        spec.process_options.gmin for spec in loaded.processes[0].request.cell_specs
    ] == ["1.00e-12", "2.50e-13"]
    assert loaded.processes[1].request.process_options.gmin is None


def test_workspace_preserves_raw_process_field_text(tmp_path: Path) -> None:
    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE work ./work\n", encoding="utf-8")
    request = NetlistRequest(
        SourceDesign(cds, "work", "top"),
        process_options=ProcessOptions(temp=27, scale=1e-6, gmin="1.00e-12"),
    ).validate()
    workspace = WorkspaceConfig(
        (
            WorkspaceProcess(
                "Process1",
                request,
                (
                    WorkspaceCellPresentation(
                        "work", "top", "schematic", "27.000", "1e-6", "1.00e-12"
                    ),
                ),
            ),
        )
    )
    destination = save_workspace(workspace, tmp_path / "workspace.toml")
    loaded = load_workspace(destination)
    presentation = loaded.processes[0].presentation[0]
    assert (presentation.temperature_text, presentation.scale_text, presentation.gmin_text) == (
        "27.000",
        "1e-6",
        "1.00e-12",
    )


def test_workspace_preserves_source_project_module_name(tmp_path: Path) -> None:
    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE work ./work\n", encoding="utf-8")
    request = NetlistRequest(SourceDesign(cds, "work", "top")).validate()
    destination = save_workspace(
        WorkspaceConfig((WorkspaceProcess("Process1", request, (), "proj2"),)),
        tmp_path / "workspace.toml",
    )
    loaded = load_workspace(destination)
    assert loaded.processes[0].project == "proj2"
    assert 'project = "proj2"' in destination.read_text(encoding="utf-8")


def test_workspace_rejects_presentation_for_unselected_cell(tmp_path: Path) -> None:
    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE work ./work\n", encoding="utf-8")
    request = NetlistRequest(SourceDesign(cds, "work", "top")).validate()
    workspace = WorkspaceConfig(
        (
            WorkspaceProcess(
                "Process1",
                request,
                (WorkspaceCellPresentation("work", "other"),),
            ),
        )
    )
    with pytest.raises(RequestValidationError, match="not selected"):
        workspace.validate()


def test_workspace_loader_rejects_v1_request_document(tmp_path: Path) -> None:
    request_path, _ = _write_request(tmp_path)
    with pytest.raises(RequestValidationError, match="workspace root|workspace format"):
        load_workspace(request_path)


@pytest.mark.parametrize("schema_version", ['"2"', "true", "2.0"])
def test_workspace_loader_rejects_non_integer_schema_version(
    tmp_path: Path, schema_version: str
) -> None:
    path = tmp_path / "workspace.toml"
    path.write_text(
        "format = \"sico-mts-netlistor-workspace\"\n"
        f"schema_version = {schema_version}\n"
        "processes = []\n",
        encoding="utf-8",
    )
    with pytest.raises(RequestValidationError, match="schema_version must be 2"):
        load_workspace(path)
