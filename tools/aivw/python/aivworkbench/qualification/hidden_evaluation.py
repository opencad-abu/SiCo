"""Hidden-holdout evaluation: two frozen candidate repeats and the Spectre
golden comparison on the reserved whole cases.

Runs inside the qualified private boundary.  The reservation is opened here
(never by the public process), the evaluator freezes the candidate bytes in
the private root, the two-attempt journal enforces exactly two independent
executions of the same frozen inputs, and the golden is generated with the
authenticated reference circuit/models on the same private stimuli.  All
hidden stimuli, decks, waveforms and logs stay under the private root; only
aggregate digests, counts and the verdict leave the boundary.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import sys

from ..electrical_experiment import ocean_export, render_deck
from ..executors.ldo_structure_common import _CHILD_ENV
from ..executors.trajectory_correlation import correlation_report
from ..executors.trajectory_xcelium import execute_candidate
from ..experiment_partition import case_digest, freeze_candidate
from ..process import run_process_group
from ..spectre_data import validate_psf_units
from ..waveform_metrics import read_waveform, validate_waveform_policy
from ..workspace import sha256_file, stable_digest, write_json_once
from .frozen_repeats import begin_attempt, compare_attempts, finish_attempt, freeze_inputs


RESERVATION = "reservation.json"
_PSF_DATASET = re.compile(r'"analysisInst"\s*\(\s*"tran"\s*"([A-Za-z0-9_.-]+)"\s*"PSF"')


def _load_reservation(private_root, config):
    path = Path(private_root)/RESERVATION
    raw = path.read_bytes()
    if sha256_file(path) != config["commitment_sha256"]:
        raise ValueError("holdout commitment mismatch")
    record = json.loads(raw)
    if (record.get("policy_digest") != config["policy_digest"] or
            record.get("domain_digest") != config["domain_digest"]):
        raise ValueError("reservation policy or domain differs")
    cases = record.get("cases", [])
    if len(cases) != config["case_count"] or not cases:
        raise ValueError("reservation case count differs")
    public = set(record.get("public_case_digests", []))
    if any(case_digest(case) in public for case in cases):
        raise ValueError("hidden cases overlap the public experiment")
    return record, cases


def _ensure_candidate_freeze(private_root, config):
    path = Path(private_root)/"candidate-freeze.json"
    if path.exists():
        record = json.loads(path.read_text())
        if record.get("candidate_digest") != config["candidate_sha256"] or \
                record.get("holdout_commitment") != config["commitment_sha256"]:
            raise ValueError("another candidate is already frozen for this holdout")
        return record
    return freeze_candidate(Path(private_root),
        {"commitment_sha256": config["commitment_sha256"], "policy_digest": config["policy_digest"],
         "domain_digest": config["domain_digest"], "case_count": config["case_count"]},
        candidate_digest=config["candidate_sha256"], contract_digest=config["contract_digest"])


def _hidden_cases(skeleton, cases):
    """Map reserved neutral cases onto the schema-2 skeleton, with event ids."""

    planned = []
    for index, case in enumerate(cases):
        stimulus, measurement = case["stimulus"], case["measurement"]
        if measurement["analysis"] != skeleton["analysis"]:
            raise ValueError("hidden case analysis differs from the skeleton plan")
        events = []
        for event_index, event in enumerate(measurement["events"]):
            events.append({"id": "holdout_%d_%d" % (index, event_index),
                           "signal": event["signal"], "start_s": event["start_s"],
                           "end_s": event["end_s"],
                           "before_window_s": event["before_window_s"],
                           "after_window_s": event["after_window_s"]})
        planned.append({"id": "reserved_%d" % index, "kind": "transient",
                        "purpose": "reserved_holdout",
                        "voltages": stimulus["voltages"], "load_ohm": stimulus["load_ohm"],
                        "load_conductance_pwl_s": stimulus["load_conductance_pwl_s"],
                        "events": events, "product_check": False})
    return planned


def _trajectory_plan(skeleton, planned):
    return {**skeleton, "cases": [{"id": case["id"], "kind": "transient",
                                   "voltages": case["voltages"], "load_ohm": case["load_ohm"],
                                   "load_conductance_pwl_s": case["load_conductance_pwl_s"],
                                   "events": case["events"]} for case in planned]}


def _phase(config, work, environment, tool, arguments, log):
    result = run_process_group((tool, *map(str, arguments)), cwd=work, environment=environment,
                               log_file=log, timeout=float(config["timeout"]))
    if result.timed_out:
        raise ValueError("%s timed out in the hidden evaluation" % Path(tool).name)
    if result.returncode != 0:
        raise ValueError("%s exited with %d in the hidden evaluation" % (Path(tool).name, result.returncode))
    return result


def _run_golden(config, private_root, source_plan):
    """Run the authenticated reference circuit/models on the hidden stimuli."""

    work = private_root/("spectre-%s" % config["run_id"])
    work.mkdir(parents=True)
    shutil.copyfile(config["circuit"], work/"circuit.scs")
    shutil.copytree(config["models"], work/"models")
    cdslib = Path(config["cdslib"])
    environment = {key: value for key, value in json.loads(Path(config["environment"]).read_text()).items()
                   if key in _CHILD_ENV}
    environment.update({"CDS_LIB": str(cdslib), "CDS_CDSLIB": str(cdslib), "PWD": str(work)})
    # Cadence's ocean wrapper starts Xvfb, which must not load the NVIDIA EGL
    # stack inside the namespace (no device nodes); force software GL.
    mesa = Path("/usr/share/glvnd/egl_vendor.d/50_mesa.json")
    if mesa.is_file():
        environment.update({"LIBGL_ALWAYS_SOFTWARE": "1", "__EGL_VENDOR_LIBRARY_FILENAMES": str(mesa)})
    signals = {pin: "V" for pin in source_plan["target"]["term_order"]}
    signals["VPROBE:p"] = "A"
    stop = source_plan["analysis"]["stop_s"]
    waves = {}
    for case in source_plan["cases"]:
        case_root = work/"cases"/case["id"]
        case_root.mkdir(parents=True)
        deck = case_root/"input.scs"
        with deck.open("x") as stream:
            stream.write(render_deck(source_plan, case["id"]))
        psf = case_root/"psf"
        _phase(config, work, environment, config["spectre"],
               (deck, "-format", "psfascii", "-raw", psf, "+log", case_root/"spectre.log"),
               case_root/"console.log")
        log = (case_root/"spectre.log").read_text(errors="replace")
        if re.search(r"(?:\*Error\*|\*E,|\*F,|^\s*(?:ERROR|FATAL)\b)", log, re.M) or \
                not re.search(r"spectre completes with 0 errors", log, re.I):
            raise ValueError("golden Spectre case failed; inspect private logs")
        names = _PSF_DATASET.findall((psf/"logFile").read_text())
        if len(names) != 1:
            raise ValueError("one golden transient PSF dataset is required")
        validate_psf_units(psf/names[0], signals)
        output = case_root/"waveforms.csv"
        script = case_root/"export.ocn"
        with script.open("x") as stream:
            stream.write(ocean_export(psf, output, signals))
        _phase(config, work, environment, config["ocean"],
               ("-nograph", "-nocdsinit", "-cdslib", cdslib, "-log", case_root/"ocean.log",
                "-replay", script), case_root/"ocean.console.log")
        if "AIVW_WAVEFORM_EXPORT_COMPLETE" not in (case_root/"ocean.log").read_text():
            raise ValueError("golden waveform export did not complete")
        # The exported golden carries every terminal plus the current probe,
        # in the declared terminal order.
        waves[case["id"]] = read_waveform(output, signals, stop)
    return waves


def run(config, *, runner=execute_candidate, golden_runner=None):
    """Execute two frozen repeats plus the golden comparison; return the
    public summary mapping."""

    private_root = Path(config["private_root"])
    spool = Path(config["spool"])
    spool.mkdir(parents=True, exist_ok=True)
    record, _cases = _load_reservation(private_root, config)
    _ensure_candidate_freeze(private_root, config)
    skeleton = json.loads(Path(config["skeleton_plan"]).read_text())
    planned = _hidden_cases(skeleton, record["cases"])
    source_plan = {**skeleton, "cases": planned}
    trajectory_plan = _trajectory_plan(skeleton, planned)
    plan_root = private_root/("plans-%s" % config["run_id"])
    plan_root.mkdir()
    source_path = plan_root/"hidden-source-plan.json"
    trajectory_path = plan_root/"hidden-trajectory-plan.json"
    write_json_once(source_path, source_plan)
    write_json_once(trajectory_path, trajectory_plan)
    model = json.loads(Path(config["model"]).read_text())
    # Each evaluation run owns its journal; a failed run stays retained and
    # never blocks a fresh, explicitly started evaluation of the frozen
    # candidate (candidate re-selection itself is forbidden above).
    journal = private_root/("repeat-journal-%s" % config["run_id"])
    frozen = freeze_inputs(journal,
        {"candidate.sv": config["candidate_source"], "model.json": config["model"],
         "hidden-source-plan.json": source_path, "hidden-trajectory-plan.json": trajectory_path,
         "policy.json": config["policy"], "circuit.scs": config["circuit"]},
        {"candidate_sha256": config["candidate_sha256"], "policy_digest": config["policy_digest"],
         "commitment_sha256": config["commitment_sha256"], "domain_digest": config["domain_digest"],
         "reference_run_id": config["reference_run_id"],
         "circuit_sha256": sha256_file(Path(config["circuit"]))})
    digest = frozen["freeze_digest"]
    policy = validate_waveform_policy(json.loads(Path(config["policy"]).read_text()))
    source = Path(config["candidate_source"]).read_text()
    environment = json.loads(Path(config["environment"]).read_text())
    renderer = _renderer(config.get("renderer", "transition_response"))
    signals = {trajectory_plan["roles"]["output"]: "V", "VPROBE:p": "A"}
    stop = trajectory_plan["analysis"]["stop_s"]
    goldens = dict(golden_runner(source_plan)) if golden_runner is not None else \
        _run_golden(config, private_root, source_plan)
    cases_meta = []
    actual = []
    for number in (1, 2):
        local = begin_attempt(journal, digest, number, run_id="%s-repeat-%d" % (config["run_id"], number))
        try:
            _executed, records = runner(local, source, model, trajectory_plan, environment,
                                        {"xrun": config["xrun"]}, float(config["timeout"]), renderer)
            cases_meta.append({item["id"]: sha256_file(Path(item["csv"])) for item in records})
            actual.append({item["id"]: read_waveform(Path(item["csv"]), signals, stop) for item in records})
            measurements = {"case_sha256": cases_meta[-1],
                            "aggregate_sha256": stable_digest(cases_meta[-1])}
            finish_attempt(journal, digest, number, status="EXECUTED", measurements=measurements)
        except Exception as exc:  # noqa: BLE001 - recorded as a failed attempt
            finish_attempt(journal, digest, number, status="FAILED",
                           measurements={"error": "%s: %s" % (type(exc).__name__, exc)})
            break
    if len(cases_meta) == 2:
        comparison = compare_attempts(journal, digest)
    else:
        comparison = {"status": "BLOCKED_REPEAT", "freeze_digest": digest,
                      "reason": "second attempt not started"}
    evidence = {"source_generation": model["source_generation"], "dataset": "hidden_holdout"}
    reports = [correlation_report(trajectory_plan, evidence, goldens, waves, policy,
                                  config["candidate_sha256"], dataset="hidden_holdout",
                                  holdout="EVALUATED") for waves in actual]
    if reports:
        write_json_once(private_root/("correlation-%s.json" % config["run_id"]), reports)
    if comparison["status"] != "PASS_REPEAT" or len(reports) != 2:
        status = "HIDDEN_HOLDOUT_FAIL" if comparison["status"] == "FAIL_REPEAT" else "HIDDEN_HOLDOUT_BLOCKED"
    elif all(report["status"] == "PASS" and report["diagnostic_status"] == "PASS" for report in reports):
        status = "HIDDEN_HOLDOUT_PASS"
    else:
        status = "HIDDEN_HOLDOUT_FAIL"
    # A bounded fingerprint of the golden evidence: sample counts plus
    # per-signal sums; publishes no hidden sample values.
    golden_digest = stable_digest(sorted(
        (case, stable_digest({"samples": len(wave.times),
                              "sums": [round(sum(wave.values[signal]), 12) for signal in sorted(wave.values)]}))
        for case, wave in goldens.items()))
    return {"schema_version": 1, "kind": "dynamic-holdout-evaluation",
            "status": status, "qualification": "NOT_ESTABLISHED",
            "case_count": len(planned), "repeat_count": len(cases_meta),
            "candidate_sha256": config["candidate_sha256"],
            "commitment_sha256": config["commitment_sha256"],
            "policy_digest": config["policy_digest"], "domain_digest": config["domain_digest"],
            "reference_run_id": config["reference_run_id"],
            "freeze_digest": digest, "repeat": comparison,
            "aggregate_measurements": [stable_digest(item) for item in cases_meta],
            "accuracy": {"correlation_status": [report["status"] for report in reports],
                         "diagnostic_status": [report["diagnostic_status"] for report in reports],
                         "failed_cases": sorted({case for report in reports for case in
                                                 (c["id"] for c in report["cases"] if c["diagnostic_status"] != "PASS")}),
                         "golden_evidence_digest": golden_digest},
            "holdout_values_included": False, "private_root_returned": False}


def _renderer(name):
    if name == "transition_response":
        from ..plugins.transition_response import render_testbench
        return render_testbench
    raise ValueError("unsupported renderer %r" % name)


def main(argv=None):  # pragma: no cover - exercised inside the boundary
    config = json.loads(Path(os.environ["HIDDEN_CONFIG"]).read_text())
    summary = run(config)
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
