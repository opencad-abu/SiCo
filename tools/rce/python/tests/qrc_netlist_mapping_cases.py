from __future__ import annotations

import sys
from pathlib import Path

import pytest

PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from qrc_netlist_customize_fixtures import _command_section, _config
from rcepy.gen_qrc import generate_qrc  # noqa: E402


@pytest.mark.parametrize("output_type", ("dspf", "sp", "spef", "extview"))
@pytest.mark.parametrize(
    ("input_type", "has_generated_mapping"),
    (
        ("OA", True),
        ("SCH+GDS", True),
        ("CDL+LAY", False),
        ("CDL+GDS", False),
    ),
)
def test_cdl_out_mapping_uses_generated_or_external_export_directory(
    tmp_path: Path,
    input_type: str,
    has_generated_mapping: bool,
    output_type: str,
) -> None:
    cfg = _config(tmp_path, input_type=input_type, output_type=output_type)

    command = generate_qrc(cfg, cfg.context()).read_text(encoding="utf-8")
    output_db = _command_section(command, "output_db")

    requires_mapping = has_generated_mapping or cfg.is_view_output
    assert ("-cdl_out_map_directory" in output_db) is requires_mapping
    if has_generated_mapping:
        assert f'"{cfg.context().cdl_dir}"' in output_db
    elif cfg.is_view_output:
        assert f'"{tmp_path / "external/cdl_export"}"' in output_db



def test_brackets_replace_does_not_emit_qrc_busbit_conversion(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path,
        netlist={
            "brackets_replace": True,
            "brackets_replace_type": "<> ===> []",
        },
    )

    command = generate_qrc(cfg, cfg.context()).read_text(encoding="utf-8")

    assert "-busbit_delimiter" not in command
    assert "-replace_square_busbit_delimiter" not in command
