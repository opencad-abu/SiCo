"""Saved LDO snapshot collected by a profile-selected read-only Virtuoso worker."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import time

from ..executor import ExecutorContext, ExecutorResult
from ..errors import AivwError
from ..ldo_live import _stage_cds_lib
from ..profiles import product_root
from ..workspace import sha256_file, stable_digest, write_json_once
from .ldo_structure_common import (
    AUTHORITY, artifacts, blocked, child_environment, project_inputs,
    read_inspection, remaining, require_source, run_phase, source_snapshot,
)


def run_ldo_snapshot(context: ExecutorContext) -> ExecutorResult:
    try:
        return _collect(context)
    except (OSError, ValueError, RuntimeError, AivwError) as exc:
        return blocked(context, exc)


def _collect(context: ExecutorContext) -> ExecutorResult:
    from .virtuoso import _run_normalizer

    deadline = time.monotonic() + context.timeout
    project, cell = project_inputs(context)
    before = source_snapshot(context)
    generation = require_source(before)
    write_json_once(context.gate_root / "source-preflight-before.json", before)
    cds = context.gate_root / "cds.lib"
    _stage_cds_lib(cds, source_root=Path(project["root"]),
                   tool_environment=context.environment,
                   approved_mapping=before["cds_lib"]["mapping"])
    raw = context.gate_root / "raw"
    raw.mkdir()
    environment = child_environment(context, cds)
    environment.update({
        "AIVW_LDO_AI_ROOT": str(product_root().parent / "ai"),
        "AIVW_LDO_SNAPSHOT_OUTPUT": str(raw), "AIVW_LDO_LIBRARY": "amsLDO",
        "AIVW_LDO_CELL": cell,
    })
    worker = product_root() / "skill" / "ldo_snapshot_worker.il"
    run_phase(context, "virtuoso", (
        "-nograph", "-nocdsinit", "-cdslib", cds,
        "-log", context.gate_root / "virtuoso.log", "-replay", worker,
    ), environment=environment, log=context.gate_root / "worker.log", deadline=deadline)
    for kind in ("library", "schematic", "symbol"):
        first = read_inspection(raw / f"{kind}-before.json", kind=kind,
                                cell=cell if kind != "library" else None)
        second = read_inspection(raw / f"{kind}-after.json", kind=kind,
                                 cell=cell if kind != "library" else None)
        if stable_digest(first) != stable_digest(second):
            raise ValueError("source_generation changed between inspection reads")
        if kind == "library" and (
            first.get("name") != "amsLDO" or not isinstance(first.get("path"), str)
            or Path(first["path"]).resolve() != Path(before["cds_lib"]["mapping"]["definitions"]["amsLDO"]["path"]).resolve()
        ):
            raise ValueError("worker library mapping disagrees with source preflight")
    authentication = {
        "schema_version": 1, "status": "PASS", "authority": AUTHORITY,
        "formal_saved_state": True, "double_read_equal": True,
        "source_generation": generation, "target": {"library": "amsLDO", "cells": [cell]},
        "snapshot_digest": stable_digest({p.name: sha256_file(p) for p in raw.glob("*.json")}),
    }
    after = source_snapshot(context, authentication=authentication)
    require_source(after, generation=generation)
    if after.get("status") != "PASS":
        raise ValueError("saved source authentication failed")
    write_json_once(context.gate_root / "source-preflight-after.json", after)
    normalized = context.gate_root / "normalized-netlist.json"
    input_manifest = context.gate_root / "input-manifest.json"
    normalization = _run_normalizer(
        raw / "schematic-before.json", raw / "symbol-before.json", normalized,
        input_manifest, context.gate_root / "normalize.log",
        replace(context, timeout=remaining(deadline), environment=child_environment(context, cds)),
    )
    if normalization["returncode"] != 0 or normalization["timed_out"]:
        raise ValueError("saved LDO inspection normalization failed; see normalize.log")
    evidence = context.gate_root / "snapshot-evidence.json"
    write_json_once(evidence, {
        **authentication, "normalizer": normalization,
        "worker_sha256": sha256_file(worker),
        "structure_authority": "preliminary_snapshot_not_cadence_si",
    })
    target = {"library": "amsLDO", "cell": cell, "module": context.recipe.target["module"]}
    view = {"library": "amsLDO", "cell": cell, "view": "schematic",
            "view_type": "schematic", "kind": "schematic"}
    def relative(path: Path) -> str:
        return path.relative_to(context.run.payload_root).as_posix()
    return ExecutorResult("PASS", {**authentication, "library_path": str(Path(project["root"]) / "amsLDO")}, {
        "source_generation": generation, "target": target, "view_identity": view,
        "snapshot_evidence": relative(evidence), "snapshot_evidence_sha256": sha256_file(evidence),
        "normalized_structure": relative(normalized), "normalized_sha256": sha256_file(normalized),
        "input_manifest": relative(input_manifest), "structure_authoritative": False,
    }, artifacts(context))
