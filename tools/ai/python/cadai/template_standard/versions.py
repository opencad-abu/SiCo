"""Pinned collection dispatch; v1/v2 are retained fixed-reference replay profiles."""

from .codec import fail

CURRENT = "sico-capture-flat-v3"
V2 = "sico-capture-flat-v2"
LEGACY = "sico-capture-flat-v1"
SYMBOL_INTERFACE_SUFFIX = "-symbol-interface-v1"


def collector(request):
    value = request["scope"].get("collector", LEGACY)
    if value not in {CURRENT, V2, LEGACY}:
        fail("unsupported collection algorithm: " + str(value))
    return value


def validator(version):
    return "sico-flat-collection-" + version.rsplit("-", 1)[1]


def symbol_interface_validator(version):
    return validator(version) + SYMBOL_INTERFACE_SUFFIX
