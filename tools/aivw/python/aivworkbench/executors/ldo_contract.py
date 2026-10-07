"""Current-run interface and TEST_LDO experiment contract evidence."""

from __future__ import annotations

import json
from pathlib import Path
import time

from ..design_ir import load_config_binding_artifact
from ..executor import ExecutorContext, ExecutorResult
from ..errors import AivwError
from ..ldo_source_contract import read_contract, verify_readback, verify_state
from ..profiles import product_root
from ..workspace import sha256_file, write_json_once
from .ldo_structure_common import (artifacts, child_environment, indexed_dependency,
                                  project_inputs, run_phase)


def collect_readback(context, destination, deadline):
    destination.mkdir()
    cds = Path(context.metadata["project_config"]["cds_lib"])
    env = child_environment(context, cds)
    env.update({"AIVW_CONTRACT_OUTPUT": str(destination),
                "AIVW_LDO_AI_ROOT": str(product_root().parent / "ai")})
    worker = destination / "worker.il"
    with worker.open("xb") as stream:
        stream.write((product_root() / "skill/ldo_contract_worker.il").read_bytes())
    run_phase(context, "virtuoso", ("-nograph", "-nocdsinit", "-cdslib", cds,
        "-log", destination / "virtuoso.log", "-replay", worker), environment=env,
        log=destination / "console.log", deadline=deadline)


def binding_inputs(context):
    project, cell = project_inputs(context)
    snapshot = context.dependencies.get("snapshot")
    binding = context.dependencies.get("config_binding")
    if snapshot is None or binding is None or snapshot.status != "PASS" or binding.status != "PASS":
        raise ValueError("experiment requires current snapshot and official config binding")
    generation = snapshot.outputs["source_generation"]
    target = {"library": "amsLDO", "cell": cell, "module": context.recipe.target["module"]}
    if snapshot.outputs["target"] != target:
        raise ValueError("snapshot target differs from experiment target")
    indexed_dependency(context, snapshot, "snapshot_evidence", "snapshot_evidence_sha256")
    load_config_binding_artifact({"executor": "virtuoso.config_binding", "status": binding.status,
        "outputs": dict(binding.outputs), "artifacts": [str(p.relative_to(context.run.payload_root)) for p in binding.artifacts]},
        context.run.payload_root, target={**target, "config_view": "config"}, source_generation=generation)
    return project, cell, generation


def failure(context, exc, *, scope, default="BLOCKED_CONTRACT"):
    from ..errors import EnvironmentError
    status = "BLOCKED_TIMEOUT" if isinstance(exc, TimeoutError) else default
    if isinstance(exc, EnvironmentError):
        status = "BLOCKED_ENVIRONMENT"
    elif any(text in str(exc) for text in ("source changed", "source_generation changed", "model source changed", "ADE state changed")):
        status = "STALE_SOURCE"
    report = {"status": status, "reason": str(exc), "scope": scope, "behavior_verdict": "NOT_ESTABLISHED"}
    write_json_once(context.gate_root / "failure.json", report)
    return ExecutorResult(status, report, artifacts=artifacts(context))


def run_ldo_contract(context: ExecutorContext) -> ExecutorResult:
    try:
        return _collect(context)
    except (OSError, ValueError, RuntimeError, KeyError, AivwError) as exc:
        return failure(context, exc, scope="source_experiment_contract")


def _collect(context):
    deadline = time.monotonic() + context.timeout
    project, cell, generation = binding_inputs(context)
    contract_path = context.recipe.input_path("experiment_contract")
    contract = read_contract(contract_path)
    interface = json.loads(context.recipe.input_path("interface_contract").read_text())
    if interface.get("status") != "source_interface_calibrated_rnm_domain_pending":
        raise ValueError("golden requires the calibrated source interface; fixture rejected")
    state = Path(project["root"]) / "amsLDO/TEST_LDO/maestro/active.state"
    state_before = sha256_file(state)
    raw = context.gate_root / "source"
    collect_readback(context, raw, deadline)
    locator = json.loads((raw / "state-locator.json").read_text())
    if Path(locator["path"]).resolve() != state.resolve() or state.is_symlink():
        raise ValueError("DD Maestro locator disagrees with approved source")
    readback = verify_readback(raw, contract, interface)
    state_copy = context.gate_root / "active.state.xml"
    with state_copy.open("xb") as stream:
        stream.write(state.read_bytes())
    normalized = verify_state(state_copy, contract)
    if sha256_file(state) != state_before or sha256_file(state_copy) != state_before:
        raise ValueError("ADE state changed during capture")
    path = context.gate_root / "experiment-contract.json"
    write_json_once(path, contract)
    write_json_once(context.gate_root / "interface-contract.json", interface)
    write_json_once(context.gate_root / "ade-settings.json", normalized)
    evidence = context.gate_root / "contract-evidence.json"
    write_json_once(evidence, {"source_generation": generation, "readback": readback,
        "state_sha256": state_before, "experiment_sha256": sha256_file(path),
        "interface_sha256": sha256_file(context.gate_root / "interface-contract.json"),
        "target_cell": cell, "scope": contract["scope"], "behavior_verdict": "NOT_ESTABLISHED",
        "correlation_status": "BLOCKED_CONTRACT"})
    outputs = {"source_generation": generation, "contract_artifact": path.relative_to(context.run.payload_root).as_posix(),
               "contract_sha256": sha256_file(path), "contract_evidence": evidence.relative_to(context.run.payload_root).as_posix(),
               "contract_evidence_sha256": sha256_file(evidence)}
    return ExecutorResult("PASS", {"scope": contract["scope"], "behavior_verdict": "NOT_ESTABLISHED",
        "correlation_status": "BLOCKED_CONTRACT"}, outputs, artifacts(context))
