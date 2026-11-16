from copy import deepcopy
import json
from pathlib import Path

import pytest

from aivworkbench.trajectory_readiness import read_adapter_certificate


ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("run_id", ["20260908T002238.344535Z-eenet-adapter-e1db36a9ae76",
                                   "20260908T002238.707861Z-eenet-adapter-c5d97ed9d07b"])
def test_network_certificate_binds_real_candidate_and_negative_checks(run_id):
    control = ROOT/".aivw/runs"/run_id/"manifest.json"
    if not control.is_file(): pytest.skip("site-specific EEnet certificate unavailable")
    value = json.loads(control.read_text())["details"]
    report = read_adapter_certificate(control, value["candidate_sha256"], value["source_generation"])
    assert report["status"] == "PASS_ADAPTER_MECHANICS"
    with pytest.raises(ValueError, match="another candidate"):
        read_adapter_certificate(control, "0"*64, value["source_generation"])
    with pytest.raises(ValueError, match="another candidate"):
        read_adapter_certificate(control, value["candidate_sha256"], "0"*64)
