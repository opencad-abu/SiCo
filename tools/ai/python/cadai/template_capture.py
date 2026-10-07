"""Validate saved OA captures before normalization or publication."""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from collections import Counter, defaultdict
from pathlib import Path

from .template_schema import (
    ASSETS,
    CAPTURE_SCHEMA,
    CAPTURE_SCHEMA_V2,
    MAX_CAPTURE_BYTES,
    TemplateError,
)

RECORD_FIELDS = {
    "instance": (
        "name libName cellName viewName xy orient bbox master_available "
        "schematic_available terminal_names"
    ),
    "instance_terminal": "instance name net",
    "instance_property": "instance name type value_skill",
    "net": "name numBits sigType is_global instance_terminal_count",
    "terminal": "name direction numBits net",
    "pin": "terminal net figure",
    "shape": "net figure",
    "master_geometry": "library cell view figures",
    "binding": "instance status objects siblings permuted_terms",
    "layout_context": "conn_ref source binding_status flatten_policy",
    "cell_property": "name type value_skill",
    "mosaic": "name xy orient rows columns rowSpacing columnSpacing status",
}
LEGACY_COUNTS = {"instance", "instance_terminal", "net", "terminal", "pin", "shape"}


def json_value(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise TemplateError("duplicate JSON field: " + key)
            result[key] = value
        return result

    def reject(value):
        raise TemplateError("non-finite JSON number: " + value)

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=reject)
    except (ValueError, TypeError, RecursionError) as exc:
        raise TemplateError("invalid capture JSON: " + str(exc)) from exc


def read_bytes(path):
    """One bounded read of a regular file, including the session-artifact path."""
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_CAPTURE_BYTES:
            raise TemplateError("capture is not a bounded regular file")
        raw = handle.read(MAX_CAPTURE_BYTES + 1)
        after = os.fstat(handle.fileno())
        if (
            len(raw) != before.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_size != after.st_size
        ):
            raise TemplateError("capture changed while reading")
    return raw


def _point(value):
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(type(n) in (int, float) and math.isfinite(n) and abs(n) <= 1e12 for n in value)
    )


def _geometry(value, depth=0):
    if depth > 24:
        raise TemplateError("capture nesting exceeds limit")
    if isinstance(value, dict):
        for key, val in value.items():
            if val is not None:
                if key in {"xy", "origin", "beginPt", "endPt"} and not _point(val):
                    raise TemplateError("invalid coordinate")
                if key in {"bbox", "bBox", "ellipseBBox", "points"}:
                    if not isinstance(val, list) or not all(_point(p) for p in val):
                        raise TemplateError("invalid coordinate list")
                    if key != "points" and (
                        len(val) != 2 or any(val[0][i] > val[1][i] for i in (0, 1))
                    ):
                        raise TemplateError("invalid bounding box")
            _geometry(val, depth + 1)
    elif isinstance(value, list):
        for val in value:
            _geometry(val, depth + 1)
    elif isinstance(value, str) and len(value) > 1_000_000:
        raise TemplateError("capture string exceeds limit")
    elif isinstance(value, float) and not math.isfinite(value):
        raise TemplateError("non-finite value")


def _text(value):
    return isinstance(value, str) and 0 < len(value) <= 4096 and not any(ord(c) < 32 for c in value)


def validate_asset(header, rows, footer, legacy=False, capture_v2=False):
    fields = RECORD_FIELDS
    semantics = header.get("classification_semantics")
    if semantics is not None:
        from .template_classification import EXPLICIT_SEMANTICS, SEMANTICS

        if semantics not in EXPLICIT_SEMANTICS:
            raise TemplateError("unsupported capture classification semantics")
        fields = {**fields, "instance": fields["instance"] + " builtin_library"}
        if semantics == SEMANTICS:
            fields["instance"] += " master_view_type"
    if capture_v2:
        from .template_capture_geometry import RECORD_FIELDS_V2, validate_geometry_capture

        fields = {**fields, **RECORD_FIELDS_V2}
        validate_geometry_capture(header, rows)
    for key in ("lib", "cell", "view", "library_path", "tool_version"):
        if not _text(header.get(key)):
            raise TemplateError("capture has no valid source " + key)
    unit = header.get("dbu_per_uu")
    if unit is not None and (type(unit) not in (int, float) or not 0 < unit <= 1e12):
        raise TemplateError("invalid database unit")
    gaps = header.get("incomplete_features", [])
    if not isinstance(gaps, list) or len(gaps) > 128 or any(not _text(g) for g in gaps):
        raise TemplateError("invalid capture feature gaps")
    if footer.get("complete") is not True:
        raise TemplateError("capture is incomplete")
    counts = Counter(r.get("kind") for r in rows)
    expected = footer.get("counts")
    counted = LEGACY_COUNTS if legacy else set(fields)
    if (
        not isinstance(expected, dict)
        or any(type(n) is not int or n < 0 for n in expected.values())
        or expected != {k: counts[k] for k in counted}
    ):
        raise TemplateError("capture footer count mismatch")
    names = {}
    for kind in ("instance", "net", "terminal"):
        values = [r.get("name") for r in rows if r.get("kind") == kind]
        if any(not _text(n) for n in values) or len(set(values)) != len(values):
            raise TemplateError("missing/duplicate " + kind + " name")
        names[kind] = set(values)
    keys, net_counts = set(), Counter()
    endpoint_names = defaultdict(set)
    for r in rows:
        kind = r.get("kind")
        if kind not in fields or set(r) - {"kind", *fields[kind].split()}:
            raise TemplateError("unsupported capture record or field")
        _geometry(r)
        if r.get("net") is not None and r["net"] not in names["net"]:
            raise TemplateError("record references missing net")
        if (
            kind in {"instance_terminal", "instance_property", "binding"}
            and r.get("instance") not in names["instance"]
        ):
            raise TemplateError("record references missing instance")
        if kind in {"instance_terminal", "instance_property", "cell_property", "binding"}:
            key = (kind, r.get("instance"), r.get("name"))
            if key in keys or (kind != "binding" and not _text(r.get("name"))):
                raise TemplateError("missing/duplicate " + kind)
            keys.add(key)
        if kind == "instance_terminal":
            net_counts[r.get("net")] += 1
            endpoint_names[r["instance"]].add(r["name"])
        if kind == "instance":
            if semantics is not None and (
                "builtin_library" not in r
                or r["builtin_library"] not in (None, "basic", "analogLib")
            ):
                raise TemplateError("missing/invalid builtin library observation")
            if semantics == "explicit_evidence_v2" and (
                "master_view_type" not in r or (r.get("master_available")
                and not _text(r["master_view_type"]))
            ):
                raise TemplateError("missing/invalid master view type observation")
            if any(not _text(r.get(k)) for k in ("libName", "cellName", "viewName")):
                raise TemplateError("invalid instance master")
            if r.get("orient") not in {
                None,
                "R0",
                "R90",
                "R180",
                "R270",
                "MX",
                "MY",
                "MXR90",
                "MYR90",
            }:
                raise TemplateError("invalid orientation")
            inventory = r.get("terminal_names")
            if inventory is not None and (
                not isinstance(inventory, list)
                or any(not _text(n) for n in inventory)
                or len(set(inventory)) != len(inventory)
            ):
                raise TemplateError("invalid terminal inventory")
        if kind == "pin" and r.get("terminal") not in names["terminal"]:
            raise TemplateError("pin references missing terminal")
        if (
            kind in {"shape", "pin"}
            and r.get("figure") is not None
            and not isinstance(r["figure"], dict)
        ):
            raise TemplateError("invalid figure")
        figure = r.get("figure")
        if figure:
            for field in ("objType", "text", "orient", "font", "justify", "labelType"):
                if figure.get(field) is not None and not isinstance(figure[field], str):
                    raise TemplateError("invalid figure " + field)
            for field in ("height", "width", "startAngle", "stopAngle"):
                v = figure.get(field)
                if v is not None and (type(v) not in (int, float) or not -1e12 <= v <= 1e12):
                    raise TemplateError("invalid figure " + field)
        for field in ("numBits", "instance_terminal_count"):
            if r.get(field) is not None and (
                type(r[field]) is not int or r[field] < (1 if field == "numBits" else 0)
            ):
                raise TemplateError("invalid " + field)
        for field in ("is_global", "master_available", "schematic_available"):
            if field in r and type(r[field]) is not bool:
                raise TemplateError("invalid " + field)
    for r in rows:
        if (
            r["kind"] == "net"
            and "instance_terminal_count" in r
            and r["instance_terminal_count"] != net_counts[r["name"]]
        ):
            raise TemplateError("net endpoint count mismatch")
        if r["kind"] == "instance" and r.get("terminal_names"):
            inventory = set(r["terminal_names"])
            if endpoint_names[r["name"]] - inventory:
                raise TemplateError("saved endpoint is absent from master terminal inventory")
    if semantics == "explicit_evidence_v2":
        from .template_capture_electrical import validate_electrical_capture

        validate_electrical_capture(rows)
    _geometry(header)


def parse_capture(raw):
    if isinstance(raw, (str, Path)):
        raw = read_bytes(Path(raw))
    if not 0 < len(raw) <= MAX_CAPTURE_BYTES:
        raise TemplateError("capture exceeds size limit or is empty")
    rows = [json_value(line) for line in raw.splitlines()]
    if len(rows) < 2 or len(rows) > 200000 or any(not isinstance(r, dict) for r in rows):
        raise TemplateError("capture needs bounded header/footer objects")
    content_sha = hashlib.sha256(raw).hexdigest()
    if (
        type(rows[0].get("schema_version")) is int
        and rows[0]["schema_version"] == 1
        and rows[0].get("status") == "raw_capture"
    ):
        if rows[0].get("kind") != "header" or rows[-1].get("kind") != "footer":
            raise TemplateError("legacy capture is incomplete")
        validate_asset(rows[0], rows[1:-1], rows[-1], legacy=True)
        return {
            "sha256": content_sha,
            "assets": {"schematic": {"header": rows[0], "rows": rows[1:-1]}},
            "missing": [],
        }
    if (
        rows[0].get("kind") != "bundle_header"
        or rows[0].get("schema_version") not in (CAPTURE_SCHEMA, CAPTURE_SCHEMA_V2)
        or rows[-1].get("kind") != "bundle_footer"
        or rows[-1].get("complete") is not True
    ):
        raise TemplateError("unsupported or incomplete template bundle")
    requested = rows[0].get("assets")
    if not isinstance(requested, list) or not requested or any(a not in ASSETS for a in requested):
        raise TemplateError("invalid requested assets")
    assets, missing, active, body = {}, [], None, []
    for row in rows[1:-1]:
        kind = row.get("kind")
        if kind == "asset_header":
            if active is not None or row.get("asset") not in ASSETS or row["asset"] in assets:
                raise TemplateError("invalid asset header")
            active, body = row, []
        elif kind == "asset_footer":
            if active is None:
                raise TemplateError("unexpected asset footer")
            capture_v2 = rows[0]["schema_version"] == CAPTURE_SCHEMA_V2
            validate_asset(active, body, row, capture_v2=capture_v2)
            if capture_v2:
                active = {**active, "capture_schema": CAPTURE_SCHEMA_V2}
            assets[active["asset"]] = {"header": active, "rows": body}
            active, body = None, []
        elif kind == "missing_asset" and active is None:
            if (
                row.get("asset") not in ASSETS
                or not _text(row.get("view"))
                or not _text(row.get("reason"))
            ):
                raise TemplateError("invalid missing asset")
            missing.append(row)
        elif active is not None:
            body.append(row)
        else:
            raise TemplateError("record outside asset")
    reported = list(assets) + [r["asset"] for r in missing]
    if (
        active is not None
        or len(set(reported)) != len(reported)
        or sorted(reported) != sorted(requested)
        or type(rows[-1].get("asset_count")) is not int
        or rows[-1]["asset_count"] != len(reported)
    ):
        raise TemplateError("bundle asset coverage mismatch")
    identities = {(a["header"]["lib"], a["header"]["cell"]) for a in assets.values()}
    if len(identities) > 1:
        raise TemplateError("bundle contains multiple source cells")
    return {"sha256": content_sha, "assets": assets, "missing": missing}


def read_capture(path):
    return parse_capture(read_bytes(Path(path)))
