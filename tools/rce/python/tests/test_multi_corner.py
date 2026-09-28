from __future__ import annotations

from pathlib import Path

import pytest

from rcepy.config import RceConfig
from rcepy.gen_qrc import generate_qrc, qrc_output_publications
from rcepy.gen_starrc import generate_starrc, starrc_output_publications
from rcepy.generators import generate_all
from rcepy.runner import RceRunner
from rcepy.xrc import build_xrc_stage_specs, xrc_output_publications
from xrc_test_support import make_xrc_config


def _file(path: Path, text: str = "test\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _cci_config(tmp_path: Path, tool: str, tech_dir: Path) -> RceConfig:
    runset = _file(tmp_path / "rce_lvs.cal", "// RCE LVS rules\n")
    return RceConfig(
        raw={
            "run": {"run_dir": str(tmp_path / "run")},
            "input": {
                "type": "CCI",
                "cci": {"dir": str(tmp_path / "input.cci"), "cell": "top"},
            },
            "lvs": {"tool": "Calibre", "runset_file": str(runset)},
            "extract": {
                "tool": tool,
                "tech_dir": str(tech_dir),
                "corner": "RCmax",
                "corners": ["RCmax", "Cmin", "rcMAX"],
                "temperature": "25",
                "corner_temperatures": ["125", "-40"],
                "rc_type": "R+Cg+Cc",
                "output_type": "dspf",
            },
            "runtime": {"ext_cpus": "2"},
            "netlist": {"output_path": str(tmp_path / "run/top.dspf")},
        },
        config_path=tmp_path / "rce.toml",
    )


def test_config_prefers_corners_and_aligns_per_corner_temperatures(
    tmp_path: Path,
) -> None:
    cfg = _cci_config(tmp_path, "QRC", tmp_path / "tech")
    ctx = cfg.context()

    assert cfg.corners == ("RCmax", "Cmin")
    assert cfg.corner_temperature_pairs() == (("RCmax", "125"), ("Cmin", "-40"))
    assert cfg.output_paths(ctx) == (
        tmp_path / "run/top_RCmax.dspf",
        tmp_path / "run/top_Cmin.dspf",
    )


def test_corner_array_does_not_require_legacy_scalar(tmp_path: Path) -> None:
    tech = tmp_path / "StarRC/RCmax"
    _file(tech / "nxtgrd")
    _file(tech / "tran.map")
    cfg = _cci_config(tmp_path, "StarRC", tech.parent)
    cfg = cfg.replace('extract', value={key: value for key, value in cfg.section('extract').items() if key != 'corner'})
    cfg = cfg.replace('extract', 'corners', value=["RCmax"])
    cfg = cfg.replace('extract', 'corner_temperatures', value=["125"])

    command = generate_starrc(cfg, cfg.context()).read_text(encoding="utf-8")

    assert f"TCAD_GRD_FILE: {tech / 'nxtgrd'}" in command
    assert "OPERATING_TEMPERATURE: 25" in command


def test_completed_run_cleanup_preserves_shared_cad_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _cci_config(tmp_path, "QRC", tmp_path / "tech")
    runner = RceRunner(cfg)
    runner.ctx.run_dir.mkdir(parents=True)
    monkeypatch.chdir(runner.ctx.run_dir)
    (runner.ctx.run_dir / "db").mkdir()
    (runner.ctx.run_dir / "log").mkdir()
    shared = runner.ctx.run_dir / ".cad"
    (shared / "ai").mkdir(parents=True)
    (shared / "ai/session").write_text("keep\n", encoding="ascii")
    (runner.ctx.run_dir / "scratch").mkdir()

    runner._cleanup_run_root()

    assert (shared / "ai/session").read_text(encoding="ascii") == "keep\n"
    assert (runner.ctx.run_dir / "scratch").is_dir()


def test_quantus_generates_named_mpc_technology_and_temperature_lists(
    tmp_path: Path,
) -> None:
    tech = tmp_path / "QRC"
    for corner in ("RCmax", "Cmin"):
        _file(tech / corner / "qrcTechFile")
    cfg = _cci_config(tmp_path, "QRC", tech)
    ctx = cfg.context()

    command = generate_qrc(cfg, ctx).read_text(encoding="utf-8")

    assert "-technology_name RCE_MPC" in command
    assert "-technology_corner RCmax Cmin" in command
    assert "-temperature 125 -40" in command
    assert f'-file_name "{tmp_path / "run/top"}"' in command
    assert (ctx.log_dir / "qrc_mpc_technology/corner.defs").read_text().splitlines() == [
        f"DEFINE RCmax {tech / 'RCmax'}",
        f"DEFINE Cmin {tech / 'Cmin'}",
    ]
    assert qrc_output_publications(cfg, ctx) == (
        (tmp_path / "run/top_RCmax_125.dspf", tmp_path / "run/top_RCmax.dspf"),
        (tmp_path / "run/top_Cmin_-40.dspf", tmp_path / "run/top_Cmin.dspf"),
    )


def test_starrc_generates_smc_corners_file_with_per_corner_temperatures(
    tmp_path: Path,
) -> None:
    tech = tmp_path / "StarRC"
    for corner in ("RCmax", "Cmin"):
        _file(tech / corner / "nxtgrd")
        _file(tech / corner / "tran.map")
    cfg = _cci_config(tmp_path, "StarRC", tech)
    ctx = cfg.context()

    command = generate_starrc(cfg, ctx).read_text(encoding="utf-8")
    corners_file = (ctx.log_dir / "star.corners").read_text(encoding="utf-8")

    assert "SIMULTANEOUS_MULTI_CORNER: YES" in command
    assert f"CORNERS_FILE: {ctx.log_dir / 'star.corners'}" in command
    assert "SELECTED_CORNERS: RCmax Cmin" in command
    assert "TCAD_GRD_FILE:" not in command
    assert "OPERATING_TEMPERATURE:" not in command
    assert "CORNER_NAME: RCmax" in corners_file
    assert "OPERATING_TEMPERATURE: 125" in corners_file
    assert "CORNER_NAME: Cmin" in corners_file
    assert "OPERATING_TEMPERATURE: -40" in corners_file
    assert starrc_output_publications(cfg, ctx) == (
        (tmp_path / "run/top.dspf.RCmax", tmp_path / "run/top_RCmax.dspf"),
        (tmp_path / "run/top.dspf.Cmin", tmp_path / "run/top_Cmin.dspf"),
    )


def test_xrc_per_corner_decks_share_lvs_and_plan_one_pdb_fmt_pair_per_corner(
    tmp_path: Path,
) -> None:
    cfg, paths = make_xrc_config(tmp_path)
    second = paths["tech_dir"] / "Cmin"
    _file(second / "xrc.cal", "// Cmin xRC rules\n")
    _file(second / "hcell_list", "stdcell stdcell\n")
    cfg = cfg.replace('extract', 'corners', value=["RCmax", "Cmin"])
    cfg = cfg.replace('extract', 'corner_temperatures', value=["125", "-40"])
    custom_svrf = "// shared custom SVRF\nPEX REDUCE ANALOG YES"
    cfg = cfg.replace('lvs', value={**cfg.section('lvs'), **{
            "custom_svrf_enable": True,
            "custom_svrf_command": custom_svrf,
        }})
    ctx = cfg.context()

    generate_all(cfg, ctx)
    stages = build_xrc_stage_specs(cfg, ctx)

    assert [stage.name for stage in stages] == [
        "xrc_lvs",
        "xrc_pdb_RCmax",
        "xrc_fmt_RCmax",
        "xrc_pdb_Cmin",
        "xrc_fmt_Cmin",
    ]
    assert len({stage.control_file for stage in stages[1:]}) == 2
    assert "PEX EXTRACT TEMPERATURE 125" in stages[1].control_file.read_text()
    assert "PEX EXTRACT TEMPERATURE -40" in stages[3].control_file.read_text()
    for control_file in {stage.control_file for stage in stages}:
        assert control_file.read_text(encoding="utf-8").endswith(
            "\n\n" + custom_svrf + "\n"
        )
    assert "-corner" not in stages[2].command
    assert xrc_output_publications(cfg, ctx) == (
        (paths["run_dir"] / "db/top_RCmax.dspf", paths["run_dir"] / "db/top_RCmax.dspf"),
        (paths["run_dir"] / "db/top_Cmin.dspf", paths["run_dir"] / "db/top_Cmin.dspf"),
    )


def test_xrc_shared_deck_uses_native_formatter_corner_selection(tmp_path: Path) -> None:
    cfg, paths = make_xrc_config(tmp_path)
    second = paths["tech_dir"] / "Cmin"
    _file(second / "hcell_list", "stdcell stdcell\n")
    shared = _file(tmp_path / "shared.xrc.cal", "PEX CORNER RCmax Cmin\n")
    cfg = cfg.replace('extract', 'corners', value=["RCmax", "Cmin"])
    cfg = cfg.replace('extract', 'corner_temperatures', value=["25", "25"])
    cfg = cfg.replace('extract', 'xrc', value={"rule_file": str(shared)})
    ctx = cfg.context()

    generate_all(cfg, ctx)
    stages = build_xrc_stage_specs(cfg, ctx)

    assert [stage.name for stage in stages] == ["xrc_lvs", "xrc_pdb", "xrc_fmt"]
    assert stages[-1].command[-5:] == [
        "-all",
        "-corner",
        "RCmax,Cmin",
        "-nowait",
        "_xrc.cal_",
    ]
    assert xrc_output_publications(cfg, ctx) == (
        (paths["run_dir"] / "db/top_RCmax", paths["run_dir"] / "db/top_RCmax.dspf"),
        (paths["run_dir"] / "db/top_Cmin", paths["run_dir"] / "db/top_Cmin.dspf"),
    )


def test_xrc_no_rc_multi_corner_formats_each_ideal_netlist_without_pdb(
    tmp_path: Path,
) -> None:
    cfg, paths = make_xrc_config(tmp_path, rc_type="NONE")
    second = paths["tech_dir"] / "Cmin"
    _file(second / "hcell_list", "stdcell stdcell\n")
    shared = _file(tmp_path / "shared.xrc.cal", "PEX CORNER RCmax Cmin\n")
    cfg = cfg.replace('extract', 'corners', value=["RCmax", "Cmin"])
    cfg = cfg.replace('extract', 'corner_temperatures', value=["25", "25"])
    cfg = cfg.replace('extract', 'xrc', value={"rule_file": str(shared)})
    ctx = cfg.context()

    generate_all(cfg, ctx)
    stages = build_xrc_stage_specs(cfg, ctx)

    assert [stage.name for stage in stages] == [
        "xrc_lvs",
        "xrc_fmt_RCmax",
        "xrc_fmt_Cmin",
    ]
    assert all("-simple" in stage.command for stage in stages[1:])
    assert all("-corner" not in stage.command for stage in stages[1:])
    assert all(
        "PEX NETLIST SIMPLE" in stage.control_file.read_text(encoding="utf-8")
        for stage in stages[1:]
    )
    assert xrc_output_publications(cfg, ctx) == (
        (
            paths["run_dir"] / "db/top_RCmax.dspf",
            paths["run_dir"] / "db/top_RCmax.dspf",
        ),
        (
            paths["run_dir"] / "db/top_Cmin.dspf",
            paths["run_dir"] / "db/top_Cmin.dspf",
        ),
    )


def test_xrc_partial_multi_corner_stop_does_not_publish_incomplete_group(
    tmp_path: Path, monkeypatch
) -> None:
    cfg, paths = make_xrc_config(tmp_path)
    second = paths["tech_dir"] / "Cmin"
    _file(second / "xrc.cal", "// Cmin xRC rules\n")
    _file(second / "hcell_list", "stdcell stdcell\n")
    cfg = cfg.replace('extract', 'corners', value=["RCmax", "Cmin"])
    cfg = cfg.replace('extract', 'corner_temperatures', value=["125", "-40"])
    runner = RceRunner(cfg, stop_after="xrc_fmt_RCmax")
    ran: list[str] = []

    monkeypatch.setattr(runner, "_prepare", lambda: None)
    monkeypatch.setattr(runner, "_write_manifest", lambda: None)
    monkeypatch.setattr(runner, "_run_stage", lambda stage: ran.append(stage.name))
    monkeypatch.setattr(
        "rcepy.runner.generate_all", lambda config, context, **kwargs: {}
    )
    monkeypatch.setattr(
        runner,
        "_publish_outputs",
        lambda: pytest.fail("partial multi-corner output must not be published"),
    )

    assert runner.run() == 0
    assert ran[-1] == "xrc_fmt_RCmax"
