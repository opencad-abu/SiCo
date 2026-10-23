"""Compile capture facts into flat semantic documents without copying source blobs."""

from ..template_source_qualification import discharged_codes
from .geometry import placement, routing
from .topology import ATTRIBUTES, project

OBSERVATIONS = (
    "hierarchy inherited_connections arrays buses parameterized_master dynamic_text wire_ownership"
).split()


def inheritance_state(views, asset):
    """Absent, resolved or present for one captured electrical observation set."""
    from ..template_source_inheritance import has_expressions, resolved

    if not has_expressions(views):
        return "absent"
    rows = (asset or {}).get("rows", [])
    terminals = {r.get("name"): r.get("net") for r in rows if r.get("kind") == "terminal"}
    signals = {r.get("name") for r in rows if r.get("kind") == "net"}
    ok, _ = resolved(views, terminals, signals)
    return "resolved" if ok else "present"


def compile_documents(record, capture, options, dependencies, *, current=False):
    env = dict(source="capture", depends_on=["capture"])
    requested = sorted([*capture["assets"], *(r["asset"] for r in capture["missing"])])
    origin = dict(library=record["source"]["lib"], cell=record["source"]["cell"], views={})
    collection = dict(
        **env,
        scope=dict(level="direct", requested_assets=requested),
        origin=origin,
        assets={},
        gaps={},
    )
    docs = dict(collection=collection)

    def gap(part, obj, reason, code="missing_required", action="complete_evidence"):
        collection["gaps"]["g" + str(len(collection["gaps"]))] = dict(
            part=part + ".json",
            object=obj,
            pointer="",
            code=code,
            operations=[
                {
                    "topology": "topology-v1",
                    "placement": "placement-reference-v1",
                    "routing": "routing-reference-v1",
                    "reuse": "whole-reuse-v1",
                    "symbol": "symbol-reference-v1",
                    "layout": "layout-reference-v1",
                }.get(part, "topology-v1")
            ],
            reason=reason[:256],
            action=action,
        )

    for name in requested:
        asset = capture["assets"].get(name)
        header = (
            asset["header"] if asset else next(r for r in capture["missing"] if r["asset"] == name)
        )
        # A missing asset records the requested view name; no captured view exists.
        origin["views"][name] = header.get("view", name)
        # Observation absence is never guessed from a missing row or generic limitation.
        observations = dict.fromkeys(OBSERVATIONS, "unknown")
        views = header.get("electrical_observation", {}).get("views", [])
        if views:
            for key, field in [
                ("hierarchy", "hierarchy_count"),
                ("arrays", "mosaic_count"),
                ("parameterized_master", "parameterized"),
            ]:
                values = [v.get(field) for v in views]
                if all(v is not None for v in values):
                    observations[key] = "present" if any(values) else "absent"
            expressions = [
                v.get(k) for v in views for k in ("terminal_expressions", "signal_expressions")
            ]
            if all(v is not None for v in expressions):
                observations["inherited_connections"] = inheritance_state(views, asset)
        if asset:
            rows = asset["rows"]
            electrical = [r for r in rows if r["kind"] in {"net", "terminal"}]
            if all(type(r.get("numBits")) is int for r in electrical):
                observations["buses"] = (
                    "present"
                    if any(
                        r["numBits"] != 1 or any(c in r["name"] for c in "<>[]") for r in electrical
                    )
                    else "absent"
                )
            if header.get("wire_net_semantics"):
                observations["wire_ownership"] = "present"
        collection["assets"][name] = dict(
            status="partial" if asset else "missing",
            dependencies=["capture"],
            observations=observations,
            reason="Some source observations or capability checks remain unresolved",
        )
        if not asset:
            gap(name, name, header["reason"], action="collect_source")
    if "topology" in record:
        docs["topology"] = project(record, dependencies, "capture")
        top = docs["topology"]
        top["evidence"] = {}
        for row in record["topology"]["devices"]:
            if row.get("classification_evidence"):
                for field in ("kind", "role", "attributes"):
                    if field in top["devices"][row["id"]]:
                        top["evidence"]["/devices/" + row["id"] + "/" + field] = "intent"
            if set(row["attributes"]) - ATTRIBUTES:
                gap(
                    "topology",
                    row["id"],
                    "Unregistered classification attributes retained privately",
                    "unsupported_feature",
                    "upgrade_reader",
                )
            if row["kind"] == "unknown" or row["role"] != "device":
                gap("topology", row["id"], "Device electrical classification is unresolved")
        for row in record["topology"].get("gaps", []):
            # The reviewed observation discharges the generic header limitations it
            # covers; the standard report must not restate them as open gaps.
            if row["code"] in discharged_codes(record):
                continue
            gap("topology", row.get("instance", "source"), row["code"])
        place = placement(record, env, gap)
        if place:
            docs["placement"] = place
            if current:
                from .routes import project as project_routes

                docs["routing"] = project_routes(record, docs, env, gap)
            else:
                # Compatibility: replay v1 fixed refs until those packages are retired.
                docs["routing"] = routing(record, place, env, gap)
        from .relations import project as project_relations

        relation = project_relations(record, gap)
        if relation is not None:
            docs["relation"] = relation
        from .parameters import project as project_parameters

        docs["parameter"] = project_parameters(record, capture)
    if "symbol" in capture["assets"]:
        from .assets import symbol

        sym = symbol(record, env, gap, current=current)
        if sym:
            docs["symbol"] = sym
            if current:
                from .symbol_checks import issues as symbol_issues

                for reason in symbol_issues(docs, record["assets"]["symbol"]["ports"]):
                    if reason != "symbol_capture_has_gaps":
                        gap("symbol", "interface", reason)
    if "layout" in capture["assets"]:
        gap("layout", "bindings", "Native layout content identity and XL bindings are unverified")
    contract = record.get("reuse_contract")
    if contract:
        connections = {
            (r.get("device"), r.get("terminal")): k
            for k, r in docs["topology"]["connections"].items()
            if "device" in r
        }
        docs["reuse"] = dict(
            source="intent",
            depends_on=["capture"],
            mode=options["reuse"]["mode"],
            core_devices=sorted(contract["core_devices"]),
            optional_groups={
                r["id"]: dict(devices=sorted(r["devices"])) for r in contract["optional_groups"]
            },
            boundaries={
                "b" + str(i): dict(
                    connection=connections[(r["endpoint"]["device"], r["endpoint"]["terminal"])],
                    allowed_kinds=sorted(r["allowed_device_kinds"]),
                    min_extra=r["min_additional_connections"],
                    max_extra=r["max_additional_connections"],
                )
                for i, r in enumerate(contract["boundary_terminals"])
            },
            allowed_rules=sorted(r["id"] for r in contract["allowed_rule_refs"]),
            constraints={},
        )
    elif "reuse" in options:
        gap(
            "reuse",
            "contract",
            "Requested reuse contract could not be qualified",
            "intent_unresolved",
            "review_intent",
        )
    return docs
