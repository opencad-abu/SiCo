"""Synthetic manual and observed CDF sources for mapping workflow acceptance."""

from pdk_standard_fixture import package, replace

from cadai.pdk_standard.ingest import publish as ingest
from cadai.pdk_standard.jsonio import sha
from cadai.pdk_standard.service import StandardData
from cadai.pdk_standard.workspace import select


def setup(workspace, count=2):
    effective, bridge, _, _ = package(workspace, count)
    manual = workspace / "manual.txt"
    manual.write_text("Synthetic PDK manual v1: width and control parameters\n")
    dep = {
        "kind": "file",
        "target": "manual",
        "method": "sha256-bytes-v1",
        "fingerprint": sha(manual.read_bytes()),
    }
    files = effective.document("file.json")
    files["items"]["manual"] = {
        "kind": "document",
        "root": "pdk",
        "path": "manual.txt",
        "purpose": "Synthetic user manual",
        "dependency": "manual_bytes",
    }
    ingest(
        effective.base.root.parent,
        effective.base,
        {"file.json": files},
        dependencies={"manual_bytes": dep},
    )
    service = StandardData(workspace, {"SICO_PDK_WORKFLOW": "generation"}, bridge)
    from cadai.pdk_standard.freshness import check

    service.updates.source_validator = lambda p, diff: check(bridge, p, diff)
    return service, bridge, manual


def source():
    return {"file_ref": "manual", "document_revision": "v1"}


def entry(ident="width", cdf="w", batch="mos", **extra):
    return {
        "entry_id": ident,
        "device": "cell0",
        "cdf": cdf,
        "manual_term": "Width",
        "meaning": "Width of one channel",
        "user_term": "单指宽度",
        "role": "input",
        "locator": {"page": 1, "section": "MOS"},
        "batch_id": batch,
        "status": "candidate",
        **extra,
    }


def create(service, entries=None, manual_source=None):
    package = select(service.workspace, "synthetic", {})
    return service.call(
        "prepare_pdk_parameter_draft",
        {
            "library": "synthetic",
            "revision": package.revision,
            "source": manual_source or source(),
            "entries": entries or [entry()],
        },
    )


def submit(service, draft, ids=None, batch="mos", changes=None):
    current = select(service.workspace, "synthetic", {})
    return service.call(
        "submit_pdk_parameter_batch",
        {
            "draft_ref": draft["draft_ref"],
            "draft_version": draft["draft_version"],
            "revision": current.revision,
            "batch_id": batch,
            "entry_ids": ids or ["width"],
            "changes": changes
            or [
                {
                    "file": "cell0/cdf.json",
                    "ops": [
                        replace("/parameters/w/write", "allow"),
                        replace("/parameters/w/requirement", "explicit"),
                    ],
                }
            ],
        },
    )


def restart(service, bridge):
    result = StandardData(service.workspace, {"SICO_PDK_WORKFLOW": "generation"}, bridge)
    result.updates.source_validator = service.updates.source_validator
    return result


def approve(service, update):
    service.updates.confirmation = lambda _: {
        "decision": "confirm",
        "actor": "fixture reviewer",
        "evidence_ref": "audit:synthetic",
    }
    return service.call("apply_pdk_data_update", {"update_ref": update["update_ref"]})
