from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest


PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from rcepy.config import RceConfig  # noqa: E402
from rcepy.netlist_postprocess import (  # noqa: E402
    BusDelimiterMapping,
    configured_bus_delimiter_mapping,
    map_netlist_bus_delimiters,
    rewrite_netlist_bus_delimiters,
)
from rcepy.runner import RceRunner  # noqa: E402


@pytest.mark.parametrize(
    ("source", "target", "header"),
    [
        ("<>", "[]", "*|BUSBIT"),
        ("[]", "<>", "*BUS_DELIMITER"),
    ],
)
def test_bus_delimiter_mapping_changes_identifiers_and_header_only(
    source: str,
    target: str,
    header: str,
) -> None:
    source_name = f"A{source[0]}7{source[1]}"
    target_name = f"A{target[0]}7{target[1]}"
    text = (
        f'{header} "{source}"\n'
        f'*COMMENT "leave {source_name} unchanged"\n'
        f'* ordinary comment keeps {source_name}\n'
        f'.TITLE "leave {source_name} unchanged"\n'
        f'*|DATE "leave multi word {source_name} unchanged"\n'
        f".param note='leave multi word {source_name} unchanged'\n"
        f'.subckt top {source_name} Z\n'
        f'*|NET {source_name} 1.0\n'
        f'R1 {source_name} Z 1 model={source_name}\n'
        f'+ {source_name} "quoted-{source_name}" ; keep {source_name}\n'
        f'+ "quoted // text {source_name}" {source_name} // keep {source_name}\n'
    )

    transformed = map_netlist_bus_delimiters(
        text, BusDelimiterMapping(source, target)
    )

    assert f'{header} "{target}"' in transformed
    assert f'.subckt top {target_name} Z' in transformed
    assert f'*|NET {target_name} 1.0' in transformed
    assert f'R1 {target_name} Z 1 model={source_name}' in transformed
    assert f'+ {target_name} "quoted-{source_name}" ; keep {source_name}' in transformed
    assert f'*COMMENT "leave {source_name} unchanged"' in transformed
    assert f'* ordinary comment keeps {source_name}' in transformed
    assert f'.TITLE "leave {source_name} unchanged"' in transformed
    assert f'*|DATE "leave multi word {source_name} unchanged"' in transformed
    assert f".param note='leave multi word {source_name} unchanged'" in transformed
    assert (
        f'+ "quoted // text {source_name}" {target_name} // keep {source_name}'
        in transformed
    )


def test_rewrite_replaces_output_atomically_and_keeps_original(tmp_path: Path) -> None:
    output = tmp_path / "top.dspf"
    original = "*|BUSBIT <>\n.subckt top A<0> Z\n"
    output.write_text(original, encoding="utf-8")
    output.chmod(0o640)

    backup = rewrite_netlist_bus_delimiters(
        output, BusDelimiterMapping("<>", "[]")
    )

    assert backup == tmp_path / "top.dspf.pre_brackets"
    assert backup.read_text(encoding="utf-8") == original
    assert output.read_text(encoding="utf-8") == (
        "*|BUSBIT []\n.subckt top A[0] Z\n"
    )
    assert stat.S_IMODE(output.stat().st_mode) == 0o640
    assert not list(tmp_path.glob(".top.dspf.brackets.*"))
    assert (
        rewrite_netlist_bus_delimiters(
            output, BusDelimiterMapping("<>", "[]")
        )
        is None
    )


def _runner_config(tmp_path: Path, tool: str) -> RceConfig:
    return RceConfig(
        raw={
            "run": {"run_dir": str(tmp_path / "run")},
            "input": {
                "type": "CDL+GDS",
                "cdl": {"cell": "top", "file": str(tmp_path / "top.cdl")},
                "gds": {"cell": "top", "file": str(tmp_path / "top.gds")},
            },
            "extract": {
                "tool": tool,
                "output_type": "dspf",
            },
            "netlist": {
                "output_path": "top.dspf",
                "brackets_replace": True,
                "brackets_replace_type": "<> ===> []",
            },
        },
        config_path=tmp_path / "rce.toml",
    )


def test_runner_updates_qrc_output_but_skips_native_xrc_mapping(
    tmp_path: Path,
) -> None:
    qrc = RceRunner(_runner_config(tmp_path / "qrc", "QRC"))
    qrc_output = Path(qrc.cfg.output_path(qrc.ctx))
    qrc_output.parent.mkdir(parents=True)
    qrc_output.write_text("*|BUSBIT <>\n.subckt top A<0>\n", encoding="utf-8")

    qrc._postprocess()

    assert "A[0]" in qrc_output.read_text(encoding="utf-8")
    assert qrc_output.with_name("top.dspf.pre_brackets").is_file()

    xrc = RceRunner(_runner_config(tmp_path / "xrc", "CalXRC"))
    xrc_output = Path(xrc.cfg.output_path(xrc.ctx))
    xrc_output.parent.mkdir(parents=True)
    xrc_output.write_text("*|BUSBIT <>\n.subckt top A<0>\n", encoding="utf-8")

    xrc._postprocess()

    assert "A<0>" in xrc_output.read_text(encoding="utf-8")
    assert not xrc_output.with_name("top.dspf.pre_brackets").exists()


def test_configured_mapping_rejects_unknown_replacement(tmp_path: Path) -> None:
    cfg = _runner_config(tmp_path, "QRC")
    cfg = cfg.replace('netlist', 'brackets_replace_type', value="{} ===> []")

    with pytest.raises(ValueError, match="Unsupported brackets replacement type"):
        configured_bus_delimiter_mapping(cfg)


def test_runner_does_not_rewrite_output_when_stopped_before_extract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tech = tmp_path / "tech/RCmax"
    tech.mkdir(parents=True)
    (tech / "qrcTechFile").write_text("tech\n", encoding="utf-8")
    cfg = RceConfig(
        raw={
            "run": {"run_dir": str(tmp_path / "run")},
            "input": {
                "type": "SVDB",
                "svdb": {"cell": "top", "dir": str(tmp_path / "svdb.top")},
            },
            "extract": {
                "tool": "QRC",
                "tech_dir": str(tmp_path / "tech"),
                "corner": "RCmax",
                "temperature": "25",
                "rc_type": "R+Cg+Cc",
                "output_type": "dspf",
            },
            "netlist": {
                "output_path": "top.dspf",
                "brackets_replace": True,
                "brackets_replace_type": "<> ===> []",
            },
        },
        config_path=tmp_path / "rce.toml",
    )
    output = Path(cfg.output_path(cfg.context()))
    output.parent.mkdir(parents=True)
    output.write_text("*|BUSBIT <>\n.subckt top A<0>\n", encoding="utf-8")
    monkeypatch.setenv("RCE_BACKUP_DONE", "1")

    assert RceRunner(cfg, dry_run=True, stop_after="query").run() == 0

    assert "A<0>" in output.read_text(encoding="utf-8")
    assert not output.with_name("top.dspf.pre_brackets").exists()
