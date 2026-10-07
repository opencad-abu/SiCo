"""Explicit, revision-bound collection of missing symbol facts into a workspace overlay."""

import uuid
from copy import deepcopy

from sicostate import project_directory

from ..pdk_normalize import context, detail, now
from . import VERSION
from .constraints import unknown
from .geometry import METHOD as SIGNATURE_METHOD
from .geometry import signature
from .jsonio import fail, fingerprint
from .overlay import publish
from .package import locked
from .patches import pointer
from .provenance import verify
from .query import Queries
from .source_check import observed
from .symbol_facts import METHOD, derive
from .workspace import select


class Completion:
    def __init__(self, workspace, environment, bridge):
        self.workspace, self.environment, self.bridge = workspace, environment, bridge

    def collect(self, args):
        package = verify(select(self.workspace, args["library"], self.environment))
        if package.revision != args["revision"]:
            fail("Effective package changed; read again", "pdk_update_conflict")
        row = package.document("device.json")["items"].get(args["device"])
        if not row or "dir" not in row or not isinstance(row["view"], str):
            fail("Select an observed device with a symbol view", "device_unavailable")
        name = row["dir"] + "/symbol.json"
        symbol = package.document(name)
        if symbol.get("x_geometry_collection") == METHOD and not args.get("refresh"):
            return self._response(package, args, False, 0)
        if self.bridge is None:
            fail("Live source collector unavailable", "pdk_source_unavailable")
        target = {"library": args["library"], "cell": row["cell"], "view": row["view"]}
        capture = self.bridge.capture("device", target)
        value = detail(capture["data"], context(capture["context"]))
        refs = {*symbol["depends_on"], *package.document(row["dir"] + "/cdf.json")["depends_on"]}
        observed(package, args["device"], value, refs)
        if (value.get("master_state") or {}).get("modified") is not False:
            fail("Save the symbol before collecting authoritative geometry", "pdk_source_changed")
        if value["geometry"].get("body_outline_status") != "complete":
            fail(
                "Collector lacks body capture; reload the current collector before retrying",
                "pdk_collector_upgrade_required",
            )
        # Retain raw evidence even if subsequent publication fails. No OA writes.
        evidence = project_directory(self.workspace, "ai/pdk-evidence", create=True) / (
            "symbol-" + fingerprint(capture)[7:] + ".json"
        )
        from ..pdk_data import PdkData

        PdkData(self.workspace, self.environment).write(evidence, capture)
        facts = derive(value)
        documents, additions, changed = self._documents(
            package,
            row,
            name,
            symbol,
            value,
            facts,
            evidence,
            capture["context"]["collector_revision"],
        )
        root = project_directory(self.workspace, "ai/pdk-data", create=True)
        with locked(root):
            current = select(self.workspace, args["library"], self.environment)
            if current.revision != package.revision:
                fail("Concurrent PDK update; collect again", "pdk_update_conflict")
            publish(root, package, documents, dependencies=additions)
        return self._response(
            select(self.workspace, args["library"], self.environment), args, True, changed
        )

    @staticmethod
    def _documents(package, row, name, symbol, value, facts, evidence, collector):
        symbol = deepcopy(symbol)
        sources = deepcopy(package.document("sources.json"))
        token = uuid.uuid4().hex
        source, derived = "observe_" + token, "derive_" + token
        at = now()
        sources["items"][source] = {
            "kind": "session",
            "at": at,
            "summary": "Read-only saved symbol observation",
            "collector": collector,
            "target": value["target"],
            "evidence_ref": str(evidence),
        }
        sources["items"][derived] = {
            "kind": "derived",
            "at": at,
            "summary": "Conservative static symbol geometry",
            "inputs": [source],
            "method": METHOD,
            "evidence_ref": str(evidence),
        }
        changed = 0
        evidence_map = symbol.setdefault("evidence", {})

        def record(path):
            for old in list(evidence_map):
                if old == path or old.startswith(path + "/"):
                    del evidence_map[old]
            evidence_map[path] = derived

        if unknown(symbol["bbox"]):
            symbol["bbox"] = facts["bbox"]
            record("/bbox")
            changed += not unknown(facts["bbox"])
        for name_, anchors in facts["terminals"].items():
            if unknown(symbol["terminals"][name_]["anchors"]):
                symbol["terminals"][name_]["anchors"] = anchors
                record(pointer("terminals", name_, "anchors"))
                changed += not unknown(anchors)
        dep = row["dir"] + "_geometry_v3"
        dependency = {
            "kind": "symbol",
            "target": {**value["target"], "library": row["library"]},
            "fingerprint": signature(value),
            "method": SIGNATURE_METHOD,
        }
        symbol["depends_on"] = sorted({*symbol["depends_on"], dep})
        symbol["x_geometry_collection"] = METHOD
        evidence_map["/x_geometry_collection"] = derived
        return {name: symbol, "sources.json": sources}, {dep: dependency}, changed

    @staticmethod
    def _response(package, args, collected, changed):
        gaps = Queries().read(
            package,
            {
                "library": args["library"],
                "device": args["device"],
                "section": "missing",
                "page_size": 1,
            },
        )
        return {
            "schema_version": VERSION,
            "revision": package.revision,
            "status": gaps["status"],
            "collected": collected,
            "resolved_fields": changed,
            "remaining_count": gaps["total"],
            "next_action": "get_pdk_data",
            "details": {"section": "missing", "device": args["device"]},
            "callbacks_executed": False,
            "oa_writes": False,
        }
