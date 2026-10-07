"""Bounded, opt-in P2 request and use contracts; v1 remains a separate branch."""

from .circuit_spec_schema import BINDINGS, ID, NET_NAME, SPEC, array, enum, mapping, obj
from .template_reuse_schema import ENDPOINT, HEX, RULE_REF, refs
from .template_schema import NAME, REF

REQUEST_VERSION = "cad.template.adapt-request.v2"
USE_VERSION = "cad.circuit.template-use.v2"
VERIFIER_VERSION = "20260919.template-adaptation.v1"
TARGET_ENDPOINT = obj({"instance": ID, "terminal": NET_NAME})
MAP = obj({
    "device_map": mapping(ID, NAME, 64),
    "terminal_map": mapping(mapping(NET_NAME, NAME, 64), NAME, 64),
    "net_map": mapping(NET_NAME, NAME, 256),
    "port_map": mapping(NET_NAME, NAME, 128),
    "omitted_groups": refs(NAME, 8),
})
DIFF = obj({
    "removed_devices": refs(NAME, 64),
    "removed_nets": refs(NAME, 256),
    "removed_ports": refs(NAME, 128),
    "added_instances": refs(ID, 64),
    "added_nets": refs(NET_NAME, 256),
    "added_ports": refs(NET_NAME, 128),
    "renamed_nets": mapping(NET_NAME, NAME, 256),
    "renamed_ports": mapping(NET_NAME, NAME, 128),
})
ADAPTATION = obj({
    "rule_ref": RULE_REF,
    "site": refs(NAME, 64),
    "input_graph_digest": HEX,
    "output_graph_digest": HEX,
    "diff": DIFF,
})
USE = obj({
    "schema": enum(USE_VERSION),
    "template_ref": REF,
    "target_spec_digest": HEX,
    **{k: v for k, v in MAP["properties"].items() if k != "omitted_groups"},
    "boundary_map": array(obj({
        "source_endpoint": ENDPOINT, "target_endpoint": TARGET_ENDPOINT, "rule_ref": RULE_REF,
    }), 4096),
    "residual_instances": refs(ID, 64),
    "adaptations": array(ADAPTATION, 9),
    "verification": obj({
        "version": enum(VERIFIER_VERSION), "record_digest": HEX,
        "checked_invariants": refs(NAME, 16), "gaps": refs(NAME, 32),
    }),
})
REQUEST = obj({
    "request_schema": enum(REQUEST_VERSION), "spec": SPEC, "template_ref": REF,
    "mapping": MAP, "allowed_rule_refs": array(RULE_REF, 3), "bindings": BINDINGS,
})


def is_v2(use):
    return isinstance(use, dict) and use.get("schema") == USE_VERSION


def device_rows(use, spec):
    """One transient view for geometry consumers; never an alternative stored use."""
    if not is_v2(use):
        return use["devices"]
    instances = {i["id"]: i for i in spec["instances"]}
    return [{"source_device": source, "instance": target,
             "master": instances[target]["master"], "terminal_map": use["terminal_map"][source]}
            for source, target in sorted(use["device_map"].items())]
