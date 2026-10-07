"""Current-recipe LDO official HED/dbAccess/runams binding executor."""

from __future__ import annotations

from pathlib import Path
import time

from ..design_ir import build_config_binding_from_official_structure, load_config_binding_artifact
from ..design_ir.assembler_io import read_json
from ..executor import ExecutorContext, ExecutorResult
from ..errors import AivwError
from ..ldo_live import _hed_summary, _runams_summary, _stage_cds_lib, _stage_connect_rule
from ..ldo_normalize import _connect_rule_evidence, normalize_ldo_structure
from ..profiles import product_root
from ..workspace import sha256_file, write_json_once
from .ldo_structure_common import (
    AUTHORITY, artifacts, blocked, child_environment, indexed_dependency,
    project_inputs, require_source, run_phase, source_snapshot,
)


def run_ldo_config_binding(context: ExecutorContext) -> ExecutorResult:
    try:
        return _collect(context)
    except (OSError, ValueError, RuntimeError, AivwError) as exc:
        return blocked(context, exc)


def _collect(context: ExecutorContext) -> ExecutorResult:
    deadline = time.monotonic() + context.timeout
    project, cell = project_inputs(context)
    snapshot = context.dependencies.get("snapshot")
    if snapshot is None or snapshot.status != "PASS":
        raise ValueError("official binding requires a PASS snapshot dependency")
    snapshot_target = snapshot.outputs.get("target")
    target = {"library": "amsLDO", "cell": cell, "module": context.recipe.target["module"]}
    if snapshot_target != target:
        raise ValueError("snapshot target disagrees with config recipe target")
    auth_path = indexed_dependency(context, snapshot, "snapshot_evidence", "snapshot_evidence_sha256")
    authentication = read_json(auth_path)
    if authentication.get("authority") != AUTHORITY:
        raise ValueError("official LDO binding requires the profile-owned snapshot worker")
    generation = snapshot.outputs.get("source_generation")
    if authentication.get("source_generation") != generation:
        raise ValueError("snapshot evidence source_generation mismatch")
    before = source_snapshot(context, authentication=authentication)
    require_source(before, generation=generation)
    if before.get("status") != "PASS":
        raise ValueError("official binding source preflight did not PASS")
    write_json_once(context.gate_root / "source-preflight-before.json", before)
    root = context.gate_root
    cds = root / "cds.lib"
    _stage_cds_lib(cds, source_root=Path(project["root"]),
                   tool_environment=context.environment,
                   approved_mapping=before["cds_lib"]["mapping"])
    policy = project["connect_rule"]
    rule_path = root / "connect-rules" / "CR_full_fast.vams"
    _stage_connect_rule(rule_path, tool_environment=context.environment, policy=policy)
    environment = child_environment(context, cds)
    workers = root / "workers"
    workers.mkdir()
    catalog = workers / "catalog.json"
    hed = workers / f"hed-{cell}.json"
    catalog_env = {**environment, "AIVW_LDO_LIBRARY": "amsLDO", "AIVW_LDO_CELLS": cell,
                   "AIVW_LDO_CATALOG_OUTPUT": str(catalog)}
    run_phase(context, "dbaccess", ("-load", product_root() / "skill" / "ldo_catalog_worker.il"),
              environment=catalog_env, log=root / "catalog.log", deadline=deadline)
    hed_env = {**environment, "AIVW_LDO_HED_OUTPUT": str(hed),
               "AIVW_LDO_SOURCE_GENERATION": generation, "AIVW_LDO_CONFIG_LIBRARY": "amsLDO",
               "AIVW_LDO_CONFIG_CELL": cell, "AIVW_LDO_CONFIG_VIEW": "config"}
    run_phase(context, "virtuoso", (
        "-nograph", "-nocdsinit", "-cdslib", cds, "-log", root / "hed.log",
        "-replay", product_root() / "skill" / "ldo_hed_worker.il",
    ), environment=hed_env, log=root / "hed.console.log", deadline=deadline)
    hed_summary, findings = _hed_summary(read_json(hed), cell)
    if findings or hed_summary.get("source_generation") != generation:
        raise ValueError("HED identity or saved-state evidence failed: " + str(findings))
    runams_root = root / "runams" / cell
    log = root / "runams.log"
    result = run_phase(context, "runams", (
        "-nocdsinit", "-lib", "amsLDO", "-cell", cell, "-view", "config",
        "-netlist", "all", "-savescripts", "-clean", "-reportinvalidbinding",
        "-netlisteropts", "amsPortConnectionByNameOrOrder=order", "-cdslib", cds,
        "-connectrules", f"userDef:{policy['name']}:{rule_path}", "-rundir", runams_root, "-log", log,
    ), environment=environment, log=root / "runams.console.log", deadline=deadline)
    summary = _runams_summary(runams_root, log, result, payload_root=root)
    if (summary["netlist_status"] != "success" or not summary["success_marker"]
        or summary["fatal_log_marker"] or summary["unresolved_count"] != 0
        or not all(summary["required_artifacts"].values())):
        raise ValueError("runams did not establish complete official binding evidence")
    normalized, evidence = normalize_ldo_structure(
        cell=cell, hed_path=hed, catalog_path=catalog, runams_root=runams_root,
        payload_root=root, runams_log=log,
    )
    write_json_once(root / "normalized-ams.json", normalized)
    rules, disciplines = _connect_rule_evidence(runams_root / "netlist", root)
    for rule in rules:
        rule["artifact"]["path"] = (root / rule["artifact"]["path"]).relative_to(context.run.payload_root).as_posix()
    binding = build_config_binding_from_official_structure(
        normalized, target={**target, "config_view": "config"}, source_generation=generation,
        payload_root=context.run.payload_root, source_root=root,
        evidence=evidence, discipline_bindings=disciplines, connect_rules=rules,
    )
    after = source_snapshot(context, authentication=authentication)
    write_json_once(root / "source-preflight-after.json", after)
    require_source(after, generation=generation)
    if after.get("status") != "PASS":
        raise ValueError("official binding source recheck did not PASS")
    path = root / "config-binding.json"
    write_json_once(path, binding.to_dict())
    outputs = {
        "binding_artifact": path.relative_to(context.run.payload_root).as_posix(),
        "binding_sha256": sha256_file(path), "source_generation": generation,
        "target": target,
    }
    # Use the exact loader consumed by recipe context, including all referenced files.
    load_config_binding_artifact({"executor": "virtuoso.config_binding", "status": "PASS",
                                 "outputs": outputs, "artifacts": [outputs["binding_artifact"]]},
                                context.run.payload_root, target={**target, "config_view": "config"},
                                source_generation=generation)
    write_json_once(root / "config-binding-evidence.json", {
        "schema_version": 1, "status": "PASS", "scope": "read_only_structure",
        "source_generation": generation, "provider": "Virtuoso/HED + dbAccess + runams",
        "binding_sha256": outputs["binding_sha256"], "official_evidence": evidence,
    })
    return ExecutorResult("PASS", {"complete": True, "identity": dict(binding.identity),
                                   "source_generation": generation}, outputs, artifacts(context))
