"""Immutable template records and atomic SQLite publication."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from .template_capture import json_value as _json
from .template_capture import read_capture
from .template_coords import Coordinates, asset_anchor, union_box
from .template_graph import normalize_schematic
from .template_layout import normalize_layout
from .template_schema import (
    CAPTURE_SCHEMA_V2,
    COORDINATE_SPACE,
    LEGACY_COORDINATE_SPACE,
    PIN_POLICY,
    RULE_VERSION,
    SCHEMA,
    SCHEMA_V3,
    TemplateError,
    canonical,
    digest,
)


def _view_asset(asset, coordinates=None, anchor=None):
    h, rows = asset["header"], asset["rows"]
    out = {
        "source": h,
        "instances": [],
        "shapes": [],
        "pins": [],
        "ports": [],
        "properties": [],
        "bindings": [],
        "nets": [],
        "mosaics": [],
        "contexts": [],
    }
    mapping = {
        "instance": "instances",
        "shape": "shapes",
        "pin": "pins",
        "terminal": "ports",
        "cell_property": "properties",
        "binding": "bindings",
        "net": "nets",
        "mosaic": "mosaics",
        "layout_context": "contexts",
    }
    for row in rows:
        if coordinates is not None:
            row = coordinates.convert(row)
        if row["kind"] in mapping:
            out[mapping[row["kind"]]].append(row)
    out["instance_properties"] = [
        coordinates.convert(r) if coordinates is not None else r
        for r in rows
        if r["kind"] == "instance_property"
    ]
    if coordinates is not None:
        out["coordinate_space"] = COORDINATE_SPACE
        out["dbu_per_uu"] = h.get("dbu_per_uu")
        out["anchor"] = anchor or {
            "kind": "view_origin",
            "source_xy_dbu": list(coordinates.anchor),
        }
        out["rounding"] = coordinates.report()
        out["bbox"] = union_box([row.get("bbox") for row in out["instances"]]) or coordinates.box(
            h.get("bbox")
        )
    else:
        out["coordinate_space"] = LEGACY_COORDINATE_SPACE
    out["coverage"] = {
        "status": "partial" if h.get("incomplete_features") else "captured",
        "gaps": h.get("incomplete_features", []),
        "ports": len(out["ports"]),
        "pins": len(out["pins"]),
        "shapes": len(out["shapes"]),
    }
    return out


def make_template(capture, category="reference", provenance=None, *, classifications=None):
    if category not in {"reference", "digital", "analog"}:
        raise TemplateError("unsupported template category")
    if not capture["assets"]:
        raise TemplateError("no readable source views")
    source = next(iter(capture["assets"].values()))["header"]
    if classifications and "schematic" not in capture["assets"]:
        raise TemplateError("classification requires a captured schematic")
    result = {
        "schema_version": SCHEMA,
        "rule_version": ("20260920.capture2.3" if any(
                            a["header"].get("capture_schema") == CAPTURE_SCHEMA_V2
                            and a["header"].get("wire_net_semantics")
                            for a in capture["assets"].values()) else
                         "20260919.capture2.1" if source.get("capture_schema") == CAPTURE_SCHEMA_V2
                         else RULE_VERSION),
        "coordinate_space": COORDINATE_SPACE,
        "source": source,
        "capture_sha256": capture["sha256"],
        "provenance": provenance or {},
        "category": category,
        "missing_assets": capture["missing"],
        "pin_policy": PIN_POLICY,
        "assets": {},
    }
    sch = capture["assets"].get("schematic")
    if sch:
        topology, placement = normalize_schematic(sch["header"], sch["rows"], classifications)
        if sch["header"].get("classification_semantics"):
            result["rule_version"] = topology["rule_version"]
        result["topology"] = topology
        result["assets"]["schematic"] = placement
        if sch["header"].get("capture_schema") == CAPTURE_SCHEMA_V2:
            from .template_wire_attachment import capture_reference, compile_reference

            placement["wire_reference"] = capture_reference(sch["header"], sch["rows"], placement)
            attachments = compile_reference(result)
            placement["wire_coverage"] = {
                net: {"eligible": row["eligible"], "gaps": row["gaps"]}
                for net, row in attachments["nets"].items()
            }
    for name in ("symbol", "layout"):
        if name in capture["assets"]:
            asset = capture["assets"][name]
            anchor = asset_anchor(asset["rows"], asset["header"].get("dbu_per_uu"))
            coordinates = Coordinates(asset["header"].get("dbu_per_uu"), anchor["source_xy_dbu"])
            result["assets"][name] = _view_asset(asset, coordinates, anchor)
    if sch and "symbol" in result["assets"]:
        symbol = result["assets"]["symbol"]
        sch_ports = {p["name"]: p for p in result["topology"]["ports"]}
        sym_ports = {p["name"]: p for p in symbol["ports"]}
        differences = [
            {"port": name, "reason": "missing_in_symbol"}
            for name in sorted(sch_ports.keys() - sym_ports.keys())
        ]
        differences += [
            {"port": name, "reason": "symbol_only"}
            for name in sorted(sym_ports.keys() - sch_ports.keys())
        ]
        differences += [
            {"port": name, "reason": "direction_or_width_differs"}
            for name in sorted(sch_ports.keys() & sym_ports.keys())
            if (sch_ports[name]["direction"], sch_ports[name]["num_bits"])
            != (sym_ports[name].get("direction"), sym_ports[name].get("numBits", 1))
        ]
        symbol["interface_differences"] = differences
        symbol["coverage"]["interface_status"] = (
            "different" if differences else "same_names_directions_widths"
        )
        symbol["coverage"]["interface_difference_count"] = len(differences)
    if "layout" in result["assets"]:
        normalize_layout(
            result["assets"]["layout"], result.get("topology"), sch["header"] if sch else None
        )
    result["template_ref"] = "tpl_" + digest(
        {
            "capture": capture["sha256"],
            "rule": result["rule_version"],
            "category": category,
            "provenance": provenance or {},
            **({"classifications": classifications or {}}
               if sch and sch["header"].get("classification_semantics") else {}),
        }
    )
    result["summary"] = summary(result)
    return result


def summary(record):
    top = record.get("topology", {})
    source = record["source"]
    gaps = top.get("gaps", [])
    result = {
        "template_ref": record.get("template_ref"),
        "rule_version": record["rule_version"],
        "library": source["lib"],
        "cell": source["cell"],
        "category": record["category"],
        "assets": sorted(record["assets"]),
        "counts": top.get("counts", {}),
        "topology_fingerprint": top.get("fingerprint"),
        "topology_level": top.get("level"),
        "gap_count": len(gaps),
        "gaps": gaps[:8],
        "coverage": {
            name: a.get("coverage", {"status": "partial", "gap_count": len(a.get("gaps", []))})
            for name, a in record["assets"].items()
        },
        "missing_assets": record["missing_assets"],
        "capture_sha256": record["capture_sha256"],
        "qualification": "layout_and_structure_reference_only",
    }
    if record.get("detail_level"):
        result["detail_level"] = record["detail_level"]
    return result


def publish(records, output):
    """Legacy writer; v3 requires the separate validated content constructor."""
    if any(r.get("schema_version") == SCHEMA_V3 for r in records):
        raise TemplateError("v3 publication requires the versioned reuse-contract writer")
    return _publish(records, output, SCHEMA)


def _publish(records, output, schema):
    import sqlite3

    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    if not records or len({r["template_ref"] for r in records}) != len(records):
        raise TemplateError("empty or duplicate template publication")
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".templates-", dir=output.parent)
    os.close(fd)
    staging = Path(temporary)
    try:
        with sqlite3.connect(str(staging)) as db:
            db.executescript("""
              CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
              CREATE TABLE templates(ref TEXT PRIMARY KEY, library TEXT, cell TEXT, category TEXT,
                device_count INTEGER, fingerprint TEXT, search_text TEXT,
                summary_json TEXT, data_json TEXT);
              CREATE INDEX lookup ON templates(library,cell,category,device_count);
              CREATE INDEX topology_lookup ON templates(fingerprint);
            """)
            db.execute("INSERT INTO metadata VALUES('schema_version',?)", (schema,))
            # Metadata describes the catalog writer/compatibility rule.  A
            # capture-v2 record carries its own capture qualification rule in
            # ``record.rule_version``; keeping the catalog metadata explicit
            # avoids pretending that one shard-wide value describes both.
            db.execute("INSERT INTO metadata VALUES('rule_version',?)", (RULE_VERSION,))
            db.execute("INSERT INTO metadata VALUES('record_rule_versions',?)", (
                canonical(sorted({r["rule_version"] for r in records})),
            ))
            for r in records:
                s = r["summary"]
                notes = [
                    (f.get("figure") or {}).get("text", "") or ""
                    for f in r["assets"].get("schematic", {}).get("shapes", [])
                ]
                words = " ".join(
                    [
                        s["library"],
                        s["cell"],
                        s["category"],
                        *s["counts"].get("device_kinds", {}),
                        *notes,
                    ]
                )
                aliases = {
                    "INV": "反相器 inverter",
                    "NAND": "与非 nand",
                    "NOR": "或非 nor",
                    "BUF": "缓冲 buffer",
                    "opamp": "运放 amplifier",
                    "comparator": "比较器 comparator",
                    "latch": "锁存 latch",
                    "MX": "选择器 mux",
                }
                words += " " + " ".join(
                    v for k, v in aliases.items() if k.lower() in s["cell"].lower()
                )
                db.execute(
                    "INSERT INTO templates VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        s["template_ref"],
                        s["library"],
                        s["cell"],
                        s["category"],
                        s["counts"].get("devices", 0),
                        s["topology_fingerprint"],
                        words.lower(),
                        canonical(s),
                        canonical(r),
                    ),
                )
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise TemplateError("catalog integrity check failed")
        db.close()
        os.link(staging, output)
    finally:
        staging.unlink(missing_ok=True)
    return {
        "catalog": str(output),
        "templates": len(records),
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
    }


def publish_bytes(path, raw):
    """Publish an artifact with no partial-reader window and no overwrites."""
    from .template_capture import read_bytes

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".capture-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if read_bytes(path) != raw:
                raise TemplateError("immutable artifact publication conflict")
    finally:
        temporary.unlink(missing_ok=True)


def build_manifest(manifest_path, output):
    path = Path(manifest_path).resolve()
    manifest = _json(path.read_bytes())
    if manifest.get("schema_version") != 1 or manifest.get("pin_policy") != PIN_POLICY:
        raise TemplateError("unsupported raw capture manifest")
    records = []
    for entry in manifest["captures"]:
        capture_path = (path.parent / entry["path"]).resolve()
        if path.parent not in capture_path.parents:
            raise TemplateError("capture path escapes manifest directory")
        capture = read_capture(capture_path)
        if capture["sha256"] != entry["sha256"]:
            raise TemplateError("capture digest mismatch")
        records.append(
            make_template(capture, entry["category"], manifest["sources"][entry["source"]])
        )
    return publish(records, output)
