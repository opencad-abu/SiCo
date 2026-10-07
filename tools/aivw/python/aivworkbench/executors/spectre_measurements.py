"""Hash-bound OCEAN waveform export and deterministic characterization metrics."""

from __future__ import annotations

import json
from pathlib import Path
import time

from ..design_ir.assembler_io import payload_artifact
from ..executor import ExecutorContext, ExecutorResult
from ..errors import AivwError
from ..spectre_data import measure_csv, validate_psf_units
from ..workspace import sha256_file, write_json_once
from .ldo_contract import failure
from .ldo_structure_common import (artifacts, child_environment, indexed_dependency,
                                    require_source, run_phase, source_snapshot)
from .spectre_golden import _ERROR, contract_inputs


def ocean_export_script(psf, output, signals):
    names = " ".join(json.dumps(name) for name in signals)
    header = ",".join(["time[s]", *[f"{n}[{u}]" for n, u in signals.items()]])
    return '''procedure(aivwExportWaveforms()
  let((aivwWaves aivwX aivwY aivwLength aivwOut aivwIndex aivwOtherX)
    openResults(%s)
    selectResult('tran)
    aivwWaves=foreach(mapcar aivwName list(%s) getData(aivwName ?result 'tran))
    unless(forall(aivwWave aivwWaves drIsWaveform(aivwWave)) error("Missing waveform"))
    aivwX=drGetWaveformXVec(car(aivwWaves))
    aivwLength=drVectorLength(aivwX)
    unless(and(aivwLength>=2 aivwLength<=250000) error("Waveform sample bound"))
    foreach(aivwWave aivwWaves
      unless(drVectorLength(drGetWaveformXVec(aivwWave))==aivwLength
        error("Waveform time axes differ")))
    aivwOut=outfile(%s "w")
    fprintf(aivwOut "%s\\n")
    for(aivwIndex 0 aivwLength-1
      fprintf(aivwOut "%%.17g" drGetElem(aivwX aivwIndex))
      foreach(aivwWave aivwWaves
        aivwOtherX=drGetWaveformXVec(aivwWave)
        unless(drGetElem(aivwOtherX aivwIndex)==drGetElem(aivwX aivwIndex)
          error("Waveform timestamps differ"))
        aivwY=drGetWaveformYVec(aivwWave)
        fprintf(aivwOut ",%%.17g" drGetElem(aivwY aivwIndex)))
      fprintf(aivwOut "\\n"))
    close(aivwOut)
    printf("AIVW_WAVEFORM_EXPORT_COMPLETE\\n")
    t
  )
)
if(errset(aivwExportWaveforms() t) then exit(0) else exit(2))
''' % (json.dumps(str(psf)), names, json.dumps(str(output)), header)


def run_spectre_measurements(context: ExecutorContext) -> ExecutorResult:
    try:
        return _collect(context)
    except (OSError, ValueError, RuntimeError, KeyError, AivwError) as exc:
        return failure(context, exc, scope="spectre_measurements", default="BLOCKED_EVIDENCE")


def _collect(context):
    deadline = time.monotonic() + context.timeout
    contract, contract_evidence, contract_root = contract_inputs(context)
    golden = context.dependencies.get("golden")
    if golden is None or golden.status != "PASS":
        raise ValueError("measurement requires PASS golden evidence")
    evidence = json.loads(indexed_dependency(context, golden, "golden_evidence", "golden_evidence_sha256").read_text())
    if (evidence["contract_sha256"] != sha256_file(contract_root / "experiment-contract.json")
            or evidence["source_generation"] != contract_evidence["source_generation"]):
        raise ValueError("golden/measurement contract or source generation mismatch")
    snapshot = context.dependencies.get("snapshot")
    if snapshot is None or snapshot.status != "PASS":
        raise ValueError("measurement requires the current PASS snapshot")
    authentication = json.loads(indexed_dependency(
        context, snapshot, "snapshot_evidence", "snapshot_evidence_sha256").read_text())
    require_source(source_snapshot(context, authentication=authentication),
                   generation=evidence["source_generation"])
    raw = payload_artifact(context.run.payload_root, evidence["waveform"])
    if raw not in golden.artifacts or sha256_file(raw) != evidence["waveform_sha256"]:
        raise ValueError("golden waveform is unindexed or changed")
    def verify_psf():
        if not evidence["psf_artifacts"]:
            raise ValueError("empty PSF artifact index")
        for record in evidence["psf_artifacts"]:
            path = payload_artifact(context.run.payload_root, record["path"])
            if (path not in golden.artifacts or path.parent != raw.parent or
                    path.stat().st_size != record["size"] or sha256_file(path) != record["sha256"]):
                raise ValueError("PSF artifact changed or is outside the indexed result set")
    verify_psf()
    validate_psf_units(raw, contract["measurements"]["signals"])
    csv = context.gate_root / "waveforms.csv"
    script = context.gate_root / "measure.ocn"
    script.write_text(ocean_export_script(raw.parent, csv, contract["measurements"]["signals"]))
    cds = Path(context.metadata["project_config"]["cds_lib"])
    run_phase(context, "ocean", ("-nograph", "-nocdsinit", "-cdslib", cds,
        "-log", context.gate_root / "ocean.log", "-replay", script),
        environment=child_environment(context, cds), log=context.gate_root / "console.log", deadline=deadline)
    log = (context.gate_root / "ocean.log").read_text(errors="replace")
    if _ERROR.search(log) or "\\o AIVW_WAVEFORM_EXPORT_COMPLETE" not in log:
        raise ValueError("OCEAN waveform extraction did not complete")
    if sha256_file(raw) != evidence["waveform_sha256"]:
        raise ValueError("golden waveform changed during measurement")
    verify_psf()
    report = measure_csv(csv, contract["measurements"]["signals"],
                         contract["measurements"]["sample_times_s"], contract["analysis"]["stop_s"])
    report.update(source_generation=evidence["source_generation"], contract_sha256=evidence["contract_sha256"],
                  golden_evidence_sha256=golden.outputs["golden_evidence_sha256"],
                  waveform_csv_sha256=sha256_file(csv), dataset="characterization")
    for sample in report["samples"]:
        sample["testbench_supply_current_A"] = -sample["values"]["V0:p"]
    result = context.gate_root / "measurements.json"
    write_json_once(result, report)
    return ExecutorResult("PASS", {"scope": "measurement_extraction", "samples": report["samples"],
        "behavior_verdict": "NOT_ESTABLISHED", "correlation_status": "BLOCKED_CONTRACT"},
        {"source_generation": evidence["source_generation"], "measurements": result.relative_to(context.run.payload_root).as_posix(),
         "measurements_sha256": sha256_file(result)}, artifacts(context))
