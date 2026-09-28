from __future__ import annotations

import sys
from pathlib import Path

PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from rcepy.config import RceConfig  # noqa: E402


def _config(
    tmp_path: Path,
    *,
    output_type: str = "dspf",
    netlist: dict[str, object] | None = None,
) -> RceConfig:
    corner_dir = tmp_path / "tech/RCmax"
    corner_dir.mkdir(parents=True, exist_ok=True)
    (corner_dir / "nxtgrd").write_text("grid\n", encoding="utf-8")
    (corner_dir / "tran.map").write_text("map\n", encoding="utf-8")

    return RceConfig(
        raw={
            "run": {"run_dir": str(tmp_path / "run")},
            "input": {
                "type": "CDL+GDS",
                "cdl": {"cell": "source_top", "file": "external/source_top.cdl"},
                "gds": {"cell": "layout_top", "file": "external/layout_top.gds"},
            },
            "lvs": {"tool": "Calibre"},
            "extract": {
                "tool": "StarRC",
                "tech_dir": str(tmp_path / "tech"),
                "corner": "RCmax",
                "temperature": "25",
                "rc_type": "R+Cg+Cc",
                "output_type": output_type,
                "top_cell_source": "layout",
                "name_source": "schematic",
            },
            "netlist": netlist or {},
        },
        config_path=tmp_path / "rce.toml",
    )
