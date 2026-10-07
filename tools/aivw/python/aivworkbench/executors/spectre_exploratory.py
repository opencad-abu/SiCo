"""Source-bound, payload-isolated Spectre observations for LDO exploration.

This executor intentionally produces characterization observations only.  It
does not evaluate tolerances or create a behavioral verdict.  Every case has
its own deck, raw PSF directory and exported CSV under the current gate root.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import time
from typing import Any, Mapping

from ..executor import ExecutorContext, ExecutorResult
from ..errors import AivwError
from ..ldo_exploratory import build_exploratory_plan, read_exploratory_policy
from ..ldo_source_contract import read_contract
from ..spectre_data import measure_csv, stage_models, verify_model_sources
from ..workspace import sha256_file, write_json_once
from .ldo_contract import failure
from .ldo_structure_common import (
    artifacts,
    child_environment,
    indexed_dependency,
    project_inputs,
    require_source,
    run_phase,
    source_snapshot,
)
from .spectre_golden import netlist_read_only


_SAFE_CASE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


def _require_dependency(context: ExecutorContext, name: str) -> ExecutorResult:
    dependency = context.dependencies.get(name)
    if dependency is None or dependency.status != "PASS":
        raise ValueError("exploratory execution requires PASS %s dependency" % name)
    return dependency


def _extract_subckts(circuit: Path, target: str) -> str:
    """Return every source subckt, excluding TEST_LDO top-level stimulus."""
    text = circuit.read_text(encoding="utf-8")
    blocks = re.findall(r"(?ms)^subckt\s+[A-Za-z_][A-Za-z0-9_]*\s+[^\n]+\n.*?^ends\s+[A-Za-z_][A-Za-z0-9_]*\s*$", text)
    if not blocks or not any(re.search(r"^subckt\s+" + re.escape(target) + r"\s+", block, re.M) for block in blocks):
        raise ValueError("official circuit does not contain target subckt %s" % target)
    return "\n\n".join(blocks) + "\n"


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("%s must be numeric" % label)
    result = float(value)
    if result != result or result in (float("inf"), float("-inf")):
        raise ValueError("%s must be finite" % label)
    return result


def _source_line(name: str, initial: float, final: float | None = None) -> str:
    if final is None or initial == final:
        return "%s (%%s 0) vsource dc=%.17g" % (name, initial)
    return "%s (%%s 0) vsource type=pwl wave=[ 0 %.17g 1u %.17g 1.001u %.17g 4m %.17g ]" % (
        name, initial, initial, final, final
    )


def build_exploratory_deck(
    circuit_text: str, topology: str, case: Mapping[str, Any], model_relative: str, model_section: str,
) -> str:
    """Build one bounded standalone deck from a source-derived case."""
    if topology not in {"LDO_MASTER", "LDO_AON"}:
        raise ValueError("unsupported exploratory topology")
    if case.get("kind") not in {"dc", "transient"}:
        raise ValueError("unsupported exploratory case kind")
    stimulus = case["stimulus"]
    if topology == "LDO_MASTER" and any(
        name in stimulus for name in ("en_v", "initial_en_v", "final_en_v")
    ):
        raise ValueError("LDO_MASTER exploratory stimulus cannot contain EN")
    vdd = _number(stimulus.get("vdd_v", stimulus.get("initial_vdd_v")), "stimulus.vdd_v")
    load = _number(stimulus["load_resistance_ohm"], "stimulus.load_resistance_ohm")
    if load <= 0:
        raise ValueError("load resistance must be positive")
    transient = case["kind"] == "transient"
    if transient:
        initial = _number(stimulus.get("initial_vdd_v", vdd), "stimulus.initial_vdd_v")
        final = _number(stimulus.get("final_vdd_v", vdd), "stimulus.final_vdd_v")
        supply = _source_line("VSUP", initial, final).replace("%s", "VDD_SRC")
    else:
        supply = _source_line("VSUP", vdd).replace("%s", "VDD_SRC")
    if not circuit_text.strip():
        raise ValueError("source circuit text is empty")
    lines = [
        "simulator lang=spectre", "global 0",
        'include "../../models/%s" section=%s' % (model_relative, model_section),
        'include "../../circuit.scs"',
        supply,
        "VPROBE (VDD_SRC VDD) vsource dc=0",
        "VSS_SRC (VSS 0) vsource dc=0",
    ]
    if topology == "LDO_MASTER":
        lines.append("XUUT (VDD VOUT VSS) LDO_MASTER")
    elif topology == "LDO_AON":
        en = _number(stimulus.get("en_v", stimulus.get("initial_en_v", 0.0)), "stimulus.en_v")
        en_final = stimulus.get("final_en_v") if transient and "final_en_v" in stimulus else None
        if en_final is not None:
            en_final = _number(en_final, "stimulus.final_en_v")
        lines.append(_source_line("VEN", en, en_final).replace("%s", "EN"))
        lines.append("XUUT (EN VDD VOUT VSS) LDO_AON")
    lines.extend([
        "RLOAD (VOUT 0) resistor r=%.17g" % load,
        "tran tran stop=0.004 errpreset=moderate maxstep=10u",
        "saveOptions options save=allpub",
        "save VOUT VDD VSS%s VPROBE:p" % (" EN" if topology == "LDO_AON" else ""),
    ])
    return "\n".join(lines) + "\n"


def _ocean_export_script(psf: Path, csv_path: Path, topology: str) -> str:
    names = ["VOUT", "VDD", "VSS"] + (["EN"] if topology == "LDO_AON" else []) + ["VPROBE:p"]
    contract_names = ["VOUT", "VDD", "VSS"] + (["EN"] if topology == "LDO_AON" else []) + ["IDD"]
    header = ",".join(["time[s]"] + [name + ("[A]" if name == "IDD" else "[V]") for name in contract_names])
    quoted = " ".join(json.dumps(name) for name in names)
    return '''envSetVal("asimenv.startup" "projectDir" 'string %s)
resultsDir(%s)
procedure(aivwExport()
  let((waves x y out n i waveX)
    openResults(%s) selectResult('tran)
    waves=foreach(mapcar name list(%s) getData(name ?result 'tran))
    unless(forall(wave waves drIsWaveform(wave)) error("missing waveform"))
    x=drGetWaveformXVec(car(waves)) n=drVectorLength(x)
    unless(and(n>=2 n<=250000) error("waveform sample bound"))
    out=outfile(%s "w") fprintf(out "%s\\n")
    for(i 0 n-1
      fprintf(out "%%.17g" drGetElem(x i))
      foreach(wave waves
        waveX=drGetWaveformXVec(wave)
        unless(drGetElem(waveX i)==drGetElem(x i) error("time axes differ"))
        y=drGetWaveformYVec(wave) fprintf(out ",%%.17g" drGetElem(y i)))
      fprintf(out "\\n")) close(out) printf("AIVW_WAVEFORM_EXPORT_COMPLETE\\n") t))
if(errset(aivwExport() t) then exit(0) else exit(2))
''' % (json.dumps(str(psf.parent)), json.dumps(str(psf)), json.dumps(str(psf)), quoted, json.dumps(str(csv_path)), header)


def _case_paths(root: Path, case_id: str) -> tuple[Path, Path, Path]:
    if not _SAFE_CASE.fullmatch(case_id):
        raise ValueError("unsafe exploratory case id")
    case_root = root / "cases" / case_id
    return case_root, case_root / "input.scs", case_root / "psf"


def run_spectre_exploratory(context: ExecutorContext) -> ExecutorResult:
    try:
        return _collect(context)
    except (OSError, ValueError, RuntimeError, KeyError, AivwError) as exc:
        return failure(context, exc, scope="spectre_exploratory", default="BLOCKED_EVIDENCE")


def _collect(context: ExecutorContext) -> ExecutorResult:
    deadline = time.monotonic() + context.timeout
    project, cell = project_inputs(context)
    snapshot = _require_dependency(context, "snapshot")
    binding = _require_dependency(context, "config_binding")
    contract_result = _require_dependency(context, "experiment_contract")
    authentication = json.loads(indexed_dependency(context, snapshot, "snapshot_evidence", "snapshot_evidence_sha256").read_text())
    generation = require_source(source_snapshot(context, authentication=authentication), generation=snapshot.outputs["source_generation"])
    if binding.outputs.get("source_generation") != generation or contract_result.outputs.get("source_generation") != generation:
        raise ValueError("exploratory dependency source_generation mismatch")
    policy_path = context.recipe.input_path("exploratory_policy")
    policy = read_exploratory_policy(policy_path)
    if policy["topology"] != cell:
        raise ValueError("exploratory policy topology differs from recipe target")
    interface_path = context.recipe.input_path("interface_contract")
    experiment_path = context.recipe.input_path("experiment_contract")
    if policy["source"]["interface_contract"] != sha256_file(interface_path) or policy["source"]["experiment_contract"] != sha256_file(experiment_path):
        raise ValueError("exploratory policy is bound to a different source contract")
    plan = build_exploratory_plan(policy)
    plan_path = context.gate_root / "exploratory-plan.json"
    write_json_once(plan_path, plan)
    model_root = context.gate_root / "models"
    models = stage_models(Path(project["model_root"]), policy["model"]["relative_path"], model_root)
    verify_model_sources(models, model_root)
    nominal = read_contract(experiment_path)
    circuit = netlist_read_only(context, context.gate_root / "netlisting", nominal, model_root / policy["model"]["relative_path"], deadline)
    circuit_text = _extract_subckts(circuit, cell)
    circuit_copy = context.gate_root / "circuit.scs"
    with circuit_copy.open("x", encoding="utf-8") as stream:
        stream.write(circuit_text)
    observations = []
    for case in plan["cases"]:
        case_id = str(case["id"])
        case_root, deck, psf = _case_paths(context.gate_root, case_id)
        case_root.mkdir(parents=True)
        deck.write_text(build_exploratory_deck(circuit_text, cell, case, policy["model"]["relative_path"], policy["model"]["section"]), encoding="utf-8")
        env = child_environment(context, Path(project["cds_lib"]))
        run_phase(context, "spectre", (deck, "-format", "psfascii", "-raw", psf, "+log", case_root / "spectre.log"), environment=env, log=case_root / "console.log", deadline=deadline)
        log = (case_root / "spectre.log").read_text(errors="replace")
        if re.search(r"(?:\*Error\*|\*E,|^\s*(?:ERROR|FATAL)\b)", log, re.M) or not re.search(r"spectre completes with 0 errors", log, re.I):
            raise ValueError("Spectre exploratory case did not complete cleanly: %s" % case_id)
        psf_log = psf / "logFile"
        if not psf_log.is_file() or not psf_log.stat().st_size:
            raise ValueError("PSF log missing for exploratory case %s" % case_id)
        raw_names = re.findall(r'"analysisInst"\s*\(\s*"tran"\s*"([A-Za-z0-9_.-]+)"\s*"PSF"', psf_log.read_text(errors="replace"))
        if len(raw_names) != 1 or not (psf / raw_names[0]).is_file():
            raise ValueError("PSF transient result missing for exploratory case %s" % case_id)
        csv_path = case_root / "waveforms.csv"
        ocean = case_root / "export.ocn"
        ocean.write_text(_ocean_export_script(psf, csv_path, cell), encoding="utf-8")
        run_phase(context, "ocean", ("-nograph", "-nocdsinit", "-cdslib", Path(project["cds_lib"]), "-log", case_root / "ocean.log", "-replay", ocean), environment=env, log=case_root / "ocean.console.log", deadline=deadline)
        ocean_log = (case_root / "ocean.log").read_text(errors="replace")
        if "AIVW_WAVEFORM_EXPORT_COMPLETE" not in ocean_log or not csv_path.is_file():
            raise ValueError("OCEAN waveform export incomplete for exploratory case %s" % case_id)
        signals = {"VOUT": "V", "VDD": "V", "VSS": "V", **({"EN": "V"} if cell == "LDO_AON" else {}), "IDD": "A"}
        measurements = measure_csv(csv_path, signals, policy["measurements"]["sample_times_s"], 0.004)
        psf_artifacts = [{"path": item.relative_to(context.run.payload_root).as_posix(), "sha256": sha256_file(item), "size": item.stat().st_size} for item in sorted(psf.rglob("*")) if item.is_file() and not item.is_symlink()]
        observations.append({"id": case_id, "kind": case["kind"], "analysis": "tran_constant_probe" if case["kind"] == "dc" else "tran", "purpose": case["purpose"], "stimulus": dict(case["stimulus"]), "status": "OBSERVED", "measurements": measurements, "deck": deck.relative_to(context.run.payload_root).as_posix(), "deck_sha256": sha256_file(deck), "psf": psf.relative_to(context.run.payload_root).as_posix(), "psf_artifacts": psf_artifacts, "spectre_log": (case_root / "spectre.log").relative_to(context.run.payload_root).as_posix(), "spectre_log_sha256": sha256_file(case_root / "spectre.log"), "ocean_log": (case_root / "ocean.log").relative_to(context.run.payload_root).as_posix(), "ocean_log_sha256": sha256_file(case_root / "ocean.log"), "console_log_sha256": sha256_file(case_root / "console.log"), "ocean_console_log_sha256": sha256_file(case_root / "ocean.console.log"), "csv": csv_path.relative_to(context.run.payload_root).as_posix(), "csv_sha256": sha256_file(csv_path), "warnings": re.findall(r"^\s*WARNING[^\n]*", log, re.M)})
        verify_model_sources(models, model_root)
    source_after = source_snapshot(context, authentication=authentication)
    generation_after = require_source(source_after, generation=generation)
    evidence = context.gate_root / "exploratory-observations.json"
    write_json_once(evidence, {"schema_version": 1, "kind": "ldo-exploratory-observations", "dataset": "exploratory", "topology": cell, "policy": policy_path.relative_to(context.run.payload_root).as_posix() if policy_path.is_relative_to(context.run.payload_root) else str(policy_path), "policy_sha256": sha256_file(policy_path), "plan": plan_path.relative_to(context.run.payload_root).as_posix(), "plan_sha256": sha256_file(plan_path), "plan_digest": plan["plan_digest"], "source_generation": generation, "source_generation_before": generation, "source_generation_after": generation_after, "interface_contract_sha256": sha256_file(interface_path), "experiment_contract_sha256": sha256_file(experiment_path), "model_files": models, "circuit_sha256": sha256_file(circuit_copy), "measurement_contract": {"signals": {"VOUT": "V", "VDD": "V", "VSS": "V", **({"EN": "V"} if cell == "LDO_AON" else {}), "IDD": "A"}, "time_unit": policy["measurements"]["time_unit"], "sample_times_s": policy["measurements"]["sample_times_s"], "extraction": policy["measurements"]["extraction"], "tolerance_status": policy["tolerance_contract"]["status"]}, "cases": observations, "behavior_verdict": "NOT_ESTABLISHED", "correlation_status": "BLOCKED_CONTRACT", "tolerance_evaluation": "not_performed", "holdout": plan["holdout"]})
    return ExecutorResult("PASS", {"scope": "source_derived_exploration", "dataset": "exploratory", "case_count": len(observations), "behavior_verdict": "NOT_ESTABLISHED", "correlation_status": "BLOCKED_CONTRACT"}, {"source_generation": generation, "observations": evidence.relative_to(context.run.payload_root).as_posix(), "observations_sha256": sha256_file(evidence), "plan": plan_path.relative_to(context.run.payload_root).as_posix(), "plan_sha256": sha256_file(plan_path), "plan_digest": plan["plan_digest"]}, artifacts(context))


__all__ = ["build_exploratory_deck", "run_spectre_exploratory"]
