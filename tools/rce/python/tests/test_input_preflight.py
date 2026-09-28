"""Direct tests for RCE input ownership and managed-path rejection."""

from __future__ import annotations

from pathlib import Path

import pytest

from rcepy.input_preflight import configured_input_paths, validate_input_locations
from rcepy.runner import RceRunner
from test_extract_stage_outputs import _config
from xrc_test_support import make_xrc_config


def test_configured_input_paths_keeps_active_files_and_ignores_name_literals(
    tmp_path: Path,
) -> None:
    cfg = _config(tmp_path, "QRC")
    source = tmp_path / "pins.spi"
    source.write_text("* pins\n")
    cfg = cfg.replace(
        "netlist",
        value={
            "pin_order_enable": True,
            "pin_order_type": "User Defined File",
            "pin_order_file": str(source),
        },
    )
    cfg = cfg.replace(
        "selection", value={"net_enable": True, "nets": "$LITERAL_NET_NAME"}
    )

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.delenv("LITERAL_NET_NAME", raising=False)
        paths = dict(configured_input_paths(cfg, cfg.context()))
    assert paths["netlist.pin_order_file"] == source
    assert "selection.nets" not in paths


def test_xrc_input_preflight_collects_each_corner_rule_and_hcell(
    tmp_path: Path,
) -> None:
    cfg, paths = make_xrc_config(tmp_path)
    cfg = cfg.replace(
        "extract", "corner", value="RCmax RCmin"
    ).replace("extract", "temperature", value="-30 125")
    second = paths["tech_dir"] / "RCmin"
    second.mkdir()
    (second / "xrc.cal").write_text("// second rules\n")
    (second / "hcell_list").write_text("stdcell stdcell\n")
    values = configured_input_paths(cfg, cfg.context())
    rule_paths = [path for name, path in values if name == "extract.xrc.rule_file"]
    hcell_paths = [path for name, path in values if name == "extract.xrc.base_hcell_file"]
    assert rule_paths == [paths["corner_dir"] / "xrc.cal", second / "xrc.cal"]
    assert hcell_paths == [paths["corner_dir"] / "hcell_list", second / "hcell_list"]


@pytest.mark.parametrize("kind", ["managed", "published", "reduced"])
def test_validate_input_locations_rejects_each_output_boundary(
    tmp_path: Path, kind: str
) -> None:
    cfg = _config(tmp_path, "QRC")
    ctx = cfg.context()
    if kind == "managed":
        source = ctx.db_dir / "cci.top"
        field = ("input", "cci", "dir")
    elif kind == "published":
        source = ctx.run_dir / "top.dspf"
        field = ("netlist", "pin_order_file")
    else:
        source = ctx.run_dir / "reduced.dspf"
        field = ("reduction", "canonical_device_file")
    source.parent.mkdir(parents=True, exist_ok=True)
    if kind == "managed":
        source.mkdir()
    else:
        source.write_text("input\n")
    cfg = cfg.replace(*field, value=str(source))
    if kind == "published":
        cfg = cfg.replace(
            "netlist", value={
                **cfg.section("netlist"),
                "pin_order_enable": True,
                "pin_order_type": "User Defined File",
                "pin_order_file": str(source),
            }
        )
    if kind == "reduced":
        cfg = cfg.replace(
            "reduction", value={
                **cfg.section("reduction"),
                "enabled": True,
                "canonical_device_file": str(source),
            }
        )

    with pytest.raises(ValueError, match="overlaps RCE-managed data"):
        validate_input_locations(
            cfg,
            ctx,
            output_paths=(ctx.run_dir / "top.dspf",),
            reduced_paths=(ctx.run_dir / "reduced.dspf",),
        )


def test_runner_delegates_preflight_without_copying_input_path_state(tmp_path: Path):
    cfg = _config(tmp_path, "QRC")
    runner = RceRunner(cfg)
    assert not hasattr(runner, "_configured_input_paths")
    assert configured_input_paths(cfg, runner.ctx)
