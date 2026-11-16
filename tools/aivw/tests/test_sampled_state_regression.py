"""Exact regression against a pre-existing immutable identified candidate."""

import json
from pathlib import Path

import pytest

from aivworkbench.manifest import verify_split_manifests
from aivworkbench.plugins.sampled_state import identify
from aivworkbench.waveform_metrics import read_waveform


def test_shared_rollout_preserves_frozen_public_model():
    control = Path(__file__).resolve().parents[3]/".aivw/runs/20260907T112113.634135Z-recipe-run-cbe5897517ab/manifest.json"
    if not control.is_file():
        pytest.skip("site-specific immutable dynamic candidate unavailable")
    root = Path(json.loads(control.read_text())["details"]["payload_root"])
    verify_split_manifests(control, root/"payload-manifest.json")
    gates = root/"checks/gates"
    plan = json.loads((gates/"public_data/experiment.json").read_text())
    evidence = json.loads((gates/"public_data/characterization-evidence.json").read_text())
    expected = json.loads((gates/"model/model.json").read_text())
    signals = {**{pin: "V" for pin in plan["target"]["term_order"]}, "VPROBE:p": "A"}
    waves = {c["id"]: read_waveform(gates/"public_data/cases"/c["id"]/"waveforms.csv",
                                   signals, plan["analysis"]["stop_s"]) for c in plan["cases"]}
    assert identify((plan, evidence, waves, expected["identification_options"])) == expected
