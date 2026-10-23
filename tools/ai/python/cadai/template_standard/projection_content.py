"""Rebuild public semantic documents; never copy a raw provenance envelope."""

import copy
import re

from ..template_project import PARAMETER_ALIASES, PARAMETERS_BY_CLASS
from .codec import fail
from .content import OBSERVATIONS
from .qualification import PROFILES
from .topology import unknown

LEVELS = ("L1", "L2", "L3")
ENV = dict(source="projection", depends_on=["capture"])
GAP_REASON = "Retained source evidence has unresolved facts"
LABEL = re.compile(r"[A-Za-z_][A-Za-z0-9_!<>.\[\]:-]{0,127}\Z")
SCALAR = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?(?:[afpnumkMGT]|meg)?\Z")


def label(value):
    if not isinstance(value, str) or not LABEL.fullmatch(value):
        fail("projection interface/master identifier is not publishable")
    return value


def ordered(values):
    def key(value):
        match = re.fullmatch(r"([a-z]+)([0-9]+)", value)
        return (match[1], int(match[2])) if match else (value, -1)

    return sorted(values, key=key)


def documents(raw, level, *, rekey=True):
    """Whitelist facts, remap all object IDs, and retain gaps without private text.

    rekey=False is used by the reader to verify a published document is already
    in this public profile, including fields normally ignored by semantic checks.
    """
    if level not in LEVELS:
        fail("unsupported projection level")
    docs, maps = {}, {}

    def ids(role, field, prefix):
        values = raw.get(role, {}).get(field, {})
        mapping = {k: prefix + str(i) if rekey else k for i, k in enumerate(ordered(values))}
        if not rekey and any(not re.fullmatch(prefix + r"[0-9]+", k) for k in mapping):
            fail("projection contains a private object ID")
        maps[role, field] = mapping
        return mapping

    def ref(role, field, key):
        return maps[role, field][key]

    def take(row, names):
        return {k: copy.deepcopy(row[k]) for k in names.split() if k in row}

    def part(role, **fields):
        docs[role] = dict(**ENV, **fields)
        return docs[role]

    def state(value):
        return unknown("Retained endpoint state", value["state"])

    top = raw.get("topology")
    if top:
        out = part(
            "topology", level="direct", masters={}, devices={}, nets={}, ports={}, connections={}
        )
        for field, prefix in [
            ("devices", "d"),
            ("nets", "n"),
            ("ports", "p"),
            ("connections", "c"),
            ("masters", "m"),
        ]:
            ids("topology", field, prefix)
        if level != "L1":
            out["masters"] = {v: dict(dependency=v) for v in maps["topology", "masters"].values()}
            out["depends_on"] = ["capture", *out["masters"]]
        for key, row in top["devices"].items():
            master = (
                unknown("Master identity excluded by L1", "not_applicable")
                if level == "L1"
                else ref("topology", "masters", row["master"])
            )
            out["devices"][ref("topology", "devices", key)] = dict(
                kind=row["kind"],
                role=row["role"],
                master=master,
                terminals=[label(p) for p in row["terminals"]]
                if isinstance(row["terminals"], list)
                else unknown("Terminal inventory unavailable"),
            )
        for key, row in top["nets"].items():
            ident = ref("topology", "nets", key)
            out["nets"][ident] = take(row, "width scope signal_type") | dict(
                name=label(row["name"]) if row["scope"] == "global" else ident
            )
        for key, row in top["ports"].items():
            out["ports"][ref("topology", "ports", key)] = take(row, "direction width") | dict(
                name=label(row["name"])
            )
        for key, row in top["connections"].items():
            value = dict(
                net=ref("topology", "nets", row["net"])
                if isinstance(row["net"], str)
                else state(row["net"])
            )
            value.update(
                dict(
                    device=ref("topology", "devices", row["device"]),
                    terminal=label(row["terminal"]),
                )
                if "device" in row
                else dict(port=ref("topology", "ports", row["port"]))
            )
            out["connections"][ref("topology", "connections", key)] = value
    for role, field in [("relation", "items"), ("reuse", "constraints")]:
        if role not in raw:
            continue
        rows = {}
        for key, ident in ids(role, field, "r").items():
            row = raw[role][field][key]
            value = take(row, "kind axis spacing_class")
            if (
                value["kind"] not in {"mirror", "row", "column", "alignment", "spacing"}
                or ("axis" in value and value["axis"] not in {"x", "y"})
                or (
                    "spacing_class" in value
                    and value["spacing_class"] not in {"compact", "normal", "wide"}
                )
            ):
                fail("unsupported public relation")
            value["devices"] = [ref("topology", "devices", d) for d in row["devices"]]
            if role == "relation":
                value["basis"] = "source_geometry"
            rows[ident] = value
        part(role, **{field: rows})
    if "reuse" in docs:
        reuse = raw["reuse"]
        docs["reuse"].update(
            mode=reuse["mode"],
            allowed_rules=copy.deepcopy(reuse["allowed_rules"]),
            core_devices=[ref("topology", "devices", d) for d in reuse["core_devices"]],
            optional_groups={},
            boundaries={},
        )
        for key, ident in ids("reuse", "optional_groups", "g").items():
            docs["reuse"]["optional_groups"][ident] = dict(
                devices=[
                    ref("topology", "devices", d) for d in reuse["optional_groups"][key]["devices"]
                ]
            )
        for key, ident in ids("reuse", "boundaries", "b").items():
            row = reuse["boundaries"][key]
            docs["reuse"]["boundaries"][ident] = take(
                row, "allowed_kinds min_extra max_extra"
            ) | dict(connection=ref("topology", "connections", row["connection"]))
    if level != "L1":
        from .projection_geometry import geometry

        geometry(raw, docs, ids, ref, part, take)
    if level == "L3" and "parameter" in raw:
        params = part("parameter", items={})["items"]
        for key, ident in ids("parameter", "items", "v").items():
            row = raw["parameter"]["items"][key]
            allowed = {
                a
                for p in PARAMETERS_BY_CLASS.get(top["devices"][row["device"]]["kind"], ())
                for a in PARAMETER_ALIASES[p]
            }
            if row["name"] not in allowed:
                continue
            value, kind = row["value"], row["type"]
            literal = (
                (kind == "integer" and type(value) is int)
                or (kind == "number" and type(value) in (int, float))
                or (kind == "string" and isinstance(value, str) and SCALAR.fullmatch(value))
            )
            params[ident] = dict(
                device=ref("topology", "devices", row["device"]),
                name=row["name"],
                type=kind if literal else unknown("Physical literal unavailable"),
                value=value if literal else unknown("Physical literal unavailable"),
                unit=unknown("Source unit unconfirmed"),
                meaning=unknown("Parameter meaning unconfirmed"),
                role="unknown",
            )
    requested = [
        a
        for a in raw["collection"]["scope"]["requested_assets"]
        if a != "layout" and (level != "L1" or a == "schematic")
    ]
    if not requested:
        fail("projection has no applicable electrical or symbol asset")
    collection = part(
        "collection", scope=dict(level="direct", requested_assets=requested), assets={}, gaps={}
    )
    for asset in requested:
        row = raw["collection"]["assets"][asset]
        collection["assets"][asset] = dict(
            status=row["status"],
            dependencies=["capture"],
            observations={k: row["observations"][k] for k in OBSERVATIONS},
            reason=GAP_REASON,
        )
    for gap_id in ordered(raw["collection"]["gaps"]):
        row = raw["collection"]["gaps"][gap_id]
        if row["part"].removesuffix(".json") not in {*docs, *requested}:
            continue
        key = "g" + str(len(collection["gaps"]))
        collection["gaps"][key] = dict(
            part=row["part"],
            object="scope",
            pointer="",
            code="unsupported_feature"
            if row["code"] == "unsupported_feature"
            else "missing_required",
            operations=[p for p in row["operations"] if p in PROFILES],
            reason=GAP_REASON,
            action="complete_evidence",
        )
    if top and any(d.get("attributes") for d in top["devices"].values()):
        collection["gaps"]["g" + str(len(collection["gaps"]))] = dict(
            part="topology.json",
            object="scope",
            pointer="",
            code="unsupported_feature",
            operations=["topology-v1"],
            reason=GAP_REASON,
            action="complete_evidence",
        )
    return docs, maps
