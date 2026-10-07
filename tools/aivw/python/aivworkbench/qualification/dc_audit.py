"""Evaluator-only verification of a frozen DC qualification, returning aggregates.

Rebuild comparisons from both simulators' CSVs, validate the reservation, and
bind all repetitions to the same candidate and source. Do not expose vectors.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from ..acceptance import compare_numeric, compare_repeats
from ..characterization_evidence import read_characterization
from ..electrical_experiment import render_deck
from ..experiment_partition import case_digest
from ..manifest import verify_split_manifests
from ..spectre_data import measure_csv
from ..plugins.measured_dc import calibrate_surface, render_surface, render_dc_testbench
from ..workspace import sha256_file, stable_digest


def _normalize_source_order(deck: str) -> str:
    """Ignore only ordering within the generated independent source block.

    JSON object key sorting can reorder voltage sources on readback. Keep every
    source declaration and all other deck bytes intact, including connections,
    values, duplicate declarations, solver settings and trailing newlines.
    """
    lines = deck.splitlines(keepends=True)
    indices = [i for i, line in enumerate(lines) if line.startswith("VS_")]
    if not indices:
        raise ValueError("Spectre deck has no generated voltage sources")
    start, stop = indices[0], indices[-1] + 1
    if indices != list(range(start, stop)):
        raise ValueError("Spectre voltage source declarations are not contiguous")
    lines[start:stop] = sorted(lines[start:stop])
    return "".join(lines)


def audit_dc_qualification(control_path: Path) -> dict:
    control=json.loads(control_path.read_text())
    root=Path(control["details"]["payload_root"])
    pair = verify_split_manifests(control_path,root/"payload-manifest.json")
    if pair["run_status"] != "PASS":
        raise ValueError("qualification manifest does not identify a successful run")
    report=json.loads((root/"qualification-summary.json").read_text())
    if report!=control["details"]["summary"] or report["status"]!="PASS":
        raise ValueError("qualification summary does not identify a successful run")
    commitment=report["holdout"]
    private=root/"reservation/reservation.json"
    if sha256_file(private)!=commitment["commitment_sha256"]:
        raise ValueError("holdout reservation commitment mismatch")
    reservation=json.loads(private.read_text())
    hidden=reservation["cases"]
    digests=[case_digest(case) for case in hidden]
    if len(set(digests))!=len(hidden) or set(digests).intersection(reservation["public_case_digests"]):
        raise ValueError("holdout cases are duplicated or exposed in public calibration")
    if reservation["domain_digest"]!=report["domain_sha256"] or reservation["policy_digest"]!=commitment["policy_digest"]:
        raise ValueError("reservation domain or policy mismatch")
    if len(hidden)!=commitment["case_count"]:
        raise ValueError("holdout count mismatch")
    frozen=json.loads((root/"reservation/candidate-freeze.json").read_text())
    candidate=root/"candidate/candidate.sv"
    if sha256_file(candidate)!=report["candidate_sha256"] or frozen["candidate_digest"]!=report["candidate_sha256"] or frozen["holdout_commitment"]!=commitment["commitment_sha256"]:
        raise ValueError("frozen candidate identity mismatch")
    model=json.loads((root/"candidate/model.json").read_text())
    parent_request=json.loads((control_path.parent/"request.json").read_text())
    if stable_digest(parent_request) != control["request_digest"]:
        raise ValueError("qualification request digest differs")
    if len(parent_request["source_runs"]) != 2:
        raise ValueError("two source characterization repeats are required")
    sources = [read_characterization(control_path.parent.parent/run/"manifest.json")
               for run in parent_request["source_runs"]]
    replay = compare_repeats([source["repeat_record"] for source in sources])
    if replay["status"] != "PASS" or replay != report["source_characterization_repeat"]:
        raise ValueError("source characterization repeats differ from qualification")
    source = sources[0]
    public_digests = sorted({case_digest({"kind": case["kind"],
        "stimulus": {"voltages": case["voltages"], "load_ohm": case["load_ohm"]},
        "measurement": source["experiment"]["analysis"]}) for case in source["experiment"]["cases"]})
    if public_digests != reservation["public_case_digests"]:
        raise ValueError("reserved public case identities differ from calibration")
    if (commitment["policy_digest"] != parent_request["acceptance_sha256"] or
            commitment["domain_digest"] != parent_request["domain_sha256"]):
        raise ValueError("holdout policy differs from qualification request")
    if source["evidence"]["source_generation"]!=report["source_generation"]:
        raise ValueError("qualification source generation differs")
    if model!=calibrate_surface(source["experiment"],source["evidence"]):
        raise ValueError("candidate calibration differs from public source evidence")
    if candidate.read_text()!=render_surface(model):
        raise ValueError("candidate source differs from frozen measured model")
    if frozen["contract_digest"]!=report["domain_sha256"]:
        raise ValueError("candidate contract digest differs")
    observed=[]
    for number, repeat in enumerate(report["repeats"], 1):
        path=Path(repeat["control_manifest"])
        c=json.loads(path.read_text()); r=Path(c["details"]["payload_root"])
        pair = verify_split_manifests(path,r/"payload-manifest.json")
        if (pair["run_status"] != "PASS" or c["run_id"] != repeat["run_id"] or
                c["request_digest"] != control["request_digest"] or
                c["details"]["summary"] != repeat["summary"] or
                json.loads((r/"summary.json").read_text()) != repeat["summary"]):
            raise ValueError("repeat summary or manifest identity differs")
        request=json.loads((path.parent/"request.json").read_text())
        expected_request = {**parent_request, "parent_run": control["run_id"],
                            "repeat": number, "candidate_sha256": report["candidate_sha256"],
                            "holdout": commitment}
        if request != expected_request:
            raise ValueError("repeat identity differs from frozen request")
        experiment=json.loads((r/"evaluator-private/experiment.json").read_text())
        cases=experiment["cases"]
        if {k:v for k,v in experiment.items() if k!="cases"}!={k:v for k,v in source["experiment"].items() if k!="cases"}:
            raise ValueError("repeat changed measurement conditions")
        identities=[case_digest({"kind":case["kind"],"stimulus":{"voltages":case["voltages"],"load_ohm":case["load_ohm"]},"measurement":experiment["analysis"]}) for case in cases]
        if identities!=digests:raise ValueError("repeat stimulus differs from reservation")
        if sha256_file(r/"evaluator-private/circuit.scs")!=source["evidence"]["circuit_sha256"]:
            raise ValueError("source circuit mismatch")
        for record in source["evidence"]["model_files"]:
            if sha256_file(r/"evaluator-private/models"/record["relative_path"])!=record["sha256"]:
                raise ValueError("repeat device model hash differs from source")
        if experiment["acceptance"]!=source["experiment"]["acceptance"]:
            raise ValueError("repeat acceptance policy differs from source")
        if sha256_file(r/"evaluator-private/xcelium/candidate.sv")!=report["candidate_sha256"]:
            raise ValueError("Xcelium used a different candidate")
        if (r/"evaluator-private/xcelium/tb.sv").read_text()!=render_dc_testbench(model,cases):
            raise ValueError("Xcelium stimulus differs from the reserved testbench")
        with (r/"evaluator-private/xcelium/rnm.csv").open() as stream:
            reader=csv.reader(stream)
            if next(reader)!=["case","VOUT[V]"]:raise ValueError("RNM CSV units differ")
            rows=list(reader)
        if [row[0] for row in rows]!=[case["id"] for case in cases]:raise ValueError("RNM case set mismatch")
        signals={p:"V" for p in experiment["target"]["term_order"]};signals["VPROBE:p"]="A"
        comparisons=[]
        for case,row in zip(cases,rows):
            actual_deck = (r/"evaluator-private/cases"/case["id"]/"input.scs").read_text()
            expected_deck = render_deck(experiment,case["id"])
            if _normalize_source_order(actual_deck) != _normalize_source_order(expected_deck):
                raise ValueError("Spectre deck differs from the reserved experiment")
            measure=measure_csv(r/"evaluator-private/cases"/case["id"]/"waveforms.csv",signals,experiment["analysis"]["steady_window_s"],experiment["analysis"]["stop_s"])
            want=measure["samples"][-1]["values"][experiment["roles"]["output"]]
            comparisons.append(compare_numeric(float(row[1]),want,unit="V",tolerance=experiment["acceptance"]["correlation"]))
        stored=json.loads((r/"evaluator-private/comparisons.json").read_text())
        if stored!={"cases":comparisons} or stable_digest(comparisons)!=repeat["summary"]["measurement_digest"] or any(m["status"]!="PASS" for m in comparisons):
            raise ValueError("correlation summary differs from simulator evidence")
        if (repeat["summary"]["case_count"] != len(cases) or
                repeat["summary"]["max_relative_error"] != max(m["relative_error"] for m in comparisons) or
                repeat["summary"]["max_absolute_error_v"] != max(m["absolute_error"] for m in comparisons)):
            raise ValueError("correlation aggregates differ from simulator evidence")
        observed.append({"run_id":repeat["run_id"],"digest":stable_digest(comparisons),
                         "max_relative_error":max(m["relative_error"] for m in comparisons),
                         "max_absolute_error_v":max(m["absolute_error"] for m in comparisons)})
    if len(observed)!=2 or observed[0]["run_id"]==observed[1]["run_id"] or observed[0]["digest"]!=observed[1]["digest"]:
        raise ValueError("independent correlation repeat mismatch")
    return {"status":"PASS","scope":"dc_slice_evidence_recomputed","run_id":control["run_id"],
            "case_count":len(hidden),"repeat_runs":len(observed),"candidate_sha256":report["candidate_sha256"],
            "max_relative_error":max(r["max_relative_error"] for r in observed),
            "max_absolute_error_v":max(r["max_absolute_error_v"] for r in observed),
            "full_behavior_qualification":"NOT_ESTABLISHED"}
