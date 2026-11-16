"""Read-only DRC rule catalog MCP tool."""

import pytest
from sico_calibre_fixture import make_flow

from cadai.drc_rules import NAME, DrcRulesError, build_catalog
from cadai.drc_rules_handler import dispatch_drc_rules
from cadai.mcp_definitions import TOOLS_BY_NAME


def _flow(tmp_path, *, groups=3):
    flow = make_flow(tmp_path)
    lines = ["LAYER M1"]
    for index in range(groups):
        name = f"G{index + 1}"
        lines.extend((f"GROUP {name} RULE_{index + 1}",
                      f"RULE_{index + 1} {{ LAYER M1 }}"))
    flow.runset.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return flow


def _arguments(flow, tmp_path):
    return {
        "config_path": str(flow.config),
        "work_root": str(tmp_path),
    }


def test_drc_rules_returns_static_groups_layers_and_provenance(tmp_path):
    flow = _flow(tmp_path)
    value = build_catalog(_arguments(flow, tmp_path))
    assert value["schema"] == "cad.drc_rules.v1"
    assert value["top_cell"] == "inv"
    assert value["source"] == "static"
    assert value["warnings"] == ["calibre_not_configured"]
    assert value["counts"] == {"groups": 3, "layers": 1}
    assert [row["name"] for row in value["groups"]] == ["G1", "G2", "G3"]
    assert value["groups"][0]["count"] == 1
    assert value["groups"][0]["members"] == ["RULE_1"]
    assert value["layers"] == ["M1"]
    assert len(value["dependencies_sha256"]) == 64
    assert value["runset"]["path"] == str(flow.runset)
    assert value["page"]["total"] == 3


def test_drc_rules_pages_groups_without_unbounded_output(tmp_path):
    flow = _flow(tmp_path)
    first = build_catalog({**_arguments(flow, tmp_path), "limit": 1})
    assert [row["name"] for row in first["groups"]] == ["G1"]
    assert first["page"]["next"] == 1
    assert first["page"]["truncated"] is True
    last = build_catalog({
        **_arguments(flow, tmp_path),
        "offset": 2,
        "limit": 1,
    })
    assert [row["name"] for row in last["groups"]] == ["G3"]
    assert last["page"]["next"] is None
    assert last["page"]["truncated"] is False


@pytest.mark.parametrize(
    "change, code",
    [
        ({"limit": 0}, "invalid_arguments"),
        ({"offset": -1}, "invalid_arguments"),
        ({"calibre_root": "/does/not/exist"}, "invalid_arguments"),
    ],
)
def test_drc_rules_rejects_invalid_arguments(tmp_path, change, code):
    flow = _flow(tmp_path)
    with pytest.raises(DrcRulesError) as failure:
        build_catalog({**_arguments(flow, tmp_path), **change})
    assert failure.value.code == code


def test_drc_rules_handler_returns_a_stable_error_envelope(tmp_path):
    ok, value = dispatch_drc_rules(NAME, {
        "config_path": "relative.toml",
        "work_root": str(tmp_path),
    })
    assert ok is False
    assert value["code"] == "invalid_arguments"


def test_drc_rules_tool_is_registered_as_read_only():
    tool = TOOLS_BY_NAME[NAME]
    assert tool["inputSchema"]["required"] == ["config_path", "work_root"]
    assert tool["annotations"]["readOnlyHint"] is True
    assert tool["annotations"]["idempotentHint"] is True
