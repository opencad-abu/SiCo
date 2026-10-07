"""Write only source-verified execution rules after conservative suite acceptance."""

from copy import deepcopy

from sicostate import project_directory

from ..pdk_normalize import now
from .jsonio import fail, fingerprint, sha
from .overlay import publish as overlay
from .package import locked
from .workspace import select


def source_id(state):
    return "cdf_validation_" + fingerprint(state["validation_ref"])[7:31]


def recovered(workspace, environment, state):
    current = select(workspace, state["library"], environment)
    row = current.document("device.json")["items"][state["device"]]
    cdf = current.document(row["dir"] + "/cdf.json")
    if cdf.get("evidence", {}).get("/execution") == source_id(state):
        return current.revision
    return None


def publish(workspace, environment, current, row, cdf, state):
    root = project_directory(workspace, "ai/pdk-data", create=True)
    execution = {
        "mode": "cdf",
        "order": state["order"],
        "implementation_ref": "builtin:cdf-callbacks:v1",
    }
    if cdf["execution"].get("mode") != "unknown" and cdf["execution"] != execution:
        fail("Existing execution rule conflicts; review required", "pdk_update_conflict")
    from pathlib import Path

    for case in state["cases"].values():
        artifact = case["artifact"]
        raw = Path(artifact["path"]).read_bytes()
        if len(raw) != artifact["bytes"] or sha(raw) != artifact["sha256"]:
            fail("CDF evidence changed", "pdk_source_changed")
    sources = deepcopy(current.document("sources.json"))
    sid = source_id(state)
    sources["items"][sid] = {
        "kind": "session",
        "at": now(),
        "summary": "Background CDF execution cases and source checks; no legal-domain inference",
        "collector": "sico-pdk-cdf-validation-v1",
        "target": state["target"],
        "evidence_ref": state["validation_ref"],
    }
    document = deepcopy(cdf)
    document["execution"] = execution
    document["evidence"] = {
        k: v
        for k, v in document.get("evidence", {}).items()
        if k != "/execution" and not k.startswith("/execution/")
    }
    document["evidence"]["/execution"] = sid
    with locked(root):
        if select(workspace, state["library"], environment).revision != current.revision:
            fail("Concurrent execution publication", "pdk_update_conflict")
        from .resource_check import check

        for dep in document["depends_on"]:
            if current.manifest["dependencies"][dep]["kind"] in {"file", "model"}:
                check(current, dep)
        return overlay(
            root, current, {row["dir"] + "/cdf.json": document, "sources.json": sources}
        )["revision"]
