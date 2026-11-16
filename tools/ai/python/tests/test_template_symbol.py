"""Symbol interface mapping, determinism, budgets and MCP boundary tests."""

import copy
import json
from pathlib import Path

import pytest
from test_circuit_templates import legacy_rows

from cadai.mcp import McpServer
from cadai.template_build import make_template, publish
from cadai.template_capture import parse_capture
from cadai.template_catalog import TemplateCatalog
from cadai.template_graph import match
from cadai.template_schema import TemplateError, TemplateUnavailable, canonical
from cadai.template_symbol import TOOL_NAMES, call_symbol
from cadai.template_symbol_plan import make_plan, members


def example_record():
    rows = legacy_rows()
    return make_template(parse_capture(("\n".join(canonical(r) for r in rows) + "\n").encode()))


def plan_args(**values):
    return {"library": "symbolWork", "cell": "Amplifier", "port_map": {}, **values}


def test_symbol_plan_deterministic_and_sizing_free():
    record = example_record()
    first = make_plan(record, plan_args(port_map={"A": "VIN", "Y": "VOUT"}))
    assert [r[0] for r in first["rows"]] == ["VIN", "VOUT"]
    assert first == make_plan(record, plan_args(port_map={"Y": "VOUT", "A": "VIN"}))
    assert first["preview_digest"] != make_plan(record, plan_args())["preview_digest"]
    assert first["interface_basis"] == "schematic_template"
    assert first["rows"][0][5] < 0 < first["rows"][1][5]
    assert all(p["global_net"] is None for p in first["ports"])


def test_analog_terminal_names_allow_sign_suffix():
    record = example_record()
    record["topology"]["ports"][0]["name"] = "Din+"
    record["topology"]["ports"][1]["name"] = "Din-"
    plan = make_plan(record, plan_args())
    assert [row[0] for row in plan["rows"]] == ["Din+", "Din-"]
    assert members("Din+") == (["Din+"], "")
    assert members("Din-") == (["Din-"], "")


@pytest.mark.parametrize(
    "mapping", [{"unknown": "P"}, {"A": "Y"}, {"A": 'bad"()'}, {"A": "A<1:0>"}, {"A": "A!"}]
)
def test_symbol_map_refusals(mapping):
    with pytest.raises(TemplateError):
        make_plan(example_record(), plan_args(port_map=mapping))


def test_bus_global_directions_and_missing_semantics():
    record = example_record()
    record["topology"]["ports"][0].update(name="DATA<7:0>", num_bits=8)
    record["topology"]["nets"][0]["num_bits"] = 8
    with pytest.raises(TemplateUnavailable, match="recapture"):
        make_plan(record, plan_args())
    assert match(record["topology"], record["topology"])["status"] == "inconclusive"
    record["source"]["net_global_semantics"] = "uniform_boolean_v1"
    p = make_plan(record, plan_args(port_map={"DATA<7:0>": "DIN<7:0>"}))
    assert p["ports"][0]["num_bits"] == 8
    for rename in ("DIN<0:7>", "DIN<8:1>", "DIN"):
        with pytest.raises(TemplateError):
            make_plan(record, plan_args(port_map={"DATA<7:0>": rename}))
    record["topology"]["nets"][0].update(is_global=True, source_name="VDD!", sig_type="supply")
    record["topology"]["ports"][0].update(name="VPWR", num_bits=1, direction="inputOutput")
    p = make_plan(record, plan_args())
    assert p["ports"][0]["global_net"] == "VDD!"
    assert p["rows"][0][1] == "inputOutput"
    record["topology"]["nets"][0]["is_global"] = None
    with pytest.raises(TemplateUnavailable, match="incomplete"):
        make_plan(record, plan_args())


def test_symbol_overlap_and_limits():
    record = example_record()
    record["topology"]["ports"][0].update(name="A<3:0>", num_bits=4)
    record["topology"]["ports"][1].update(name="A<2>")
    with pytest.raises(TemplateError, match="overlapping"):
        make_plan(record, plan_args())
    with pytest.raises(TemplateError, match="budget"):
        members("A<1000000:0>")
    record["topology"]["ports"] *= 100
    with pytest.raises(TemplateUnavailable, match="128"):
        make_plan(record, plan_args())
    record = example_record()
    record["topology"]["ports"][0]["direction"] = "switch"
    with pytest.raises(TemplateUnavailable, match="direction"):
        make_plan(record, plan_args())


def test_mcp_symbol_adapter_preview_and_create(tmp_path, monkeypatch):
    monkeypatch.setenv("SICO_CIRCUIT_TEMPLATES_DIR", str(tmp_path))
    record = example_record()
    publish([record], tmp_path / "catalog.sqlite3")
    catalog = TemplateCatalog(tmp_path)
    assert (
        catalog.query({"rule_version": record["rule_version"], "offset": 0, "limit": 1})["total"]
        == 1
    )
    assert catalog.query({"rule_version": "19000101.1", "offset": 0, "limit": 1})["total"] == 0

    class Client:
        def __init__(self):
            self.seen = []

        def call(self, method, args):
            self.seen.append((method, args))
            return True, {"ok": True, "value": json.dumps({"ok": True, "stage": "previewed"})}

    client = Client()
    args = {
        **plan_args(),
        "template_ref": record["template_ref"],
        "task_ref": "task:test",
        "request_id": "test",
    }
    result = call_symbol("preview_template_symbol", args, workspace=tmp_path, client=client)
    assert result["ok"] and result["plan"]["template_ref"] == record["template_ref"]
    assert all(Path(a["path"]).is_file() for a in result["artifacts"])
    assert client.seen[0][0] == "eval_skill_native"
    assert 'aiTemplateSymbolPreview("task:test" "test"' in client.seen[0][1]["code"]
    # Create uses the retained preview; it does not reread mutable catalog/config data.
    monkeypatch.setenv("SICO_CIRCUIT_TEMPLATES_DIR", str(tmp_path / "absent"))
    call_symbol(
        "create_template_symbol",
        {
            "preview_ref": "template-symbol-preview:test",
            "preview_digest": result["plan"]["preview_digest"],
            "request_id": "create",
        },
        workspace=tmp_path,
        client=client,
    )
    assert "aiTemplateSymbolCreate(" in client.seen[1][1]["code"]
    count = len(client.seen)
    server = McpServer(client, workspace=tmp_path)
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "create_template_symbol",
                "arguments": {"preview_ref": "bad", "request_id": "new"},
            },
        }
    )
    assert response["error"]["code"] == -32602 and len(client.seen) == count
    restricted = McpServer(client, workspace=tmp_path, profile="verification")
    listed = restricted.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert not TOOL_NAMES & {t["name"] for t in listed["result"]["tools"]}


def stress_record():
    """Synthetic interface fixture; never presented as an electrically qualified circuit."""
    record = copy.deepcopy(example_record())
    record["source"]["net_global_semantics"] = "uniform_boolean_v1"
    rows = [
        ("VDD", "inputOutput", 1, "VDD!", "supply"),
        ("VSS", "inputOutput", 1, "VSS!", "ground"),
        ("VINP", "input", 1, None, "signal"),
        ("VINN", "input", 1, None, "signal"),
        ("DATA<7:0>", "input", 8, None, "signal"),
        ("LONG_CONTROL_LABEL_0123456789", "input", 1, None, "signal"),
        ("OUTP", "output", 1, None, "signal"),
        ("OUTN", "output", 1, None, "signal"),
    ]
    record["topology"]["ports"] = [
        {"id": "p" + str(i), "name": n, "direction": d, "num_bits": b, "net": "n" + str(i)}
        for i, (n, d, b, g, s) in enumerate(rows)
    ]
    record["topology"]["nets"] = [
        {
            "id": "n" + str(i),
            "source_name": g or n,
            "is_global": bool(g),
            "num_bits": b,
            "sig_type": s,
        }
        for i, (n, d, b, g, s) in enumerate(rows)
    ]
    record["template_ref"] = "tpl_" + "b" * 64
    record["summary"]["template_ref"] = record["template_ref"]
    return record
