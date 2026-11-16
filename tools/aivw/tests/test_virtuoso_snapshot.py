from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from aivworkbench import cdns_ipc
from aivworkbench.executor import execute_workflow
from aivworkbench.executors import virtuoso
from aivworkbench.recipe import load_builtin_recipe
from aivworkbench.registry import builtin_registry
from aivworkbench.cdns_ipc import (
    McpToolCall,
    McpToolResult,
    CdnsIpcUnavailable,
)
from aivworkbench.workspace import allocate_split_run, resolve_launch_paths, stable_digest


def _run(tmp_path: Path):
    launch = tmp_path / "launch"
    payload = tmp_path / "payload"
    launch.mkdir()
    payload.mkdir()
    return allocate_split_run(
        resolve_launch_paths(launch),
        payload,
        library="saradc",
        cell="comparator_new",
        view="rnm",
        kind="snapshot-test",
        request_digest=stable_digest({"snapshot": "test"}),
    )


def _symbol() -> dict:
    ports = [
        ("out", "output", "wreal"),
        ("outb", "output", "wreal"),
        ("Din+", "input", "wreal"),
        ("Din-", "input", "wreal"),
        ("clk", "input", "logic"),
    ]
    return {
        "ok": True,
        "kind": "symbol",
        "cellview": {"lib": "saradc", "cell": "comparator_new", "view": "symbol"},
        "modified": False,
        "terminals": [
            {
                "name": name,
                "direction": direction,
                "data_type": data_type,
                "port_kind": "wire",
            }
            for name, direction, data_type in ports
        ],
        "port_order": [item[0] for item in ports],
        "term_order": [],
        "max_items": 100,
        "truncated": False,
    }


def _schematic() -> dict:
    ports = [
        ("out", "output", "wreal"),
        ("outb", "output", "wreal"),
        ("Din+", "input", "wreal"),
        ("Din-", "input", "wreal"),
        ("clk", "input", "logic"),
    ]
    return {
        "ok": True,
        "kind": "schematic",
        "cellview": {
            "lib": "saradc",
            "cell": "comparator_new",
            "view": "schematic",
        },
        "modified": False,
        "instances": [],
        "nets": [
            {
                "name": name,
                "bits": 1,
                "signal_type": "digital" if data_type == "logic" else "analog",
                "global": False,
            }
            for name, _direction, data_type in ports
        ],
        "terminals": [
            {
                "name": name,
                "direction": direction,
                "data_type": data_type,
                "port_kind": "wire",
                "pins": [{"net": name}],
            }
            for name, direction, data_type in ports
        ],
        "max_items": 100,
        "truncated": False,
    }


def _payloads() -> list[dict]:
    context = {
        "ok": True,
        "cwd": "/workarea/xh/amsVerify",
        "window": "window:1",
        "display_cellview": {
            "lib": "saradc",
            "cell": "comparator_new",
            "view": "schematic",
        },
        "edit_cellview": {
            "lib": "saradc",
            "cell": "comparator_new",
            "view": "schematic",
        },
        "selection_count": 0,
    }
    library = {
        "ok": True,
        "kind": "library",
        "name": "saradc",
        "path": "/workarea/xh/amsVerify/data/saradc",
        "technology_library": "gpdk045",
        "truncated": False,
    }
    schematic = _schematic()
    symbol = _symbol()
    return [
        context,
        library,
        schematic,
        symbol,
        deepcopy(schematic),
        deepcopy(symbol),
        deepcopy(library),
        deepcopy(context),
    ]


def _install_payloads(monkeypatch, payloads: list[dict]) -> None:
    def fake_call(calls, *, environment, cad_ai_entry, timeout):
        assert len(calls) == len(payloads)
        assert timeout > 0
        return tuple(
            McpToolResult(call.name, payload) for call, payload in zip(calls, payloads)
        )

    monkeypatch.setattr(virtuoso, "_call_snapshot_tools", fake_call)


def test_authenticated_snapshot_double_reads_saved_source_and_normalizes(
    tmp_path: Path, monkeypatch
) -> None:
    _install_payloads(monkeypatch, _payloads())
    recipe = load_builtin_recipe("saradc.comparator_new")
    run = _run(tmp_path)

    result = execute_workflow(
        recipe,
        builtin_registry(),
        run,
        environment={"PATH": "/usr/bin:/bin"},
        targets=("snapshot",),
    )

    snapshot = result.gates["snapshot"]
    assert result.status == "PASS"
    assert snapshot.status == "PASS"
    assert snapshot.outputs["structure_authoritative"] is False
    assert len(snapshot.outputs["source_generation"]) == 64
    assert snapshot.outputs["target"] == {
        "library": "saradc",
        "cell": "comparator_new",
        "module": "comparator_new",
    }
    assert snapshot.outputs["view_identity"] == {
        "library": "saradc",
        "cell": "comparator_new",
        "view": "schematic",
        "view_type": "schematic",
        "kind": "schematic",
    }
    normalized = run.payload_root / str(snapshot.outputs["normalized_structure"])
    assert snapshot.outputs["normalized_sha256"] == hashlib.sha256(
        normalized.read_bytes()
    ).hexdigest()
    payload = json.loads(normalized.read_text(encoding="utf-8"))
    assert payload["status"] == "PASS"
    assert payload["cells"][0]["ports"] == ["out", "outb", "Din+", "Din-", "clk"]
    evidence = json.loads(
        (run.payload_root / str(snapshot.outputs["snapshot_evidence"])).read_text(
            encoding="utf-8"
        )
    )
    assert evidence["formal_saved_state"] is True
    assert evidence["double_read_equal"] is True
    assert evidence["structure_authority"] == "preliminary_snapshot_not_cadence_si"


@pytest.mark.parametrize(
    ("mutate", "status", "code"),
    [
        (lambda values: values[2].update(truncated=True), "BLOCKED_INPUT", "truncated_inspection"),
        (
            lambda values: values[3].update(modified=True),
            "BLOCKED_UNSAVED_SOURCE",
            "unsaved_or_unknown_source",
        ),
        (
            lambda values: values[4]["nets"].append(
                {"name": "changed", "bits": 1, "signal_type": "analog", "global": False}
            ),
            "STALE_SOURCE",
            "source_changed",
        ),
    ],
)
def test_snapshot_fails_closed_for_incomplete_unsaved_or_changing_source(
    mutate, status: str, code: str, tmp_path: Path, monkeypatch
) -> None:
    payloads = _payloads()
    mutate(payloads)
    _install_payloads(monkeypatch, payloads)
    result = execute_workflow(
        load_builtin_recipe("saradc.comparator_new"),
        builtin_registry(),
        _run(tmp_path),
        targets=("snapshot",),
    )

    assert result.status == "BLOCKED_WORKFLOW"
    assert result.gates["snapshot"].status == status
    assert result.gates["snapshot"].summary["code"] == code


def test_ipc_adapter_requires_delegated_private_session_before_starting_child(
    tmp_path: Path,
) -> None:
    with pytest.raises(CdnsIpcUnavailable) as error:
        cdns_ipc.call_read_only_tools(
            (McpToolCall("get_context", {}),),
            environment={},
            cad_ai_entry=tmp_path / "sico-ai",
            timeout=1.0,
        )
    assert error.value.code == "session_credentials_unavailable"


def test_ipc_adapter_refuses_non_read_only_tool(tmp_path: Path) -> None:
    with pytest.raises(CdnsIpcUnavailable) as error:
        cdns_ipc.call_read_only_tools(
            (McpToolCall("eval_skill", {"code": "1+1"}),),
            environment={},
            cad_ai_entry=tmp_path / "sico-ai",
            timeout=1.0,
        )
    assert error.value.code == "unsafe_tool"


def test_compiled_ipc_executes_native_client_without_source_or_site_python(tmp_path, monkeypatch):
    from types import SimpleNamespace

    entry = tmp_path / "aiassistant"
    entry.write_bytes(b"\x7fELF")
    monkeypatch.setattr(cdns_ipc, "_validate_session_environment", lambda env: "test-token")
    monkeypatch.setattr(cdns_ipc, "compiled_module", lambda filename: True)
    monkeypatch.setattr(cdns_ipc, "native_entry", lambda *args, **kwargs: entry)
    monkeypatch.setattr(cdns_ipc, "production_python",
                        lambda **kwargs: pytest.fail("Native IPC must not select site Python"))
    commands = []

    def execute(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({
            "jsonrpc": "2.0", "id": "aivw-read-000", "result": {
                "isError": False, "content": [{"type": "text", "text": '{"ok":true}'}]}}) + "\n")

    monkeypatch.setattr(cdns_ipc.subprocess, "run", execute)
    result = cdns_ipc.call_read_only_tools((McpToolCall("get_context", {}),),
        environment={}, cad_ai_entry=tmp_path / "missing-source", timeout=2)
    assert commands == [(str(entry), "mcp", "--request-timeout", "2")]
    assert result[0].payload == {"ok": True}
