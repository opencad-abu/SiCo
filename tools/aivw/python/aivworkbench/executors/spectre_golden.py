"""OCEAN read-only netlisting and Spectre characterization in the recipe DAG."""

from __future__ import annotations

import json
from pathlib import Path
import re
import time

from ..executor import ExecutorContext, ExecutorResult
from ..errors import AivwError
from ..ldo_source_contract import read_contract, verify_readback, verify_state
from ..spectre_data import stage_models, verify_model_sources
from ..workspace import sha256_file, write_json_once
from .ldo_contract import binding_inputs, collect_readback, failure
from .ldo_structure_common import (artifacts, child_environment, indexed_dependency,
                                  require_source, run_phase, source_snapshot)


_ERROR = re.compile(r"(?:\*Error\*|\*E,|\*F,|^\s*(?:ERROR|FATAL)\b)", re.M)


def contract_inputs(context):
    dependency = context.dependencies.get("experiment_contract")
    if dependency is None or dependency.status != "PASS":
        raise ValueError("Spectre requires a PASS experiment contract dependency")
    contract_path = indexed_dependency(context, dependency, "contract_artifact", "contract_sha256")
    evidence_path = indexed_dependency(context, dependency, "contract_evidence", "contract_evidence_sha256")
    return read_contract(contract_path), json.loads(evidence_path.read_text()), evidence_path.parent


def ocean_netlist_script(root: Path, contract: dict, model_path: Path) -> str:
    """Build the bounded OCEAN request from the validated ADE contract."""
    source = contract["source"]
    analysis = contract["analysis"]
    return '''envSetVal("asimenv.startup" "projectDir" 'string %s)
simulator('spectre)
design(%s %s %s "r")
resultsDir(%s)
envOption('switchViewList '("spectre" "schematic") 'stopViewList '("spectre"))
modelFile(list(%s %s))
analysis('tran ?stop %s ?errpreset %s)
temp(%s)
aivwNetlist=createNetlist(?recreateAll t ?display nil)
printf("AIVW_NETLIST %%L\\n" aivwNetlist)
exit(if(aivwNetlist then 0 else 2))
''' % (
        json.dumps(str(root / "simulation")), json.dumps(source["library"]),
        json.dumps(source["cell"]), json.dumps(source["view"]),
        json.dumps(str(root / "psf")), json.dumps(str(model_path)),
        json.dumps(analysis["model_section"] if "model_section" in analysis else contract["model"]["section"]),
        json.dumps("%.17g" % analysis["stop_s"]),
        json.dumps(analysis["errpreset"]), "%.17g" % analysis["temperature_c"],
    )


def netlist_read_only(context, root, contract, model_path, deadline):
    root.mkdir()
    script = root / "netlist.ocn"
    script.write_text(ocean_netlist_script(root, contract, model_path))
    cds = Path(context.metadata["project_config"]["cds_lib"])
    env = child_environment(context, cds)
    env["CDS_Netlisting_Mode"] = "Analog"
    run_phase(context, "ocean", ("-nograph", "-nocdsinit", "-cdslib", cds,
        "-log", root / "ocean.log", "-replay", script), environment=env,
        log=root / "console.log", deadline=deadline)
    log = (root / "ocean.log").read_text(errors="replace")
    matches = re.findall(r'^\\o AIVW_NETLIST "([^"\n]+)"\s*$', log, re.M)
    if (_ERROR.search(log) or len(matches) != 1 or "End netlisting" not in log
            or not re.search(r"Errors: 0\s+Warnings: 0", log)):
        raise ValueError("OCEAN did not establish clean official netlisting")
    exported = Path(matches[0])
    if exported.is_symlink() or not exported.resolve().is_relative_to(root.resolve()):
        raise ValueError("OCEAN netlist escaped exclusive payload")
    circuit = exported.parent / "netlist"
    if not circuit.is_file() or circuit.is_symlink() or not circuit.stat().st_size:
        raise ValueError("official circuit netlist missing")
    text = circuit.read_text()
    if re.search(r"(?im)^\s*(?:include|ahdl_include|simulator|parameters)\b", text):
        # subckt parameter declarations are allowed; only top-level parameters
        # would need a separate design-variable contract.
        depth = 0
        for line in text.splitlines():
            if line.startswith("subckt "):
                depth += 1
            elif line.startswith("ends "):
                depth -= 1
            elif depth == 0 and re.match(r"\s*(?:include|ahdl_include|simulator|parameters)\b", line):
                raise ValueError("unsupported circuit include or global parameter")
    expected = {"LDO_MASTER": ["VDD", "VOUT", "VSS"], "LDO_AON": ["EN", "VDD", "VOUT", "VSS"]}
    for cell, ports in expected.items():
        found = re.findall(r"^subckt " + cell + r" ([^\n]+)$", text, re.M)
        if len(found) != 1 or found[0].split() != ports:
            raise ValueError("official Spectre subckt interface mismatch")
    if not all(line in text for line in (
        "I0 (net8 VDD_INT VDD_AON_OUT 0) LDO_AON", "I1 (net9 VDD_INT VDD_PD2_OUT 0) LDO_PD2",
        "I2 (VDD_EXT VDD_INT 0) LDO_MASTER", "V0 (VDD_EXT 0) vsource dc=5 type=dc",
        "R1 (VDD_PD2_OUT 0) resistor r=10K", "R2 (VDD_AON_OUT 0) resistor r=10K")):
        raise ValueError("official testbench supply/cascade/load does not match contract")
    return circuit


def run_spectre_golden(context: ExecutorContext) -> ExecutorResult:
    if "experiment_contract" not in context.recipe.resolved_inputs:
        return ExecutorResult("BLOCKED_CONTRACT", {"reason": "no qualified Spectre experiment contract"})
    try:
        return _collect(context)
    except (OSError, ValueError, RuntimeError, KeyError, AivwError) as exc:
        return failure(context, exc, scope="spectre_characterization", default="BLOCKED_EVIDENCE")


def _collect(context):
    deadline = time.monotonic() + context.timeout
    project, cell, generation = binding_inputs(context)
    contract, evidence, contract_root = contract_inputs(context)
    if evidence["source_generation"] != generation:
        raise ValueError("experiment contract source_generation mismatch")
    interface_path = contract_root / "interface-contract.json"
    if sha256_file(interface_path) != evidence["interface_sha256"]:
        raise ValueError("contract interface artifact hash mismatch")
    interface = json.loads(interface_path.read_text())
    # Hash all settings before starting any simulator.
    verify_state(Path(project["root"]) / "amsLDO/TEST_LDO/maestro/active.state", contract)
    snapshot = context.dependencies["snapshot"]
    authentication = json.loads(indexed_dependency(context, snapshot, "snapshot_evidence", "snapshot_evidence_sha256").read_text())
    require_source(source_snapshot(context, authentication=authentication), generation=generation)
    model_root = context.gate_root / "models"
    models = stage_models(Path(project["model_root"]), contract["model"]["relative_path"], model_root)
    write_json_once(context.gate_root / "model-index.json", {"files": models, "section": contract["model"]["section"]})
    verify_model_sources(models, model_root)
    circuit = netlist_read_only(context, context.gate_root / "netlisting", contract,
                               model_root / contract["model"]["relative_path"], deadline)
    verify_model_sources(models, model_root)
    circuit_copy = context.gate_root / "circuit.scs"
    with circuit_copy.open("xb") as stream:
        stream.write(circuit.read_bytes())
    a = contract["analysis"]
    deck = context.gate_root / "input.scs"
    with deck.open("x") as stream:
        stream.write('simulator lang=spectre\nglobal 0\ninclude "models/%s" section=%s\ninclude "circuit.scs"\n' %
                     (contract["model"]["relative_path"], contract["model"]["section"]))
        stream.write("simulatorOptions options reltol=%.17g vabstol=%.17g iabstol=%.17g temp=%.17g\n" %
                     (a["reltol"], a["vabstol_v"], a["iabstol_a"], a["temperature_c"]))
        stream.write("tran tran stop=%.17g errpreset=%s\nsave %s\n" %
                     (a["stop_s"], a["errpreset"], " ".join(contract["measurements"]["signals"])))
    env = child_environment(context, Path(project["cds_lib"]))
    psf = context.gate_root / "psf"
    run_phase(context, "spectre", (deck, "-format", "psfascii", "-raw", psf,
        "+log", context.gate_root / "spectre.log"), environment=env,
        log=context.gate_root / "spectre.console.log", deadline=deadline)
    verify_model_sources(models, model_root)
    log = (context.gate_root / "spectre.log").read_text(errors="replace")
    if _ERROR.search(log) or not re.search(r"spectre completes with 0 errors", log, re.I):
        raise ValueError("Spectre completion/convergence evidence missing or fatal")
    psf_log = (psf / "logFile").read_text()
    result_files = re.findall(r'"analysisInst"\s*\(\s*"tran"\s*"([A-Za-z0-9_.-]+)"\s*"PSF"', psf_log)
    if len(result_files) != 1:
        raise ValueError("PSF log must identify exactly one transient result")
    raw = psf / result_files[0]
    if not raw.is_file() or not raw.stat().st_size:
        raise ValueError("Spectre transient waveform is missing")
    post = context.gate_root / "source-after"
    collect_readback(context, post, deadline)
    if verify_readback(post, contract, interface)["digest"] != evidence["readback"]["digest"]:
        raise ValueError("source changed during golden simulation")
    verify_state(Path(project["root"]) / "amsLDO/TEST_LDO/maestro/active.state", contract)
    after_circuit = netlist_read_only(context, context.gate_root / "netlisting-after", contract,
                                    model_root / contract["model"]["relative_path"], deadline)
    if circuit_copy.read_bytes() != after_circuit.read_bytes():
        raise ValueError("official hierarchical circuit changed during simulation")
    verify_model_sources(models, model_root)
    require_source(source_snapshot(context, authentication=authentication), generation=generation)
    evidence_path = context.gate_root / "golden-evidence.json"
    psf_records = [{"path": p.relative_to(context.run.payload_root).as_posix(),
                    "sha256": sha256_file(p), "size": p.stat().st_size}
                   for p in sorted(psf.rglob("*")) if p.is_file() and not p.is_symlink()]
    write_json_once(evidence_path, {"source_generation": generation, "contract_sha256": sha256_file(contract_root / "experiment-contract.json"),
        "scope": contract["scope"], "observed_cell": cell, "circuit_sha256": sha256_file(circuit_copy),
        "deck_sha256": sha256_file(deck), "waveform_sha256": sha256_file(raw),
        "waveform": raw.relative_to(context.run.payload_root).as_posix(), "psf_artifacts": psf_records,
        "model_index_sha256": sha256_file(context.gate_root / "model-index.json"),
        "warnings": re.findall(r'^\s*WARNING \([^\n]+', log, re.M),
        "warning_policy": "retained_characterization_only_no_behavioral_acceptance",
        "source_recheck": "PASS", "convergence": "completed_zero_errors", "behavior_verdict": "NOT_ESTABLISHED"})
    outputs = {"source_generation": generation, "golden_evidence": evidence_path.relative_to(context.run.payload_root).as_posix(),
               "golden_evidence_sha256": sha256_file(evidence_path)}
    return ExecutorResult("PASS", {"scope": contract["scope"], "behavior_verdict": "NOT_ESTABLISHED"}, outputs, artifacts(context))
