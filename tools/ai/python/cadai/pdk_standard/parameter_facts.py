"""Plan small objective CDF enrichments with explicit evidence and conflict checks."""

from copy import deepcopy

from ..pdk_normalize import now
from . import validate
from .draft_sources import manual
from .fact_domains import completeness, conditional_cases
from .fact_units import scale, unit
from .interface import collection_parameters
from .jsonio import fail, fingerprint
from .patches import pointer
from .provenance import source_for


def unresolved(value):
    return isinstance(value, dict) and (
        value.get("state") == "unknown" or value.get("kind") == "unknown"
    )


class ParameterFacts:
    def __init__(self, package, facts, report_ref):
        self.package, self.facts, self.report_ref = package, facts, report_ref
        self.documents, self.dependencies, self.fields, self.records = {}, {}, [], []
        self.sources = deepcopy(package.document("sources.json"))
        self.active_id = None

    def plan(self):
        devices = self.package.document("device.json")["items"]
        ids, targets, established = set(), set(), []
        for fact in self.facts:
            self.active_id = fact["id"]
            target = (fact["device"], fact["parameter"], fact["field"])
            changes = fact["disposition"] == "established" or (
                fact["disposition"] == "unresolved" and "value" in fact
            )
            if fact["id"] in ids or (changes and target in targets):
                fail("Duplicate fact ID or established target")
            ids.add(fact["id"])
            row = devices.get(fact["device"], {})
            if "dir" not in row:
                fail("Fact requires an observed device/CDF")
            name = row["dir"] + "/cdf.json"
            original = self.package.document(name)
            inputs, scope = collection_parameters(original)
            parameter = fact["parameter"]
            if parameter not in scope:
                fail("Detailed facts are restricted to confirmed inputs and necessary dependencies")
            if parameter not in inputs and fact["field"] not in {"type", "unit", "default"}:
                fail("Auxiliary dependencies accept only necessary type/unit/default facts")
            # All records, including contextual constraints, have an actual CDF binding.
            for ref in original["depends_on"]:
                self.dependencies[ref] = self.package.manifest["dependencies"][ref]
            identity = manual(
                self.package, {k: fact["source"][k] for k in ("file_ref", "document_revision")}
            )
            self.dependencies[identity["dependency"]] = self.package.manifest["dependencies"][
                identity["dependency"]
            ]
            record = {**deepcopy(fact), "source_identity": identity}
            self.records.append(record)
            pending_domain = fact["disposition"] == "unresolved" and "value" in fact
            if pending_domain and (
                fact["field"] != "domain"
                or fact["basis"] != "incomplete"
                or fact["value"] != {"kind": "unknown", "reason": fact["reason"]}
            ):
                fail("Unresolved updates only accept an unknown domain with the recorded reason")
            if fact["disposition"] != "established" and not pending_domain:
                if fact["disposition"] == "context" and "value" not in fact:
                    fail("Context record requires its observed value")
                record["result"] = "retained_" + fact["disposition"]
                continue
            if "value" not in fact:
                fail("Established fact requires value")
            if fact["disposition"] == "established" and unresolved(fact["value"]):
                fail("Unknown facts cannot be established")
            if not pending_domain and fact["basis"] in {
                "pcell_limit",
                "model_characterization",
                "recommendation",
                "incomplete",
            }:
                fail("Contextual/partial bounds cannot be promoted to effective facts")
            if name not in self.documents:
                self.documents[name] = deepcopy(original)
            targets.add(target)
            established.append((name, record))
        # Apply definitions before numeric facts, independent of request ordering.
        established.sort(key=lambda pair: {"type": 0, "unit": 1}.get(pair[1]["field"], 2))
        for name, record in established:
            self.active_id = record["id"]
            self.field(name, record)
        for name, record in established:
            self.active_id = record["id"]
            p = self.documents[name]["parameters"][record["parameter"]]
            if record["disposition"] == "established":
                completeness(record, p)
            validate.document(name, self.documents[name])
            # Changing a selector may invalidate another parameter's conditional domain.
            for parameter in self.documents[name]["parameters"]:
                conditional_cases(self.documents[name]["parameters"], parameter)
        self.documents = {n: d for n, d in self.documents.items() if d != self.package.document(n)}
        if self.documents:
            self.documents["sources.json"] = self.sources
        self.active_id = None
        return self

    def field(self, name, record):
        field, parameter = record["field"], record["parameter"]
        path = pointer("parameters", parameter, field)
        document = self.documents[name]
        definition = document["parameters"][parameter]
        value = deepcopy(record["value"])
        if field == "domain":
            from .constraints import domain

            typ = definition["type"]
            domain(value, typ if isinstance(typ, str) else "unknown", document["parameters"])
        if field == "unit":
            base, factor = unit(value)
            if base != value or factor != 1:
                fail("Effective unit must be canonical; use input_unit for source values")
            typ = definition["type"]
            if typ in ("string", "boolean") and value != "1":
                fail("String/boolean fields must be dimensionless", "pdk_fact_unit_conflict")
        numeric_fact = (
            field in {"default", "domain", "grid"} and record["disposition"] == "established"
        )
        if numeric_fact:
            if not isinstance(definition["unit"], str) or not record.get("input_unit"):
                fail("Numeric facts require explicit input_unit and established parameter unit")
            value = scale(field, value, record["input_unit"], definition["unit"])
        old = definition.get(field)
        if "expected" in record and record["expected"] != old:
            fail("Expected fact differs from current value at " + name + path, "pdk_fact_conflict")
        record["normalized"] = value
        prior = self.package.document("sources.json")["items"].get(source_for(document, path), {})
        if old == value:
            record["result"] = "unchanged"
            return
        refinement = field == "meaning" and prior.get("kind") == "session"
        if old is not None and not unresolved(old) and not refinement:
            if "expected" not in record or record["expected"] != old:
                fail(
                    "Known fact conflict at "
                    + name
                    + path
                    + "; supply exact expected after source review",
                    "pdk_fact_conflict",
                )
            # Reuniting existing numeric facts needs explicit migration, not relabelling.
            if field == "unit" and old != value:
                fail(
                    "Known unit changes require explicit rebase/conversion of all dependent values",
                    "pdk_fact_unit_conflict",
                )
        source = record["source"]
        sid = "parameter_fact_" + fingerprint([self.report_ref, record["id"]])[7:31]
        self.sources["items"][sid] = {
            "kind": "document",
            "at": now(),
            "summary": record["reason"][:256],
            "file_ref": source["file_ref"],
            "locator": {**source["locator"], "revision": source["document_revision"]},
            "evidence_ref": self.report_ref,
        }
        if numeric_fact and record["input_unit"] != definition["unit"]:
            derived = sid + "_si"
            self.sources["items"][derived] = {
                "kind": "derived",
                "at": now(),
                "summary": "Explicit decimal unit conversion",
                "inputs": [sid],
                "method": "sico-fact-unit-scaling-v1",
                "evidence_ref": self.report_ref,
            }
            sid = derived
        definition[field] = value
        evidence = document.setdefault("evidence", {})
        for old_path in list(evidence):
            if old_path == path or old_path.startswith(path + "/"):
                del evidence[old_path]
        evidence[path] = sid
        ref = record["source_identity"]["dependency"]
        document["depends_on"] = sorted(set(document["depends_on"]) | {ref})
        self.fields.append({"file": name, "path": path, "value": value, "source": sid})
        record["result"] = "collected"
