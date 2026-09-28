from __future__ import annotations

import sys
from pathlib import Path

import pytest

PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from qrc_netlist_customize_fixtures import _command_section, _config
from rcepy.gen_qrc import generate_qrc  # noqa: E402


@pytest.mark.parametrize(
    ("output_type", "qrc_type", "suffix"),
    (
        ("dspf", "dspf", ".dspf"),
        ("sp", "spice", ".sp"),
        ("spice", "spice", ".sp"),
        ("hspice", "spice", ".sp"),
        ("spef", "spef", ".spef"),
    ),
)
def test_file_output_formats_emit_required_file_name(
    tmp_path: Path, output_type: str, qrc_type: str, suffix: str
) -> None:
    cfg = _config(tmp_path, output_type=output_type)

    command = generate_qrc(cfg, cfg.context()).read_text(encoding="utf-8")

    assert f"-type {qrc_type}" in _command_section(command, "output_db")
    output_setup = _command_section(command, "output_setup")
    assert f'-file_name "{cfg.output_path(cfg.context())}"' in output_setup
    assert cfg.output_path(cfg.context()).endswith(suffix)



@pytest.mark.parametrize(
    ("netlist", "legacy", "expected"),
    (
        ({}, {}, "false"),
        ({"dspf_remove_instances": "FALSE"}, {}, "false"),
        ({"dspf_remove_instances": "TRUE"}, {}, "true"),
        ({}, {"NETLIST_DSPF_RM_INSTANCE": True}, "false"),
        (
            {"dspf_remove_instances": "FALSE"},
            {"NETLIST_DSPF_RM_INSTANCE": True},
            "false",
        ),
    ),
)
def test_dspf_explicitly_controls_qrc_instance_output(
    tmp_path: Path,
    netlist: dict[str, object],
    legacy: dict[str, object],
    expected: str,
) -> None:
    cfg = _config(tmp_path, netlist=netlist, legacy=legacy)

    command = generate_qrc(cfg, cfg.context()).read_text(encoding="utf-8")

    assert f"-disable_instances {expected}" in _command_section(command, "output_db")



@pytest.mark.parametrize(
    "output_type", ("sp", "spice", "hspice", "spef", "extview", "smartview")
)
def test_disable_instances_is_only_emitted_for_dspf(
    tmp_path: Path, output_type: str
) -> None:
    cfg = _config(
        tmp_path,
        output_type=output_type,
        netlist={"dspf_remove_instances": "TRUE"},
    )

    command = generate_qrc(cfg, cfg.context()).read_text(encoding="utf-8")

    assert "-disable_instances" not in _command_section(command, "output_db")



def test_hierarchy_delimiter_only_changes_qrc_output_format(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path,
        netlist={
            "hierarchy_delimiter_enable": True,
            "hierarchy_delimiter": ".",
        },
    )

    command = generate_qrc(cfg, cfg.context()).read_text(encoding="utf-8")

    assert '-hierarchy_delimiter "/"' in _command_section(command, "input_db")
    assert '-hierarchy_delimiter "."' in _command_section(command, "output_db")



@pytest.mark.parametrize("legacy", ({}, {"NETLIST_HIERARCHY_DELIMETER": False}))
def test_disabled_hierarchy_delimiter_keeps_slash_default(
    tmp_path: Path, legacy: dict[str, object]
) -> None:
    cfg = _config(
        tmp_path,
        netlist={"hierarchy_delimiter": "."},
        legacy=legacy,
    )

    command = generate_qrc(cfg, cfg.context()).read_text(encoding="utf-8")

    assert '-hierarchy_delimiter "/"' in _command_section(command, "input_db")
    assert '-hierarchy_delimiter "/"' in _command_section(command, "output_db")



@pytest.mark.parametrize("separator", ("/", ".", "|", ":"))
def test_spef_accepts_ieee_hierarchy_delimiters(
    tmp_path: Path, separator: str
) -> None:
    cfg = _config(
        tmp_path,
        output_type="spef",
        netlist={
            "hierarchy_delimiter_enable": True,
            "hierarchy_delimiter": separator,
        },
    )

    command = generate_qrc(cfg, cfg.context()).read_text(encoding="utf-8")

    assert f'-hierarchy_delimiter "{separator}"' in _command_section(
        command, "output_db"
    )



def test_spef_rejects_non_ieee_hierarchy_delimiter(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path,
        output_type="spef",
        netlist={
            "hierarchy_delimiter_enable": True,
            "hierarchy_delimiter": "_",
        },
    )

    with pytest.raises(ValueError, match="QRC SPEF hierarchy delimiter"):
        generate_qrc(cfg, cfg.context())
