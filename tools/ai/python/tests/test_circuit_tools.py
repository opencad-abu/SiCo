"""Contract and MCP integration tests for the first circuit workflow tools."""
import json
import re
import sqlite3
from pathlib import Path

import pytest

from cadai.circuit_tools import CIRCUIT_TOOL_NAMES, CircuitArgumentError, build_circuit_skill
from cadai.device_catalog import DeviceCatalogUnavailable, query_catalog
from cadai.mcp import McpServer, TOOLS


TARGET = {"library": "basic", "cell": "ipin", "view": "symbol",
          "oa_view_type": "schematicSymbol", "presentation": "foreground"}


class Client:
    def __init__(self):
        self.calls = []

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        return True, {"ok": True, "value": json.dumps({"ok": True, "stage": "preflight"})}


def call(server, name, args):
    return server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": name, "arguments": args}})


@pytest.fixture
def catalog(tmp_path):
    path = tmp_path / "catalog.sqlite3"
    with sqlite3.connect(str(path)) as db:
        db.executescript('''CREATE TABLE metadata(key TEXT, value_json TEXT);
            CREATE TABLE cells(library TEXT, cell TEXT, search_text TEXT, data_json TEXT);
            CREATE TABLE sections(id INTEGER, data_json TEXT);
            CREATE TABLE cell_sections(library TEXT, cell TEXT, section_id INTEGER);''')
        db.execute("INSERT INTO metadata VALUES ('schema_version', '1')")
        for name in ("vdc", "vpulse"):
            record = {"library": "analogLib", "cell": name, "doc_status": "documented",
                      "runtime_status": "captured", "categories": ["Sources"], "description": "source",
                      "guidance": {"purpose_zh": "电压源"}, "common_parameters": ["vdc"], "sources": ["file:///manual"],
                      "runtime": {"views": ["symbol"], "symbol": {"terminals": [{"name": "PLUS"}]},
                                  "base_cdf": [{"name": "vdc", "paramType": "string", "defValue": ""},
                                               {"name": "acm", "paramType": "string", "defValue": ""}]}}
            db.execute("INSERT INTO cells VALUES(?,?,?,?)", ("analogLib", name, "电压源 " + name, json.dumps(record)))
        section = {"source": "file:///manual", "parameters": [{"name": "vdc", "simulator_support": {"spectre": "x"}}]}
        db.execute("INSERT INTO sections VALUES (1, ?)", (json.dumps(section),))
        db.execute("INSERT INTO cell_sections VALUES('analogLib','vdc',1)")
    return path


def test_lookup_paginated_read_only_and_parameter_names(catalog):
    before = catalog.read_bytes()
    first = query_catalog({"query": "电压", "limit": 1}, database=catalog)
    assert first["returned"] == 1 and first["next_offset"] == 1 and first["total"] == 2
    second = query_catalog({"offset": 1}, database=catalog)
    assert second["cells"][0]["cell"] == "vpulse" and second["truncated"] is False
    assert query_catalog({"query": "%"}, database=catalog)["total"] == 0
    assert query_catalog({"cell": "VDC"}, database=catalog)["total"] == 0
    detail = query_catalog({"library": "analogLib", "cell": "vdc", "parameter": "vdc"}, database=catalog)
    params = detail["cells"][0]["parameters"]
    assert params["base_cdf"] == [{"name": "vdc", "paramType": "string", "defValue": ""}]
    assert params["documented"][0]["simulator_support"]["spectre"] == "x"
    assert detail["cells"][0]["symbol"]["terminals"][0]["name"] == "PLUS"
    assert detail["live"] is False and catalog.read_bytes() == before


@pytest.mark.parametrize("args", [{"limit": True}, {"offset": -1}, {"limit": 31}, {"query": " x"},
                                      {"database": "/tmp/arbitrary"}, {"parameter": "vdc"}])
def test_lookup_argument_rejection(args, catalog):
    with pytest.raises(CircuitArgumentError):
        query_catalog(args, database=catalog)


def test_unavailable_or_unsupported_database_is_explicit(tmp_path, catalog):
    with pytest.raises(DeviceCatalogUnavailable):
        query_catalog({}, database=tmp_path / "missing.sqlite3")
    assert not (tmp_path / "missing.sqlite3").exists()
    with sqlite3.connect(str(catalog)) as db:
        db.execute("UPDATE metadata SET value_json='99'")
    with pytest.raises(DeviceCatalogUnavailable, match="schema"):
        query_catalog({}, database=catalog)


def test_live_builds_fixed_calls_and_server_routes():
    assert build_circuit_skill("preflight_circuit_flow", TARGET) == (
        'aiCircuitPreflight("basic" "ipin" "symbol" "schematicSymbol" "foreground")')
    code = build_circuit_skill("open_design_view", {**TARGET, "request_id": "one"})
    assert re.fullmatch(r'aiCircuitOpen\("basic" "ipin" "symbol" "schematicSymbol" "foreground" "read" "one" "design:[a-f0-9]{32}"\)', code)
    assert build_circuit_skill("inspect_design_context", {"context_ref": 'design:a"b'}) == 'aiCircuitInspect("design:a\\"b")'
    client = Client()
    response = call(McpServer(client), "preflight_circuit_flow", TARGET)
    assert response["result"]["isError"] is False
    assert client.calls == [("eval_skill_native", {"code": build_circuit_skill("preflight_circuit_flow", TARGET)})]


@pytest.mark.parametrize("updates", [{"view": "symbol schematic"}, {"library": "x/y"}, {"oa_view_type": "layout"},
    {"presentation": "batch"}, {"access": "w"}, {"request_id": "a\nb"}, {"keep_open": False}])
def test_open_rejects_ambiguous_target_and_unsupported_options(updates):
    with pytest.raises(CircuitArgumentError):
        build_circuit_skill("open_design_view", {**TARGET, "request_id": "one", **updates})


def test_mcp_lookup_uses_site_config_without_virtuoso_call(monkeypatch, catalog):
    monkeypatch.setenv("SICO_AI_DEVICE_CATALOG", str(catalog))
    client = Client()
    response = call(McpServer(client), "query_device_catalog", {"cell": "vdc"})
    detail = json.loads(response["result"]["content"][0]["text"])
    assert detail["returned"] == 1 and client.calls == []
    assert call(McpServer(client), "query_device_catalog", {"parameter": "vdc"})["error"]["code"] == -32602


def test_verification_profile_does_not_expand_with_new_tools():
    server = McpServer(Client(), profile="verification")
    for name in CIRCUIT_TOOL_NAMES:
        assert call(server, name, {})["error"]["code"] == -32602
    definitions = {t["name"]: t for t in TOOLS}
    assert CIRCUIT_TOOL_NAMES <= definitions.keys()
    assert definitions["open_design_view"]["annotations"]["readOnlyHint"] is False
    assert definitions["query_device_catalog"]["annotations"]["readOnlyHint"] is True


def test_catalog_setting_reaches_both_agent_mcp_environments(tmp_path):
    from cadai.launch import build_claude_mcp_config, build_codex_command
    from cadai.runtime import RuntimePaths
    setting = str(tmp_path / "site catalog.sqlite3")
    env = {"SICO_AI_DEVICE_CATALOG": setting}
    command = build_codex_command("/bin/codex", "/bin/python3", tmp_path, tmp_path, 30, environment=env)
    assert any('"SICO_AI_DEVICE_CATALOG"' in value for value in command)
    runtime = RuntimePaths(tmp_path, tmp_path / "socket", tmp_path / "spool")
    config = json.loads(build_claude_mcp_config("/bin/python3", tmp_path / "sico-ai", runtime, "token", 30, environment=env))
    assert config["mcpServers"]["sico_ic"]["env"]["SICO_AI_DEVICE_CATALOG"] == setting
    with pytest.raises(ValueError, match="absolute"):
        build_codex_command("/bin/codex", "/bin/python3", tmp_path, tmp_path, 30,
                            environment={"SICO_AI_DEVICE_CATALOG": "relative.sqlite3"})

def test_device_catalog_legacy_name_still_read_and_new_name_wins(monkeypatch, catalog, tmp_path):
    from cadai.device_catalog import catalog_path

    monkeypatch.delenv("SICO_AI_DEVICE_CATALOG", raising=False)
    monkeypatch.setenv("CAD_AI_DEVICE_CATALOG", str(catalog))
    assert catalog_path() == catalog.resolve()
    newest = tmp_path / "newest.sqlite3"
    monkeypatch.setenv("SICO_AI_DEVICE_CATALOG", str(newest))
    assert catalog_path() == newest.resolve()
