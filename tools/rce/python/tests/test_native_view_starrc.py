from __future__ import annotations

import sys
from pathlib import Path

import pytest


PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from rcepy.config import RceConfig  # noqa: E402
from rcepy.gen_starrc import generate_starrc  # noqa: E402


def _config(
    tmp_path: Path,
    *,
    input_type: str = "OA",
    output_type: str = "view",
    name_source: str = "schematic",
    view: dict[str, str] | None = None,
    netlist: dict[str, object] | None = None,
) -> RceConfig:
    corner = tmp_path / "tech/RCmax"
    corner.mkdir(parents=True, exist_ok=True)
    for name in ("nxtgrd", "tran.map", "OA_DEVICE_MAP", "OA_LAYER_MAP"):
        (corner / name).write_text(f"{name}\n", encoding="utf-8")
    (tmp_path / "cds.lib").write_text(
        "DEFINE source_lib /oa/source\n"
        "DEFINE layout_lib /oa/layout\n"
        "DEFINE output_lib /oa/output\n",
        encoding="utf-8",
    )

    return RceConfig(
        raw={
            "run": {
                "run_dir": str(tmp_path / "run"),
                "cds_lib": str(tmp_path / "cds.lib"),
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
                "cdl": {"cell": "source_top", "file": "source_top.cdl"},
                "gds": {"cell": "layout_top", "file": "layout_top.gds"},
            },
            "lvs": {"tool": "Calibre"},
            "extract": {
                "tool": "StarRC",
                "tech_dir": str(tmp_path / "tech"),
                "corner": "RCmax",
                "temperature": "25",
                "rc_type": "R+Cg+Cc",
                "output_type": output_type,
                "top_cell_source": "layout",
                "name_source": name_source,
                "view": view or {},
            },
            "netlist": netlist or {},
        },
        config_path=tmp_path / "rce.toml",
    )


def _command(cfg: RceConfig) -> str:
    return generate_starrc(cfg, cfg.context()).read_text(encoding="utf-8")


@pytest.mark.parametrize("output_type", ("view", "starrcview"))
def test_starrc_native_view_emits_complete_oa_writer_output(
    tmp_path: Path, output_type: str
) -> None:
    cfg = _config(
        tmp_path,
        output_type=output_type,
        netlist={
            "hierarchy_delimiter_enable": True,
            "hierarchy_delimiter": ".",
        },
    )

    command = _command(cfg)
    lines = set(command.splitlines())

    assert "NETLIST_FORMAT: OA" in lines
    assert "HIERARCHICAL_SEPARATOR: |" in lines
    assert f"OA_LIB_DEF: {tmp_path / 'cds.lib'}" in lines
    assert "OA_LIB_NAME: source_lib" in lines
    assert "OA_CELL_NAME: source_top" in lines
    assert "OA_VIEW_NAME: starrc" in lines
    assert f"OA_DEVICE_MAPPING_FILE: {tmp_path / 'tech/RCmax/OA_DEVICE_MAP'}" in lines
    assert f"OA_LAYER_MAPPING_FILE: {tmp_path / 'tech/RCmax/OA_LAYER_MAP'}" in lines
    assert f"OA_CDLOUT_RUNDIR: {tmp_path / 'run/db/cdl'}" in lines
    assert "OA_PORT_ANNOTATION_VIEW: source_lib source_top schematic" in lines
    assert "OA_PROPERTY_ANNOTATION_VIEW: source_lib source_top schematic" in lines
    assert not any(line.startswith("NETLIST_FILE:") for line in lines)
    assert "NETLIST_PASSIVE_PARAMS: YES" not in lines
    assert cfg.output_path(cfg.context()) == ""


def test_starrc_layout_names_use_layout_target_and_annotation(tmp_path: Path) -> None:
    cfg = _config(tmp_path, name_source="layout")

    lines = set(_command(cfg).splitlines())

    assert "XREF: NO" in lines
    assert "OA_LIB_NAME: layout_lib" in lines
    assert "OA_CELL_NAME: layout_top" in lines
    assert "OA_PORT_ANNOTATION_VIEW: layout_lib layout_top layout" in lines
    assert not any(line.startswith("OA_PROPERTY_ANNOTATION_VIEW:") for line in lines)


def test_starrc_explicit_oa_target_and_mapping_files(tmp_path: Path) -> None:
    device = tmp_path / "maps/device.map"
    layer = tmp_path / "maps/layer.map"
    device.parent.mkdir(parents=True)
    device.write_text("device\n", encoding="utf-8")
    layer.write_text("layer\n", encoding="utf-8")
    cfg = _config(
        tmp_path,
        view={
            "library": "output_lib",
            "cell": "postlayout_top",
            "name": "postlayout",
            "device_mapping_file": "maps/device.map",
            "layer_mapping_file": "maps/layer.map",
        },
    )

    lines = set(_command(cfg).splitlines())

    assert "OA_LIB_NAME: output_lib" in lines
    assert "OA_CELL_NAME: postlayout_top" in lines
    assert "OA_VIEW_NAME: postlayout" in lines
    assert f"OA_DEVICE_MAPPING_FILE: {device}" in lines
    assert f"OA_LAYER_MAPPING_FILE: {layer}" in lines


def test_starrc_omits_annotation_without_matching_oa_input(
    tmp_path: Path,
) -> None:
    cfg = _config(
        tmp_path,
        input_type="CDL+GDS",
        view={"library": "output_lib", "cell": "postlayout_top"},
    )

    lines = set(_command(cfg).splitlines())

    assert "OA_LIB_NAME: output_lib" in lines
    assert not any(line.startswith("OA_CDLOUT_RUNDIR:") for line in lines)
    assert not any(line.startswith("OA_PORT_ANNOTATION_VIEW:") for line in lines)
    assert not any(line.startswith("OA_PROPERTY_ANNOTATION_VIEW:") for line in lines)


@pytest.mark.parametrize(
    ("file_name", "message"),
    (
        ("OA_DEVICE_MAP", "StarRC OA device mapping file"),
        ("OA_LAYER_MAP", "StarRC OA layer mapping file"),
    ),
)
def test_starrc_view_requires_foundry_oa_mapping_files(
    tmp_path: Path, file_name: str, message: str
) -> None:
    cfg = _config(tmp_path)
    (tmp_path / "tech/RCmax" / file_name).unlink()

    with pytest.raises(FileNotFoundError, match=message):
        _command(cfg)

    assert not (tmp_path / "run/log/star.cmd").exists()


@pytest.mark.parametrize(
    "override",
    (
        "NETLIST_FORMAT: SPF",
        "HIERARCHICAL_SEPARATOR: /",
        "OA_LIB_NAME: other_lib",
        "OA_LIB_NAME: Source_Lib",
        "OA_VIEW_NAME: other_view",
        "OA_DEVICE_MAPPING_FILE: /other/device.map",
        "OA_CDLOUT_RUNDIR: /other/cdl",
        "BLOCK: wrong_top",
        "CELL_TYPE: SCHEMATIC",
        "XREF: NO",
    ),
)
def test_starrc_view_rejects_common_option_semantic_overrides(
    tmp_path: Path, override: str
) -> None:
    cfg = _config(tmp_path)
    (tmp_path / "tech/RCmax/common.opt").write_text(
        f"{override}\n", encoding="utf-8"
    )

    with pytest.raises(
        ValueError, match=r"StarRC OA view output require.*common\.opt"
    ):
        _command(cfg)

    assert not (tmp_path / "run/log/star.cmd").exists()


def test_starrc_view_checks_the_complete_annotation_target(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    (tmp_path / "tech/RCmax/common.opt").write_text(
        "OA_PORT_ANNOTATION_VIEW: source_lib wrong_cell schematic\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError, match=r"(?i)OA_PORT_ANNOTATION_VIEW.*wrong_cell"
    ):
        _command(cfg)


def test_starrc_view_allows_equivalent_common_output_settings(
    tmp_path: Path,
) -> None:
    cfg = _config(tmp_path)
    common = "\n".join(
        (
            "BLOCK: layout_top",
            "CELL_TYPE: LAYOUT",
            "XREF: YES",
            "NETLIST_FORMAT: OA",
            "HIERARCHICAL_SEPARATOR: |",
            "OA_LIB_NAME: source_lib",
            "OA_VIEW_NAME: starrc",
            f"OA_CDLOUT_RUNDIR: {tmp_path / 'run/db/cdl'}",
            "OA_PORT_ANNOTATION_VIEW: source_lib source_top schematic",
            "",
        )
    )
    (tmp_path / "tech/RCmax/common.opt").write_text(common, encoding="utf-8")

    command = _command(cfg)

    assert command.endswith(f"\n{common}")


def test_starrc_view_rejects_ascii_parasitic_information_options(
    tmp_path: Path,
) -> None:
    cfg = _config(tmp_path, netlist={"parasitic_coordinates": True})

    with pytest.raises(ValueError, match="stores parasitic geometry natively"):
        _command(cfg)
