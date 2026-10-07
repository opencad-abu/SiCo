"""Execute an identified model on recipe stimuli with bounded Xcelium argv."""

from __future__ import annotations

import json
import re
import time

from ..executor import ExecutorResult
from ..process import run_process_group
from ..trajectory_evidence import indexed_file, public_trajectories
from ..waveform_metrics import read_waveform
from ..workspace import sha256_file, stable_digest, write_json_once
from .ldo_structure_common import _CHILD_ENV


def model_inputs(context):
    producers = [d for d in context.dependencies.values() if "dynamic_model" in d.outputs]
    if len(producers) != 1 or producers[0].status != "PASS":
        raise ValueError("one successful model producer required")
    d = producers[0]
    path = indexed_file(context, d, d.outputs["dynamic_model"], d.outputs["dynamic_model_sha256"])
    source = indexed_file(context, d, d.outputs["model_source"], d.outputs["model_source_sha256"])
    model = json.loads(path.read_text())
    identity = dict(model)
    digest = identity.pop("model_digest")
    plugin = context.metadata["registry"].require_model_class(context.recipe.model_class)
    if model["model_class"] != plugin.name or stable_digest(identity) != digest or plugin.handler.render_source(model) != source.read_text():
        raise ValueError("identified candidate bytes differ")
    return model, source


def execute_candidate(root, source_text, model, plan, environment, tools, timeout, render_testbench):
    """Controller-owned execution shared by DAG and Agent feedback adapters."""
    if not tools.get("xrun") or timeout <= 0:
        raise ValueError("qualified Xcelium and a positive deadline are required")
    root.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic()+timeout
    source = root/"candidate.sv"
    with source.open("x") as stream:
        stream.write(source_text)
    env = {k: v for k, v in environment.items() if k in _CHILD_ENV}
    records = []
    for case in plan["cases"]:
        local = root/case["id"]
        local.mkdir()
        tb = local/"tb.sv"
        with tb.open("x") as stream:
            stream.write(render_testbench(model, plan, case))
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            raise ValueError("trajectory Xcelium deadline exhausted")
        result = run_process_group((tools["xrun"], "-64bit", "-sv", "-top", "trajectory_tb", "-xmlibdirname", str(local/"worklib"),
                                    "-l", str(local/"xrun.log"), str(source), str(tb)),
                                   cwd=local, environment=env, log_file=local/"console.log", timeout=remaining)
        write_json_once(local/"process.json", result.to_dict())
        if result.returncode != 0 or result.timed_out:
            raise ValueError("Xcelium trajectory failed; inspect isolated logs")
        log = (local/"xrun.log").read_text(errors="replace")
        if re.search(r"\*[EF],", log) or "AIVW_TRAJECTORY_COMPLETE" not in log:
            raise ValueError("Xcelium trajectory completion evidence missing")
        csv = local/"waveforms.csv"
        read_waveform(csv, {plan["roles"]["output"]: "V", "VPROBE:p": "A"}, plan["analysis"]["stop_s"])
        records.append({"id": case["id"], "case_digest": stable_digest(case), "csv": csv,
                        "sha256": sha256_file(csv)})
    return source, records


def run_trajectory_xcelium(context):
    try:
        plan, evidence, _ = public_trajectories(context)
        model, source = model_inputs(context)
        if model["experiment_digest"] != stable_digest(plan) or model["source_generation"] != evidence["source_generation"]:
            raise ValueError("candidate source/experiment binding differs")
        plugin = context.metadata["registry"].require_model_class(context.recipe.model_class)
        candidate, records = execute_candidate(context.gate_root, source.read_text(), model, plan,
                                               context.environment, context.tools, context.timeout, plugin.handler.render_testbench)
        report = {"schema_version": 1, "kind": "xcelium-trajectories", "status": "EXECUTED",
                  "source_generation": evidence["source_generation"], "experiment_digest": stable_digest(plan),
                  "candidate_sha256": sha256_file(candidate), "cases": [
                      {**r, "csv": str(r["csv"].relative_to(context.run.payload_root))} for r in records],
                  "qualification": "NOT_ESTABLISHED"}
        path = context.gate_root/"trajectory-evidence.json"
        write_json_once(path, report)
        return ExecutorResult("PASS", {"case_count": len(records), "scope": "xcelium_execution_only"},
            {"rnm_trajectories": str(path.relative_to(context.run.payload_root)), "rnm_trajectories_sha256": sha256_file(path),
             "source_generation": evidence["source_generation"]}, tuple(sorted(p for p in context.gate_root.rglob("*") if p.is_file())))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        files = tuple(sorted(p for p in context.gate_root.rglob("*") if p.is_file()))
        return ExecutorResult("FAIL_EXECUTION", {"code": "trajectory_execution_failed", "reason": str(exc)}, artifacts=files)
