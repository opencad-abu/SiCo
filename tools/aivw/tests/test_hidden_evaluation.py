"""Hidden-holdout evaluation: repeats, golden comparison and verdicts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aivworkbench.qualification import hidden_evaluation as module
from aivworkbench.waveform_metrics import Waveform
from aivworkbench.workspace import sha256_file


ANALYSIS = {"stop_s": 2e-4, "maxstep_s": 2e-8, "reltol": 1e-3, "vabstol_v": 1e-6,
            "iabstol_a": 1e-12, "temperature_c": 27.0}
MEASUREMENT = {"sample_step_s": 1e-6, "settling_relative_band": 0.05,
               "settling_absolute_band_v": 0.01, "minimum_dwell_s": 1e-7}
TIMES = (0.0, 5e-5, 8e-5, 9e-5, 1e-4, 1.1e-4, 1.5e-4, 2e-4)
VOUT = (0.0, 0.0, 0.0, 0.5, 1.0, 1.0, 1.0, 1.0)
CURRENT = (0.0, 0.0, 0.0, 5e-5, 1e-4, 1e-4, 1e-4, 1e-4)


def _case(index):
    return {"kind": "electrical_dynamic_trajectory",
            "stimulus": {"target": {"library": "amsLDO", "cell": "LDO_MASTER",
                                    "term_order": ["VDD", "VOUT", "VSS"]},
                         "roles": {"output": "VOUT"}, "model": {},
                         "voltages": {"VDD": {"pwl": [[0.0, 0.0], [80e-6, 0.0], [82e-6, 5.0],
                                                        [400e-6, 5.0], [800e-6, 5.0]]},
                                      "VSS": {"dc_v": 0.0}},
                         "load_ohm": 10000.0, "load_conductance_pwl_s": None},
            "measurement": {"analysis": dict(ANALYSIS),
                            "definition": dict(MEASUREMENT),
                            "events": [{"signal": "VDD", "start_s": 80e-6, "end_s": 82e-6,
                                        "before_window_s": [5e-5, 8e-5],
                                        "after_window_s": [1.5e-4, 1.8e-4]}]}}


def _policy(path):
    path.write_text(json.dumps({
        "schema_version": 1, "kind": "physical-time-correlation", "status": "approved",
        "decision_source": "test_decision",
        "correlation": {
            "voltage": {"unit": "V", "relative": 0.05, "absolute": 1e-3, "normalization": "reference_magnitude"},
            "current": {"unit": "A", "relative": 0.05, "absolute": 1e-6, "normalization": "reference_magnitude"},
            "time": {"unit": "s", "relative": 0.05, "absolute": 1e-7, "normalization": "reference_magnitude"}},
        "product": {"current_limit_a": None, "startup_limit_s": None, "settling_limit_s": None}}))
    return path


def _fixture(tmp_path, *, cases=2):
    private = tmp_path/"private"
    private.mkdir(mode=0o700)
    record = {"schema_version": 1, "kind": "reserved_whole_case_holdout", "domain_digest": "b"*64,
              "policy_digest": "a"*64, "seed": 1, "nonce": "nonce",
              "cases": [_case(index) for index in range(cases)],
              "public_case_digests": [], "state": "reserved_before_fit"}
    (private/"reservation.json").write_text(json.dumps(record))
    candidate = tmp_path/"candidate.sv"
    candidate.write_text("module ldo_candidate; endmodule\n")
    model = tmp_path/"model.json"
    model.write_text(json.dumps({"source_generation": "source"}))
    skeleton = tmp_path/"skeleton.json"
    skeleton.write_text(json.dumps({"schema_version": 2, "analysis": dict(ANALYSIS),
                                    "roles": {"output": "VOUT", "supply": "VDD", "controls": []},
                                    "target": {"term_order": ["VDD", "VOUT", "VSS"]},
                                    "measurement": dict(MEASUREMENT)}))
    environment = tmp_path/"environment.json"
    environment.write_text("{}")
    circuit = tmp_path/"circuit.scs"
    circuit.write_text("// circuit\n")
    config = {"private_root": str(private), "spool": str(tmp_path/"spool"),
              "candidate_source": str(candidate), "model": str(model), "skeleton_plan": str(skeleton),
              "environment": str(environment), "policy": str(_policy(tmp_path/"policy.json")),
              "circuit": str(circuit),
              "xrun": "/bin/true", "timeout": 5, "run_id": "test-run",
              "candidate_sha256": sha256_file(candidate), "contract_digest": "d"*64,
              "commitment_sha256": sha256_file(private/"reservation.json"),
              "policy_digest": "a"*64, "domain_digest": "b"*64, "case_count": cases,
              "reference_run_id": "test-reference"}
    return private, config


def _golden(plan, *, scale=1.0):
    return {case["id"]: Waveform(TIMES, {"VOUT": "V", "VPROBE:p": "A"},
                                 {"VOUT": tuple(scale*v for v in VOUT),
                                  "VPROBE:p": tuple(scale*c for c in CURRENT)})
            for case in plan["cases"]}


def _runner(*, vary_second_attempt=False, scale=1.0):
    def run(root, source_text, model, plan, environment, tools, timeout, renderer):
        records = []
        for case in plan["cases"]:
            local = root/case["id"]
            local.mkdir()
            factor = scale*(1.0 if not (vary_second_attempt and root.name == "attempt_2") else 1.2)
            rows = ["time[s],VOUT[V],VPROBE:p[A]"] + [
                "%.17g,%.17g,%.17g" % (t, factor*v, factor*c) for t, v, c in zip(TIMES, VOUT, CURRENT)]
            csv = local/"waveforms.csv"
            csv.write_text("\n".join(rows) + "\n")
            records.append({"id": case["id"], "csv": str(csv)})
        return root/"candidate.sv", records
    return run


def test_hidden_holdout_passes_with_identical_repeats_and_golden(tmp_path):
    private, config = _fixture(tmp_path)
    summary = module.run(config, runner=_runner(), golden_runner=_golden)
    assert summary["status"] == "HIDDEN_HOLDOUT_PASS", summary["accuracy"]
    assert summary["repeat"]["status"] == "PASS_REPEAT"
    assert summary["accuracy"]["correlation_status"] == ["PASS", "PASS"]
    assert summary["accuracy"]["failed_cases"] == []
    assert (private/"repeat-journal-test-run"/"attempt_2"/"result.json").is_file()
    encoded = json.dumps(summary)
    assert "voltages" not in encoded and "pwl" not in encoded
    assert summary["holdout_values_included"] is False


def test_different_repeats_fail_the_holdout(tmp_path):
    _, config = _fixture(tmp_path)
    summary = module.run(config, runner=_runner(vary_second_attempt=True), golden_runner=_golden)
    assert summary["status"] == "HIDDEN_HOLDOUT_FAIL"
    assert summary["repeat"]["status"] == "FAIL_REPEAT"


def test_candidate_golden_mismatch_fails_the_holdout(tmp_path):
    _, config = _fixture(tmp_path)
    summary = module.run(config, runner=_runner(scale=1.5), golden_runner=_golden)
    assert summary["status"] == "HIDDEN_HOLDOUT_FAIL"
    assert summary["repeat"]["status"] == "PASS_REPEAT"
    assert summary["accuracy"]["diagnostic_status"] == ["FAIL_CORRELATION", "FAIL_CORRELATION"]


def test_commitment_mismatch_blocks_before_any_execution(tmp_path):
    private, config = _fixture(tmp_path)
    config["commitment_sha256"] = "0"*64
    with pytest.raises(ValueError, match="holdout commitment mismatch"):
        module.run(config, runner=_runner(), golden_runner=_golden)
    assert not (private/"repeat-journal-test-run").exists()


def test_foreign_candidate_freeze_is_rejected(tmp_path):
    private, config = _fixture(tmp_path)
    (private/"candidate-freeze.json").write_text(json.dumps(
        {"candidate_digest": "e"*64, "contract_digest": "d"*64, "holdout_commitment": config["commitment_sha256"]}))
    with pytest.raises(ValueError, match="another candidate is already frozen"):
        module.run(config, runner=_runner(), golden_runner=_golden)
