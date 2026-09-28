from __future__ import annotations

import sys
from pathlib import Path

import pytest

PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from rcepy.gen_starrc import generate_starrc  # noqa: E402
from starrc_netlist_customize_fixtures import _config


@pytest.mark.parametrize(
    ("output_type", "netlist_format", "suffix"),
    (
        ("dspf", "SPF", "dspf"),
        ("spf", "SPF", "spf"),
        ("spef", "SPEF", "spef"),
        ("DSPF", "SPF", "dspf"),
    ),
)
def test_starrc_emits_supported_parasitic_netlist_formats(
    tmp_path: Path,
    output_type: str,
    netlist_format: str,
    suffix: str,
) -> None:
    cfg = _config(tmp_path, output_type=output_type)

    command = generate_starrc(cfg, cfg.context()).read_text(encoding="utf-8")

    assert f"NETLIST_FORMAT: {netlist_format}" in command
    assert f"NETLIST_FILE: {tmp_path}/run/db/layout_top.{suffix}" in command



@pytest.mark.parametrize("output_type", ("sp", "spice", "hspice"))
def test_starrc_rejects_ordinary_spice_output(
    tmp_path: Path, output_type: str
) -> None:
    cfg = _config(tmp_path, output_type=output_type)

    with pytest.raises(
        ValueError,
        match=r"StarRC output type.*NETLIST_FORMAT.*ordinary SPICE.*dspf, spf, spef",
    ):
        generate_starrc(cfg, cfg.context())

    assert not (tmp_path / "run/log/star.cmd").exists()



@pytest.mark.parametrize("separator", ("/", ".", "|", ":"))
def test_starrc_accepts_documented_hierarchy_separators(
    tmp_path: Path, separator: str
) -> None:
    cfg = _config(
        tmp_path,
        netlist={
            "hierarchy_delimiter_enable": True,
            "hierarchy_delimiter": separator,
        },
    )

    command = generate_starrc(cfg, cfg.context()).read_text(encoding="utf-8")

    assert f"HIERARCHICAL_SEPARATOR: {separator}" in command



def test_disabled_hierarchy_delimiter_keeps_starrc_slash_default(
    tmp_path: Path,
) -> None:
    cfg = _config(
        tmp_path,
        netlist={
            "hierarchy_delimiter_enable": False,
            "hierarchy_delimiter": "\\",
        },
    )

    command = generate_starrc(cfg, cfg.context()).read_text(encoding="utf-8")

    assert "HIERARCHICAL_SEPARATOR: /" in command



@pytest.mark.parametrize("separator", ("", "\\", "_", "::"))
def test_starrc_rejects_unsupported_hierarchy_separator(
    tmp_path: Path, separator: str
) -> None:
    cfg = _config(
        tmp_path,
        netlist={
            "hierarchy_delimiter_enable": True,
            "hierarchy_delimiter": separator,
        },
    )

    with pytest.raises(ValueError, match="Unsupported StarRC hierarchy delimiter"):
        generate_starrc(cfg, cfg.context())
