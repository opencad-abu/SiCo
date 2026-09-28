from __future__ import annotations

from pathlib import Path

from rcepy.config import RceConfig
from rcepy.runner import RceRunner


def make_xrc_config(
    tmp_path: Path,
    *,
    rc_type: str = "RCC",
    output_type: str = "dspf",
) -> tuple[RceConfig, dict[str, Path]]:
    run_dir = tmp_path / "run"
    tech_dir = tmp_path / "tech"
    corner_dir = tech_dir / "RCmax"
    source = tmp_path / "top.cdl"
    layout = tmp_path / "top.gds"
    lvs_rule = tmp_path / "lvs.cal"
    suffix = {
        "sp": "sp",
        "spice": "sp",
        "hspice": "sp",
        "spef": "spef",
        "spectre": "scs",
    }.get(output_type, "dspf")
    output = run_dir / "db" / f"top.{suffix}"
    svdb = run_dir / "db" / "svdb.top"
    config_path = tmp_path / "rce.toml"

    corner_dir.mkdir(parents=True)
    (corner_dir / "xrc.cal").write_text("// foundry xRC rules\n", encoding="utf-8")
    (corner_dir / "hcell_list").write_text(
        "stdcell stdcell\n", encoding="utf-8"
    )
    source.write_text(".subckt top A Z\n.ends top\n", encoding="utf-8")
    layout.write_text("mock gds\n", encoding="utf-8")
    lvs_rule.write_text("// foundry RCE LVS rules\n", encoding="utf-8")

    raw = {
        "run": {"run_dir": str(run_dir), "cds_lib": ""},
        "input": {
            "type": "CDL+GDS",
            "cdl": {"file": source.name, "cell": "top"},
            "gds": {"file": layout.name, "cell": "top"},
        },
        "lvs": {
            "tool": "Calibre",
            "runset_file": lvs_rule.name,
            "ignore_error": False,
        },
        "extract": {
            "tool": "CalXRC",
            "tech_dir": str(tech_dir),
            "corner": "RCmax",
            "temperature": "-30",
            "rc_type": rc_type,
            "top_cell_source": "schematic",
            "name_source": "schematic",
            "output_type": output_type,
        },
        "runtime": {"lvs_cpus": "1", "ext_cpus": "3"},
        "netlist": {},
    }
    return RceConfig(raw=raw, config_path=config_path), {
        "run_dir": run_dir,
        "tech_dir": tech_dir,
        "corner_dir": corner_dir,
        "source": source,
        "layout": layout,
        "lvs_rule": lvs_rule,
        "output": output,
        "svdb": svdb,
        "runset": run_dir / "_xrc.cal_",
    }


def install_fake_calibre(mgc_home: Path) -> Path:
    calibre = mgc_home / "bin" / "calibre"
    calibre.parent.mkdir(parents=True)
    calibre.write_text(
        """#!/bin/sh
set -eu
printf '%s\n' "$*" >> "$FAKE_CALIBRE_TRACE"
case "$*" in
  *"-lvs"*)
    if [ "${FAKE_LVS_FAILURE_CODE:-0}" != 0 ]; then
      echo 'mock LVS startup or license failure'
      exit "$FAKE_LVS_FAILURE_CODE"
    fi
    mkdir -p db/svdb.top/top.phdb db/svdb.top/top.xdb
    : > db/svdb.top/top.sp
    : > db/svdb.top/top.phdb/pdb.seg
    : > db/svdb.top/top.xdb/pdb.seg
    if [ "${FAKE_LVS_INCORRECT:-0}" = 1 ]; then
      echo 'LVS completed. INCORRECT. See report file: lvs.report'
      exit "${FAKE_LVS_EXIT_CODE:-0}"
    fi
    echo 'LVS completed. CORRECT. See report file: lvs.report'
    ;;
  *"-pdb"*)
    if [ "${FAKE_PDB_FAIL:-0}" = 1 ]; then
      echo 'mock PDB failure'
      exit 23
    fi
    mkdir -p db/svdb.top/pex.db
    : > db/svdb.top/pex.db/top_raw.bspef
    echo "xRC Errors  =  ${FAKE_PDB_XRC_ERRORS:-${FAKE_XRC_ERRORS:-0}}"
    ;;
  *"-fmt"*)
    if [ "${FAKE_FMT_EMPTY:-0}" = 1 ]; then
      : > db/top.dspf
    else
      echo '* mock extracted netlist' > db/top.dspf
    fi
    echo "xRC Errors  =  ${FAKE_FMT_XRC_ERRORS:-${FAKE_XRC_ERRORS:-0}}"
    ;;
  *)
    echo 'unexpected calibre invocation' >&2
    exit 64
    ;;
esac
""",
        encoding="utf-8",
    )
    calibre.chmod(0o755)
    return calibre


def xrc_stages(cfg: RceConfig):
    return [stage for stage in RceRunner(cfg)._stages() if stage.name.startswith("xrc_")]


def expected_commands(calibre: Path, paths: dict[str, Path]) -> list[list[str]]:
    return [
        [
            str(calibre),
            "-lvs",
            "-hier",
            "-spice",
            str(paths["svdb"] / "top.sp"),
            "-hcell",
            str(paths["corner_dir"] / "hcell_list"),
            "-nowait",
            "_xrc.cal_",
        ],
        [
            str(calibre),
            "-xrc",
            "-pdb",
            "-rcc",
            "-turbo",
            "3",
            "-nowait",
            "_xrc.cal_",
        ],
        [str(calibre), "-xrc", "-fmt", "-all", "-nowait", "_xrc.cal_"],
    ]


def expected_trace(paths: dict[str, Path]) -> list[str]:
    return [" ".join(command[1:]) for command in expected_commands(Path("calibre"), paths)]
