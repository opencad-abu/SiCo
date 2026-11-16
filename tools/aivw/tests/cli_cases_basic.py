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


def test_wrapper_is_named_aivw_and_reports_cwd_artifact_root(tmp_path: Path) -> None:
    result = subprocess.run(
        [str(WRAPPER), "info", "--json"],
        cwd=tmp_path,
        env=_environment(tmp_path),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["command"] == "aivw"
    assert payload["artifact_root"] == str(tmp_path / ".aivw")


def test_cli_detaches_inherited_mps_selectors(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    environment.update(
        {
            "CDS_MPS_SESSION": "host",
            "CDS_MPS_FUTURE_SELECTOR": "future",
        }
    )
    result = subprocess.run(
        [str(WRAPPER), "info", "--json"],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    cli = (ROOT / "python" / "aivworkbench" / "cli.py").read_text(encoding="utf-8")
    assert "detach_mps_environment(os.environ)" in cli


def test_m1ai_run_requires_an_explicit_gate(tmp_path: Path) -> None:
    result = subprocess.run(
        [str(WRAPPER), "run", "m1-ai", "--json"],
        cwd=tmp_path,
        env=_environment(tmp_path),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "--gate golden-nominal" in result.stderr


def test_m1ai_correlation_without_evidence_is_a_blocked_input(tmp_path: Path) -> None:
    result = subprocess.run(
        [str(WRAPPER), "run", "m1-ai", "--gate", "correlation"],
        cwd=tmp_path,
        env=_environment(tmp_path),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert "status: BLOCKED_INPUT" in result.stdout


def test_m1ai_evidence_gates_require_source_manifests(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    for gate, option in (
        ("rnm-evidence", "--rnm-source-manifest"),
        ("spectre-evidence", "--spectre-source-manifest"),
    ):
        result = subprocess.run(
            [str(WRAPPER), "run", "m1-ai", "--gate", gate],
            cwd=tmp_path,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 1
        assert option in result.stderr


def test_cli_lists_and_validates_generic_recipe(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    listed = subprocess.run(
        [str(WRAPPER), "recipes", "--json"],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert listed.returncode == 0, listed.stderr
    assert "saradc.comparator_new" in json.loads(listed.stdout)["recipes"]

    validated = subprocess.run(
        [str(WRAPPER), "recipe", "saradc.comparator_new", "--json"],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert validated.returncode == 0, validated.stderr
    payload = json.loads(validated.stdout)
    assert payload["status"] == "PASS"
    assert payload["target"]["view"] == "rnm"
