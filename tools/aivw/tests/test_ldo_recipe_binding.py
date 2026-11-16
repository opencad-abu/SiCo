"""Controlled process evidence exercises the real config gate and context loader."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from aivworkbench.design_ir import load_config_binding_artifact
from aivworkbench.executor import ExecutorResult
from aivworkbench.executors import ldo_config_binding, ldo_structure_common, virtuoso
from aivworkbench.profiles import load_profile
from aivworkbench.recipe import load_builtin_recipe
from aivworkbench.recipe_runner import run_recipe_through
from aivworkbench.registry import builtin_registry
from aivworkbench.process import ProcessResult
from aivworkbench.ldo_qualification import validate_topology_evidence
from aivworkbench.workspace import resolve_launch_paths, sha256_file, write_json_once


GENERATION = "a" * 64


def _run(tmp_path, monkeypatch, *, cell="LDO_MASTER", failure=None):
    launch = tmp_path / "launch"
    launch.mkdir()
    payload = tmp_path / "payload"
    payload.mkdir()
    source = tmp_path / "source"
    source.mkdir()
    setup = tmp_path / "setup.bashrc"
    setup.write_text(f"export PROJ_AMS_DB_DIR={payload}\n")
    mapping = {"definitions": {}}
    for lib in ("amsLDO", "gpdk045", "analogLib", "basic"):
        path = source / lib
        path.mkdir()
        mapping["definitions"][lib] = {"path": str(path), "resolved": True}
    rule = source / "rule.vams"
    rule.write_text("connectrules CR_full_fast; endconnectrules\n")
    profile = load_profile("amsverify")
    project = {**profile.projects["ldo"], "root": str(source), "connect_rule": {
        "name": "CR_full_fast", "source_relative_to_ams_home": "rule.vams",
        "sha256": sha256_file(rule), "policy": "approved_cadence_builtin_staged_read_only",
    }}
    profile = replace(profile, setup_script=setup, projects={"ldo": project})
    recipe = load_builtin_recipe("ldo.linear_regulator" if cell == "LDO_MASTER" else "ldo.aon_config_binding")
    calls = []
    snapshots = []

    def snapshot(context):
        target = {"library": "amsLDO", "cell": cell, "module": cell}
        evidence = context.gate_root / "snapshot-evidence.json"
        auth = {"schema_version": 1, "status": "PASS", "authority": ldo_structure_common.AUTHORITY,
                "source_generation": GENERATION, "formal_saved_state": True,
                "double_read_equal": True, "target": {"library": "amsLDO", "cells": [cell]}}
        write_json_once(evidence, auth)
        normalized = context.gate_root / "normalized-netlist.json"
        write_json_once(normalized, {"status": "PASS", "cell_count": 1, "cells": [{
            "name": cell, "instances": [], "nets": [], "terminals": [], "connections": [],
            "provenance": {"identity": {"lib": "amsLDO", "cell": cell, "view": "schematic"}},
        }]})
        return ExecutorResult("PASS", outputs={
            "source_generation": GENERATION, "target": target,
            "snapshot_evidence": evidence.relative_to(context.run.payload_root).as_posix(),
            "snapshot_evidence_sha256": "b" * 64 if failure == "snapshot_hash" else sha256_file(evidence),
            "normalized_structure": normalized.relative_to(context.run.payload_root).as_posix(),
            "normalized_sha256": sha256_file(normalized),
            "view_identity": {"library": "amsLDO", "cell": cell, "view": "schematic", "kind": "schematic", "view_type": "schematic"},
        }, artifacts=(evidence, normalized))

    def source_snapshot(context, *, authentication):
        snapshots.append(context.gate_id)
        return {"status": "PASS", "source_generation": "b" * 64 if failure == "source_changed" and len(snapshots) == 2 else GENERATION,
                "errors": [], "cds_lib": {"mapping": mapping}}

    def phase(context, tool, arguments, *, environment, log, deadline):
        calls.append(tool)
        assert "SICO_AI_TOKEN" not in environment and "OPENAI_API_KEY" not in environment
        assert deadline > 0
        log.write_text("worker finished\n")
        if failure == "timeout":
            raise TimeoutError("worker timed out")
        if tool == "dbaccess":
            write_json_once(Path(environment["AIVW_LDO_CATALOG_OUTPUT"]), {
                "schema_version": 1, "provider": "dbAccess", "authoritative": True, "read_only": True,
                "cells": [{"name": cell, "schematic": {"opened": True, "terminal_count": 3, "instance_count": 1}}],
            })
        elif tool == "virtuoso":
            write_json_once(Path(environment["AIVW_LDO_HED_OUTPUT"]), {
                "schema_version": 1, "provider": "Virtuoso/HED", "authority": "authenticated-cdns-ipc-read-only",
                "config_opened": True, "config": {"library": "amsLDO", "cell": cell, "view": "config"},
                "top": {"library": "amsLDO", "cell": cell, "view": "schematic"},
                "traversal": {"status": "OK"}, "formal_saved_state": True,
                "double_read_equal": failure != "hed_incomplete", "source_generation": GENERATION,
            })
        else:
            args = list(map(str, arguments))
            netlist = Path(args[args.index("-rundir") + 1]) / "netlist"
            for name in (".dbTermInfo", "netlist.vams", "digital/ihnl/globalmap", "digital/map/current"):
                p = netlist / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text('repList "schematic spectre"\nstopList "spectre"\n')
            (netlist / ".binding.cfg").write_text(f'config designtop="amsLDO.{cell}:schematic"\nconfig cell="child" lib="amsLDO" view="schematic"\n')
            (netlist / ".instBindInfoTable").write_text(f'"I0" ((("amsLDO" "child" "schematic" "schematic") ("amsLDO" "{cell}" "schematic" "schematic")))\n')
            (netlist / ".netlistStatus").write_text("success\n")
            (netlist / "digital/.cellTblWithoutBind").write_text("unresolved" if failure == "unresolved" else "")
            if failure != "discipline_missing":
                (netlist / "xrunArgs").write_text("-discipline logic\n-amsconnrules CR_full_fast\n")
            Path(args[args.index("-log") + 1]).write_text("AMS UNL completed successfully\n" + ("*F, FAILED\n" if failure == "fatal" else ""))
        return ProcessResult((tool,), str(context.gate_root), "start", "end", 0, 0, False, str(log))

    monkeypatch.setattr(ldo_config_binding, "source_snapshot", source_snapshot)
    monkeypatch.setattr(ldo_config_binding, "run_phase", phase)
    if failure == "source_artifact_changed":
        original = ldo_config_binding.normalize_ldo_structure
        def normalize(**kwargs):
            value = original(**kwargs)
            (kwargs["runams_root"] / "netlist/.dbTermInfo").write_text("tampered\n")
            return value
        monkeypatch.setattr(ldo_config_binding, "normalize_ldo_structure", normalize)
    result = run_recipe_through(
        profile, resolve_launch_paths(launch), recipe, through="config_binding",
        registry=builtin_registry(executor_handlers={"virtuoso.snapshot": snapshot, "virtuoso.config_binding": virtuoso.run_config_binding}),
        environment={"CDSHOME": str(source), "AMSHOME": str(source),
                     "SICO_AI_TOKEN": "private-bridge-token", "OPENAI_API_KEY": "private-model-key"},
    )
    return result, calls


@pytest.mark.parametrize("cell", ["LDO_MASTER", "LDO_AON"])
def test_current_recipe_binding_reaches_bounded_context(tmp_path, monkeypatch, cell):
    report, calls = _run(tmp_path, monkeypatch, cell=cell)
    assert report["status"] == "PASS"
    assert calls == ["dbaccess", "virtuoso", "runams"]
    assert report["details"]["agent_context_status"] == "PASS"
    context = report["details"]["agent_context"]
    binding = context["context"]["config_binding"]
    assert binding["identity"]["cell"] == cell
    assert binding["artifact_locator"]["path"] == "checks/gates/config_binding/config-binding.json"
    assert all(k not in binding for k in ("liblist", "viewlist", "instance_bindings", "connect_rules"))
    assert context["truncated"] is False
    gate = report["details"]["gate_results"]["config_binding"]
    loaded = load_config_binding_artifact(gate, report["details"]["payload_root"],
                                          target={"library": "amsLDO", "cell": cell, "config_view": "config"},
                                          source_generation=GENERATION)
    assert all(x["path"].startswith("checks/gates/config_binding/") for x in loaded.payload["source_artifacts"])
    assert report["manifest_pair"]["status"] == "PASS"


@pytest.mark.parametrize("failure,status", [
    ("snapshot_hash", "BLOCKED_INPUT"), ("hed_incomplete", "BLOCKED_INPUT"),
    ("unresolved", "BLOCKED_INPUT"), ("fatal", "BLOCKED_INPUT"),
    ("discipline_missing", "BLOCKED_INPUT"), ("source_changed", "STALE_SOURCE"),
    ("timeout", "BLOCKED_TIMEOUT"), ("source_artifact_changed", "BLOCKED_INPUT"),
])
def test_current_recipe_rejects_untrusted_evidence(tmp_path, monkeypatch, failure, status):
    report, calls = _run(tmp_path, monkeypatch, failure=failure)
    assert report["status"] == "BLOCKED_WORKFLOW"
    assert report["details"]["gate_results"]["config_binding"]["status"] == status
    assert report["details"]["agent_context_status"] != "PASS"
    assert not (Path(report["details"]["payload_root"]) / "checks/gates/config_binding/config-binding.json").exists()
    if failure == "snapshot_hash":
        assert calls == []


def test_recipe_target_snapshot_cannot_be_promoted_to_m3_family_qualification():
    result = validate_topology_evidence(
        {"schema_version": 1, "topology": "LDO_MASTER", "source_generation": GENERATION},
        topology="LDO_MASTER", source_snapshot={"status": "PASS", "authenticated": True,
                                                "source_generation": GENERATION, "scope": "recipe_target"},
    )
    assert "one recipe target snapshot cannot establish M3 family qualification" in result["findings"]
