# Original test bodies retain their imports and Qt lifetime references.
# ruff: noqa: F401, F841, I001
"""agent native binding contract provenance contracts."""

from __future__ import annotations

import pytest


def test_native_identity_is_required_and_retired_owner_cannot_reuse_task(tmp_path):
    import json
    import os
    import subprocess
    from pathlib import Path

    from sico.transport.circuit import encode_value
    from sico.transport.native_bindings import NATIVE_CONTRACT, native_wire

    root = Path(__file__).absolute().parents[2]
    binary = Path("/software/eda/cadence/installs/IC23.10.130/tools/dfII/bin/dbAccess")
    if not binary.is_file():
        pytest.skip("dbAccess unavailable")
    prefix = [
        f"load({json.dumps(str(root.parent / 'sico/skill++/SICO.ils'))})",
        'm3Binding=makeTable("m3Binding" nil) m3Binding[\'instanceId]="instance" '
        "m3Binding['generation]=\"g\" m3Binding['instanceBridge]=t",
        f"m3Binding['cwd]={json.dumps(str(tmp_path))}",
        'sicoBindings["test"]=m3Binding',
    ]
    identity = dict(
        contract=NATIVE_CONTRACT,
        session_id="owner",
        binding_id="lifetime",
        instance_id="instance",
        generation="g",
        cwd=str(tmp_path),
        task_id="task",
    )
    alias = tmp_path / "project-alias"
    alias.symlink_to(tmp_path, target_is_directory=True)

    def request(name, function, values, *, authenticated=True, changes=None):
        rid = str(len(prefix)).zfill(32)
        metadata = dict(identity, request_id=rid, **(changes or {}))
        line = rid + " circuit_call bound " + function + " " + encode_value([values, None, None])
        if authenticated:
            line += " @binding " + native_wire(metadata)
        prefix.append(
            "aiWriteTextFile("
            + json.dumps(str(tmp_path / (name + ".json")))
            + ' sicoNativeRequest("test" '
            + json.dumps(line)
            + "))"
        )

    def request_context(name, *, changes=None):
        rid = str(len(prefix)).zfill(32)
        metadata = dict(identity, request_id=rid, **(changes or {}))
        line = rid + " get_project_context bound @binding " + native_wire(metadata)
        prefix.append(
            "aiWriteTextFile("
            + json.dumps(str(tmp_path / (name + ".json")))
            + ' sicoNativeRequest("test" '
            + json.dumps(line)
            + "))"
        )

    request("missing", "aiCircuitBeginTask", ["design", "background", True], authenticated=False)
    request("begin", "aiCircuitBeginTask", ["design", "background", True])
    request_context("alias", changes={"cwd": str(alias)})
    request("same", "aiCircuitBeginTask", ["design", "background", True])
    request(
        "retired",
        "aiCircuitBeginTask",
        ["design", "background", True],
        changes={"binding_id": "next"},
    )
    request(
        "generation",
        "aiCircuitBeginTask",
        ["design", "background", True],
        changes={"generation": "other"},
    )
    request(
        "unreserved", "aiCopilotOpenTarget", ["basic", "ipin", "symbol", "schematicSymbol", "r"]
    )
    request(
        "wrong_path",
        "aiCopilotOpenTarget",
        ["basic", "ipin", "symbol", "schematicSymbol", "r"],
        changes={"resources": [["basic", "ipin", "symbol", str(tmp_path / "other-library")]]},
    )
    prefix.append("exit()")
    script = tmp_path / "ownership.il"
    script.write_text("\n".join(prefix) + "\n")
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"LD_LIBRARY_PATH", "LD_PRELOAD", "SICO_ROOT"}
    }
    completed = subprocess.run(
        [str(binary), "-nocdsinit", "-load", str(script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=25,
    )
    results = {
        name: json.loads((tmp_path / (name + ".json")).read_text())
        for name in (
            "missing", "begin", "alias", "same", "retired", "generation", "unreserved",
            "wrong_path"
        )
    }
    assert results["missing"]["code"] == results["generation"]["code"] == "binding_identity_invalid"
    assert results["alias"]["ok"]
    assert (
        results["begin"]["result"]["circuit"]["ok"] and results["same"]["result"]["circuit"]["ok"]
    )
    assert results["retired"]["result"]["circuit"]["code"] == "task_source_mismatch"
    for name in ("unreserved", "wrong_path"):
        assert not results[name]["ok"]
        assert results[name]["code"] == "binding_reservation_changed"
        assert results[name]["diagnostic"]["category"] == "binding"
        assert not results[name]["diagnostic"]["function_entered"]
        assert not results[name]["diagnostic"]["operation_dispatched"]
    assert "unclosed" not in completed.stdout + completed.stderr


def test_declared_output_row_is_gated_for_reads_and_writes(tmp_path):
    """The native gate compares every declared output row with its reservation.

    The retained-model read inside run_simulation_setup carries the operation's
    expected target; the ContextBroker reserves that row before dispatch, so the
    read reaches its function while an unreserved or aliased row is still
    rejected before function entry.
    """
    import json
    import os
    import subprocess
    from pathlib import Path

    from sico.transport.circuit import encode_value
    from sico.transport.native_bindings import NATIVE_CONTRACT, native_wire

    root = Path(__file__).absolute().parents[2]
    binary = Path("/software/eda/cadence/installs/IC23.10.130/tools/dfII/bin/dbAccess")
    if not binary.is_file():
        pytest.skip("dbAccess unavailable")
    library = tmp_path / "probeLib"
    library.mkdir()
    (tmp_path / "cds.lib").write_text(f"DEFINE probeLib {library}\n")
    run_dir = tmp_path / "runs"
    run_dir.mkdir()
    prefix = [
        f"load({json.dumps(str(root.parent / 'sico/skill++/SICO.ils'))})",
        'm3Binding=makeTable("m3Binding" nil) m3Binding[\'instanceId]="instance" '
        "m3Binding['generation]=\"g\" m3Binding['instanceBridge]=t",
        f"m3Binding['cwd]={json.dumps(str(tmp_path))}",
        'sicoBindings["test"]=m3Binding',
    ]
    identity = dict(
        contract=NATIVE_CONTRACT,
        session_id="owner",
        binding_id="lifetime",
        instance_id="instance",
        generation="g",
        cwd=str(tmp_path),
        task_id="task",
    )
    expected = [
        "task:probe",
        "foreground",
        ["probeLib", "cell", "maestro"],
        "maestro",
        "",
        str(library),
        str(run_dir),
    ]

    def request(name, function, values, *, resources=None):
        rid = str(len(prefix)).zfill(32)
        metadata = dict(identity, request_id=rid)
        if resources:
            metadata["resources"] = resources
        line = (
            rid + " circuit_call bound " + function + " "
            + encode_value([values, None, expected])
        )
        line += " @binding " + native_wire(metadata)
        prefix.append(
            "aiWriteTextFile("
            + json.dumps(str(tmp_path / (name + ".json")))
            + ' sicoNativeRequest("test" '
            + json.dumps(line)
            + "))"
        )

    request(
        "read_reserved",
        "aiSimModels",
        ["simulation:missing"],
        resources=[["probeLib", "cell", "maestro", str(library)]],
    )
    request("read_unreserved", "aiSimModels", ["simulation:missing"])
    request(
        "read_alias",
        "aiSimModels",
        ["simulation:missing"],
        resources=[["probeLib", "cell", "maestro", str(tmp_path / "other-library")]],
    )
    request(
        "write_reserved",
        "aiSimRun",
        ["simulation:missing", [], "run1"],
        resources=[["probeLib", "cell", "maestro", str(library)]],
    )
    request("write_unreserved", "aiSimRun", ["simulation:missing", [], "run2"])
    prefix.append("exit()")
    script = tmp_path / "simulation-gate.il"
    script.write_text("\n".join(prefix) + "\n")
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"LD_LIBRARY_PATH", "LD_PRELOAD", "SICO_ROOT"}
    }
    completed = subprocess.run(
        [str(binary), "-nocdsinit", "-load", str(script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=25,
    )
    results = {
        name: json.loads((tmp_path / (name + ".json")).read_text())
        for name in (
            "read_reserved",
            "read_unreserved",
            "read_alias",
            "write_reserved",
            "write_unreserved",
        )
    }
    # A reserved row lets the guarded read reach the retained-setup table.
    assert results["read_reserved"]["ok"] is True
    assert results["read_reserved"]["result"]["circuit"]["code"] == "setup_not_found"
    # Missing or aliased rows stop both reads and writes before function entry.
    for name in ("read_unreserved", "read_alias", "write_unreserved"):
        assert results[name]["ok"] is False, name
        assert results[name]["code"] == "binding_reservation_changed", name
        assert results[name]["diagnostic"]["category"] == "binding"
        assert results[name]["diagnostic"]["function_entered"] is False
        assert results[name]["diagnostic"]["operation_dispatched"] is False
    rejected = results["read_unreserved"]["diagnostic"]["raw_error"]
    assert "requested" in rejected and str(library) in rejected
    assert "reserved" in rejected and "nil" in rejected
    aliased = results["read_alias"]["diagnostic"]["raw_error"]
    assert str(library) in aliased and str(tmp_path / "other-library") in aliased
    # A matching reservation still passes the write gate and reaches the claim check.
    assert results["write_reserved"]["code"] != "binding_reservation_changed"
    assert "durable claim" in results["write_reserved"]["error"]
    assert "unclosed" not in completed.stdout + completed.stderr
