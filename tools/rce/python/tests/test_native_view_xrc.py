from __future__ import annotations

import sys
from pathlib import Path

import pytest


PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from rcepy.generators import generate_all  # noqa: E402
from rcepy.runner import RceRunner  # noqa: E402
from xrc_test_support import make_xrc_config, xrc_stages  # noqa: E402


def _view_config(tmp_path: Path):
    cfg, paths = make_xrc_config(tmp_path, output_type="view")
    cfg = cfg.replace('extract', 'view', value={
        "kind": "calibre",
        "library": "sxTest",
        "cell": "top",
        "name": "calibre",
        "schematic_library": "sxTest",
    })
    (paths["corner_dir"] / "calview.cellmap").write_text(
        "((p cap c) (analogLib cap symbol) ((PLUS) (MINUS)))\n",
        encoding="utf-8",
    )
    return cfg, paths


def test_xrc_view_generates_calibreview_netlist_and_batch_setup(
    tmp_path: Path,
) -> None:
    cfg, paths = _view_config(tmp_path)
    ctx = cfg.context()

    generate_all(cfg, ctx)

    netlist = ctx.db_dir / "top.pex.netlist"
    runset = paths["runset"].read_text(encoding="utf-8")
    assert f'PEX NETLIST "{netlist}" CALIBREVIEW SOURCENAMES' in runset
    assert runset.rfind("SOURCE CASE YES") > runset.rfind('INCLUDE "')
    setup_path = ctx.log_dir / "calibreview.setup"
    setup = setup_path.read_text(encoding="utf-8")
    assert f"calibre_view_netlist_file : {netlist}" in setup
    assert "output_library : sxTest" in setup
    assert "schematic_library : sxTest" in setup
    assert "cell_name : top" in setup
    assert f"cellmap_file : {paths['corner_dir'] / 'calview.cellmap'}" in setup
    assert "calibreview_name : calibre" in setup
    assert "calibreview_type : maskLayout" in setup
    assert "parasitic_placement : arrayed" in setup
    assert "show_parasitic_polygons : off" in setup
    assert "open_calibreview : don't_open" in setup


def test_xrc_view_formatter_checks_fresh_intermediate_netlist(tmp_path: Path) -> None:
    cfg, _ = _view_config(tmp_path)
    ctx = cfg.context()

    stage = xrc_stages(cfg)[-1]

    assert stage.name == "xrc_fmt"
    assert stage.expected_paths == (ctx.db_dir / "top.pex.netlist",)
    assert stage.require_nonempty_outputs


def test_xrc_view_uses_custom_view_name_and_cellmap(tmp_path: Path) -> None:
    cfg, paths = _view_config(tmp_path)
    custom = tmp_path / "custom.cellmap"
    custom.write_text("mock mapping\n", encoding="utf-8")
    cfg = cfg.replace('extract', 'view', value={**cfg.section('extract', 'view'), **{"name": "pex_calibre", "cellmap_file": str(custom)}})

    generate_all(cfg, cfg.context())

    setup = (paths["run_dir"] / "log" / "calibreview.setup").read_text()
    assert "calibreview_name : pex_calibre" in setup
    assert f"cellmap_file : {custom}" in setup


def test_xrc_view_supports_layout_names_with_explicit_target(tmp_path: Path) -> None:
    cfg, paths = _view_config(tmp_path)
    cfg = cfg.replace('extract', 'name_source', value="layout")
    cfg = cfg.replace('extract', 'view', value={**cfg.section('extract', 'view'), **{"library": "results_lib", "cell": "postlayout_top"}})

    generate_all(cfg, cfg.context())

    runset = cfg.context().run_dir.joinpath("_xrc.cal_").read_text(encoding="utf-8")
    setup = (paths["run_dir"] / "log/calibreview.setup").read_text(
        encoding="utf-8"
    )
    assert "CALIBREVIEW LAYOUTNAMES" in runset
    assert "output_library : results_lib" in setup
    assert "cell_name : postlayout_top" in setup


def test_xrc_view_layout_names_use_implicit_oa_layout_target(tmp_path: Path) -> None:
    cfg, paths = _view_config(tmp_path)
    cfg = cfg.replace('input', value={
        "type": "OA",
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
    })
    cfg = cfg.replace('extract', 'name_source', value="layout")
    cfg = cfg.replace('extract', 'view', value={"kind": "calibre"})

    generate_all(cfg, cfg.context())

    runset = paths["runset"].read_text(encoding="utf-8")
    setup = (paths["run_dir"] / "log/calibreview.setup").read_text(
        encoding="utf-8"
    )
    assert "CALIBREVIEW LAYOUTNAMES" in runset
    assert "output_library : layout_lib" in setup
    assert "cell_name : layout_top" in setup
    assert "schematic_library : source_lib" in setup


def test_xrc_view_rejects_implicit_target_without_selected_oa_namespace(
    tmp_path: Path,
) -> None:
    cfg, _ = _view_config(tmp_path)
    cfg = cfg.replace('input', value={
        "type": "SCH+GDS",
        "schematic": {
            "lib": "source_lib",
            "cell": "source_top",
            "view": "schematic",
        },
        "gds": {"file": "layout.gds", "cell": "layout_top"},
    })
    cfg = cfg.replace('extract', 'name_source', value="layout")
    cfg = cfg.replace('extract', 'view', value={"kind": "calibre"})

    with pytest.raises(ValueError, match="has no OA target"):
        generate_all(cfg, cfg.context())


def test_xrc_view_requires_foundry_cellmap(tmp_path: Path) -> None:
    cfg, paths = _view_config(tmp_path)
    (paths["corner_dir"] / "calview.cellmap").unlink()

    with pytest.raises(FileNotFoundError, match="Calibre View cellmap file"):
        generate_all(cfg, cfg.context())


def test_xrc_view_rejects_text_netlist_name_customization(tmp_path: Path) -> None:
    cfg, _ = _view_config(tmp_path)
    cfg = cfg.replace('netlist', value={
        "hierarchy_delimiter_enable": True,
        "hierarchy_delimiter": "/",
    })

    with pytest.raises(ValueError, match="controls OA names natively"):
        generate_all(cfg, cfg.context())


def test_non_xrc_native_views_remain_pathless(tmp_path: Path) -> None:
    cfg, _ = _view_config(tmp_path)
    cfg = cfg.replace('extract', 'tool', value="QRC")
    cfg = cfg.replace('extract', 'view', 'kind', value="smart")

    assert cfg.output_path(cfg.context()) == ""


def test_runner_does_not_expect_native_view_as_file(tmp_path: Path) -> None:
    cfg, _ = _view_config(tmp_path)
    cfg = cfg.replace('extract', 'tool', value="QRC")
    cfg = cfg.replace('extract', 'view', 'kind', value="smart")

    stage = RceRunner(cfg)._extract_stage()

    assert stage.expected_paths == ()
    assert not stage.require_nonempty_outputs
