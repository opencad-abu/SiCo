"""Explicit, revision-bound collection of missing symbol facts into a workspace overlay."""

import uuid
from copy import deepcopy
from pathlib import Path

from sicostate import project_directory

from ..pdk_errors import PdkUnavailable
from ..pdk_normalize import context, detail, now
from . import VERSION
from .constraints import unknown
from .geometry import METHOD as SIGNATURE_METHOD
from .geometry import signature
from .jsonio import fail, fingerprint, sha
from .overlay import publish
from .package import locked
from .patches import pointer
from .provenance import verify
from .query import Queries
from .source_check import observed
from .symbol_facts import METHOD, derive
from .workspace import select


class Pending:
    """Read-only package view that includes documents collected in this turn."""

    def __init__(self, package, documents):
        self.package, self.documents = package, documents

    @property
    def roots(self):
        return self.package.roots

    @property
    def manifest(self):
        return self.package.manifest

    @property
    def revision(self):
        return self.package.revision

    def available(self, name):
        return name in self.documents or self.package.available(name)

    def document(self, name):
        if name in self.documents:
            return self.documents[name]
        return self.package.document(name)


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
        simulation_name = row["dir"] + "/simulation.json"
        netlist_name = args.get("netlist_name")
        identity_evidence = args.get("identity_evidence")
        if bool(netlist_name) != bool(identity_evidence):
            fail("netlist_name and identity_evidence must be supplied together")
        if netlist_name:
            if not isinstance(netlist_name, str) or not netlist_name or not isinstance(
                    identity_evidence, str) or not Path(identity_evidence).is_file():
                fail("A ruled netlist override needs its retained scope record")
        builtin_evidence = args.get("builtin_evidence")
        probe = None
        if builtin_evidence is not None:
            if not isinstance(builtin_evidence, str) or not builtin_evidence:
                fail("builtin_evidence must name a recorded builtin probe file")
            from .simulation_builtin import load as load_builtin
            probe = load_builtin(builtin_evidence)
        symbol = package.document(name)
        from .simulation_collection import entries as deck_entries

        simulation_possible = package.available(simulation_name) or all(
            deck_entries(package, simulator) for simulator in ('hspiceD', 'spectre')) or bool(
            (probe or {}).get('devices', {}).get(row['cell']))
        needs_simulation = simulation_possible and not package.available(simulation_name)
        if (symbol.get("x_geometry_collection") == METHOD
                and not needs_simulation and not args.get("refresh")):
            return self._response(package, args, False, 0)
        if self.bridge is None:
            fail("Live source collector unavailable", "pdk_source_unavailable")
        target = {"library": args["library"], "cell": row["cell"], "view": row["view"]}
        capture = self.bridge.capture("device", target)
        value = detail(capture["data"], context(capture["context"]))
        refs = {*symbol["depends_on"], *package.document(row["dir"] + "/cdf.json")["depends_on"]}
        try:
            observed(package, args["device"], value, refs)
        except PdkUnavailable as exc:
            if exc.code == "pdk_source_changed":
                fail(str(exc) + "; run get_pdk_source_impact and refresh the PDK baseline "
                     "before collecting this device", exc.code)
            raise
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
        documents, additions, changed, notes = self._documents(
            package,
            row,
            name,
            symbol,
            value,
            facts,
            evidence,
            capture["context"]["collector_revision"],
            netlist_name,
            identity_evidence,
            probe,
        )
        root = project_directory(self.workspace, "ai/pdk-data", create=True)
        with locked(root):
            current = select(self.workspace, args["library"], self.environment)
            if current.revision != package.revision:
                fail("Concurrent PDK update; collect again", "pdk_update_conflict")
            publish(root, package, documents, dependencies=additions)
        return self._response(
            select(self.workspace, args["library"], self.environment), args, True, changed, notes
        )

    @staticmethod
    def _documents(package, row, name, symbol, value, facts, evidence, collector,
                   netlist_name=None, identity_evidence=None, probe=None):
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
        if probe is not None:
            # One recorded simulator-builtin probe backs every builtin claim
            # this collection writes; the deterministic source ids make a
            # repeated collection reuse the same run record.
            for simulator in ('hspiceD', 'spectre'):
                run = probe['runs'][simulator]
                sources["items"].setdefault(probe['sources'][simulator], {
                    "kind": "simulation",
                    "at": at,
                    "summary": "Recorded simulator-builtin netlist probe",
                    "run_ref": run["run_ref"],
                    "simulator": simulator + " " + run["version"],
                    "evidence_ref": probe["path"],
                })
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
        documents = {name: symbol, "sources.json": sources}
        additions = {dep: dependency}
        notes = []
        from .model_collection import METHOD as MODEL_METHOD
        from .model_collection import ensure as ensure_models

        model_source = "model_deck_derivation"
        pending = ensure_models(package, model_source)
        view = package
        if pending:
            if pending["source"] and pending["document"]:
                sources["items"][model_source] = {
                    "kind": "derived",
                    "at": at,
                    "summary": "Model configurations and corners derived from "
                               "registered vendor deck sections",
                    "inputs": [source],
                    "method": MODEL_METHOD,
                    "evidence_ref": str(evidence),
                }
            if pending["document"]:
                documents["model.json"] = pending["document"]
            if pending["files"] != package.document("file.json")["items"]:
                file_document = deepcopy(package.document("file.json"))
                file_document["items"] = pending["files"]
                documents["file.json"] = file_document
            additions.update(pending["dependencies"])
            notes.extend(pending["issues"])
            if pending["document"] or "file.json" in documents:
                view = Pending(package, documents)
        if not package.available(row["dir"] + "/simulation.json"):
            from .simulation_collection import build, build_builtin

            ruling = None
            if netlist_name:
                # A user-ruled vendor anomaly: the manual documents the netlist
                # identity while the CDF default has no deck anywhere; record
                # the ruling as its own user source and keep the CDF untouched.
                blob = Path(identity_evidence).read_bytes()
                ruling = "confirm_" + token
                sources["items"][ruling] = {
                    "kind": "user",
                    "at": at,
                    "summary": "Confirmed netlist identity override: " + netlist_name,
                    "confirmed_by": "current_conversation_user",
                    "scope_ref": identity_evidence,
                    "scope_digest": sha(blob),
                    "evidence_ref": identity_evidence,
                }
                sources["items"][derived]["inputs"] = [source, ruling]
            options = {"net_name": netlist_name}
            if probe is not None:
                options["setup"] = probe.get("setup")
            simulation, interface_dependency, note = build(
                view, row, value, derived, derived, **options)
            if simulation is None and probe is not None and not netlist_name:
                # No decks resolved: the recorded builtin probe is the only
                # remaining evidence for a support-library primitive.
                simulation, interface_dependency, note = build_builtin(
                    view, row, value, derived, derived, probe)
            if simulation:
                if ruling:
                    for simulator in simulation["interfaces"]:
                        simulation.setdefault("evidence", {})[
                            pointer("interfaces", simulator, "name")] = ruling
                documents[row["dir"] + "/simulation.json"] = simulation
                additions.update(interface_dependency)
                changed += 1
            else:
                sources["items"][derived]["summary"] += "; simulation deferred: " + note
                notes.append({"code": "simulation_not_collected", "device": row["cell"],
                              "message": note})
        return documents, additions, changed, notes

    @staticmethod
    def _response(package, args, collected, changed, notes=()):
        from .acceptance_state import collection_complete
        acceptance_required = collection_complete(package)
        from .blockers import report as blocker_report

        blockers = blocker_report(package)
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
            "status": "acceptance_required" if acceptance_required else gaps["status"],
            "collected": collected,
            "resolved_fields": changed,
            "remaining_count": gaps["total"],
            "acceptance_required": acceptance_required,
            "next_action": ("accept_pdk_data" if acceptance_required else
                            blockers["next_action"] if blockers["publication_blockers"] else
                            "get_pdk_data"),
            "details": {"section": "missing", "device": args["device"]},
            "notes": list(notes),
            "blocking_root_causes": [row["code"] for row in blockers["publication_blockers"]],
            "callbacks_executed": False,
            "oa_writes": False,
        }
