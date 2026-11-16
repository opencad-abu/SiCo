from copy import deepcopy
import json
from pathlib import Path

import pytest

from aivworkbench.gate_feedback import GateFeedback
from aivworkbench.trajectory_evaluator import TrajectoryGateEvaluator, evaluate_candidate_sequence
from aivworkbench.waveform_metrics import Waveform
from aivworkbench.workspace import stable_digest


RECIPES = Path(__file__).resolve().parents[1]/"recipes/ldo"


def inputs():
    plan = json.loads((RECIPES/"experiment/ldo_master_dynamic_v1.json").read_text())
    evidence = {"dataset": "public_calibration", "source_generation": "source",
                "experiment_digest": stable_digest(plan), "target": plan["target"]}
    model = {"module": "LDO_MASTER", "ports": plan["target"]["term_order"],
             "source_generation": "source", "experiment_digest": stable_digest(plan)}
    signals = {"VOUT": "V", "VPROBE:p": "A"}
    wave = Waveform((0., .0004), signals, {"VOUT": (1., 1.), "VPROBE:p": (.001, .001)})
    policy = json.loads((RECIPES/"dynamic_acceptance_proposal_v1.json").read_text())
    return dict(plan=plan, evidence=evidence, golden={c["id"]: wave for c in plan["cases"]},
                model=model, policy=policy, environment={}, tools={}, renderer=lambda *args: "")


def candidate():
    return {"module": "LDO_MASTER", "source_generation": "source", "source":
            "module LDO_MASTER(input real VDD, output var real VOUT, input real VSS); endmodule"}


class StubEvaluator:
    def __init__(self):
        self.evidence = {"source_generation": "source", "experiment_digest": "experiment"}
        self.model = {"module": "LDO_MASTER"}
        self.calls = []

    def __call__(self, candidate, context):
        self.calls.append({"module": candidate["module"], **dict(context)})
        return GateFeedback("PASS", "physical_trajectory_correlation", {}, {"index": len(self.calls)})


def test_provider_free_sequence_records_revision_chain():
    evaluator = StubEvaluator()
    first, second = candidate(), candidate()
    second["source"] = second["source"] + "// revision\n"
    report = evaluate_candidate_sequence(evaluator, [first, second])
    revisions = report["revisions"]
    assert report["kind"] == "provider-free-trajectory-revision"
    assert [r["action_kind"] for r in revisions] == ["PROPOSE_MODEL", "REQUEST_REVISION"]
    assert revisions[0]["parent_sha256"] is None and revisions[0]["feedback_sha256"] is None
    assert revisions[1]["parent_sha256"] == revisions[0]["candidate_sha256"]
    assert revisions[1]["feedback_sha256"] == revisions[0]["gate_feedback_sha256"]
    assert revisions[1]["gate_feedback_sha256"] == revisions[1]["gate_feedback"]["sha256"]
    assert revisions[0]["candidate_sha256"] == stable_digest(first)
    assert evaluator.calls == [
        {"module": "LDO_MASTER", "source_generation": "source",
         "dataset": "public_calibration", "experiment_digest": "experiment"},
    ] * 2


@pytest.mark.parametrize("change", ["plan", "source", "cases", "ports", "target", "dataset"])
def test_reject_inconsistent_evaluation_inputs_before_allocation(tmp_path, change):
    value = inputs()
    if change == "plan": value["model"]["experiment_digest"] = "wrong"
    if change == "source": value["model"]["source_generation"] = "wrong"
    if change == "cases": value["golden"].pop("startup")
    if change == "ports": value["model"]["ports"] = ["VDD", "VOUT"]
    if change == "target": value["evidence"]["target"] = {}
    if change == "dataset": value["evidence"]["dataset"] = "hidden_holdout"
    with pytest.raises(ValueError):
        TrajectoryGateEvaluator(tmp_path/"evaluation", **value)
    assert not (tmp_path/"evaluation").exists()


def test_input_ownership_and_mutation_detection(tmp_path):
    value = inputs()
    evaluator = TrajectoryGateEvaluator(tmp_path/"evaluation", **value)
    value["policy"]["status"] = "approved"
    assert evaluator.policy["status"] == "proposed"
    evaluator.plan["analysis"]["stop_s"] *= 2
    assert evaluator(candidate(), {"source_generation": "source"}).code == "trajectory_evaluation_inputs_changed"


def test_missing_tool_persists_failure_feedback(tmp_path):
    evaluator = TrajectoryGateEvaluator(tmp_path/"evaluation", **inputs())
    feedback = evaluator(candidate(), {"source_generation": "source"})
    assert feedback.code == "trajectory_execution_failure"
    assert feedback.status == "FAIL"
    assert (evaluator.root/"revision_000/feedback.json").is_file()
    assert (evaluator.root/"revision_000/candidate-action.json").is_file()


@pytest.mark.parametrize("old,new", [("input real VDD", "inout real VDD"),
                                     ("VSS", "EN"), ("output var real VOUT", "input real VOUT")])
def test_interface_changes_rejected_before_execution(tmp_path, old, new):
    evaluator = TrajectoryGateEvaluator(tmp_path/"evaluation", **inputs())
    value = candidate()
    value["source"] = value["source"].replace(old, new)
    feedback = evaluator(value, {"source_generation": "source"})
    assert feedback.code == "trajectory_candidate_interface"
    assert not list(evaluator.root.glob("revision_*"))


def test_system_assessment_does_not_promote_public_pass():
    from aivworkbench.trajectory_readiness import assess_sampled_candidate
    model = {**inputs()["model"], "model_class": "electrical.sampled_state",
             "load_boundary": "environment", "current_boundary": "diagnostic", "unsupported": []}
    report = {"source_generation": "source", "experiment_digest": model["experiment_digest"],
              "candidate_sha256": "candidate", "status": "PASS", "diagnostic_status": "PASS"}
    revisions = {"source_generation": "source", "revisions": [{"gate_feedback": {"evidence": {
        "candidate_source_sha256": "candidate", "report_digest": stable_digest(report)}}}]}
    result = assess_sampled_candidate(model, report, revisions)
    assert result["status"] == "BLOCKED_SYSTEM_INTEGRATION"
    assert result["system_simulation"] == "NOT_RUN"
    forged = deepcopy(report)
    forged["candidate_sha256"] = "other"
    with pytest.raises(ValueError):
        assess_sampled_candidate(model, forged, revisions)
