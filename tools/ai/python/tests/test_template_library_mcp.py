"""Public MCP consumers share tier resolution and preserve source-independent plans."""

import json
from pathlib import Path

import pytest
from template_circuit_fixture import geometry_args, persisted_case
from test_template_circuit_transport import NoLiveClient, rpc, unpack
from test_template_library_tiers import fingerprint, install, record
from test_template_library_tiers import roots as reader_roots
from test_template_symbol import plan_args

from cadai.mcp import McpServer
from cadai.template_project import LEVELS, project_record
from cadai.template_schema import TemplateError, arguments

roots = reader_roots


def test_mcp_rule_filter_roundtrips_legacy_capture_and_projection_versions(roots):
    workspace, private, _, _ = roots
    base = record("rule-filter")
    captured = record("capture2-rule-filter")
    captured["rule_version"] = captured["summary"]["rule_version"] = "20260919.capture2.1"
    items = [base, captured, *(project_record(base, level) for level in LEVELS)]
    for index, item in enumerate(items):
        install(private, item, shard=str(index))
    server = McpServer(NoLiveClient(), workspace=workspace)
    listed = unpack(rpc(server, "query_circuit_templates", {"tier": "private"}))
    assert listed["total"] == len(items)
    for row in listed["templates"]:
        version = row["rule_version"]
        selected = unpack(
            rpc(server, "query_circuit_templates", {"tier": "private", "rule_version": version})
        )
        assert {item["template_ref"] for item in selected["templates"]} == {
            item["template_ref"] for item in items if item["rule_version"] == version
        }


@pytest.mark.parametrize(
    "version",
    ["20260919..1", "20260919/capture2.1", "20260919.capture2.", "20260919.capture2.1;",
     "20260919.capture2.1\n", "20260919." + "a" * 32 + ".1"],
)
def test_named_rule_filter_keeps_bounded_version_validation(version):
    with pytest.raises(TemplateError, match="rule_version"):
        arguments("query_circuit_templates", {"rule_version": version})


@pytest.mark.parametrize("tier", ["private", "project", "builtin"])
def test_query_get_prepare_geometry_and_symbol_use_same_tier(roots, tmp_path, monkeypatch, tier):
    workspace, private, project, builtin = roots
    args, item = persisted_case(tmp_path / "seed", monkeypatch)
    root = {"private": private, "project": project, "builtin": builtin}[tier]
    install(root, item)
    before = fingerprint(root)
    server = McpServer(NoLiveClient(), workspace=workspace)
    output = unpack(rpc(server, "query_circuit_templates", {"tier": tier}))
    assert output["total"] == 1
    reference = output["templates"][0]["template_ref"]
    assert reference == args["template_ref"]
    for section in ("summary", "topology", "schematic"):
        detail = unpack(
            rpc(
                server,
                "get_circuit_template",
                {
                    "template_ref": reference,
                    "section": section,
                },
            )
        )
        assert detail["catalog_origin"]["tier"] == tier
    prepared = unpack(rpc(server, "prepare_template_circuit", args))
    geometry = unpack(rpc(server, "preview_circuit_geometry", geometry_args(prepared)))
    assert geometry["plan"]["geometry_checks_passed"]
    assert prepared["plan"]["template_evidence"]["template_ref"] == reference
    assert "catalog_origin" not in json.dumps(prepared["plan"])

    class SymbolClient:
        def call(self, method, values):
            assert method == "eval_skill_native"
            assert values["code"].startswith("aiTemplateSymbolPreview(")
            return True, {"ok": True, "value": json.dumps({"ok": True})}

    symbol = unpack(
        rpc(
            McpServer(SymbolClient(), workspace=workspace),
            "preview_template_symbol",
            {
                **plan_args(),
                "template_ref": reference,
                "task_ref": "task:reader",
                "request_id": "reader",
            },
        )
    )
    assert symbol["plan"]["template_ref"] == reference
    assert all(private in Path(a["path"]).parents for a in symbol["artifacts"])
    assert {k: v for k, v in fingerprint(root).items() if k in before} == before
    if tier != "private":
        assert fingerprint(root) == before


def test_mcp_cross_tier_match_reports_both_origins(roots):
    pytest.importorskip("networkx")
    workspace, private, project, _ = roots
    left, right = record("left"), record("right")
    install(private, left)
    install(project, right)
    output = unpack(
        rpc(
            McpServer(NoLiveClient(), workspace=workspace),
            "match_circuit_template",
            {
                "template_ref": left["template_ref"],
                "target_ref": right["template_ref"],
            },
        )
    )
    assert output["matched"]
    assert output["catalog_origin"]["tier"] == "private"
    assert output["target_origins"]["catalog_origin"]["tier"] == "project"


def test_mcp_catalog_corruption_is_tool_unavailability(roots):
    workspace, _, project, _ = roots
    project.mkdir()
    (project / "catalog.sqlite3").write_text("corrupt")
    output = rpc(McpServer(NoLiveClient(), workspace=workspace), "query_circuit_templates", {})
    assert "error" not in output
    assert output["result"]["isError"]
    assert (
        json.loads(output["result"]["content"][0]["text"])["code"] == "template_catalog_unavailable"
    )
