"""Recompute the source evidence required by the v3 reuse writer.

The capture header is an observation, not an approval bit.  A v3 record binds
the observation and the source topology to a digest, then every reader that
uses the record can run the same bounded checks again.  This module deliberately
does not import the v3 schema so it can be used by both the writer and the
schema reader without a circular dependency.
"""

from __future__ import annotations

import copy
import math
import re

from .template_classification import SEMANTICS, graphic_role
from .template_schema import TemplateError, digest
from .template_topology import electrical_issues, evidence_gaps

QUALIFICATION_SCHEMA = "cad.template.reuse-qualification.v2"
OBSERVATION_SCHEMA = "cad.template.electrical-observation.v1"
PROOF_CODES = (
    "hierarchy_not_expanded",
    "parameter_values_are_static",
    "inherited_connections_not_resolved",
    "mosaic_members_not_expanded",
    "bus_members_not_expanded",
    "dynamic_text_not_evaluated",
    "master_geometry_not_captured",
)
GEOMETRY_GAPS = frozenset({"coordinate_scale_missing", "coordinate_rounded_to_dbu"})
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _error(message):
    raise TemplateError("source qualification: " + message)


def _point(value):
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(type(v) in (int, float) and math.isfinite(v) for v in value)
    )


def _source_view(record):
    source = record.get("source") or {}
    return source.get("lib"), source.get("cell"), source.get("view")


def _asset_instances(record):
    asset = ((record.get("assets") or {}).get("schematic") or {})
    rows = asset.get("instances")
    if not isinstance(rows, list):
        _error("schematic instance inventory is missing")
    result = []
    topology_devices = {
        d.get("id"): d for d in (record.get("topology") or {}).get("devices", [])
    }
    seen_devices = set()
    for row in rows:
        if not isinstance(row, dict):
            _error("schematic instance inventory is malformed")
        values = (row.get("libName"), row.get("cellName"), row.get("viewName"))
        # Normalized PDK-neutral records may deliberately omit the raw master
        # fields.  In that case the immutable topology master is the only
        # allowed fallback; it is still checked against the topology below.
        if any(not isinstance(v, str) or not v.strip() for v in values):
            device = next(
                (d for d in (record.get("topology") or {}).get("devices", [])
                 if d.get("id") == row.get("device")),
                None,
            )
            master = (device or {}).get("master") or {}
            values = (master.get("library"), master.get("cell"), master.get("view"))
        if any(not isinstance(v, str) or not v.strip() for v in values):
            _error("schematic instance master identity is incomplete")
        device_id = row.get("device")
        if device_id not in topology_devices or device_id in seen_devices:
            _error("schematic instance device coverage is incomplete")
        expected_master = topology_devices[device_id].get("master") or {}
        if all(isinstance(expected_master.get(k), str) for k in ("library", "cell", "view")):
            if values != tuple(expected_master[k] for k in ("library", "cell", "view")):
                _error("schematic instance master differs from topology master")
        seen_devices.add(device_id)
        result.append((tuple(values), row))
    if seen_devices != set(topology_devices):
        _error("schematic instance inventory does not cover topology devices")
    return result


def _graphic_views(record):
    if record.get("source", {}).get("classification_semantics") != SEMANTICS:
        return set()
    result = set()
    for row in record.get("topology", {}).get("ignored_graphics", []):
        master = row.get("master") or {}
        target = tuple(master.get(k) for k in ("library", "cell", "view"))
        if any(not isinstance(v, str) or not v for v in target):
            _error("ignored graphic master identity is incomplete")
        role = graphic_role(dict(cellName=target[1], master_available=True,
                                 master_view_type=row.get("master_view_type"),
                                 builtin_library=row.get("builtin_library")))
        if not role or row.get("role") != role or row.get("kind") != role:
            _error("ignored graphic lacks installed symbol evidence")
        result.add(target)
    return result


def _expected_views(record):
    source = _source_view(record)
    if any(not isinstance(v, str) or not v for v in source):
        _error("source view identity is incomplete")
    expected = {source}
    for target, _ in _asset_instances(record):
        expected.add(target)
    return expected


def _list_or_null(value, field):
    if value is None:
        return
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        _error(field + " must be null or a string list")


def _observation(record):
    source = record.get("source") or {}
    observation = source.get("electrical_observation")
    if not isinstance(observation, dict) or observation.get("schema") != OBSERVATION_SCHEMA:
        _error("electrical observation with the reviewed schema is required")
    views = observation.get("views")
    if not isinstance(views, list) or not views:
        _error("electrical observation must contain views")
    graphics = _graphic_views(record)
    current = source.get("classification_semantics") == SEMANTICS
    expected = _expected_views(record) | graphics
    indexed = {}
    for row in views:
        if not isinstance(row, dict):
            _error("electrical observation view is malformed")
        required = {
            "target", "view_type", "source_view", "parameterized", "hierarchy_count",
            "instance_count", "mosaic_count", "terminal_expressions", "signal_expressions",
        }
        if set(row) != required:
            _error("electrical observation view fields are incomplete")
        target = row["target"]
        if not isinstance(target, list) or len(target) != 3 or any(
            not isinstance(v, str) or not v for v in target
        ):
            _error("electrical observation target is malformed")
        key = tuple(target)
        if key in indexed:
            _error("electrical observation contains duplicate views")
        if key not in expected and (current or key[0] != "basic"):
            _error("electrical observation contains an unlisted source/master view")
        if row["source_view"] not in {"source", "master"}:
            _error("electrical observation source_view is invalid")
        if row["source_view"] == "source" and key != _source_view(record):
            _error("only the selected source cell may be marked source")
        if row["source_view"] == "master" and key == _source_view(record):
            _error("source cell cannot also be marked master")
        if not isinstance(row["view_type"], str) or not row["view_type"]:
            _error("electrical observation view_type is missing")
        if type(row["parameterized"]) is not bool:
            _error("electrical observation parameterized must be boolean")
        for field in ("hierarchy_count", "instance_count", "mosaic_count"):
            if type(row[field]) is not int or row[field] < 0:
                _error("electrical observation " + field + " is invalid")
        _list_or_null(row["terminal_expressions"], "terminal_expressions")
        _list_or_null(row["signal_expressions"], "signal_expressions")
        indexed[key] = row
    missing = expected - set(indexed)
    if missing:
        _error("electrical observation does not cover the source/master inventory")
    source_row = indexed[_source_view(record)]
    source_instances = len(_asset_instances(record))
    if current:
        source_instances += len(record.get("topology", {}).get("ignored_graphics", []))
        if source_row["instance_count"] != source_instances:
            _error("source instance count differs from the captured inventory")
        if any(indexed[key]["view_type"] != "schematicSymbol" or
               indexed[key]["instance_count"] != 0 for key in graphics):
            _error("ignored graphics must be flat symbol masters")
    if source_row["instance_count"] < source_instances:
        _error("source instance count is smaller than the captured inventory")
    proof = {
        "hierarchy_not_expanded": all(row["hierarchy_count"] == 0 for row in indexed.values()),
        "parameter_values_are_static": all(not row["parameterized"] for row in indexed.values()),
        "inherited_connections_not_resolved": all(
            not row["terminal_expressions"] and not row["signal_expressions"]
            for row in indexed.values()
        ),
        "mosaic_members_not_expanded": all(row["mosaic_count"] == 0 for row in indexed.values()),
        "bus_members_not_expanded": _bus_proof(record),
        "dynamic_text_not_evaluated": source.get("dynamic_text_policy")
        == "ignored_for_electrical_graph",
        "master_geometry_not_captured": _geometry_proof(record),
    }
    return observation, proof


def _bus_proof(record):
    topology = record.get("topology") or {}
    for net in topology.get("nets", []):
        name = net.get("source_name")
        if net.get("num_bits") != 1 or not isinstance(name, str):
            return False
        if "<" in name or "[" in name or ">" in name or "]" in name:
            return False
    for device in topology.get("devices", []):
        for pin in device.get("pins", []):
            name = pin.get("name")
            if not isinstance(name, str) or any(c in name for c in "<[]>"):
                return False
    return True


def _geometry_proof(record):
    asset = ((record.get("assets") or {}).get("schematic") or {})
    wire = asset.get("wire_reference")
    from .template_wire_contacts import LEGACY_VERSION, VERSION, has_contact_evidence

    if not isinstance(wire, dict) or wire.get("schema") not in {LEGACY_VERSION, VERSION}:
        return False
    try:
        has_contact_evidence(wire, asset.get("shapes", []))
    except TemplateError:
        return False
    if not isinstance(wire.get("dependency_digest"), str) or not HEX64.fullmatch(
        wire["dependency_digest"]
    ):
        return False
    geometry = wire.get("geometry")
    if not isinstance(geometry, list):
        return False
    devices = record.get("topology", {}).get("devices", [])
    expected = {d.get("id"): {p.get("name") for p in d.get("pins", [])} for d in devices}
    expected_masters = {
        d.get("id"): d.get("master") or {}
        for d in devices
    }
    if any(not isinstance(k, str) or not isinstance(v, set) for k, v in expected.items()):
        return False
    indexed = {}
    for row in geometry:
        if not isinstance(row, dict) or not isinstance(row.get("device"), str):
            return False
        device = row["device"]
        if device in indexed or device not in expected:
            return False
        box = row.get("occupied_bbox")
        if not isinstance(box, list) or len(box) != 2 or not all(_point(p) for p in box):
            return False
        if not isinstance(row.get("library_path"), str) or not row["library_path"]:
            # PDK-neutral records may keep only the immutable master identity.
            # Native capture-v2 records always carry the resolved library path.
            if not (isinstance(row.get("master"), dict) and row["master"].get("library")):
                return False
        master = expected_masters[device]
        if isinstance(row.get("master"), dict):
            if any(row["master"].get(k) != master.get(k) for k in ("library", "cell", "view")):
                return False
        if not isinstance(row.get("revision"), str) or not HEX64.fullmatch(row["revision"]):
            if not (isinstance(row.get("master"), dict) and row["master"].get("revision")):
                return False
        terminals = row.get("terminals")
        if not isinstance(terminals, list):
            return False
        terminal_names = set()
        for terminal in terminals:
            if not isinstance(terminal, dict) or not isinstance(terminal.get("name"), str):
                return False
            name = terminal["name"]
            if name in terminal_names:
                return False
            terminal_names.add(name)
            anchors = terminal.get("anchors")
            if not isinstance(anchors, list) or not anchors:
                return False
            anchor_ids = set()
            for anchor in anchors:
                if not isinstance(anchor, dict) or not isinstance(anchor.get("id"), str):
                    return False
                if anchor["id"] in anchor_ids or not _point(anchor.get("xy")):
                    return False
                anchor_ids.add(anchor["id"])
        if terminal_names != expected[device]:
            return False
        indexed[device] = row
    if set(indexed) != set(expected):
        return False
    ports = record.get("topology", {}).get("ports", [])
    port_rows = wire.get("ports")
    if not isinstance(port_rows, list):
        return False
    port_names = {p.get("name") for p in ports}
    if len(port_rows) != len(ports):
        return False
    seen_ports = set()
    for row in port_rows:
        if (
            not isinstance(row, dict)
            or row.get("port") not in port_names
            or row.get("port") in seen_ports
            or not isinstance(row.get("id"), str)
            or not _point(row.get("xy"))
        ):
            return False
        seen_ports.add(row["port"])
    if seen_ports != port_names:
        return False
    gaps = wire.get("gaps", [])
    return isinstance(gaps, list) and not gaps


def qualification_input(record):
    """Return the immutable source facts used for the qualification digest."""
    topology = copy.deepcopy(record.get("topology") or {})
    topology.pop("fingerprint", None)
    topology.pop("counts", None)
    for device in topology.get("devices", []):
        device["role"] = device.get("source_role", device.get("role"))
        device["kind"] = device.get("source_kind", device.get("kind"))
        if "source_attributes" in device:
            device["attributes"] = copy.deepcopy(device["source_attributes"])
        for key in ("source_role", "source_kind", "source_attributes"):
            device.pop(key, None)
    source = copy.deepcopy(record.get("source") or {})
    # Paths and tool/session details are provenance, not electrical proof.
    for key in ("working_directory", "forced_cds_lib", "reuse_qualification"):
        source.pop(key, None)
    return {
        "capture_sha256": record.get("capture_sha256"),
        "source": source,
        "topology": topology,
        "wire_reference": copy.deepcopy(
            ((record.get("assets") or {}).get("schematic") or {}).get("wire_reference")
        ),
    }


def qualification_issues(record):
    """Explain independent source defects before callers spend a round trip adding types."""
    issues = []
    try:
        _, proof = _observation(record)
    except TemplateError as exc:
        issues.append(dict(code="source_not_qualified", object_ref=None,
                           message=str(exc)[:2000], next_action="repair_source_and_recapture"))
    else:
        for code, passed in proof.items():
            if not passed:
                issues.append(dict(code=code, object_ref=None,
                                   message="Source evidence does not establish " + code + ".",
                                   next_action="repair_source_and_recapture"))
    try:
        issues.extend(electrical_issues(record["topology"]))
    except TemplateError as exc:
        issues.append(dict(code="source_topology_unsupported", object_ref=None,
                           message=str(exc)[:2000], next_action="select_supported_source"))
    for row in record["topology"].get("gaps", []):
        code = row["code"]
        if code not in {*PROOF_CODES, *GEOMETRY_GAPS}:
            issues.append(dict(code=code, object_ref=row.get("instance"),
                               message="Source capture reports " + code + ".",
                               next_action="repair_source_and_recapture"))
    return issues


def qualify_source(record):
    """Validate and attach a recomputable qualification to a v3 candidate."""
    observation, proof = _observation(record)
    missing = sorted(code for code, passed in proof.items() if not passed)
    if missing:
        _error("evidence gaps prevent v3 reuse: " + ", ".join(missing))
    unresolved = sorted(set(evidence_gaps(record["topology"])) - set(PROOF_CODES))
    if unresolved:
        _error("unresolved source gaps: " + ", ".join(unresolved))
    # The generic capture header is deliberately retained for audit, but its
    # five reviewed limitations are cleared in the normalized electrical graph.
    record["topology"]["gaps"] = [
        row for row in record["topology"].get("gaps", [])
        if row.get("code") not in PROOF_CODES
    ]
    wire = ((record.get("assets") or {}).get("schematic") or {}).get("wire_reference")
    qualification = {
        "schema": QUALIFICATION_SCHEMA,
        "proof": proof,
        "resolved_gaps": list(PROOF_CODES),
        "observation_digest": digest(observation),
        "wire_reference_digest": digest(wire),
        "input_digest": digest(qualification_input(record)),
    }
    record.setdefault("source", {})["reuse_qualification"] = qualification
    return record


def validate_qualification(record):
    """Re-run source checks and reject a tampered or stale qualification."""
    qualification = (record.get("source") or {}).get("reuse_qualification")
    if not isinstance(qualification, dict) or qualification.get("schema") != QUALIFICATION_SCHEMA:
        _error("reusable v3 record lacks qualification evidence")
    expected_keys = {
        "schema", "proof", "resolved_gaps", "observation_digest", "wire_reference_digest",
        "input_digest",
    }
    if set(qualification) != expected_keys:
        _error("qualification fields are incomplete")
    observation, proof = _observation(record)
    if qualification["proof"] != proof or any(not proof.get(code) for code in PROOF_CODES):
        _error("qualification proof no longer matches source observation")
    wire = ((record.get("assets") or {}).get("schematic") or {}).get("wire_reference")
    if qualification["observation_digest"] != digest(observation):
        _error("electrical observation digest changed")
    if qualification["wire_reference_digest"] != digest(wire):
        _error("wire reference digest changed")
    if qualification["input_digest"] != digest(qualification_input(record)):
        _error("source topology or capture identity changed")
    unresolved = evidence_gaps(record["topology"])
    if unresolved:
        _error("qualified record contains unresolved source gaps: " + ", ".join(unresolved))
    if qualification["resolved_gaps"] != list(PROOF_CODES):
        _error("qualification resolved gap list is invalid")
    return True


def require_qualified(record):
    """Adaptation entry point: legacy structural v3 records are read-only."""
    try:
        validate_qualification(record)
    except TemplateError:
        raise
    return True
