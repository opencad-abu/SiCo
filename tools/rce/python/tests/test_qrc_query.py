from __future__ import annotations

import sys
from pathlib import Path


PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from rcepy.config import RceConfig  # noqa: E402
from rcepy.gen_query import generate_query  # noqa: E402


def test_calibre_query_writes_pin_locations_before_agf_hierarchy(
    tmp_path: Path,
) -> None:
    cfg = RceConfig(
        raw={
            "run": {"run_dir": str(tmp_path / "run")},
            "input": {
                "type": "CDL+GDS",
                "cdl": {"file": "top.cdl", "cell": "source_top"},
                "gds": {"file": "top.gds", "cell": "layout_top"},
            },
            "extract": {"tool": "QRC"},
        },
        config_path=tmp_path / "rce.toml",
    )
    ctx = cfg.context()

    lines = generate_query(cfg, ctx).read_text(encoding="utf-8").splitlines()
    prefix = f"{ctx.cci_dir}/{ctx.layout_cell}"
    pin_locations = lines.index("layout netlist pin locations YES")
    hierarchy = lines.index("layout netlist hierarchy AGF")
    netlist_write = lines.index(f"layout netlist write {prefix}_pin_xy.spi")
    separated = lines.index("layout netlist separated properties YES")
    properties_write = lines.index(f"layout separated properties write {prefix}.props")

    assert pin_locations < hierarchy < netlist_write < separated < properties_write
    assert properties_write == netlist_write + 2
    assert lines.count("layout netlist pin locations YES") == 1
