from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOT = ROOT.parents[1]
WRAPPER = ROOT / "bin" / "aivw"
PRODUCTION_PYTHON_ROOT = PRODUCT_ROOT / "runtime/python"
PRODUCTION_PYTHON = PRODUCTION_PYTHON_ROOT / "bin" / "python3"


# Retired spellings are rejected input, and an ambient SICO_HOME must not bind
# the launcher under test to another installation.
def _environment(cwd: Path) -> dict[str, str]:
    return {
        **{
            key: value for key, value in os.environ.items()
            if key not in ("CAD_HOME", "SICO_HOME", "CAD_PYTHON_ROOT", "CAD_PYTHON")
        },
        "SICO_HOME": str(PRODUCT_ROOT),
        "SICO_PYTHON_ROOT": "/stale/python",
        "SICO_PYTHON": "/stale/python3",
        "PWD": str(cwd),
    }


def test_cli_rejects_generic_execution_until_handlers_are_bound(tmp_path: Path) -> None:
    result = subprocess.run(
        [str(WRAPPER), "run-recipe", "saradc.comparator_new"],
        cwd=tmp_path,
        env=_environment(tmp_path),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert "--dry-run" in result.stderr
    assert "--through" in result.stderr


def test_cli_rejects_publication_through_generic_execution(tmp_path: Path) -> None:
    result = subprocess.run(
        [str(WRAPPER), "run-recipe", "saradc.comparator_new", "--through", "publish"],
        cwd=tmp_path,
        env=_environment(tmp_path),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert "human-approved promotion" in result.stderr


def test_cli_verify_run_rejects_missing_manifests(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            str(WRAPPER),
            "verify-run",
            "--control-manifest",
            str(tmp_path / "control.json"),
            "--payload-manifest",
            str(tmp_path / "payload.json"),
        ],
        cwd=tmp_path,
        env=_environment(tmp_path),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert "cannot read control manifest" in result.stderr


def test_cli_connectivity_checker_passes_a_complete_inventory(tmp_path: Path) -> None:
    source = "module top (a, y); input a; output y; leaf I0 (a, y); endmodule\nmodule leaf (a, y); input a; output y; endmodule\n"
    candidate = tmp_path / "candidate.sv"
    netlist = tmp_path / "netlist"
    map_path = tmp_path / "map"
    globalmap = tmp_path / "globalmap"
    candidate.write_text(source, encoding="utf-8")
    netlist.write_text(source, encoding="utf-8")
    map_path.write_text("( ( net\n )\n )\n", encoding="utf-8")
    globalmap.write_text(
        "$model\nlib/top/schematic top\nlib/leaf/schematic leaf\n$endmodel\n$net\n$endnet\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            str(WRAPPER),
            "check-connectivity",
            "--netlist",
            str(netlist),
            "--map",
            str(map_path),
            "--globalmap",
            str(globalmap),
            "--candidate",
            str(candidate),
            "--module",
            "top",
            "--port",
            "a",
            "--port",
            "y",
            "--json",
        ],
        cwd=tmp_path,
        env=_environment(tmp_path),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "PASS"
