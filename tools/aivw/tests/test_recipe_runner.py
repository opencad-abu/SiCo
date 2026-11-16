from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

from aivworkbench.manifest import verify_split_manifests
from aivworkbench.profiles import load_profile
from aivworkbench.recipe import load_builtin_recipe
from aivworkbench.recipe_runner import run_recipe_dry_run
from aivworkbench.recipe_runner import run_recipe_through
from aivworkbench.executor import ExecutorContext, ExecutorResult
from aivworkbench.registry import builtin_registry
from aivworkbench.workspace import resolve_launch_paths


def _temporary_profile(tmp_path: Path):
    payload_root = tmp_path / "payload"
    payload_root.mkdir()
    setup = tmp_path / "setup.bashrc"
    setup.write_text(f"export PROJ_AMS_DB_DIR={payload_root}\n", encoding="utf-8")
    return replace(load_profile("amsverify"), setup_script=setup)


def test_generic_recipe_dry_run_uses_split_storage_without_eda(tmp_path: Path) -> None:
    profile = _temporary_profile(tmp_path)
    launch_dir = tmp_path / "launch"
    launch_dir.mkdir()
    recipe = load_builtin_recipe("saradc.comparator_new")

    manifest = run_recipe_dry_run(profile, resolve_launch_paths(launch_dir), recipe)

    assert manifest["status"] == "DRY_RUN_PASS"
    assert manifest["details"]["design_verdict"] == "NOT_RUN"
    assert manifest["details"]["mode"] == "dry_run"
    pair = manifest["manifest_pair"]
    assert pair["status"] == "PASS"
    assert pair["artifact_counts"] == {"control": 2, "payload": 0}
    control_root = launch_dir / ".aivw" / "runs" / str(manifest["run_id"])
    payload_root = (
        tmp_path
        / "payload"
        / "saradc.comparator_new.rnm"
        / "runs"
        / str(manifest["run_id"])
    )
    assert (control_root / "request.json").is_file()
    assert (control_root / "report.json").is_file()
    assert (payload_root / "payload-manifest.json").is_file()
    assert (
        verify_split_manifests(
            control_root / "manifest.json", payload_root / "payload-manifest.json"
        )["status"]
        == "PASS"
    )


def test_generic_recipe_bounded_execution_indexes_gate_results(tmp_path: Path) -> None:
    profile = _temporary_profile(tmp_path)
    launch_dir = tmp_path / "launch"
    launch_dir.mkdir()
    recipe = load_builtin_recipe("saradc.comparator_new")

    def handler(context: ExecutorContext) -> ExecutorResult:
        if context.gate_id == "structure":
            assert context.metadata["project"] == "adc"
            assert context.metadata["cds_lib"] == (
                "/workarea/xh/amsVerify/adc_verification_v5.0_001/WORK/saradc/cds.lib"
            )
            assert context.metadata["project_environment"] == {
                "PROJECT": "/workarea/xh/amsVerify/adc_verification_v5.0_001",
                "FLOW_HOME": "/workarea/xh/amsVerify/adc_verification_v5.0_001",
            }
        evidence = context.gate_root / "evidence.txt"
        evidence.write_text(context.gate_id + "\n", encoding="utf-8")
        return ExecutorResult("PASS", artifacts=(evidence,))

    registry = builtin_registry(
        executor_handlers={
            str(item["executor"]): handler
            for item in recipe.payload["workflow"]["gates"]
        }
    )
    manifest = run_recipe_through(
        profile,
        resolve_launch_paths(launch_dir),
        recipe,
        through="model_check",
        registry=registry,
        environment={"PATH": "/qualified/bin"},
        tools={"xrun": "/qualified/bin/xrun"},
        tool_evidence=(
            {
                "name": "xrun",
                "path": "/qualified/bin/xrun",
                "status": "PASS",
                "version_output": "23.09-s002",
            },
        ),
    )

    assert manifest["status"] == "PASS"
    assert manifest["details"]["selected_gates"] == [
        "snapshot",
        "structure",
        "generate",
        "connectivity",
        "model_check",
    ]
    assert manifest["details"]["publication"] == "NOT_AUTHORIZED"
    assert manifest["details"]["agent_context"]["code"] == "design_ir_assembly_failed"
    assert manifest["details"]["agent_context_status"] == "BLOCKED_CONTEXT_ASSEMBLY"
    assert "golden" not in manifest["details"]["gate_results"]
    assert manifest["manifest_pair"]["status"] == "PASS"
    assert manifest["manifest_pair"]["run_status"] == "PASS"
    assert manifest["manifest_pair"]["artifact_counts"] == {"control": 2, "payload": 10}
    request = Path(manifest["run_dir"]) / "request.json"
    assert "23.09-s002" in request.read_text(encoding="utf-8")


def test_generic_recipe_execution_rejects_publication_without_approval(
    tmp_path: Path,
) -> None:
    profile = _temporary_profile(tmp_path)
    launch_dir = tmp_path / "launch"
    launch_dir.mkdir()
    recipe = load_builtin_recipe("saradc.comparator_new")

    import pytest

    with pytest.raises(Exception, match="human-approved promotion"):
        run_recipe_through(
            profile,
            resolve_launch_paths(launch_dir),
            recipe,
            through="publish",
            environment={"PATH": "/qualified/bin"},
        )


def test_recipe_runner_publishes_bounded_design_ir_context_from_snapshot(tmp_path: Path) -> None:
    profile = _temporary_profile(tmp_path)
    launch_dir = tmp_path / "launch"
    launch_dir.mkdir()
    recipe = load_builtin_recipe("saradc.comparator_new")
    generation = "a" * 64

    def handler(context: ExecutorContext) -> ExecutorResult:
        if context.gate_id != "snapshot":
            return ExecutorResult("PASS")
        normalized = context.gate_root / "normalized-netlist.json"
        normalized.parent.mkdir(parents=True, exist_ok=True)
        normalized.write_text(
            json.dumps(
                {
                    "status": "PASS",
                    "cell_count": 1,
                    "cells": [{
                        "name": "comparator_new",
                        "instances": [],
                        "nets": [{"name": "out", "signal_type": "analog", "bits": 1}],
                        "terminals": [{"name": "out", "direction": "output", "pins": [{"net": "out"}]}],
                        "connections": [],
                        "provenance": {"identity": {
                            "lib": "saradc", "cell": "comparator_new", "view": "schematic"
                        }},
                    }],
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return ExecutorResult(
            "PASS",
            outputs={
                "source_generation": generation,
                "normalized_sha256": hashlib.sha256(normalized.read_bytes()).hexdigest(),
                "normalized_structure": normalized.relative_to(context.run.payload_root).as_posix(),
                "target": {"library": "saradc", "cell": "comparator_new", "module": "comparator_new"},
                "view_identity": {
                    "library": "saradc", "cell": "comparator_new", "view": "schematic",
                    "view_type": "schematic", "kind": "schematic",
                },
            },
            artifacts=(normalized,),
        )

    registry = builtin_registry(
        executor_handlers={
            str(item["executor"]): handler
            for item in recipe.payload["workflow"]["gates"]
        }
    )
    manifest = run_recipe_through(
        profile,
        resolve_launch_paths(launch_dir),
        recipe,
        through="model_check",
        registry=registry,
    )
    assert manifest["details"]["agent_context_status"] == "PASS"
    context = manifest["details"]["agent_context"]
    assert context["context"]["source_generation"] == generation
    assert "design_ir" not in context["context"]
    assert context["context"]["artifact_locators"][0]["sha256"]


def test_recipe_runner_uses_snapshot_outputs_hash_not_summary(tmp_path: Path) -> None:
    profile = _temporary_profile(tmp_path)
    launch_dir = tmp_path / "launch"
    launch_dir.mkdir()
    recipe = load_builtin_recipe("saradc.comparator_new")

    def handler(context: ExecutorContext) -> ExecutorResult:
        if context.gate_id != "snapshot":
            return ExecutorResult("PASS")
        normalized = context.gate_root / "normalized-netlist.json"
        normalized.parent.mkdir(parents=True, exist_ok=True)
        normalized.write_text(
            json.dumps({
                "status": "PASS", "cell_count": 1, "cells": [{
                    "name": "comparator_new", "instances": [], "nets": [],
                    "terminals": [{"name": "out", "direction": "output", "pins": []}],
                    "connections": [], "provenance": {"identity": {
                        "lib": "saradc", "cell": "comparator_new", "view": "schematic"
                    }},
                }]
            }),
            encoding="utf-8",
        )
        digest = hashlib.sha256(normalized.read_bytes()).hexdigest()
        return ExecutorResult(
            "PASS",
            summary={"normalized_sha256": "b" * 64},
            outputs={
                "source_generation": "a" * 64,
                "normalized_sha256": digest,
                "normalized_structure": normalized.relative_to(context.run.payload_root).as_posix(),
                "target": {"library": "saradc", "cell": "comparator_new", "module": "comparator_new"},
                "view_identity": {"library": "saradc", "cell": "comparator_new", "view": "schematic", "view_type": "schematic", "kind": "schematic"},
            },
            artifacts=(normalized,),
        )

    registry = builtin_registry(
        executor_handlers={
            **{
                str(item["executor"]): handler
                for item in recipe.payload["workflow"]["gates"]
            },
            "virtuoso.config_binding": handler,
        }
    )
    manifest = run_recipe_through(
        profile, resolve_launch_paths(launch_dir), recipe,
        through="model_check", registry=registry,
    )
    assert manifest["details"]["agent_context_status"] == "PASS"
    assert manifest["details"]["agent_context"]["context"]["source_generation"] == "a" * 64


def test_recipe_runner_binds_config_gate_to_bounded_context(tmp_path: Path) -> None:
    profile = _temporary_profile(tmp_path)
    launch_dir = tmp_path / "launch"
    launch_dir.mkdir()
    recipe = load_builtin_recipe("saradc.comparator_new")
    generation = "a" * 64

    def handler(context: ExecutorContext) -> ExecutorResult:
        if context.gate_id == "snapshot":
            normalized = context.gate_root / "normalized-netlist.json"
            normalized.parent.mkdir(parents=True, exist_ok=True)
            normalized.write_text(json.dumps({
                "status": "PASS", "cell_count": 1, "cells": [{
                    "name": "comparator_new", "instances": [], "nets": [],
                    "terminals": [{"name": "out", "direction": "output", "pins": []}],
                    "connections": [], "provenance": {"identity": {
                        "lib": "saradc", "cell": "comparator_new", "view": "schematic"
                    }},
                }]
            }), encoding="utf-8")
            return ExecutorResult("PASS", outputs={
                "source_generation": generation,
                "normalized_sha256": hashlib.sha256(normalized.read_bytes()).hexdigest(),
                "normalized_structure": normalized.relative_to(context.run.payload_root).as_posix(),
                "target": {"library": "saradc", "cell": "comparator_new", "module": "comparator_new"},
                "view_identity": {"library": "saradc", "cell": "comparator_new", "view": "schematic", "view_type": "schematic", "kind": "schematic"},
            }, artifacts=(normalized,))
        if context.gate_id == "config_binding":
            config = {
                "schema_version": "aivw.config.binding.v1",
                "identity": {"library": "saradc", "cell": "comparator_new", "view": "config", "source_generation": generation},
                "top": {"library": "saradc", "cell": "comparator_new", "view": "schematic"},
                "liblist": ["saradc"], "viewlist": ["config", "schematic"], "stoplist": ["spectre"],
                "cell_bindings": [], "instance_bindings": [], "discipline_bindings": [],
                "connect_rules": [], "source_artifacts": [],
            }
            artifact = context.gate_root / "config-binding.json"
            artifact.write_text(json.dumps(config), encoding="utf-8")
            return ExecutorResult("PASS", outputs={
                "binding_artifact": artifact.relative_to(context.run.payload_root).as_posix(),
                "binding_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                "source_generation": generation,
            }, artifacts=(artifact,))
        return ExecutorResult("PASS")

    registry = builtin_registry(
        executor_handlers={
            **{
                str(item["executor"]): handler
                for item in recipe.payload["workflow"]["gates"]
            },
            "virtuoso.config_binding": handler,
        }
    )
    # Use a config gate name in the closure while preserving the generic recipe.
    payload = json.loads(json.dumps(recipe.payload))
    payload["target"]["source_views"]["config"] = "config"
    payload["workflow"]["gates"].insert(1, {"id": "config_binding", "executor": "virtuoso.config_binding", "needs": ["snapshot"]})
    payload["workflow"]["gates"] = [
        gate if gate["id"] != "structure" else {**gate, "needs": ["snapshot", "config_binding"]}
        for gate in payload["workflow"]["gates"]
    ]
    from aivworkbench.recipe import Recipe
    configured_recipe = Recipe(recipe.path, payload, recipe.sha256, recipe.resolved_inputs)
    manifest = run_recipe_through(profile, resolve_launch_paths(launch_dir), configured_recipe, through="model_check", registry=registry)
    assert manifest["details"]["agent_context_status"] == "PASS"
    context = manifest["details"]["agent_context"]["context"]
    assert context["config_binding"]["identity"]["view"] == "config"
    assert "liblist" not in context["config_binding"]
