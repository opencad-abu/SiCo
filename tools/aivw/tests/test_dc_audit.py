import json
from pathlib import Path

import pytest

from aivworkbench.electrical_experiment import read_experiment, render_deck
from aivworkbench.qualification.dc_audit import audit_dc_qualification, _normalize_source_order


ROOT=Path(__file__).resolve().parents[3]
RUNS=["20260907T072336.041824Z-dc-qualification-4e75b9893f3f","20260907T072733.697225Z-dc-qualification-4cbc6d500868"]


@pytest.mark.parametrize("kind", ["dc", "transient"])
def test_audit_accepts_json_reordering_of_independent_voltage_sources(kind):
    plan = read_experiment(ROOT/"tools/aivw/recipes/ldo/experiment/ldo_aon_characterization_v2.json")
    case = next(c for c in plan["cases"] if c["kind"] == kind)
    case["voltages"] = {pin: case["voltages"][pin] for pin in ("VDD", "VSS", "EN")}
    original = render_deck(plan, case["id"])
    reordered = render_deck(json.loads(json.dumps(plan, sort_keys=True)), case["id"])
    assert original != reordered
    assert _normalize_source_order(original) == _normalize_source_order(reordered)


@pytest.mark.parametrize("before,after", [
    ("dc=5", "dc=4"),
    ("(EN 0)", "(EN VDD)"),
    ("r=10000", "r=20000"),
    ("temp=27", "temp=28"),
    ("VS_EN (EN 0) vsource dc=2.5\n", ""),
    ("VS_EN (EN 0) vsource dc=2.5\n", "VS_VSS (VSS 0) vsource dc=0\n"),
])
def test_audit_source_normalization_preserves_electrical_differences(before, after):
    deck = ("simulator lang=spectre\n"
            "VS_VDD (VDD 0) vsource dc=5\n"
            "VS_VSS (VSS 0) vsource dc=0\n"
            "VS_EN (EN 0) vsource dc=2.5\n"
            "RLOAD (OUT VSS) resistor r=10000\n"
            "simulatorOptions options temp=27\n")
    assert before in deck
    assert _normalize_source_order(deck) != _normalize_source_order(deck.replace(before, after))


def test_audit_does_not_reorder_sources_across_other_statements():
    deck = ("VS_VDD (VDD 0) vsource dc=5\n"
            "simulator lang=spice\n"
            "VS_VSS (VSS 0) vsource dc=0\n")
    with pytest.raises(ValueError, match="not contiguous"):
        _normalize_source_order(deck)


def control(run):
    path=ROOT/".aivw/runs"/run/"manifest.json"
    if not path.is_file():pytest.skip("site-specific private evaluator evidence unavailable")
    return path


@pytest.mark.parametrize("run",RUNS)
def test_real_dc_qualification_audit_exposes_only_aggregates(run):
    result=audit_dc_qualification(control(run))
    assert result["status"]=="PASS"
    assert result["case_count"]==20 and result["repeat_runs"]==2
    assert result["max_relative_error"]<=0.05
    assert result["full_behavior_qualification"]=="NOT_ESTABLISHED"
    assert not {"cases","seed","vectors","stimulus"}.intersection(result)


def test_audit_recomputes_comparison_instead_of_trusting_summary(monkeypatch):
    import aivworkbench.qualification.dc_audit as module
    path=control(RUNS[0])
    real=module.compare_numeric
    def changed(*args,**kwargs):
        result=real(*args,**kwargs)
        result["actual"]+=0.5
        return result
    monkeypatch.setattr(module,"compare_numeric",changed)
    with pytest.raises(ValueError,match="summary differs"):
        audit_dc_qualification(path)


def test_audit_revalidates_both_source_characterization_repeats(monkeypatch):
    import aivworkbench.qualification.dc_audit as module
    path = control(RUNS[0])
    real = module.read_characterization
    calls = []

    def changed(source_path):
        result = real(source_path)
        calls.append(source_path)
        if len(calls) == 2:
            result["repeat_record"]["inputs"]["source_generation"] = "0" * 64
        return result

    monkeypatch.setattr(module, "read_characterization", changed)
    with pytest.raises(ValueError, match="source characterization repeats differ"):
        audit_dc_qualification(path)
    assert len(calls) == 2


def test_audit_rejects_mismatched_generated_stimulus(monkeypatch):
    import aivworkbench.qualification.dc_audit as module
    path=control(RUNS[0])
    real=module.render_dc_testbench
    monkeypatch.setattr(module,"render_dc_testbench",lambda *args:real(*args)+"// changed\n")
    with pytest.raises(ValueError,match="stimulus differs"):
        audit_dc_qualification(path)
