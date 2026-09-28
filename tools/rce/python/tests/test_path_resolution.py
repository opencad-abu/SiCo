from __future__ import annotations

import sys
from pathlib import Path

import pytest


PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from rcepy.config import RceConfig  # noqa: E402
from rcepy.gen_cdl import generate_cdl  # noqa: E402
from rcepy.gen_starrc import generate_starrc  # noqa: E402
from rcepy.generators import generate_all  # noqa: E402
from rcepy.runner import RceRunner  # noqa: E402
from xrc_test_support import make_xrc_config  # noqa: E402


def test_config_paths_expand_environment_and_use_documented_bases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    root = tmp_path / "site"
    monkeypatch.setenv("RCE_TEST_ROOT", str(root))
    cfg = RceConfig(
        raw={
            "run": {"run_dir": "$RCE_TEST_ROOT/run"},
            "input": {
                "type": "CDL+GDS",
                "cdl": {"cell": "top", "file": "top.cdl"},
                "gds": {"cell": "top", "file": "top.gds"},
            },
            "netlist": {"output_path": "results/top.dspf"},
        },
        config_path=config_dir / "rce.toml",
    )

    assert cfg.resolve_path("relative/file") == config_dir / "relative/file"
    assert cfg.resolve_path("${RCE_TEST_ROOT}/deck") == root / "deck"
    assert cfg.run_dir == root / "run"
    assert cfg.output_path(cfg.context()) == str(root / "run/results/top.dspf")


def test_gui_absolute_paths_are_not_rebased_from_colocated_toml(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "data/rce/test.top"
    run_dir.mkdir(parents=True)
    source = tmp_path / "data/cdl/test.top/top.cdl"
    layout = tmp_path / "data/gds/test.top/top.gds"
    source.parent.mkdir(parents=True)
    layout.parent.mkdir(parents=True)
    source.write_text(".subckt top A Z\n.ends top\n", encoding="utf-8")
    layout.write_text("mock gds\n", encoding="utf-8")
    output = run_dir / "top.dspf"
    cfg = RceConfig(
        raw={
            "run": {"run_dir": str(run_dir)},
            "input": {
                "type": "CDL+GDS",
                "cdl": {"cell": "top", "file": str(source)},
                "gds": {"cell": "top", "file": str(layout)},
            },
            "netlist": {"output_path": str(output)},
        },
        config_path=run_dir / "rce.toml",
    )

    ctx = cfg.context()
    assert cfg.run_dir == run_dir
    assert ctx.source_path == str(source)
    assert ctx.layout_path == str(layout)
    assert cfg.output_path(ctx) == str(output)


def test_undefined_or_empty_environment_variable_in_path_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RCE_PATH_MISSING", raising=False)
    cfg = RceConfig(
        raw={"extract": {"tech_dir": "$RCE_PATH_MISSING/tech"}},
        config_path=tmp_path / "rce.toml",
    )

    with pytest.raises(
        ValueError, match=r"Undefined or empty environment variable \$RCE_PATH_MISSING"
    ):
        cfg.path("extract", "tech_dir")

    monkeypatch.setenv("RCE_PATH_MISSING", "")
    with pytest.raises(ValueError, match="empty environment variable"):
        cfg.path("extract", "tech_dir")


def test_source_items_expand_only_an_existing_file_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    names_file = tmp_path / "nets.list"
    names_file.write_text("VDD, VSS # supplies\n", encoding="utf-8")
    monkeypatch.setenv("RCE_NET_FILE", str(names_file))
    cfg = RceConfig(
        raw={"selection": {"nets": "${RCE_NET_FILE}"}},
        config_path=tmp_path / "rce.toml",
    )

    assert cfg.source_items("selection", "nets") == ["VDD", "VSS"]
    cfg = cfg.replace('selection', 'nets', value=["$VDD", "${SIGNAL}"])
    assert cfg.source_items("selection", "nets") == ["$VDD", "${SIGNAL}"]
    cfg = cfg.replace('selection', 'nets', value="$UNDEFINED_NET")
    assert cfg.source_items("selection", "nets") == ["$UNDEFINED_NET"]


def test_oa_generators_resolve_all_common_path_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "config"
    site = tmp_path / "site"
    tech = site / "qrc"
    config_dir.mkdir()
    tech.mkdir(parents=True)
    (tech / "qrcTechFile").write_text("tech\n", encoding="utf-8")
    for name in ("cds.lib", "header.cdl", "layers.map", "lvs.cal"):
        (site / name).write_text(f"{name}\n", encoding="utf-8")
    monkeypatch.setenv("RCE_SITE", str(site))
    cfg = RceConfig(
        raw={
            "run": {"run_dir": "run", "cds_lib": "$RCE_SITE/cds.lib"},
            "input": {
                "type": "OA",
                "schematic": {
                    "lib": "test",
                    "cell": "top",
                    "view": "schematic",
                    "cdl_header_file": "${RCE_SITE}/header.cdl",
                },
                "layout": {
                    "lib": "test",
                    "cell": "top",
                    "view": "layout",
                    "layer_map": "$RCE_SITE/layers.map",
                },
            },
            "lvs": {"runset_file": "${RCE_SITE}/lvs.cal"},
            "extract": {"tool": "QRC", "tech_dir": "$RCE_SITE/qrc"},
        },
        config_path=config_dir / "rce.toml",
    )

    assert RceRunner(cfg, generate_only=True).run() == 0
    run_dir = config_dir / "run"
    assert f'incFILE                   = "{site / "header.cdl"}"' in (
        run_dir / "db/cdl/si.env"
    ).read_text(encoding="utf-8")
    assert f'layerMap "{site / "layers.map"}"' in (
        run_dir / "db/gds/streamout.cmd"
    ).read_text(encoding="utf-8")
    assert f'INCLUDE "{site / "lvs.cal"}"' in (run_dir / "log/lvs.cal").read_text(
        encoding="utf-8"
    )
    assert f'-technology_directory "{tech}"' in (run_dir / "log/qrc.ccl").read_text(
        encoding="utf-8"
    )
    assert f"SOFTINCLUDE {site / 'cds.lib'}" in (run_dir / "db/cdl/cds.lib").read_text(
        encoding="utf-8"
    )


def test_explicit_empty_cdl_include_does_not_fall_back_to_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CDL_HEADER_FILE", str(tmp_path / "environment-header.cdl"))
    cfg = RceConfig(
        raw={
            "run": {"run_dir": "run"},
            "input": {
                "type": "OA",
                "schematic": {
                    "lib": "test",
                    "cell": "top",
                    "view": "schematic",
                    "cdl_header_file": "",
                },
                "layout": {"cell": "top"},
            },
        },
        config_path=tmp_path / "rce.toml",
    )

    si_env = generate_cdl(cfg, cfg.context()).read_text(encoding="utf-8")
    assert 'incFILE                   = ""' in si_env
    assert "environment-header.cdl" not in si_env


def test_xrc_overrides_and_pin_file_use_shared_path_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, paths = make_xrc_config(tmp_path)
    tech = paths["tech_dir"]
    corner_dir = paths["corner_dir"]
    rule = corner_dir / "custom.cal"
    hcell = corner_dir / "custom.hcells"
    pin_file = tmp_path / "pins.spi"
    rule.write_text("// custom\n", encoding="utf-8")
    hcell.write_text("macro macro\n", encoding="utf-8")
    pin_file.write_text(".subckt top A Z\n.ends\n", encoding="utf-8")
    monkeypatch.setenv("RCE_XRC_TECH", str(tech))
    monkeypatch.setenv("RCE_PIN_ROOT", str(tmp_path))
    monkeypatch.setenv("RCE_LVS_ROOT", str(tmp_path))
    cfg = cfg.replace('lvs', 'runset_file', value="${RCE_LVS_ROOT}/lvs.cal")
    cfg = cfg.replace('extract', 'tech_dir', value="$RCE_XRC_TECH")
    cfg = cfg.replace('extract', 'xrc', value={
        "rule_file": "custom.cal",
        "hcell_file": "custom.hcells",
    })
    cfg = cfg.replace('netlist', value={
        "pin_order_enable": True,
        "pin_order_type": "User Defined File",
        "pin_order_file": "${RCE_PIN_ROOT}/pins.spi",
    })

    generate_all(cfg, cfg.context())
    text = paths["runset"].read_text(encoding="utf-8")
    assert f'INCLUDE "{paths["lvs_rule"]}"' in text
    assert f'INCLUDE "{rule}"' in text
    assert f'PEX PIN ORDER FILE "{pin_file}"' in text
    stages = RceRunner(cfg)._stages()
    assert str(hcell) in stages[0].command


def test_xrc_tech_dir_already_ending_in_corner_is_not_appended_twice(
    tmp_path: Path,
) -> None:
    cfg, paths = make_xrc_config(tmp_path)
    corner_dir = paths["corner_dir"]
    cfg = cfg.replace('extract', 'tech_dir', value=str(corner_dir))

    generate_all(cfg, cfg.context())

    text = paths["runset"].read_text(encoding="utf-8")
    assert f'INCLUDE "{corner_dir / "xrc.cal"}"' in text
    assert str(corner_dir / "RCmax") not in text
    assert str(corner_dir / "hcell_list") in RceRunner(cfg)._stages()[0].command


def test_xrc_missing_rule_error_includes_corner_directory(tmp_path: Path) -> None:
    cfg, paths = make_xrc_config(tmp_path)
    rule = paths["corner_dir"] / "xrc.cal"
    rule.unlink()

    with pytest.raises(FileNotFoundError) as exc_info:
        generate_all(cfg, cfg.context())

    assert str(rule) in str(exc_info.value)


def test_starrc_input_paths_expand_from_config_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    star_root = tmp_path / "star"
    star_root.mkdir()
    (star_root / "tech.nxtgrd").write_text("grid\n", encoding="utf-8")
    (config_dir / "maps").mkdir()
    (config_dir / "maps/layer.map").write_text("map\n", encoding="utf-8")
    monkeypatch.setenv("STAR_ROOT", str(star_root))
    cfg = RceConfig(
        raw={
            "run": {"run_dir": "run"},
            "input": {
                "type": "CDL+GDS",
                "cdl": {"cell": "top", "file": "top.cdl"},
                "gds": {"cell": "top", "file": "top.gds"},
            },
            "extract": {
                "tool": "StarRC",
                "starrc": {
                    "tcad_grd_file": "$STAR_ROOT/tech.nxtgrd",
                    "mapping_file": "maps/layer.map",
                    "three_d_ic": True,
                    "three_d_ic_subckt_file": "${STAR_ROOT}/3dic.spi",
                },
            },
        },
        config_path=config_dir / "rce.toml",
    )

    command = generate_starrc(cfg, cfg.context()).read_text(encoding="utf-8")
    assert f"TCAD_GRD_FILE: {tmp_path / 'star/tech.nxtgrd'}" in command
    assert f"MAPPING_FILE: {config_dir / 'maps/layer.map'}" in command
    assert f"3D_IC_SUBCKT_FILE:{tmp_path / 'star/3dic.spi'}" in command
    assert f"COUPLING_REPORT_FILE: {config_dir / 'run/cc.rep'}" in command
