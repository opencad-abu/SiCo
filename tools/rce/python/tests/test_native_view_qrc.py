from __future__ import annotations

import sys
from pathlib import Path

import pytest


PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from rcepy.config import RceConfig  # noqa: E402
from rcepy.gen_qrc import generate_qrc  # noqa: E402


def _config(
    tmp_path: Path,
    *,
    input_type: str = "OA",
    output_type: str = "view",
    view: dict[str, object] | None = None,
    netlist: dict[str, object] | None = None,
    create_cds_lib: bool = True,
) -> RceConfig:
    corner = tmp_path / "tech/RCmax"
    corner.mkdir(parents=True, exist_ok=True)
    (corner / "qrcTechFile").write_text("tech\n", encoding="utf-8")
    cds_lib = tmp_path / "cds.lib"
    if create_cds_lib:
        cds_lib.write_text("DEFINE layout_lib ./layout_lib\n", encoding="utf-8")

    return RceConfig(
        raw={
            "run": {
                "run_dir": str(tmp_path / "run"),
                "cds_lib": str(cds_lib),
            },
            "input": {
                "type": input_type,
                "schematic": {
                    "lib": "source_lib",
                    "cell": "source_top",
                    "view": "schematic",
                },
                "layout": {
                    "lib": "layout_lib",
                    "cell": "layout_top",
                    "view": "layout",
                },
            },
            "lvs": {"tool": "Calibre"},
            "extract": {
                "tool": "QRC",
                "tech_dir": str(tmp_path / "tech"),
                "corner": "RCmax",
                "temperature": "25",
                "rc_type": "R+Cg+Cc",
                "output_type": output_type,
                "name_source": "layout",
                "view": view or {},
            },
            "runtime": {"lvs_cpus": "1", "ext_cpus": "1"},
            "netlist": netlist or {},
        },
        config_path=tmp_path / "rce.toml",
    )


def _command_section(command: str, name: str) -> str:
    return command.split(f"{name} \\\n", 1)[1].split("\n\n", 1)[0]


def test_view_defaults_to_native_smart_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = _config(tmp_path)

    command = generate_qrc(cfg, cfg.context()).read_text(encoding="utf-8")
    input_db = _command_section(command, "input_db")
    output_db = _command_section(command, "output_db")
    output_setup = _command_section(command, "output_setup")

    assert '-design_cell_name "layout_top layout layout_lib"' in input_db
    assert f'-library_definitions_file "{tmp_path / "cds.lib"}"' in input_db
    assert "-format" not in input_db
    assert "-type smart_view" in output_db
    assert '-view_name "av_extracted"' in output_db
    assert '-res_component "presistor"' in output_db
    assert "-cap_component" not in output_db
    assert "-cap_property_name" not in output_db
    assert "-res_property_name" not in output_db
    assert "-enable_cellview_check" not in output_db
    assert '-net_name_space "SCHEMATIC"' in output_setup
    assert '-temporary_directory_name "qrcTemp"' in output_setup
    assert "-file_name" not in output_setup


def test_qrc_temp_directory_remains_tool_native_after_cwd_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launch_temp = tmp_path / "launch" / ".cad"
    changed = tmp_path / "changed"
    changed.mkdir()
    monkeypatch.chdir(changed)
    monkeypatch.setenv("CAD_TEMP_DIR", str(launch_temp))
    cfg = _config(tmp_path)

    command = generate_qrc(cfg, cfg.context()).read_text(encoding="utf-8")

    assert '-temporary_directory_name "qrcTemp"' in command
    assert str(launch_temp) not in command


def test_extracted_view_uses_target_and_extracted_only_options(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path,
        view={
            "kind": "extracted",
            "library": "results_lib",
            "cell": "simulation_top",
            "name": "my_extracted",
            "layout_view": "maskLayout",
        },
    )

    command = generate_qrc(cfg, cfg.context()).read_text(encoding="utf-8")
    input_db = _command_section(command, "input_db")
    output_db = _command_section(command, "output_db")

    assert '-design_cell_name "simulation_top maskLayout results_lib"' in input_db
    assert "-format" not in input_db
    assert "-type extracted_view" in output_db
    assert '-view_name "my_extracted"' in output_db
    assert '-cap_component "pcapacitor"' in output_db
    assert '-cap_property_name "c"' in output_db
    assert '-res_component "presistor"' in output_db
    assert '-res_property_name "r"' in output_db
    assert "-enable_cellview_check true" in output_db


@pytest.mark.parametrize(
    ("output_type", "qrc_type", "view_name"),
    (
        ("extview", "extracted_view", "av_extracted"),
        ("smartview", "smart_view", "av_extracted"),
    ),
)
def test_legacy_view_aliases_keep_native_meaning(
    tmp_path: Path, output_type: str, qrc_type: str, view_name: str
) -> None:
    cfg = _config(tmp_path, output_type=output_type)

    command = generate_qrc(cfg, cfg.context()).read_text(encoding="utf-8")
    output_db = _command_section(command, "output_db")

    assert f"-type {qrc_type}" in output_db
    assert f'-view_name "{view_name}"' in output_db


def test_canonical_smart_view_supports_coordinate_output(tmp_path: Path) -> None:
    cfg = _config(tmp_path, netlist={"parasitic_coordinates": True})

    command = generate_qrc(cfg, cfg.context()).read_text(encoding="utf-8")

    assert "-output_xy parasitic_res parasitic_cap" in _command_section(
        command, "output_db"
    )


def test_view_requires_accessible_library_definitions(tmp_path: Path) -> None:
    cfg = _config(tmp_path, create_cds_lib=False)

    with pytest.raises(FileNotFoundError, match="OA library definitions file"):
        generate_qrc(cfg, cfg.context())


def test_view_requires_layout_view_reference(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg = cfg.replace('input', 'layout', 'view', value="")

    with pytest.raises(ValueError, match="requires an OA layout view"):
        generate_qrc(cfg, cfg.context())


def test_schematic_gds_view_uses_source_target_without_top_layout_view(
    tmp_path: Path,
) -> None:
    cfg = _config(tmp_path, input_type="SCH+GDS")
    cfg = cfg.replace('input', 'layout', value={
        "lib": "stale_layout_lib",
        "cell": "stale_layout_top",
        "view": "stale_layout",
    })

    command = generate_qrc(cfg, cfg.context()).read_text(encoding="utf-8")
    input_db = _command_section(command, "input_db")

    assert '-design_cell_name "source_top layout source_lib"' in input_db
    assert "stale_layout" not in input_db


def test_schematic_gds_view_accepts_explicit_layout_view_token(
    tmp_path: Path,
) -> None:
    cfg = _config(
        tmp_path,
        input_type="SCH+GDS",
        view={"layout_view": "maskLayout"},
    )

    command = generate_qrc(cfg, cfg.context()).read_text(encoding="utf-8")

    assert '-design_cell_name "source_top maskLayout source_lib"' in _command_section(
        command, "input_db"
    )

@pytest.mark.parametrize("input_type", ["CDL+GDS", "CDL+LAY", "SVDB", "CCI"])
def test_external_cdl_run_directory_maps_native_view(tmp_path: Path, input_type: str) -> None:
    cfg = _config(tmp_path, input_type=input_type, view={"library": "results", "cell": "top"})
    directory = tmp_path / "export cdl"
    directory.mkdir()
    cfg = cfg.replace('input', 'cdl', value={"run_directory": str(directory)})
    with pytest.raises(ValueError, match="containing si.env"):
        generate_qrc(cfg, cfg.context())
    (directory / "si.env").write_text("simulator = auCdl\n")
    command = generate_qrc(cfg, cfg.context()).read_text()
    assert f'-cdl_out_map_directory "{directory}"' in _command_section(command, "output_db")
