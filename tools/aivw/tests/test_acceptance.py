from copy import deepcopy
import math

import pytest

from aivworkbench.acceptance import compare_numeric, compare_repeats, compare_waveform


def rule(relative=0.03, absolute=None, unit="V"):
    return {"unit": unit, "relative": relative, "absolute": absolute, "normalization": "reference_magnitude"}


@pytest.mark.parametrize("actual,reference,status", [(2.575,2.5,"PASS"),(2.425,2.5,"PASS"),(2.57501,2.5,"FAIL_TOLERANCE"),(1.2125,1.25,"PASS"),(1.2875,1.25,"PASS"),(1.2,1.25,"FAIL_TOLERANCE")])
def test_inclusive_product_boundaries(actual, reference, status):
    assert compare_numeric(actual,reference,unit="V",tolerance=rule())["status"] == status


def test_product_failure_does_not_change_reference_agreement():
    assert compare_numeric(2.6,2.5,unit="V",tolerance=rule())["status"] == "FAIL_TOLERANCE"
    assert compare_numeric(2.6,2.5,unit="V",tolerance=rule(0.05))["status"] == "PASS"


def test_zero_reference_blocks_without_explicit_absolute_scale():
    assert compare_numeric(0.,0.,unit="V",tolerance=rule(0.05))["status"] == "BLOCKED_CONTRACT"
    assert compare_numeric(0.001,0.,unit="V",tolerance=rule(0.05,0.001))["status"] == "PASS"
    assert compare_numeric(0.002,0.,unit="V",tolerance=rule(0.05,0.001))["status"] == "FAIL_TOLERANCE"


@pytest.mark.parametrize("value", [None,True,float('nan'),float('inf'),"1.0"])
def test_nonfinite_or_missing_measurement_rejected(value):
    with pytest.raises(ValueError):
        compare_numeric(value,1.,unit="V",tolerance=rule())


def test_units_and_missing_tolerance_rejected():
    with pytest.raises(ValueError): compare_numeric(1.,1.,unit="mV",tolerance=rule())
    with pytest.raises(ValueError): compare_numeric(1.,1.,unit="V",tolerance=rule(None))
    with pytest.raises(ValueError): compare_numeric(1.,1.,unit="V",tolerance=rule(-0.1))


def test_waveform_peak_cannot_be_hidden_by_average():
    result=compare_waveform([1.,1.2,1.],[1.,1.,1.],times=[0.,1.,2.],unit="V",tolerance=rule(0.05))
    assert result["status"] == "FAIL_TOLERANCE"
    assert result["failed_samples"] == 1
    with pytest.raises(ValueError): compare_waveform([1.,1.],[1.,1.],times=[1.,0.],unit="V",tolerance=rule())


def runs():
    first={"run_id":"one","manifest_status":"PASS","execution_status":"PASS","inputs":{"source":"a","candidate":"b","seed":1,"experiment":"c"},"measurements":{"v":1.}}
    second=deepcopy(first);second["run_id"]="two"
    return [first,second]


def test_repeat_requires_same_experiment_and_measurements():
    data=runs(); assert compare_repeats(data)["status"] == "PASS"
    data[1]["inputs"]["seed"]=2; assert compare_repeats(data)["status"] == "FAIL_REPEAT"
    data=runs();data[1]["measurements"]["v"]=1.001
    assert compare_repeats(data)["status"] == "FAIL_REPEAT"


def test_repeat_rejects_reused_run_or_missing_evidence():
    data=runs();data[1]["run_id"]="one"
    with pytest.raises(ValueError):compare_repeats(data)
    data=runs();data[1]["manifest_status"]="FAIL"
    with pytest.raises(ValueError):compare_repeats(data)
    assert compare_repeats(runs()[:1])["status"] == "BLOCKED_EVIDENCE"
