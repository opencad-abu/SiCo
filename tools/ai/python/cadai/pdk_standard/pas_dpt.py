"""Bounded reads of the PAS device property table for PAS-developed cells.

Only cells with the pdk.dat marker were built by the PAS/GTE flow; their DPT
field names are PDK-defined (for example minL/maxL/minFw/maxFw), so the SKILL
side returns a bounded candidate set plus a diagnostic dump for review.
"""

import json

from .jsonio import fail

FIELD_LIMIT = 64
NAME_LIMIT = 64
VALUE_LIMIT = 256
DUMP_LIMIT = 4096
KEY_LIMIT = 200


def _literal(value):
    return json.dumps(value, ensure_ascii=False)


def read(bridge, library, cell):
    for value in (library, cell):
        if not isinstance(value, str) or not 1 <= len(value) <= 256:
            fail("Invalid PAS DPT target")
    result = bridge.call("aiPdkPasDpt(" + " ".join(map(_literal, (library, cell))) + ")")
    return project(result, library, cell)


def project(result, library, cell):
    identity = result if isinstance(result, dict) else {}
    if identity.get("library") != library or identity.get("cell") != cell:
        fail("Invalid PAS DPT reply")
    rows = result.get("fields")
    if rows is None:
        rows = []
    if not isinstance(rows, list) or len(rows) > FIELD_LIMIT:
        fail("Invalid PAS DPT field table")
    fields = {}
    for row in rows:
        if not isinstance(row, list) or len(row) != 2:
            fail("Invalid PAS DPT field row")
        name, value = row
        if not isinstance(name, str) or not 1 <= len(name) <= NAME_LIMIT:
            fail("Invalid PAS DPT field name")
        if value is None:
            continue
        if isinstance(value, bool) or isinstance(value, (int, float)):
            fields[name] = value
        elif isinstance(value, str) and len(value) <= VALUE_LIMIT:
            fields[name] = value
        else:
            fail("Unsupported PAS DPT field value: " + name)
    dump = result.get("dump")
    if dump is not None and (not isinstance(dump, str) or len(dump) > DUMP_LIMIT):
        fail("Invalid PAS DPT diagnostic dump")
    keys = result.get("keys")
    if keys is None:
        keys = []
    if (not isinstance(keys, list) or len(keys) > KEY_LIMIT
            or any(not isinstance(k, str) or not 1 <= len(k) <= NAME_LIMIT for k in keys)):
        fail("Invalid PAS DPT key list")
    reference = result.get("extension_ref")
    if not isinstance(reference, str):
        reference = "pas:device-property-table:v1"
    return {
        "library": library,
        "cell": cell,
        "pdk_dat": bool(result.get("pdk_dat")),
        "dpt": bool(result.get("dpt")),
        "extension_ref": reference,
        "fields": fields,
        "keys": keys,
        "diagnostic": dump,
    }
