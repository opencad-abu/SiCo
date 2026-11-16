"""Complete a saved capture through the public extraction tool without a live round trip."""

import json
from pathlib import Path

import pytest
from template_extract_fixture import request
from test_template_circuit_transport import rpc, unpack
from test_template_extract_reuse import extract

from cadai.mcp import McpServer
from cadai.template_catalog import TemplateCatalog, call_template
from cadai.template_schema import TemplateError, TemplateUnavailable


def partial(tmp_path):
    args = request()
    missing = args["classifications"].pop("M1")
    client, _, result = extract(tmp_path, args=args)
    completion = result["continuation"]["arguments"] | dict(classifications={"M1": missing})
    return client, result, completion


def test_complete_only_missing_evidence_offline_and_keep_original(tmp_path):
    client, original, args = partial(tmp_path)
    paths = [Path(original["package"]) / "package.json", Path(original["capture"])]
    before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths]
    server = McpServer(None, workspace=tmp_path)
    completed = unpack(rpc(server, "extract_circuit_templates", args))
    assert completed["ok"] and completed["live"] is False
    assert completed["completed_from"] == original["template_ref"]
    assert len(client.calls) == 1
    assert [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths] == before
    # Same raw + merged evidence + intent produces the same result as live extraction.
    live = call_template("extract_circuit_templates", request(), workspace=tmp_path, client=client)
    assert completed["template_ref"] == live["template_ref"]
    assert unpack(rpc(server, "extract_circuit_templates", args)) == completed


@pytest.mark.parametrize("fault", ["missing", "modified", "symlink", "replace", "source", "assets",
                                  "no_reuse", "wrong_instance"])
def test_completion_cannot_change_pinned_evidence_or_bypass_raw_integrity(tmp_path, fault):
    client, result, args = partial(tmp_path)
    raw = Path(result["capture"])
    if fault == "missing":
        raw.unlink()
    elif fault == "modified":
        rows = [json.loads(line) for line in raw.read_text().splitlines()]
        rows[1]["tool_version"] = "modified-version"
        raw.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    elif fault == "symlink":
        raw.unlink()
        raw.symlink_to(client.path)
    elif fault == "replace":
        args["classifications"]["M0"] = args["classifications"]["M1"]
    elif fault == "source":
        args["library"] = "another"
    elif fault == "assets":
        args["assets"] = ["schematic"]
    elif fault == "no_reuse":
        args.pop("reuse")
    else:
        args["classifications"]["absent"] = args["classifications"].pop("M1")
    with pytest.raises((TemplateError, TemplateUnavailable)):
        call_template("extract_circuit_templates", args, workspace=tmp_path, client=client)
    assert len(client.calls) == 1
    assert len(list(TemplateCatalog(workspace=tmp_path).root.rglob("package.json"))) == 1


def test_insufficient_completion_remains_actionable_and_offline(tmp_path):
    client, result, args = partial(tmp_path)
    args.pop("classifications")
    response = rpc(McpServer(None, workspace=tmp_path), "extract_circuit_templates", args)
    assert response["result"]["isError"]
    failed = json.loads(response["result"]["content"][0]["text"])
    assert failed["template_ref"] == result["template_ref"]
    assert failed["reuse"]["status"] == "needs_classification"
    assert failed["reuse"]["issues"][0]["object_ref"] == "M1"
    assert failed["live"] is False and len(client.calls) == 1
