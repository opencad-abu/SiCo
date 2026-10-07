"""Bind candidate revisions to real, public Xcelium/Spectre correlation.

The caller injects a qualified provider into the existing AgentRuntime. This
module supplies deterministic feedback; it never creates another reasoning
loop, approves a policy, or grants access to evaluator-private holdout data.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from copy import deepcopy
from dataclasses import asdict

from ..connectivity import parse_verilog
from ..electrical_experiment import validate_experiment
from ..executors.trajectory_correlation import correlation_report, feedback_from_report
from ..executors.trajectory_xcelium import execute_candidate
from ..waveform_metrics import read_waveform, validate_waveform_policy
from ..workspace import sha256_file, stable_digest, write_json_once
from .qualification import GateFeedback, default_candidate_gate, run_candidate_revision_workflow
from .runtime import RuntimeConfig


class TrajectoryGateEvaluator:
    def __init__(self, root, *, plan, evidence, golden, model, policy, environment, tools, renderer, timeout=240.):
        self.root = Path(root)
        if self.root.exists() or self.root.is_symlink():
            raise ValueError("revision evaluation requires a fresh payload directory")
        validate_experiment(plan)
        if (evidence["dataset"] != "public_calibration" or
                model["source_generation"] != evidence["source_generation"] or
                model["experiment_digest"] != stable_digest(plan) or
                evidence["experiment_digest"] != stable_digest(plan) or
                evidence["target"] != plan["target"] or
                model["ports"] != plan["target"]["term_order"] or
                set(golden) != {c["id"] for c in plan["cases"]}):
            raise ValueError("Agent gate requires bound public characterization")
        self.plan, self.evidence, self.golden, self.model = deepcopy((plan, evidence, golden, model))
        self.policy = deepcopy(validate_waveform_policy(policy))
        self.environment, self.tools = dict(environment), dict(tools)
        self.renderer, self.timeout = renderer, timeout
        self.reports = []
        self.root.mkdir(parents=True)
        self.input_digest = self._input_digest()
        write_json_once(self.root/"evaluation-inputs.json", {
            "plan": self.plan, "model": self.model, "policy": self.policy,
            "source_generation": self.evidence["source_generation"],
            "input_digest": self.input_digest, "scope": "public_calibration_only"})

    def _input_digest(self):
        return stable_digest({"plan": self.plan, "evidence": self.evidence,
                              "model": self.model, "policy": self.policy,
                              "golden": {k: asdict(v) for k, v in self.golden.items()}})

    def __call__(self, candidate, context):
        if self._input_digest() != self.input_digest:
            return GateFeedback("BLOCKED", "trajectory_evaluation_inputs_changed", {}, {})
        structural = default_candidate_gate(candidate, context)
        if structural.status != "PASS":
            return structural
        if candidate.get("module") != self.model["module"] or context["source_generation"] != self.evidence["source_generation"]:
            return GateFeedback("BLOCKED", "trajectory_candidate_identity", {}, {})
        # This adapter evaluates the registered task ABI. It does not accept
        # candidate-owned TBs, file I/O, simulator commands or verdicts.
        source = candidate["source"]
        import re
        stripped = re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.S)
        system_tasks = re.findall(r"\$[A-Za-z_][A-Za-z0-9_]*", stripped)
        if any(task != "$fatal" for task in system_tasks) or "`" in stripped:
            return GateFeedback("BLOCKED", "candidate_executable_surface", {}, {})
        if re.search(r"\b(initial|always|always_comb|always_ff|final|import|export|bind|force|release)\b", stripped) or "#" in stripped:
            return GateFeedback("BLOCKED", "candidate_scheduling_owned_by_testbench", {}, {})
        if len(re.findall(r"\bmodule\b", stripped)) != 1 or re.search(r"\btrajectory_tb\b|\$root", stripped):
            return GateFeedback("BLOCKED", "candidate_module_surface", {}, {})
        try:
            # The connectivity parser owns module ports, not task/function
            # bodies containing their own input declarations.
            header = re.match(r"\s*module\s+\w+\s*\([^;]*\)\s*;", stripped)
            if header is None:
                raise ValueError("candidate requires an explicit ANSI interface")
            modules = parse_verilog(re.sub(r"\bvar\s+(?=real\b)", "", header.group())+"\nendmodule")
            expected = [(p, "output" if p == self.plan["roles"]["output"] else "input")
                        for p in self.plan["target"]["term_order"]]
            if (len(modules) != 1 or modules[0].name != self.model["module"] or
                    [(p.name, p.direction) for p in modules[0].ports] != expected):
                raise ValueError("candidate ports differ from source interface")
        except ValueError as exc:
            return GateFeedback("BLOCKED", "trajectory_candidate_interface", {"reason": str(exc)}, {})
        local = self.root/("revision_%03d" % len(self.reports))
        local.mkdir()
        write_json_once(local/"candidate-action.json", candidate)
        try:
            compiled, records = execute_candidate(local, source, self.model, self.plan, self.environment, self.tools,
                                                   self.timeout, self.renderer)
            signals = {self.plan["roles"]["output"]: "V", "VPROBE:p": "A"}
            waves = {r["id"]: read_waveform(r["csv"], signals, self.plan["analysis"]["stop_s"]) for r in records}
            report = correlation_report(self.plan, self.evidence, self.golden, waves, self.policy, sha256_file(compiled))
            write_json_once(local/"correlation.json", report)
            feedback = feedback_from_report(report)
            self.reports.append(report)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            feedback = GateFeedback("FAIL", "trajectory_execution_failure", {"reason": str(exc)}, {
                "candidate_source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                "source_generation": self.evidence["source_generation"]})
            self.reports.append({"status": "FAIL_EXECUTION"})
        write_json_once(local/"feedback.json", feedback.to_dict())
        return feedback


def run_trajectory_revision(provider, evaluator, *, run_id, initial_context=None):
    """Use the existing revision protocol with a real behavior evaluator."""
    config = RuntimeConfig(max_turns=8, max_tool_calls=8, turn_timeout_seconds=evaluator.timeout+10,
                           total_timeout_seconds=4*evaluator.timeout+60,
                           events_path=evaluator.root/"events.jsonl", checkpoint_path=evaluator.root/"checkpoint.json",
                           required_action_budget_fields=frozenset({"tokens", "simulation_cases", "simulation_seconds"}),
                           max_action_simulation_cases=len(evaluator.plan["cases"]),
                           max_action_simulation_seconds=evaluator.timeout)
    result = run_candidate_revision_workflow(provider, source_generation=evaluator.evidence["source_generation"],
        run_id=run_id, gate_evaluator=evaluator, config=config,
        initial_context={**(initial_context or {}), "module": evaluator.model["module"], "dataset": "public_calibration",
                         "experiment_digest": evaluator.evidence["experiment_digest"]})
    write_json_once(evaluator.root/"revision-workflow.json", result.report.to_dict())
    return result
