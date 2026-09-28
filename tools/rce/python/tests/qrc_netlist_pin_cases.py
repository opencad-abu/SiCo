from __future__ import annotations

import sys
from pathlib import Path

import pytest

PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from qrc_netlist_customize_fixtures import _command_section, _config
from rcepy.gen_qrc import generate_qrc  # noqa: E402


def test_user_pin_order_uses_native_qrc_option_in_schematic_namespace(
    tmp_path: Path,
) -> None:
    pin_file = tmp_path / "pins/top.spi"
    pin_file.parent.mkdir(parents=True)
    pin_file.write_text(".subckt source_top A Z\n.ends\n", encoding="utf-8")
    cfg = _config(
        tmp_path,
        netlist={
            "pin_order_enable": True,
            "pin_order_type": "User Defined File",
            "pin_order_file": "pins/top.spi",
        },
    )

    command = generate_qrc(cfg, cfg.context()).read_text(encoding="utf-8")

    expected = tmp_path / "pins/top.spi"
    assert f'-pin_order_file "{expected}"' in _command_section(command, "output_db")



def test_user_pin_order_rejects_missing_or_unknown_user_file(
    tmp_path: Path,
) -> None:
    missing = _config(
        tmp_path / "missing",
        netlist={
            "pin_order_enable": True,
            "pin_order_type": "User Defined File",
            "pin_order_file": "pins/missing.spi",
        },
    )
    with pytest.raises(FileNotFoundError, match="QRC pin-order file"):
        generate_qrc(missing, missing.context())

    unknown = _config(
        tmp_path / "unknown",
        netlist={"pin_order_enable": True, "pin_order_type": "Layout"},
    )
    with pytest.raises(ValueError, match="Unsupported QRC pin-order type"):
        generate_qrc(unknown, unknown.context())



@pytest.mark.parametrize("output_type", ("dspf", "sp", "spef"))
def test_user_pin_order_rejects_layout_namespace_for_file_netlist(
    tmp_path: Path, output_type: str
) -> None:
    cfg = _config(
        tmp_path,
        output_type=output_type,
        name_source="layout",
        netlist={"pin_order_enable": True},
    )

    with pytest.raises(
        ValueError, match="QRC User Pin Order.*schematic.*file netlists"
    ):
        generate_qrc(cfg, cfg.context())



def test_legacy_pin_order_key_is_ignored(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path,
        name_source=None,
        legacy={"EXT_PIN_CONTROL_ENABLE": True},
    )

    command = generate_qrc(cfg, cfg.context()).read_text(encoding="utf-8")
    assert "user_pin_order" not in command



@pytest.mark.parametrize("output_type", ("extview", "smartview"))
def test_view_output_forces_schematic_namespace_and_allows_pin_order(
    tmp_path: Path, output_type: str
) -> None:
    cfg = _config(
        tmp_path,
        input_type="OA",
        output_type=output_type,
        name_source="layout",
        netlist={"pin_order_enable": True},
    )

    command = generate_qrc(cfg, cfg.context()).read_text(encoding="utf-8")

    assert '-pin_order_file "' in _command_section(command, "output_db")
    assert '-net_name_space "SCHEMATIC"' in _command_section(command, "output_setup")
