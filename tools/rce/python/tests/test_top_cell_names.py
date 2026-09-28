from __future__ import annotations

import sys
from pathlib import Path

import pytest


PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from rcepy.config import RceConfig  # noqa: E402
from rcepy.gen_qrc import generate_qrc  # noqa: E402
from rcepy.gen_query import generate_query  # noqa: E402
from rcepy.gen_starrc import generate_starrc  # noqa: E402
from rcepy.runner import RceRunner  # noqa: E402


def _config(
    tmp_path: Path,
    input_type: str,
    *,
    top_cell_source: str | None = None,
    name_source: str | None = None,
    tool: str = "QRC",
    output_type: str = "dspf",
) -> RceConfig:
    cds_lib = tmp_path / "cds.lib"
    cds_lib.write_text("DEFINE layout_lib ./layout_lib\n", encoding="utf-8")
    extract = {
        "tool": tool,
        "tech_dir": str(tmp_path / "tech"),
        "corner": "RCmax",
        "temperature": "25",
        "rc_type": "R+Cg+Cc",
        "output_type": output_type,
    }
    if top_cell_source is not None:
        extract["top_cell_source"] = top_cell_source
    if name_source is not None:
        extract["name_source"] = name_source
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
                    "cell": "schematic_top",
                    "view": "schematic",
                },
                "layout": {
                    "lib": "layout_lib",
                    "cell": "layout_top",
                    "view": "layout",
                },
                "cdl": {"file": "schematic_top.cdl", "cell": "schematic_top"},
                "gds": {"file": "layout_top.gds", "cell": "layout_top"},
                "svdb": {"dir": "external.svdb", "cell": "database_top"},
                "cci": {"dir": "external.cci", "cell": "database_top"},
            },
            "lvs": {"tool": "Calibre"},
            "extract": extract,
            "runtime": {"lvs_cpus": "1", "ext_cpus": "1"},
            "netlist": {},
        },
        config_path=tmp_path / "rce.toml",
    )


@pytest.mark.parametrize(
    "input_type",
    ["OA", "SCH+GDS", "CDL+LAY", "CDL+GDS"],
)
@pytest.mark.parametrize(
    ("top_cell_source", "expected_top"),
    [("schematic", "schematic_top"), ("layout", "layout_top")],
)
def test_paired_inputs_select_top_without_rekeying_layout_database(
    tmp_path: Path,
    input_type: str,
    top_cell_source: str,
    expected_top: str,
) -> None:
    cfg = _config(
        tmp_path,
        input_type,
        top_cell_source=top_cell_source,
        name_source="schematic",
    )

    ctx = cfg.context()

    assert ctx.top_cell_source == top_cell_source
    assert ctx.name_source == "schematic"
    assert ctx.top_cell == expected_top
    assert ctx.source_cell == "schematic_top"
    assert ctx.layout_cell == "layout_top"
    assert ctx.svdb_dir.endswith("/db/svdb.layout_top")
    assert ctx.cci_dir.endswith("/db/cci.layout_top")
    assert cfg.output_path(ctx).endswith(f"/db/{expected_top}.dspf")


def test_legacy_config_defaults_to_layout_and_rejects_unknown_source(
    tmp_path: Path,
) -> None:
    cfg = _config(tmp_path, "CDL+GDS")
    assert cfg.context().top_cell_source == "layout"
    assert cfg.context().name_source == "layout"
    assert cfg.context().top_cell == "layout_top"

    cfg = cfg.replace('extract', 'top_cell_source', value="schematic")
    assert cfg.context().name_source == "schematic"

    cfg = cfg.replace('extract', 'top_cell_source', value="symbol")
    with pytest.raises(ValueError, match="schematic.*layout"):
        cfg.context()


def test_name_source_rejects_unknown_value(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path,
        "CDL+GDS",
        top_cell_source="layout",
        name_source="symbol",
    )

    with pytest.raises(ValueError, match="extract.name_source.*schematic.*layout"):
        cfg.context()


@pytest.mark.parametrize(("input_type", "section"), [("SVDB", "svdb"), ("CCI", "cci")])
def test_database_inputs_have_one_layout_identity(
    tmp_path: Path, input_type: str, section: str
) -> None:
    cfg = _config(
        tmp_path,
        input_type,
        top_cell_source="schematic",
        name_source="schematic",
    )

    ctx = cfg.context()

    assert ctx.top_cell_source == "layout"
    assert ctx.name_source == "schematic"
    assert ctx.top_cell == "database_top"
    assert ctx.source_cell == "database_top"
    assert ctx.layout_cell == "database_top"
    assert getattr(ctx, f"{section}_dir").endswith(f"external.{section}")


@pytest.mark.parametrize(
    ("top_cell_source", "name_source", "namespace", "output_cell"),
    [
        ("schematic", "layout", "LAYOUT", "schematic_top"),
        ("layout", "schematic", "SCHEMATIC", "layout_top"),
    ],
)
def test_qrc_keeps_query_identity_layout_and_switches_output_namespace(
    tmp_path: Path,
    top_cell_source: str,
    name_source: str,
    namespace: str,
    output_cell: str,
) -> None:
    cfg = _config(
        tmp_path,
        "CDL+GDS",
        top_cell_source=top_cell_source,
        name_source=name_source,
    )
    tech_dir = tmp_path / "tech/RCmax"
    tech_dir.mkdir(parents=True)
    (tech_dir / "qrcTechFile").write_text("tech\n", encoding="utf-8")
    ctx = cfg.context()

    query = generate_query(cfg, ctx).read_text(encoding="utf-8")
    command = generate_qrc(cfg, ctx).read_text(encoding="utf-8")
    query_prefix = f"{ctx.cci_dir}/layout_top"

    assert f"gds write {query_prefix}.agf" in query
    assert '-run_name "layout_top"' in command
    assert f'-layer_map_file "{query_prefix}.gds.map"' in command
    extraction_setup = command.split("extraction_setup \\\n", 1)[1].split("\n\n", 1)[0]
    output_setup = command.split("output_setup \\\n", 1)[1].split("\n\n", 1)[0]
    assert '-net_name_space "SCHEMATIC"' in extraction_setup
    assert f'-net_name_space "{namespace}"' in output_setup
    assert cfg.output_path(ctx).endswith(f"/db/{output_cell}.dspf")

    query_stage = next(stage for stage in RceRunner(cfg)._stages() if stage.name == "query")
    assert query_stage.command[3] == "layout_top"


def test_qrc_extracted_view_target_remains_the_oa_layout_cell(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path,
        "OA",
        top_cell_source="schematic",
        name_source="layout",
        output_type="extview",
    )
    tech_dir = tmp_path / "tech/RCmax"
    tech_dir.mkdir(parents=True)
    (tech_dir / "qrcTechFile").write_text("tech\n", encoding="utf-8")

    command = generate_qrc(cfg, cfg.context()).read_text(encoding="utf-8")

    assert '-design_cell_name "layout_top layout layout_lib"' in command
    output_setup = command.split("output_setup \\\n", 1)[1].split("\n\n", 1)[0]
    assert '-net_name_space "SCHEMATIC"' in output_setup


@pytest.mark.parametrize(
    ("top_cell_source", "block", "cell_type"),
    [
        ("schematic", "schematic_top", "SCHEMATIC"),
        ("layout", "layout_top", "LAYOUT"),
    ],
)
def test_starrc_block_uses_the_selected_name_domain(
    tmp_path: Path, top_cell_source: str, block: str, cell_type: str
) -> None:
    corner = tmp_path / "tech/RCmax"
    corner.mkdir(parents=True)
    (corner / "nxtgrd").write_text("grid\n", encoding="utf-8")
    (corner / "tran.map").write_text("map\n", encoding="utf-8")
    cfg = _config(
        tmp_path,
        "CDL+GDS",
        top_cell_source=top_cell_source,
        name_source="schematic",
        tool="StarRC",
    )

    command = generate_starrc(cfg, cfg.context()).read_text(encoding="utf-8")

    assert f"BLOCK: {block}" in command
    assert f"CELL_TYPE: {cell_type}" in command
    assert "NET_TYPE: SCHEMATIC" in command
    assert "XREF: YES" in command


@pytest.mark.parametrize(
    ("name_source", "xref"),
    [("schematic", "YES"), ("layout", "NO")],
)
def test_starrc_name_source_controls_net_and_instance_xref(
    tmp_path: Path, name_source: str, xref: str
) -> None:
    corner = tmp_path / "tech/RCmax"
    corner.mkdir(parents=True)
    (corner / "nxtgrd").write_text("grid\n", encoding="utf-8")
    (corner / "tran.map").write_text("map\n", encoding="utf-8")
    cfg = _config(
        tmp_path,
        "CDL+GDS",
        top_cell_source="layout",
        name_source=name_source,
        tool="StarRC",
    )

    command = generate_starrc(cfg, cfg.context()).read_text(encoding="utf-8")

    assert f"XREF: {xref}" in command


def test_starrc_rejects_schematic_top_with_layout_names_when_names_differ(
    tmp_path: Path,
) -> None:
    corner = tmp_path / "tech/RCmax"
    corner.mkdir(parents=True)
    (corner / "nxtgrd").write_text("grid\n", encoding="utf-8")
    (corner / "tran.map").write_text("map\n", encoding="utf-8")
    cfg = _config(
        tmp_path,
        "CDL+GDS",
        top_cell_source="schematic",
        name_source="layout",
        tool="StarRC",
    )

    with pytest.raises(ValueError, match="cannot combine.*layout namespace"):
        generate_starrc(cfg, cfg.context())


def test_starrc_allows_schematic_top_with_layout_names_when_names_match(
    tmp_path: Path,
) -> None:
    corner = tmp_path / "tech/RCmax"
    corner.mkdir(parents=True)
    (corner / "nxtgrd").write_text("grid\n", encoding="utf-8")
    (corner / "tran.map").write_text("map\n", encoding="utf-8")
    cfg = _config(
        tmp_path,
        "CDL+GDS",
        top_cell_source="schematic",
        name_source="layout",
        tool="StarRC",
    )
    cfg = cfg.replace('input', 'gds', 'cell', value="schematic_top")

    command = generate_starrc(cfg, cfg.context()).read_text(encoding="utf-8")

    assert "BLOCK: schematic_top" in command
    assert "XREF: NO" in command


def test_starrc_schematic_top_requires_xref(tmp_path: Path) -> None:
    corner = tmp_path / "tech/RCmax"
    corner.mkdir(parents=True)
    (corner / "nxtgrd").write_text("grid\n", encoding="utf-8")
    (corner / "tran.map").write_text("map\n", encoding="utf-8")
    cfg = _config(
        tmp_path,
        "CDL+GDS",
        top_cell_source="schematic",
        tool="StarRC",
    )
    cfg = cfg.replace('extract', 'starrc', value={"xref": True})
    command = generate_starrc(cfg, cfg.context()).read_text(encoding="utf-8")
    assert "XREF: YES" in command

    cfg = cfg.replace('extract', 'starrc', value={"xref": "NO"})

    with pytest.raises(ValueError, match="schematic top-cell.*xref = YES"):
        generate_starrc(cfg, cfg.context())
