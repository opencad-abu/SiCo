from pathlib import Path

import pytest

from aivworkbench.acceptance import compare_repeats
from aivworkbench.characterization_evidence import read_characterization
from aivworkbench.plugins.measured_dc import calibrate_surface, predict_surface


ROOT=Path(__file__).resolve().parents[3]
RUNS={
    "LDO_MASTER": ["20260907T061638.842379Z-recipe-run-bf717cd8514a", "20260907T062434.312865Z-recipe-run-bf717cd8514a"],
    "LDO_AON": ["20260907T061905.444555Z-recipe-run-c99a7c9a3a52", "20260907T063414.726367Z-recipe-run-c99a7c9a3a52"],
}


def read(run):
    path=ROOT/".aivw"/"runs"/run/"manifest.json"
    if not path.is_file():pytest.skip("site-specific real EDA evidence unavailable")
    return read_characterization(path)


@pytest.mark.parametrize("cell",list(RUNS))
def test_real_characterization_repeats_and_measured_candidate(cell):
    records=[read(run) for run in RUNS[cell]]
    assert compare_repeats([r["repeat_record"] for r in records])["status"]=="PASS"
    record=records[0];plan=record["experiment"];evidence=record["evidence"]
    model=calibrate_surface(plan,evidence)
    assert model["scope"]=="static_dc_fixed_controls"
    assert model["ai_generation"]=="not_performed_for_registered_calibration"
    assert evidence["product_status"]=="PASS"
    assert evidence["qualification_status"]=="NOT_ESTABLISHED"
    for case,obs in zip(plan["cases"],evidence["cases"]):
        if case["purpose"] in {"nominal_load","supply_probe"}:
            result=predict_surface(model,supply_v=case["voltages"]["VDD"]["dc_v"],load_ohm=case["load_ohm"])
            assert result==pytest.approx(obs["measurements"]["samples"][-1]["values"]["VOUT"],abs=1e-12)


def test_rebuilt_measurement_mismatch_is_rejected(monkeypatch):
    import aivworkbench.characterization_evidence as boundary
    path=ROOT/".aivw/runs"/RUNS["LDO_MASTER"][0]/"manifest.json"
    if not path.is_file():pytest.skip("site-specific real EDA evidence unavailable")
    real=boundary.measure_csv
    def changed(*args,**kwargs):
        value=real(*args,**kwargs)
        value["samples"][-1]["values"]["VOUT"]+=.1
        return value
    monkeypatch.setattr(boundary,"measure_csv",changed)
    with pytest.raises(ValueError,match="summary differs"):
        read_characterization(path)
