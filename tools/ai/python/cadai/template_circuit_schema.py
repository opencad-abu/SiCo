"""Explicit template-to-spec mappings; no project PDK defaults or discovery."""

from .circuit_spec_schema import (
    BINDINGS,
    ID,
    NET_NAME,
    TARGET,
    TEXT,
    VALUE,
    array,
    enum,
    mapping,
    obj,
)
from .template_schema import REF
from .template_adapt_schema import USE as TEMPLATE_USE_V2

USE_VERSION = "cad.circuit.template-use.v1"
# Source topology IDs are normalized IDs returned by get_circuit_template.
SOURCE_DEVICE = {"type": "string", "pattern": r"^d[0-9]+$", "maxLength": 96}
SOURCE_NET = {"type": "string", "pattern": r"^n[0-9]+$", "maxLength": 96}
SOURCE_NAME = {"type": "string", "minLength": 1, "maxLength": 256}
TERMINALS = mapping(NET_NAME, SOURCE_NAME)
# Explicit opt-in per source global net; never infer scope from a renamed net.
NET_SCOPE_MAP = mapping(enum("local"), SOURCE_NET, 256)
DEVICE_USE = obj(
    {
        "source_device": SOURCE_DEVICE,
        "instance": ID,
        "master": ID,
        "terminal_map": TERMINALS,
    }
)
TEMPLATE_USE_V1 = obj(
    {
        "schema": enum(USE_VERSION),
        "template_ref": REF,
        "devices": array(DEVICE_USE, 64, 1),
        "net_map": mapping(NET_NAME, SOURCE_NET, 256),
        "port_map": mapping(NET_NAME, SOURCE_NAME, 128),
        "net_scope_map": NET_SCOPE_MAP,
    },
    ("schema", "template_ref", "devices", "net_map", "port_map"),
)
TEMPLATE_USE = {"type": "object", "additionalProperties": True,
                "oneOf": [TEMPLATE_USE_V1, TEMPLATE_USE_V2]}
DEVICE_SELECTION = obj(
    {
        **DEVICE_USE["properties"],
        "name": ID,
        "parameters": mapping(VALUE),
        "unconnected": mapping(TEXT, SOURCE_NAME),
    },
    ("source_device", "instance", "master", "parameters"),
)
PREPARE = obj(
    {
        "template_ref": REF,
        "target": TARGET,
        "bindings": BINDINGS,
        "devices": array(DEVICE_SELECTION, 64, 1),
        "net_map": mapping(NET_NAME, SOURCE_NET, 256),
        "port_map": mapping(NET_NAME, SOURCE_NAME, 128),
        "net_scope_map": NET_SCOPE_MAP,
    },
    ("template_ref", "target", "bindings", "devices"),
)
