from __future__ import annotations

import sys
from pathlib import Path

PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from rcepy.config import RceConfig  # noqa: E402


def _config(
    tmp_path: Path,
    tool: str,
    *,
    selection: dict[str, object] | None = None,
    filters: dict[str, object] | None = None,
    legacy: dict[str, object] | None = None,
) -> RceConfig:
    tech_dir = tmp_path / "tech"
    corner_dir = tech_dir / "RCmax"
    corner_dir.mkdir(parents=True, exist_ok=True)
    if tool == "QRC":
        (corner_dir / "qrcTechFile").write_text("tech\n", encoding="utf-8")
    elif tool == "StarRC":
        (corner_dir / "nxtgrd").write_text("grid\n", encoding="utf-8")
        (corner_dir / "tran.map").write_text("map\n", encoding="utf-8")
    else:  # pragma: no cover - protects the test helper itself
        raise ValueError(f"Unsupported test tool: {tool}")

    raw: dict[str, object] = {
        "run": {"run_dir": str(tmp_path / "run")},
        "input": {
            "type": "CDL+GDS",
            "cdl": {"cell": "source_top", "file": "input/source_top.cdl"},
            "gds": {"cell": "layout_top", "file": "input/layout_top.gds"},
        },
        "lvs": {"tool": "Calibre"},
        "extract": {
            "tool": tool,
            "tech_dir": str(tech_dir),
            "corner": "RCmax",
            "temperature": "25",
            "rc_type": "R+Cg+Cc",
            "output_type": "dspf",
            "top_cell_source": "layout",
            "name_source": "schematic",
        },
        "runtime": {"lvs_cpus": "1", "ext_cpus": "1"},
        "netlist": {},
    }
    if selection is not None:
        raw["selection"] = selection
    if filters is not None:
        raw["filter"] = filters
    raw.update(legacy or {})
    return RceConfig(raw=raw, config_path=tmp_path / "rce.toml")
