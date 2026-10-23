"""Read literal saved property observations; never evaluate SKILL or infer CDF rules."""

import json
import math
import re

from .topology import unknown

NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z")
TEXT = re.compile(r"[\w .+%-]*\Z", re.UNICODE)


def literal(row):
    raw, kind = row.get("value_skill"), row.get("type")
    if not isinstance(raw, str):
        return None, unknown("Saved property literal unavailable")
    if kind in {"int", "integer", "float", "double"} and NUMBER.fullmatch(raw):
        value = float(raw) if kind in {"float", "double"} else int(raw)
        if math.isfinite(value) and abs(value) <= 2**53 - 1:
            return "number" if isinstance(value, float) else "integer", value + 0
    if kind in {"boolean", "bool"} and raw in {"t", "nil"}:
        return "boolean", raw == "t"
    if kind == "string":
        try:
            value = json.loads(raw)
        except ValueError:
            value = None
        if isinstance(value, str) and TEXT.fullmatch(value) and len(value) <= 256:
            return "string", value
    return None, unknown("Expression or unsupported literal retained in private capture")


def project(record, capture):
    source_ids = {r["source_name"]: r["id"] for r in record["topology"]["devices"]}
    params = {}
    for row in capture["assets"]["schematic"]["rows"]:
        if row["kind"] != "instance_property" or row["instance"] not in source_ids:
            continue
        kind, value = literal(row)
        params["p" + str(len(params))] = dict(
            device=source_ids[row["instance"]],
            name=row["name"],
            type=kind or unknown("Literal type unresolved"),
            value=value,
            unit="1" if kind in {"boolean", "string"} else unknown("Source unit unconfirmed"),
            meaning=unknown("Parameter meaning unconfirmed"),
            role="unknown",
        )
    return dict(source="capture", depends_on=["capture"], items=params)
