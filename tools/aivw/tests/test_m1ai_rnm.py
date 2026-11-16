from __future__ import annotations

import json
import sys
from pathlib import Path
from dataclasses import replace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ai" / "skills" / "schematic-to-rnm" / "scripts"))

from aivworkbench.manifest import resolve_indexed_artifact, verify_split_manifests
from aivworkbench.m1ai_correlation import _validate_source_run
from aivworkbench.m1ai_rnm import render_comparator_rnm, run_m1_ai_rnm
from aivworkbench.profiles import load_profile, product_root
from aivworkbench.workspace import resolve_launch_paths
from check_rnm import parse_module


def _supplement() -> dict[str, object]:
    return json.loads(
        (product_root() / "contracts" / "comparator_new_rnm.json").read_text(
            encoding="utf-8"
        )
    )


def test_comparator_rnm_render_preserves_escaped_port_contract() -> None:
    text = render_comparator_rnm(_supplement(), source_sha256="contract", spec_sha256="spec")
    assert "module comparator_new (out, outb, \\Din+ , \\Din- , clk);" in text
    assert "input \\Din+ ;" in text
    assert "input \\Din- ;" in text
    assert "always @(posedge clk)" in text
    assert "assign out = out_state;" in text
    assert "assign outb = outb_state;" in text


def test_rnm_supplement_requires_explicit_correlation_status() -> None:
    supplement = _supplement()
    assert supplement["status"] == "assumption_pending_spectre_correlation"
    assert supplement["provenance"]["requires_spectre_correlation"] is True
    region = supplement["behavior"]["approved_region_policy"]
    assert region["status"] == "approved_direction_pending_calibration"
    assert region["strategy"] == "indeterminate_band"
    assert region["encoding"] == "undecided"
    assert "Spectre waveform equivalence" in supplement["verification"]["does_not_prove"]


def test_checker_preserves_oa_escaped_identifiers() -> None:
    module = parse_module(render_comparator_rnm(_supplement(), source_sha256="x", spec_sha256="y"))
    assert module is not None
    assert module.port_names() == ["out", "outb", "Din+", "Din-", "clk"]
    assert module.net_types["Din+"] == "wreal1driver"
    assert module.net_types["Din-"] == "wreal1driver"


def test_rnm_runner_writes_eda_payload_outside_control_root(tmp_path: Path) -> None:
    launch_dir = tmp_path / "launch"
    payload_dir = tmp_path / "payload"
    launch_dir.mkdir()
    payload_dir.mkdir()
    setup = tmp_path / "setup.bashrc"
    setup.write_text(f"export PROJ_AMS_DB_DIR={payload_dir}\n", encoding="utf-8")
    profile = replace(load_profile("amsverify"), setup_script=setup)
    xrun = tmp_path / "xrun"
    xrun.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  -version) echo 'TOOL: xrun(64) 23.09-s002'; exit 0 ;;\n"
        "esac\n"
        "case \"$*\" in\n"
        "  *smoke_tb.sv*) echo 'AIVW_RNM_SMOKE checks=5 PASS' ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    xrun.chmod(0o755)

    manifest = run_m1_ai_rnm(
        profile,
        resolve_launch_paths(launch_dir),
        xrun_path=str(xrun),
    )

    assert manifest["status"] == "PASS"
    control = Path(manifest["manifest_pair"]["control_manifest"])
    payload = Path(manifest["manifest_pair"]["payload_manifest"])
    assert control.is_relative_to(launch_dir / ".aivw")
    assert payload.is_relative_to(payload_dir)
    assert not any(control.parent.rglob("xcelium.d"))
    model = resolve_indexed_artifact(control, "ai-generation/comparator_new.rnm.sv")
    assert model.storage_role == "payload"
    assert model.path.is_relative_to(payload_dir)
    assert verify_split_manifests(control, payload)["status"] == "PASS"
    source = _validate_source_run(
        {
            "manifest": str(control),
            "manifest_sha256": manifest["manifest_pair"]["control_manifest_sha256"],
            "run_id": manifest["run_id"],
            "artifact": {"path": model.relative_path, "sha256": model.sha256},
        },
        role="rnm",
    )
    assert source["kind"] == "m1-ai-rnm-generation-check-tb"
    assert source["artifact"]["storage_role"] == "payload"
