from __future__ import annotations

import sys
from pathlib import Path

import pytest

PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from rcepy.gen_starrc import generate_starrc  # noqa: E402
from starrc_netlist_customize_fixtures import _config


def test_starrc_user_pin_order_uses_input_cdl_for_dspf(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path,
        netlist={
            "pin_order_enable": True,
            "pin_order_type": "CDL Netlist File",
        },
    )

    command = generate_starrc(cfg, cfg.context()).read_text(encoding="utf-8")

    expected = tmp_path / "external/source_top.cdl"
    assert f"SPICE_SUBCKT_FILE: {expected}" in command



def test_starrc_user_pin_order_accepts_existing_user_file(tmp_path: Path) -> None:
    pin_file = tmp_path / "pins/custom.cdl"
    pin_file.parent.mkdir(parents=True)
    pin_file.write_text(".subckt layout_top A Z\n.ends\n", encoding="utf-8")
    cfg = _config(
        tmp_path,
        output_type="spf",
        netlist={
            "pin_order_enable": True,
            "pin_order_type": "User Defined File",
            "pin_order_file": "pins/custom.cdl",
        },
    )

    command = generate_starrc(cfg, cfg.context()).read_text(encoding="utf-8")

    assert f"SPICE_SUBCKT_FILE: {pin_file}" in command



def test_starrc_rejects_user_pin_order_for_spef(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path,
        output_type="spef",
        netlist={"pin_order_enable": True},
    )

    with pytest.raises(
        ValueError, match=r"StarRC User Pin Order.*only for DSPF/SPF"
    ):
        generate_starrc(cfg, cfg.context())



@pytest.mark.parametrize("included", (False, True))
@pytest.mark.parametrize("source", ("CDL Netlist File", "User Defined File"))
def test_starrc_rejects_common_pin_file_override(
    tmp_path: Path, included: bool, source: str
) -> None:
    chosen = tmp_path / "chosen.cdl"
    chosen.write_text(".subckt source_top Z A\n.ends\n", encoding="utf-8")
    other = tmp_path / "Chosen.cdl"  # Case-only difference must still conflict.
    other.write_text(".subckt source_top A Z\n.ends\n", encoding="utf-8")
    cfg = _config(tmp_path, netlist={
        "pin_order_enable": True, "pin_order_type": source,
        "pin_order_file": str(chosen),
    })
    cfg = cfg.replace('input', 'cdl', 'file', value=str(chosen))
    common = tmp_path / "tech/RCmax/common.opt"
    override = tmp_path / "override.opt" if included else common
    override.write_text(f"SPICE_SUBCKT_FILE: {other}\n", encoding="utf-8")
    if included:
        common.write_text(f"INCLUDE_FILE: {override}\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"SPICE_SUBCKT_FILE.*common\.opt"):
        generate_starrc(cfg, cfg.context())
    assert not (tmp_path / "run/log/star.cmd").exists()



@pytest.mark.parametrize("included", (False, True))
def test_starrc_pin_order_rejects_common_output_format_override(
    tmp_path: Path, included: bool
) -> None:
    cfg = _config(tmp_path, netlist={"pin_order_enable": True})
    common = tmp_path / "tech/RCmax/common.opt"
    override = tmp_path / "override.opt" if included else common
    override.write_text("NETLIST_FORMAT: SPEF\n", encoding="utf-8")
    if included:
        intermediate = tmp_path / "intermediate.opt"
        intermediate.write_text(f"INCLUDE_FILE: {override}\n", encoding="utf-8")
        common.write_text(f"INCLUDE_FILE: {intermediate}\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"NETLIST_FORMAT: SPF.*NETLIST_FORMAT: SPEF"):
        generate_starrc(cfg, cfg.context())
    assert not (tmp_path / "run/log/star.cmd").exists()



def test_starrc_allows_equivalent_common_pin_file_path(tmp_path: Path) -> None:
    pin_file = tmp_path / "pins with spaces/top.cdl"
    pin_file.parent.mkdir()
    pin_file.write_text(".subckt source_top Z A\n.ends\n", encoding="utf-8")
    cfg = _config(tmp_path, netlist={
        "pin_order_enable": True, "pin_order_type": "User Defined File",
        "pin_order_file": str(pin_file),
    })
    (tmp_path / "tech/RCmax/common.opt").write_text(
        'SPICE_SUBCKT_FILE: "../pins with spaces/./top.cdl"\nNETLIST_FORMAT: SPF\n',
        encoding="utf-8",
    )
    assert generate_starrc(cfg, cfg.context()).is_file()



def test_starrc_disabled_pin_order_preserves_foundry_setting(tmp_path: Path) -> None:
    cfg = _config(tmp_path, netlist={"pin_order_enable": False})
    (tmp_path / "tech/RCmax/common.opt").write_text(
        "SPICE_SUBCKT_FILE: foundry.cdl\n", encoding="utf-8"
    )
    command = generate_starrc(cfg, cfg.context()).read_text(encoding="utf-8")
    assert command.count("SPICE_SUBCKT_FILE:") == 1
    assert "SPICE_SUBCKT_FILE: foundry.cdl" in command



def test_starrc_rejects_missing_user_pin_order_file(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path,
        netlist={
            "pin_order_enable": True,
            "pin_order_type": "User Defined File",
            "pin_order_file": "pins/missing.cdl",
        },
    )

    with pytest.raises(FileNotFoundError, match="StarRC pin-order file"):
        generate_starrc(cfg, cfg.context())



def test_starrc_rejects_unsupported_pin_order_source(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path,
        netlist={
            "pin_order_enable": True,
            "pin_order_type": "Layout",
        },
    )

    with pytest.raises(ValueError, match="Unsupported StarRC pin-order type"):
        generate_starrc(cfg, cfg.context())
