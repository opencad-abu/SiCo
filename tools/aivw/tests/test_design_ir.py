from __future__ import annotations

import pytest

from aivworkbench.design_ir import (
    DesignIRAssemblyError,
    DesignIRValidationError,
    ConfigBindingValidationError,
    assemble_design_ir_from_snapshot,
    bind_config_to_target,
    config_binding_artifact_locator,
    load_config_binding_artifact,
    build_snapshot_context,
    build_design_ir,
    design_ir_digest,
    diff_design_ir,
    normalize_design_ir,
)

import hashlib
import json


def _ir(**updates):
    identity = {
        "library": "lib",
        "cell": "top",
        "module": "top",
        "view": "schematic",
        "source_generation": "a" * 64,
    }
    identity.update(updates.pop("identity", {}))
    return build_design_ir(
        identity=identity,
        instances=updates.pop("instances", [{"name": "M2", "master": "nmos"}, {"name": "M1", "master": "pmos"}]),
        nets=updates.pop("nets", [{"name": "out", "bits": 1}]),
        terminals=updates.pop("terminals", [{"name": "vdd", "direction": "input"}]),
        **updates,
    )


def test_normalization_and_digest_are_order_independent() -> None:
    first = _ir()
    second = _ir(instances=[{"master": "pmos", "name": "M1"}, {"master": "nmos", "name": "M2"}])
    assert normalize_design_ir(first)["instances"][0]["name"] == "M1"
    assert design_ir_digest(first) == design_ir_digest(second)


def test_diff_reports_targeted_revision_change() -> None:
    before = _ir()
    after = _ir(instances=[{"name": "M1", "master": "pmos"}, {"name": "M2", "master": "pmos"}])
    changes = diff_design_ir(before, after)
    assert {item["path"] for item in changes} == {"instances.1.master"}
    assert changes[0]["kind"] == "changed"


def test_schema_rejects_unknown_or_incomplete_fields() -> None:
    with pytest.raises(DesignIRValidationError, match="unknown fields"):
        normalize_design_ir({"schema_version": 2, "identity": {}, "unexpected": 1})
    with pytest.raises(DesignIRValidationError, match="identity.source_generation"):
        _ir(identity={"source_generation": ""})


def _snapshot_fixture(tmp_path):
    normalized = {
        "schema_version": "cad.schematic-to-rnm.normalized-netlist.v1",
        "status": "PASS",
        "cell_count": 1,
        "cells": [
            {
                "name": "top",
                "instances": [{"name": "M1", "master": "nmos", "terminals": []}],
                "nets": [{"name": "vdd", "signal_type": "analog", "bits": 1}],
                "terminals": [{"name": "vdd", "direction": "input", "pins": [{"net": "vdd"}]}],
                "connections": [],
                "provenance": {"identity": {"lib": "lib", "cell": "top", "view": "schematic"}},
            }
        ],
    }
    path = tmp_path / "checks" / "gates" / "snapshot" / "normalized-netlist.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(normalized, sort_keys=True), encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    gate = {
        "status": "PASS",
        "executor": "virtuoso.snapshot",
        "outputs": {
            "normalized_structure": "checks/gates/snapshot/normalized-netlist.json",
            "normalized_sha256": digest,
            "source_generation": "a" * 64,
            "target": {"library": "lib", "cell": "top", "module": "top"},
            "view_identity": {
                "library": "lib", "cell": "top", "view": "schematic",
                "view_type": "schematic", "kind": "schematic",
            },
        },
    }
    return path, gate


def test_snapshot_assembler_binds_normalized_structure_and_context(tmp_path) -> None:
    _path, gate = _snapshot_fixture(tmp_path)
    design_ir = assemble_design_ir_from_snapshot(gate, tmp_path)
    assert design_ir.source_generation == "a" * 64
    assert design_ir.payload["identity"]["library"] == "lib"
    assert design_ir.payload["behavior_evidence"]["record_counts"]["instances"] == 1
    context = build_snapshot_context(gate, tmp_path, max_bytes=4096)
    assert "design_ir" not in context.value
    assert context.value["source_generation"] == "a" * 64
    assert context.value["artifact_locators"][0]["path"].endswith("normalized-netlist.json")


def test_snapshot_assembler_rejects_unbound_or_tampered_artifact(tmp_path) -> None:
    path, gate = _snapshot_fixture(tmp_path)
    with pytest.raises(DesignIRAssemblyError, match="authoritative"):
        assemble_design_ir_from_snapshot({"status": "PASS", "summary": gate["outputs"]}, tmp_path)
    with pytest.raises(DesignIRAssemblyError, match="executor"):
        assemble_design_ir_from_snapshot({**gate, "executor": "fake.snapshot"}, tmp_path)
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(DesignIRAssemblyError, match="hash"):
        assemble_design_ir_from_snapshot(gate, tmp_path)


def test_snapshot_assembler_rejects_identity_conflict(tmp_path) -> None:
    path, gate = _snapshot_fixture(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["cells"][0]["provenance"]["identity"]["cell"] = "other"
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    gate["outputs"]["normalized_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(DesignIRAssemblyError, match="identity"):
        assemble_design_ir_from_snapshot(gate, tmp_path)


def _config_binding():
    rule_hash = "b" * 64
    return {
        "schema_version": "aivw.config.binding.v1",
        "identity": {
            "library": "lib",
            "cell": "top",
            "view": "config",
            "source_generation": "a" * 64,
        },
        "top": {"library": "lib", "cell": "top", "view": "schematic"},
        "liblist": ["lib", "basic"],
        "viewlist": ["config", "schematic", "symbol"],
        "stoplist": ["spectre"],
        "cell_bindings": [{"library": "lib", "cell": "top", "view": "schematic"}],
        "instance_bindings": [{
            "parent_library": "lib", "parent_cell": "top", "parent_view": "schematic",
            "instance": "U1", "bound_library": "lib", "bound_cell": "child", "bound_view": "schematic",
        }],
        "discipline_bindings": [{"net": "vout", "discipline": "electrical", "domain": "analog"}],
        "connect_rules": [{
            "name": "ams_rules",
            "sha256": rule_hash,
            "artifact": {"path": "inputs/ConnRules.vams", "sha256": rule_hash, "size": 12, "media_type": "text/plain"},
        }],
        "source_artifacts": [{"path": "inputs/config", "sha256": "c" * 64, "size": 10, "media_type": "text/plain"}],
    }


def test_config_binding_is_target_and_generation_bound() -> None:
    binding = bind_config_to_target(
        _config_binding(),
        target={"library": "lib", "cell": "top"},
        source_generation="a" * 64,
    )
    assert binding.identity["cell"] == "top"
    assert binding.payload["discipline_bindings"][0]["domain"] == "analog"
    assert binding.payload["connect_rules"][0]["artifact"]["sha256"] == "b" * 64


def test_config_binding_rejects_another_config_view() -> None:
    with pytest.raises(ConfigBindingValidationError, match="target view"):
        bind_config_to_target(_config_binding(), target={"library": "lib", "cell": "top", "config_view": "other"}, source_generation="a" * 64)


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda value: value["identity"].update({"source_generation": "d" * 64}), "source_generation"),
        (lambda value: value["connect_rules"][0]["sha256"] == "d" * 64, ""),
        (lambda value: value["discipline_bindings"][0].update({"domain": "unknown"}), "domain"),
        (lambda value: value["top"].update({"cell": "other"}), "identity and top"),
    ],
)
def test_config_binding_rejects_stale_or_invalid_records(mutate, message: str) -> None:
    value = _config_binding()
    if message == "":
        value["connect_rules"][0]["sha256"] = "d" * 64
    else:
        mutate(value)
    with pytest.raises(ConfigBindingValidationError, match=message or "hash"):
        bind_config_to_target(value, target={"library": "lib", "cell": "top"}, source_generation="a" * 64)


def test_config_binding_artifact_requires_gate_index_hash_and_referenced_files(tmp_path) -> None:
    artifact = tmp_path / "binding.json"
    source = tmp_path / "inputs" / "ConnRules.vams"
    source.parent.mkdir()
    source.write_text("connect rules\n", encoding="utf-8")
    binding = _config_binding()
    binding["source_artifacts"] = []
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    binding["connect_rules"][0]["sha256"] = digest
    binding["connect_rules"][0]["artifact"]["sha256"] = digest
    binding["connect_rules"][0]["artifact"]["size"] = source.stat().st_size
    artifact.write_text(json.dumps(binding), encoding="utf-8")
    artifact_digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    gate = {
        "status": "PASS",
        "executor": "virtuoso.config_binding",
        "outputs": {"binding_artifact": "binding.json", "binding_sha256": artifact_digest},
        "artifacts": ["binding.json"],
    }
    loaded = load_config_binding_artifact(
        gate,
        tmp_path,
        target={"library": "lib", "cell": "top"},
        source_generation="a" * 64,
    )
    assert loaded.payload["connect_rules"][0]["artifact"]["path"] == "inputs/ConnRules.vams"
    locator = config_binding_artifact_locator(gate, tmp_path)
    assert locator.to_dict() == {
        "path": "binding.json",
        "sha256": artifact_digest,
        "size": artifact.stat().st_size,
        "media_type": "application/json",
    }
    with pytest.raises(ConfigBindingValidationError, match="not indexed"):
        load_config_binding_artifact(
            {**gate, "artifacts": []},
            tmp_path,
            target={"library": "lib", "cell": "top"},
            source_generation="a" * 64,
        )


def test_snapshot_context_exposes_config_locator_without_binding_payload(tmp_path) -> None:
    _path, gate = _snapshot_fixture(tmp_path)
    binding = _config_binding()
    binding["connect_rules"] = []
    binding["source_artifacts"] = []
    artifact = tmp_path / "config-binding.json"
    artifact.write_text(json.dumps(binding, sort_keys=True), encoding="utf-8")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    config_gate = {
        "status": "PASS",
        "executor": "virtuoso.config_binding",
        "outputs": {"binding_artifact": "config-binding.json", "binding_sha256": digest},
        "artifacts": ["config-binding.json"],
    }
    from aivworkbench.design_ir import config_binding_artifact_locator

    context = build_snapshot_context(
        gate,
        tmp_path,
        config_binding=binding,
        config_binding_locator=config_binding_artifact_locator(config_gate, tmp_path),
    )
    config_summary = context.value["config_binding"]
    assert config_summary["artifact_locator"]["path"] == "config-binding.json"
    assert "liblist" not in config_summary
    assert len(context.value["artifact_locators"]) == 2


def test_config_binding_locator_rejects_tamper_and_path_alias(tmp_path) -> None:
    artifact = tmp_path / "binding.json"
    artifact.write_text(json.dumps(_config_binding()), encoding="utf-8")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    gate = {
        "status": "PASS",
        "executor": "virtuoso.config_binding",
        "outputs": {"binding_artifact": "binding.json", "binding_sha256": digest},
        "artifacts": ["binding.json"],
    }
    artifact.write_text(artifact.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ConfigBindingValidationError, match="hash"):
        config_binding_artifact_locator(gate, tmp_path)
    bad = {**gate, "outputs": {**gate["outputs"], "binding_artifact": "../binding.json"}}
    with pytest.raises(ConfigBindingValidationError, match="not indexed"):
        config_binding_artifact_locator(bad, tmp_path)


def test_snapshot_context_rejects_unbound_or_incomplete_config_locator(tmp_path) -> None:
    _path, gate = _snapshot_fixture(tmp_path)
    locator = {"path": "config-binding.json", "sha256": "a" * 64, "size": 1}
    with pytest.raises(DesignIRAssemblyError, match="requires a config binding"):
        build_snapshot_context(gate, tmp_path, config_binding_locator=locator)
    binding = _config_binding()
    with pytest.raises(DesignIRAssemblyError, match="JSON hash"):
        build_snapshot_context(
            gate,
            tmp_path,
            config_binding=binding,
            config_binding_locator=locator,
        )
