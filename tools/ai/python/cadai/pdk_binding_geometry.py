"""Creator-only body observation: discovery v1 deliberately omits symbol bodies."""

import json

from .circuit_geometry_schema import BOX
from .circuit_spec_schema import digest, validate
from .pdk_binding_data import require
from .skill_diagnostics import FIELDS
from .skill_result import call_skill


def capture_body(client, device):
    values = [device["target"][k] for k in ("library", "cell", "view")]
    values.append(device["library"]["resolved_path"])
    code = "aiCrBindingGeometry(" + " ".join(json.dumps(v) for v in values) + ")"
    ok, result = call_skill(client, code)
    require(ok, "body observation failed: " + str(result))
    return result


def validate_body(body, pins):
    validate(body["occupied_bbox"], BOX)
    expected = {t["name"]: sorted(a["xy"] for a in t["anchors"]) for t in pins}
    actual = {t[0]: sorted(t[1]) for t in body["pin_centers"]}
    require(expected == actual, "body observation pin centers differ from PDK detail")
    result = dict(occupied_bbox=body["occupied_bbox"],
                  signature_digest=digest({k: v for k, v in body.items() if k not in FIELDS}))
    if "annotation_bbox" in body:
        validate(body["annotation_bbox"], BOX)
        result["annotation_bbox"] = body["annotation_bbox"]
    return result
