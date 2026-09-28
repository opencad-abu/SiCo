from __future__ import annotations

import sys
from pathlib import Path

import pytest


PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from rcepy.cli import build_parser  # noqa: E402
from rcepy.generators import generate_all  # noqa: E402
from rcepy.runner import RceRunner  # noqa: E402
from xrc_test_support import make_xrc_config  # noqa: E402


def test_xrc_translates_source_and_user_file_pin_order(tmp_path: Path) -> None:
    cfg, paths = make_xrc_config(tmp_path)
    cfg = cfg.replace('netlist', value={
        "pin_order_enable": True,
        "pin_order_type": "CDL Netlist File",
    })

    generate_all(cfg, cfg.context())
    assert "PEX PIN ORDER SOURCE" in paths["runset"].read_text(encoding="utf-8")

    pin_file = tmp_path / "simulator.spi"
    pin_file.write_text(".subckt top Z A\n.ends top\n", encoding="utf-8")
    cfg = cfg.replace('netlist', value={**cfg.section('netlist'), **{"pin_order_type": "User Defined File", "pin_order_file": pin_file.name}})

    generate_all(cfg, cfg.context())
    assert f'PEX PIN ORDER FILE "{pin_file}"' in paths["runset"].read_text(
        encoding="utf-8"
    )


def test_xrc_rejects_removed_hier_cell_selection(tmp_path: Path) -> None:
    cfg, _ = make_xrc_config(tmp_path)
    cfg = cfg.replace('selection', value={
        "cell_enable": True,
        "cell_type": "Hier Cells",
        "cells": ["macro"],
    })

    with pytest.raises(ValueError, match="only 'Block Cells' is supported"):
        generate_all(cfg, cfg.context())


@pytest.mark.parametrize("input_type", ["SVDB", "CCI"])
def test_xrc_rejects_external_database_inputs(tmp_path: Path, input_type: str) -> None:
    cfg, _ = make_xrc_config(tmp_path)
    key = input_type.lower()
    cfg = cfg.replace('input', value={
        "type": input_type,
        key: {"dir": str(tmp_path / key), "cell": "top"},
    })

    with pytest.raises(ValueError, match="supported input types"):
        generate_all(cfg, cfg.context())


def test_relative_run_dir_is_resolved_from_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, _ = make_xrc_config(tmp_path)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    cfg = type(cfg)(cfg.to_dict(), config_dir / "rce.toml")
    cfg = cfg.replace('run', 'run_dir', value="relative-run")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    assert cfg.run_dir == config_dir / "relative-run"


@pytest.mark.parametrize("runner_options", [{"dry_run": True}, {"generate_only": True}])
def test_stop_after_must_belong_to_selected_flow(
    tmp_path: Path, runner_options: dict[str, bool]
) -> None:
    cfg, paths = make_xrc_config(tmp_path)

    with pytest.raises(ValueError, match="not part of this flow"):
        RceRunner(cfg, stop_after="query", **runner_options).run()
    assert not paths["run_dir"].exists()


def test_cli_requires_an_explicit_subcommand() -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args([])

    assert exc_info.value.code == 2


def test_cli_does_not_expose_legacy_run() -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["legacy-run", "rce.toml"])

    assert exc_info.value.code == 2


def test_generate_rejects_run_only_options() -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["generate", "rce.toml", "--dry-run"])

    assert exc_info.value.code == 2
