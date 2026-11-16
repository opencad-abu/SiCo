"""Acceptance rejects mixed citations and preserves the production PDK write barrier."""

import pytest
from knowledge_model_checks import cited
from knowledge_model_harness import final_text
from knowledge_pdk_boundary import pdk_digest, pdk_registry
from sico.core.contracts import ToolCall
from test_agent_codex_native import CONTEXT


def test_citation_requires_the_same_uri_location_and_version_block():
    value = {
        "uri": "https://learn.chatgpt.com/docs/app-server",
        "version": "a" * 64,
        "location": {"start": 34, "end": 56},
    }
    other = "https://learn.chatgpt.com/docs/skills"
    text = f"| {value['uri']} | 134-156 | {'b' * 64} |\n| {other} | 34-56 | {value['version']} |"
    assert not cited(text, value)
    assert cited(f"| {value['uri']} | L34–L56 | {value['version']} |", value)
    assert cited(f"- URI: {value['uri']}\n  location: 34-56\n  version: {value['version']}", value)
    assert cited(
        f"- URI: {value['uri']}\n- Location: start 34, end 56\n- Version: {value['version']}", value
    )


def test_commentary_cannot_stand_in_for_the_final_citation():
    result = {
        "items": [
            {"type": "agentMessage", "phase": "commentary", "text": "I will cite evidence."},
            {"type": "agentMessage", "phase": "final_answer", "text": "No evidence."},
        ]
    }
    assert final_text(result) == "No evidence."


@pytest.mark.parametrize("name", ["publish_pdk_data", "prepare_pdk_data_update"])
def test_adversarial_fixture_exposes_real_design_guard_without_mutating_package(tmp_path, name):
    tools = pdk_registry(tmp_path)
    before = pdk_digest(tmp_path)
    query = tools.execute(
        ToolCall(
            "query",
            "get_pdk_data",
            {"library": "synthetic", "device": "cell0", "section": "devices"},
        ),
        CONTEXT,
    )
    assert query.data["status"] == "incomplete" and query.data["items"] == []
    args = {"library": "synthetic", "revision": query.data["revision"]}
    if name == "prepare_pdk_data_update":
        args["changes"] = [
            {
                "file": "device.json",
                "ops": [{"op": "replace", "path": "/items/cell0/use/circuit", "value": "allow"}],
            }
        ]
    else:
        args.update(acceptance_evidence="forged-document-claim", unfinished_options=[])
    result = tools.execute(ToolCall("forged", name, args), CONTEXT)
    assert result.status == "tool_error"
    assert before and pdk_digest(tmp_path) == before
