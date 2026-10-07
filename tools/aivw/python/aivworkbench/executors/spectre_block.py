"""Data-driven block characterization using the qualified source adapter.

LDO source acquisition remains in its existing adapter. Numerical controls,
terminal roles, source values and acceptance rules come from recipe artifacts.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
import re
import time

from ..acceptance import compare_numeric
from ..electrical_experiment import extract_subcircuits, ocean_export, read_experiment, render_deck
from ..executor import ExecutorContext, ExecutorResult
from ..errors import AivwError
from ..spectre_data import measure_csv, stage_models, validate_psf_units, verify_model_sources
from ..workspace import sha256_file, stable_digest, write_json_once
from .ldo_contract import binding_inputs, failure
from .ldo_structure_common import artifacts, child_environment, indexed_dependency, require_source, run_phase, source_snapshot
from .spectre_golden import contract_inputs, netlist_read_only


def _record(path: Path, root: Path) -> dict:
    if not path.is_file() or path.is_symlink():
        raise ValueError("characterization artifact is missing or a symlink")
    return {"path": path.relative_to(root).as_posix(), "sha256": sha256_file(path), "size": path.stat().st_size}


def _product_result(csv_path: Path, signals: dict, experiment: dict, case: dict) -> dict:
    if not case["product_check"]:
        return {"status": "NOT_APPLICABLE", "scope": "nominal_dc_only"}
    output = experiment["roles"]["output"]
    column = list(signals).index(output) + 1
    lower, upper = experiment["analysis"]["steady_window_s"]
    with csv_path.open() as stream:
        rows = list(csv.reader(stream))[1:]
    selected = [float(row[column]) for row in rows if lower <= float(row[0]) <= upper]
    # Require the entire observation interval. Interpolate its boundary samples
    # through the same validated CSV reader, never extrapolate.
    boundary = measure_csv(csv_path, signals, [lower, upper], experiment["analysis"]["stop_s"])
    selected += [row["values"][output] for row in boundary["samples"]]
    if len(selected) < 2:
        raise ValueError("product window has insufficient samples")
    goal = experiment["acceptance"]["target_v"]
    worst = max(selected, key=lambda v: abs(v-goal))
    result = compare_numeric(worst, goal, unit="V", tolerance=experiment["acceptance"]["product"])
    return {**result, "scope": "nominal_dc_observation_window",
            "window_s": [lower, upper], "sample_count": len(selected),
            "observed_min_v": min(selected), "observed_max_v": max(selected)}


def run_block_characterization(context: ExecutorContext) -> ExecutorResult:
    try:
        return _collect(context)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AivwError) as exc:
        return failure(context, exc, scope="block_characterization", default="BLOCKED_EVIDENCE")


def _collect(context: ExecutorContext) -> ExecutorResult:
    deadline = time.monotonic() + context.timeout
    project, cell, generation = binding_inputs(context)
    nominal, source_contract_evidence, _ = contract_inputs(context)
    if source_contract_evidence["source_generation"] != generation:
        raise ValueError("source contract generation mismatch")
    path = context.recipe.input_path("characterization_plan")
    plan = read_experiment(path)
    original_hash = sha256_file(path)
    if plan["target"]["cell"] != cell or plan["target"]["library"] != context.recipe.target["library"]:
        raise ValueError("block experiment target differs from recipe")
    interface = context.recipe.input_path("interface_contract")
    if sha256_file(interface) != plan["interface_sha256"]:
        raise ValueError("block experiment interface hash mismatch")
    declaration = json.loads(interface.read_text())["variants"][cell]
    if declaration["spectre_term_order"] != plan["target"]["term_order"]:
        raise ValueError("block terminal order differs from source interface")
    decision_path = context.recipe.input_path("acceptance_policy")
    if decision_path.name != plan["acceptance"]["decision"] or sha256_file(decision_path) != plan["acceptance"]["decision_sha256"]:
        raise ValueError("acceptance decision hash mismatch")
    decision = json.loads(decision_path.read_text())
    if decision["status"] != "approved_method" or decision["targets"][cell] != {
        "nominal_supply_v": plan["acceptance"]["nominal_supply_v"],
        "nominal_output_v": plan["acceptance"]["target_v"],
    }:
        raise ValueError("acceptance method/target mismatch")
    for kind in ("product", "correlation"):
        if decision[kind + "_relative_tolerance"] != plan["acceptance"][kind]["relative"]:
            raise ValueError("experiment changed approved tolerance")
        if plan["acceptance"][kind]["absolute"] is not None:
            raise ValueError("absolute tolerance has no decision in this method version")
    root = context.gate_root
    write_json_once(root / "experiment.json", plan)
    with (root / "acceptance-decision.json").open("xb") as stream:
        stream.write(decision_path.read_bytes())
    snapshot = context.dependencies["snapshot"]
    authentication = json.loads(indexed_dependency(context, snapshot, "snapshot_evidence", "snapshot_evidence_sha256").read_text())
    require_source(source_snapshot(context, authentication=authentication), generation=generation)
    model_root = root / "models"
    models = stage_models(Path(project["model_root"]), plan["model"]["relative_path"], model_root)
    verify_model_sources(models, model_root)
    circuit = netlist_read_only(context, root / "netlisting", nominal,
                               model_root / plan["model"]["relative_path"], deadline)
    circuit_text = extract_subcircuits(circuit.read_text(), plan["target"])
    with (root / "circuit.scs").open("x") as stream:
        stream.write(circuit_text)
    signals = {pin: "V" for pin in plan["target"]["term_order"]}
    signals["VPROBE:p"] = "A"
    env = child_environment(context, Path(project["cds_lib"]))
    records = []
    for case in plan["cases"]:
        case_root = root / "cases" / case["id"]
        case_root.mkdir(parents=True)
        deck = case_root / "input.scs"
        with deck.open("x") as stream:
            stream.write(render_deck(plan, case["id"]))
        psf = case_root / "psf"
        run_phase(context, "spectre", (deck, "-format", "psfascii", "-raw", psf, "+log", case_root / "spectre.log"),
                  environment=env, log=case_root / "console.log", deadline=deadline)
        log = (case_root / "spectre.log").read_text(errors="replace")
        if re.search(r"(?:\*Error\*|\*E,|\*F,|^\s*(?:ERROR|FATAL)\b)", log, re.M) or not re.search(r"spectre completes with 0 errors", log, re.I):
            raise ValueError("Spectre case failed; inspect isolated logs")
        names = re.findall(r'"analysisInst"\s*\(\s*"tran"\s*"([A-Za-z0-9_.-]+)"\s*"PSF"', (psf / "logFile").read_text())
        if len(names) != 1:
            raise ValueError("one transient PSF dataset is required")
        validate_psf_units(psf / names[0], signals)
        output = case_root / "waveforms.csv"
        script = case_root / "export.ocn"
        with script.open("x") as stream:
            stream.write(ocean_export(psf, output, signals))
        run_phase(context, "ocean", ("-nograph", "-nocdsinit", "-cdslib", project["cds_lib"], "-log", case_root / "ocean.log", "-replay", script),
                  environment=env, log=case_root / "ocean.console.log", deadline=deadline)
        if "AIVW_WAVEFORM_EXPORT_COMPLETE" not in (case_root / "ocean.log").read_text():
            raise ValueError("waveform export did not complete")
        measurements = measure_csv(output, signals, plan["analysis"]["sample_times_s"], plan["analysis"]["stop_s"])
        product = _product_result(output, signals, plan, case)
        records.append({"id": case["id"], "case_digest": stable_digest(case), "kind": case["kind"],
                        "purpose": case["purpose"], "measurements": measurements,
                        "product": product, "artifacts": [_record(p, context.run.payload_root) for p in sorted(case_root.rglob("*")) if p.is_file()],
                        "warnings": re.findall(r"^\s*WARNING[^\n]*", log, re.M)})
        verify_model_sources(models, model_root)
    after = netlist_read_only(context, root / "netlisting-after", nominal,
                             model_root / plan["model"]["relative_path"], deadline)
    if circuit_text != extract_subcircuits(after.read_text(), plan["target"]):
        raise ValueError("source changed during block characterization")
    require_source(source_snapshot(context, authentication=authentication), generation=generation)
    verify_model_sources(models, model_root)
    if sha256_file(path) != original_hash or sha256_file(decision_path) != plan["acceptance"]["decision_sha256"]:
        raise ValueError("experiment or acceptance policy changed during execution")
    checked = [r["product"]["status"] for r in records if r["product"]["status"] != "NOT_APPLICABLE"]
    product_status = "PASS" if checked and all(s == "PASS" for s in checked) else "FAIL_TOLERANCE" if checked else "NOT_EVALUATED"
    evidence = {"schema_version": 1, "kind": "electrical-block-characterization", "dataset": "public_calibration",
                "target": plan["target"], "source_generation": generation, "source_recheck": "PASS",
                "experiment_sha256": sha256_file(root / "experiment.json"), "experiment_digest": stable_digest(plan),
                "acceptance_sha256": plan["acceptance"]["decision_sha256"],
                "circuit_sha256": sha256_file(root / "circuit.scs"), "model_files": models,
                "measurement_contract": {"signals": signals, "time_unit": "s", "current_sign": "VPROBE:p_positive_from_supply_into_block",
                                         "steady_window_s": plan["analysis"]["steady_window_s"], "sampling": "linear_interpolation_no_extrapolation"},
                "cases": records, "product_status": product_status,
                "product_scope": {"supply_v": plan["acceptance"]["nominal_supply_v"],
                                  "load_ohm": sorted({c["load_ohm"] for c in plan["cases"] if c["product_check"]}),
                                  "model_section": plan["model"]["section"], "temperature_c": plan["analysis"]["temperature_c"],
                                  "window_s": plan["analysis"]["steady_window_s"]},
                "rnm_status": "NOT_RUN", "correlation_status": "NOT_RUN", "holdout_status": "NOT_USED_PUBLIC_DATA",
                "qualification_status": "NOT_ESTABLISHED"}
    output = root / "characterization-evidence.json"
    write_json_once(output, evidence)
    # A successful acquisition does not assert product or model qualification.
    return ExecutorResult("PASS", {"scope": "source_characterization", "case_count": len(records),
                                  "product_status": product_status, "qualification_status": "NOT_ESTABLISHED"},
                          {"source_generation": generation, "characterization_evidence": output.relative_to(context.run.payload_root).as_posix(),
                           "characterization_evidence_sha256": sha256_file(output)}, artifacts(context))
