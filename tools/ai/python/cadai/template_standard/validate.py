"""Validate standard package semantics at publication and every fresh read."""

import re

from .codec import VERSION, fail
from .identity import pointer, reference
from .qualification import PROFILES
from .topology import validate as validate_topology

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
REQUIRED = {
    "collection": "scope assets gaps",
    "topology": "level masters devices nets ports connections",
    "placement": "frame instances anchors annotations",
    "routing": "placement_ref nodes segments crossings",
    "relation": "items",
    "parameter": "items",
    "reuse": "mode core_devices optional_groups boundaries allowed_rules constraints",
    "qualification": "template_ref checks",
    "symbol": "frame terminals anchors outline labels",
    "layout": "asset dependency binding_status bindings",
}


def keys(row, required, optional=""):
    if not isinstance(row, dict) or set(required.split()) - row.keys():
        fail("missing required fields: " + required)
    if {k for k in row if not k.startswith("x_")} - set((required + " " + optional).split()):
        fail("unregistered fields: " + str(sorted(row)))


def acyclic(rows, field):
    done = set()

    def visit(key, active):
        if key in active:
            fail("cyclic " + field)
        if key not in rows:
            fail("missing " + field + " reference")
        if key in done:
            return
        edges = rows[key].get(field, [])
        if isinstance(edges, str):
            edges = [edges]
        for edge in edges:
            visit(edge, active | {key})
        done.add(key)

    for key in rows:
        visit(key, set())


def validate(package, tables, templates):
    keys(
        package,
        "format schema_version package_id revision created_at "
        "dependencies pdks rules files required_features",
    )
    if package["format"] != "sico.circuit-template.package" or package["schema_version"] != VERSION:
        fail("unsupported package format/version")
    if package["required_features"] != []:
        fail("unsupported required_features")
    from .projection_checks import validate as validate_projections

    validate_projections(package, tables, templates)
    sources = tables["sources"]["items"]
    acyclic(sources, "inputs")
    acyclic(tables["category"]["items"], "parent")
    for table in (
        sources,
        package["dependencies"],
        package["rules"],
        package["pdks"],
        tables["category"]["items"],
        tables["template"]["items"],
        tables["file"]["items"],
    ):
        if not isinstance(table, dict) or any(not ID.fullmatch(k) for k in table):
            fail("invalid table ID")
    for row in sources.values():
        required = {
            "capture": "collector scope_digest",
            "user": "confirmed_by scope_digest evidence_ref",
            "derived": "inputs method",
            "document": "file_ref locator",
            "validation": "validator evidence_ref",
        }.get(row.get("kind"))
        if required is None:
            fail("invalid source kind")
        keys(row, "kind at summary " + required, "evidence_ref")
    for row in package["dependencies"].values():
        keys(row, "kind target fingerprint method")
        if (
            row["kind"]
            not in "schematic master interface cdf symbol layout technology file".split()
        ):
            fail("invalid dependency kind")
        keys(row["target"], "file_ref" if "file_ref" in row["target"] else "library cell view")
        if "file_ref" in row["target"] and row["target"]["file_ref"] not in tables["file"]["items"]:
            fail("missing dependency resource")
        if not (isinstance(row["fingerprint"], str) and DIGEST.fullmatch(row["fingerprint"])):
            keys(row["fingerprint"], "state reason")
            if row["fingerprint"]["state"] != "unknown":
                fail("invalid fingerprint state")
    for row in package["rules"].values():
        keys(row, "name version implementation_digest")
        if row["name"] not in {"rename", "omit_optional_group", "add_boundary_group"}:
            fail("unregistered adaptation rule")
        if not DIGEST.fullmatch(row["implementation_digest"]):
            fail("invalid rule digest")
    for role, doc in tables.items():
        if role != "sources":
            envelope(doc, sources, package)
            from .shards import fields

            for field in fields(role):
                if (
                    field != "evidence"
                    and field in doc
                    and any(not ID.fullmatch(k) for k in doc[field])
                ):
                    fail("invalid semantic table ID: " + role + "." + field)
    directories = set()
    for ident, entry in tables["template"]["items"].items():
        keys(
            entry,
            "name description categories granularity detail_level dir template_ref use",
            "reason derived_from",
        )
        if not ID.fullmatch(entry["dir"]) or entry["dir"] in directories:
            fail("invalid/duplicate template directory")
        directories.add(entry["dir"])
        if entry["detail_level"] not in {"raw", "L1", "L2", "L3"}:
            fail("invalid detail level")
        keys(entry["use"], "reference reuse")
        if any(v not in {"allow", "deny", "unknown"} for v in entry["use"].values()):
            fail("invalid use decision")
        docs = templates[ident]
        if not {"collection", "qualification"} <= docs.keys():
            fail("collection and qualification are required")
        for role, doc in docs.items():
            optional = {"collection": "origin legacy_refs", "placement": "rounding"}.get(role, "")
            keys(doc, "source depends_on " + REQUIRED[role], "evidence " + optional)
            envelope(doc, sources, package)
            from .shards import fields

            for field in fields(role):
                if (
                    field != "evidence"
                    and field in doc
                    and any(not ID.fullmatch(k) for k in doc[field])
                ):
                    fail("invalid semantic table ID: " + role + "." + field)
        collection = docs["collection"]
        if entry["detail_level"] == "raw" and "origin" not in collection:
            fail("raw template requires origin")
        requested = collection["scope"]["requested_assets"]
        if not requested or set(requested) != set(collection["assets"]):
            fail("asset coverage differs from request")
        if "topology" in docs:
            validate_topology(docs["topology"])
        from .semantics import validate as validate_semantics

        validate_semantics(package, tables, docs)
        expected = reference(package, tables, docs, ident)
        if entry["template_ref"] != expected or docs["qualification"]["template_ref"] != expected:
            fail("immutable_reference_conflict: content/qualification reference mismatch")
        checks = docs["qualification"]["checks"]
        if set(checks) != set(PROFILES):
            fail("qualification must cover all profiles")
        for row in checks.values():
            if row["result"] in {"passed", "failed"}:
                keys(row, "result at validator evidence_ref", "gap_refs")
            elif row["result"] in {"unknown", "not_applicable"}:
                keys(row, "result reason", "gap_refs")
            else:
                fail("invalid qualification result")
            if set(row.get("gap_refs", [])) - collection["gaps"].keys():
                fail("missing qualification gap")


def envelope(doc, sources, package):
    if doc["source"] not in sources or set(doc["depends_on"]) - package["dependencies"].keys():
        fail("missing envelope source/dependency")
    for path, source in doc.get("evidence", {}).items():
        pointer(doc, path)
        if source not in sources:
            fail("missing evidence source")
