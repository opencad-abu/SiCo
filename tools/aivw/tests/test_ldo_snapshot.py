"""Saved-state, truncation and source-change boundaries for the LDO worker."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aivworkbench.executor import execute_workflow
from aivworkbench.executors import ldo_snapshot
from aivworkbench.profiles import load_profile
from aivworkbench.recipe import load_builtin_recipe
from aivworkbench.registry import builtin_registry
from aivworkbench.workspace import allocate_split_run, resolve_launch_paths, stable_digest


@pytest.mark.parametrize("failure,status", [
    (None, "PASS"), ("unsaved", "BLOCKED_INPUT"), ("truncated", "BLOCKED_INPUT"),
    ("different_reads", "STALE_SOURCE"), ("source_changed", "STALE_SOURCE"),
    ("library_changed", "BLOCKED_INPUT"),
])
def test_ldo_worker_snapshot_requires_complete_saved_consistent_source(tmp_path, monkeypatch, failure, status):
    launch = tmp_path / "launch"
    payload = tmp_path / "payload"
    launch.mkdir()
    payload.mkdir()
    source = tmp_path / "source"
    mapping = {"definitions": {}}
    for library in ("amsLDO", "gpdk045", "analogLib", "basic"):
        path = source / library
        path.mkdir(parents=True)
        mapping["definitions"][library] = {"path": str(path), "resolved": True}
    project = {**load_profile("amsverify").projects["ldo"], "root": str(source)}
    metadata = {"project_config": project, "read_only_structure_adapter": "ldo.official_ams.v1"}
    recipe = load_builtin_recipe("ldo.linear_regulator")
    run = allocate_split_run(resolve_launch_paths(launch), payload, library="amsLDO",
                             cell="LDO_MASTER", view="rnm_candidate", kind="test",
                             request_digest=stable_digest({"test": failure}))

    def source_snapshot(context, *, authentication=None):
        return {"source_generation": "b" * 64 if authentication and failure == "source_changed" else "a" * 64,
                "errors": [], "status": "PASS" if authentication else "BLOCKED_ENVIRONMENT",
                "cds_lib": {"mapping": mapping}}

    def phase(context, tool, arguments, *, environment, log, deadline):
        root = Path(environment["AIVW_LDO_SNAPSHOT_OUTPUT"])
        lib = {"ok": True, "kind": "library", "name": "amsLDO", "truncated": False,
               "path": str(source / ("basic" if failure == "library_changed" else "amsLDO"))}
        sch = {"ok": True, "kind": "schematic", "cellview": {"lib": "amsLDO", "cell": "LDO_MASTER", "view": "schematic"},
               "modified": failure == "unsaved", "truncated": failure == "truncated", "instances": [],
               "nets": [{"name": "VOUT", "bits": 1, "signal_type": "supply", "global": False}],
               "terminals": [{"name": "VOUT", "direction": "output", "pins": [{"net": "VOUT"}]}]}
        sym = {"ok": True, "kind": "symbol", "cellview": {"lib": "amsLDO", "cell": "LDO_MASTER", "view": "symbol"},
               "modified": False, "truncated": False,
               "terminals": [{"name": "VOUT", "direction": "output"}], "port_order": [], "term_order": []}
        for phase in ("before", "after"):
            for kind, value in (("library", lib), ("schematic", sch), ("symbol", sym)):
                value = dict(value)
                if failure == "different_reads" and phase == "after" and kind == "schematic":
                    value["changed"] = True
                (root / f"{kind}-{phase}.json").write_text(json.dumps(value))

    monkeypatch.setattr(ldo_snapshot, "source_snapshot", source_snapshot)
    monkeypatch.setattr(ldo_snapshot, "run_phase", phase)
    result = execute_workflow(recipe, builtin_registry(), run, targets=("snapshot",),
                              metadata=metadata, environment={"CDSHOME": str(source), "PATH": "/usr/bin:/bin"})
    snapshot = result.gates["snapshot"]
    assert snapshot.status == status
    if status == "PASS":
        assert snapshot.outputs["source_generation"] == "a" * 64
        normalized = json.loads((run.payload_root / snapshot.outputs["normalized_structure"]).read_text())
        assert normalized["status"] == "PASS" and normalized["cells"][0]["name"] == "LDO_MASTER"
    else:
        assert "normalized_structure" not in snapshot.outputs
