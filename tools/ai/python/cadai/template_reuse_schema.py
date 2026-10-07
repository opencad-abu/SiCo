"""Template v3 reuse contracts: references only, never a second electrical graph."""

from __future__ import annotations

from .circuit_spec_schema import CircuitSpecError, array, enum, obj, unique, validate
from .template_schema import NAME, REF, SCHEMA_V3, TemplateError
from .template_topology import topology_parts

HEX = {"type": "string", "pattern": r"^[0-9a-f]{64}$", "maxLength": 64}


def refs(item, maximum, minimum=0):
    return {**array(item, maximum, minimum), "uniqueItems": True}


ENDPOINT = obj({"device": NAME, "terminal": NAME})
RULE_REF = obj({"id": NAME, "version": NAME, "implementation_digest": HEX})
BOUNDARY = obj(
    {
        "endpoint": ENDPOINT,
        "allowed_device_kinds": refs(NAME, 64, 1),
        "min_additional_connections": {"type": "integer", "minimum": 0, "maximum": 4096},
        "max_additional_connections": {"type": "integer", "minimum": 0, "maximum": 4096},
    }
)
OPTIONAL_GROUP = obj(
    {
        "id": NAME,
        "devices": refs(NAME, 64, 1),
        "boundary_connections": refs(ENDPOINT, 4096),
        "omit": obj({"remove_nets": refs(NAME, 256), "remove_ports": refs(NAME, 128)}),
    }
)
PLACEMENT = obj(
    {
        "kind": enum("mirror", "row", "column", "alignment", "spacing"),
        "devices": refs(NAME, 64, 2),
        "strength": enum("hard", "preference"),
        "axis": enum("x", "y"),
        "spacing_class": enum("compact", "normal", "wide"),
    },
    ("kind", "devices", "strength", "axis"),
)
ROUTING = obj(
    {
        "asset": enum("schematic"),
        "status": enum("unavailable", "captured"),
        "anchor_ids": refs(NAME, 4096),
        "segment_ids": refs(NAME, 8192),
    }
)
REUSE_CONTRACT = obj(
    {
        "core_devices": refs(NAME, 64, 1),
        "core_connections": refs(ENDPOINT, 4096, 1),
        "boundary_terminals": array(BOUNDARY, 256),
        "optional_groups": array(OPTIONAL_GROUP, 64),
        "allowed_rule_refs": array(RULE_REF, 32),
        "placement_constraints": array(PLACEMENT, 128),
        "routing_reference": ROUTING,
    }
)
ADDITIONS = obj(
    {
        "schema_version": enum(SCHEMA_V3),
        "template_ref": REF,
        "granularity": {
            "type": ["string", "null"],
            "enum": ["atomic", "block", "subsystem", "ip", None],
        },
        "reuse_contract": REUSE_CONTRACT,
        "base_template_ref": REF,
    },
    ("schema_version", "template_ref", "granularity", "reuse_contract"),
    additionalProperties=True,
)


def endpoint(value):
    return value["device"], value["terminal"]


class ContractError(TemplateError):
    """Semantic contract failure with repairable object/field context."""

    def __init__(self, message, code, object_ref, required_fields, details=None):
        super().__init__("reuse_contract: " + message)
        self.code, self.object_ref = code, object_ref
        self.required_fields = required_fields
        self.details = details or {}


def _require(condition, message, code=None, object_ref=None, field=None, **details):
    if not condition:
        if code:
            raise ContractError(message, code, object_ref, [field], details)
        raise TemplateError("reuse_contract: " + message)


def _groups(contract, core, devices, nets, ports, pins, boundary_nets):
    groups = {}
    for group in contract["optional_groups"]:
        _require(group["id"] not in groups, "duplicate optional group id", "group_id_duplicate",
                 group["id"], "reuse.optional_groups")
        groups[group["id"]] = group
    owners = dict.fromkeys(core, "core")
    for key, group in groups.items():
        for device in group["devices"]:
            _require(device in devices and device not in owners, "invalid/overlapping group device",
                     "group_device_overlap", device, "reuse.optional_groups", group=key)
            owners[device] = key
    for device in sorted(set(devices) - set(owners)):
        _require(False, "core/groups must partition all electrical devices",
                 "device_partition_incomplete", device,
                 "reuse.core_instances/reuse.optional_groups")
    for group in groups.values():
        members = set(group["devices"])
        own_nets = {n for (d, _), n in pins.items() if d in members and n is not None}
        other_nets = {n for (d, _), n in pins.items() if d not in members and n is not None}
        shared = own_nets & other_nets
        _require(shared <= boundary_nets, "group crosses an undeclared core boundary",
                 "group_boundary_undeclared", group["id"], "reuse.boundary_terminals",
                 nets=[nets[n]["source_name"] for n in sorted(shared - boundary_nets)])
        actual = {p for p, n in pins.items() if p[0] in members and n in shared}
        declared = {endpoint(p) for p in group["boundary_connections"]}
        _require(declared == actual, "group boundary_connections must cover every boundary pin",
                 "group_boundary_incomplete", group["id"], "reuse.optional_groups")
        removed = {n for n in own_nets - other_nets if nets[n]["is_global"] is False}
        removed_ports = {name for name, p in ports.items() if p["net"] in removed}
        _require(set(group["omit"]["remove_nets"]) == removed, "group omitted nets are ambiguous",
                 "group_omit_nets", group["id"], "reuse.optional_groups")
        _require(
            set(group["omit"]["remove_ports"]) == removed_ports,
            "group omitted ports are ambiguous", "group_omit_ports", group["id"],
            "reuse.optional_groups",
        )


def _routing(record, reference):
    if reference["status"] == "unavailable":
        _require(
            not reference["anchor_ids"] and not reference["segment_ids"],
            "unavailable routing cannot claim attachments",
        )
        return
    asset = record.get("assets", {}).get("schematic", {})
    for key, collection in (("anchor_ids", "anchors"), ("segment_ids", "segments")):
        rows = asset.get(collection, [])
        validate(
            rows, array(obj({"id": NAME}, additionalProperties=True), 8192), "routing " + collection
        )
        inventory = unique(rows, "id", "routing " + collection)
        _require(
            reference[key] and set(reference[key]) <= set(inventory),
            "routing reference contains missing/empty " + collection,
        )


def validate_record(record):
    """Read v3 without rewriting content or granting adaptation/creation eligibility."""
    try:
        validate(record, ADDITIONS, "template v3")
        _require(record.get("base_template_ref") != record["template_ref"], "self base reference")
        devices, nets, ports, pins = topology_parts(record.get("topology"))
        contract = record["reuse_contract"]
        core = set(contract["core_devices"])
        _require(core <= set(devices), "core refers to absent devices")
        protected = {p for p in pins if p[0] in core}
        _require(
            {endpoint(p) for p in contract["core_connections"]} == protected,
            "core_connections must cover every protected terminal exactly once",
        )
        boundary_nets = set()
        for boundary in contract["boundary_terminals"]:
            pin = endpoint(boundary["endpoint"])
            _require(
                pin in protected and pins[pin] is not None, "boundary is not a connected core pin",
                "boundary_not_core", pin[0], "reuse.boundary_terminals", terminal=pin[1]
            )
            net = pins[pin]
            _require(net not in boundary_nets, "declare a boundary net only once",
                     "boundary_net_duplicate", pin[0], "reuse.boundary_terminals",
                     terminal=pin[1], net=nets[net]["source_name"])
            boundary_nets.add(net)
            _require(
                boundary["min_additional_connections"] <= boundary["max_additional_connections"],
                "boundary minimum exceeds maximum", "boundary_range_invalid", pin[0],
                "reuse.boundary_terminals", terminal=pin[1],
            )
            external = [
                devices[d]["kind"] for (d, _), n in pins.items() if d not in core and n == net
            ]
            _require(
                boundary["min_additional_connections"]
                <= len(external)
                <= boundary["max_additional_connections"],
                "source exceeds boundary range", "boundary_source_count", pin[0],
                "reuse.boundary_terminals", terminal=pin[1], observed=len(external),
            )
            _require(
                set(external) <= set(boundary["allowed_device_kinds"]),
                "source has disallowed devices on boundary", "boundary_source_kind", pin[0],
                "reuse.boundary_terminals", terminal=pin[1], observed_kinds=sorted(set(external)),
            )
        _groups(contract, core, devices, nets, ports, pins, boundary_nets)
        rules = [(r["id"], r["version"]) for r in contract["allowed_rule_refs"]]
        _require(len(rules) == len(set(rules)), "duplicate rule version")
        for constraint in contract["placement_constraints"]:
            _require(
                set(constraint["devices"]) <= set(devices), "placement refers to absent devices"
            )
            if constraint["kind"] == "mirror":
                _require(len(constraint["devices"]) == 2, "mirror requires two devices")
            _require(
                ("spacing_class" in constraint) == (constraint["kind"] == "spacing"),
                "spacing_class is required only for spacing",
            )
        _routing(record, contract["routing_reference"])
        observation = record.get("source", {}).get("electrical_observation")
        if observation is not None:
            _require(
                isinstance(observation, dict)
                and observation.get("schema") == "cad.template.electrical-observation.v1"
                and isinstance(observation.get("views"), list)
                and all(isinstance(row, dict) for row in observation["views"]),
                "electrical observation evidence is malformed",
            )
    except CircuitSpecError as exc:
        raise TemplateError(str(exc)) from exc
