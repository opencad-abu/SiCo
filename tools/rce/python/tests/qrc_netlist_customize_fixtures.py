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
    input_type: str = "CDL+GDS",
    output_type: str = "dspf",
    name_source: str | None = "schematic",
    netlist: dict[str, object] | None = None,
    legacy: dict[str, object] | None = None,
) -> RceConfig:
    tech_dir = tmp_path / "tech/RCmax"
    tech_dir.mkdir(parents=True, exist_ok=True)
    (tech_dir / "qrcTechFile").write_text("tech\n", encoding="utf-8")
    cds_lib = tmp_path / "cds.lib"
    cds_lib.write_text("DEFINE layout_lib ./layout_lib\n", encoding="utf-8")

    extract: dict[str, object] = {
        "tool": "QRC",
        "tech_dir": str(tmp_path / "tech"),
        "corner": "RCmax",
        "temperature": "25",
        "rc_type": "R+Cg+Cc",
        "output_type": output_type,
        "top_cell_source": "layout",
    }
    if name_source is not None:
        extract["name_source"] = name_source
    if output_type.casefold() in {
        "view",
        "extview",
        "smartview",
        "calibreview",
        "starrcview",
    }:
        extract["view"] = {
            "library": "layout_lib",
            "cell": "layout_top",
            "layout_view": "layout",
        }

    raw: dict[str, object] = {
        "run": {"run_dir": str(tmp_path / "run"), "cds_lib": str(cds_lib)},
        "input": {
            "type": input_type,
            "schematic": {
                "lib": "source_lib",
                "cell": "source_top",
                "view": "schematic",
            },
            "layout": {
                "lib": "layout_lib",
                "cell": "layout_top",
                "view": "layout",
            },
            "cdl": {"file": "external/source_top.cdl", "cell": "source_top"},
            "gds": {"file": "external/layout_top.gds", "cell": "layout_top"},
        },
        "lvs": {"tool": "Calibre"},
        "extract": extract,
        "runtime": {"lvs_cpus": "1", "ext_cpus": "1"},
        "netlist": netlist or {},
    }
    if output_type in {"extview", "smartview"} and input_type in {"CDL+LAY", "CDL+GDS"}:
        # Native view output needs the export mapping for an external CDL.
        mapping = tmp_path / "external/cdl_export"
        mapping.mkdir(parents=True, exist_ok=True)
        (mapping / "si.env").write_text("simSimulator = \"auCdl\"\n", encoding="utf-8")
        raw["input"]["cdl"]["run_directory"] = str(mapping)
    raw.update(legacy or {})
    return RceConfig(raw=raw, config_path=tmp_path / "rce.toml")


def _command_section(command: str, name: str) -> str:
    return command.split(f"{name} \\\n", 1)[1].split("\n\n", 1)[0]
