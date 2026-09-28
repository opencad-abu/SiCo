"""B0 preflight for a future isolated worker; this module never launches jobs."""

from __future__ import annotations

from cadai.inspection import build_read_only_skill

from ..transport.framing import strict_json

PROTOCOL = "cad_ai_background_probe.v1"
MAX_PROBE_BYTES = 256 * 1024


def _target(arguments):
    if not isinstance(arguments, dict) or set(arguments) - {
        "lib", "cell", "view", "view_type", "max_items", "include_placement"
    }:
        raise ValueError("Background inspection requires an explicit database target")
    for key in ("lib", "cell"):
        if not isinstance(arguments.get(key), str) or not arguments[key]:
            raise ValueError("Background inspection requires lib and cell")
    if arguments.get("view_type", "schematic") != "schematic":
        raise ValueError("Background probe supports only schematic views")
    build_read_only_skill("inspect_schematic", arguments)
    return {"lib": arguments["lib"], "cell": arguments["cell"],
            "view": arguments.get("view") or "schematic"}


def build_background_probe(method, arguments):
    if method != "inspect_schematic":
        raise ValueError("Background probe supports only inspect_schematic")
    target = _target(arguments)
    # Reuse the existing validated fixed-shape serializer, including escaping.
    expression = build_read_only_skill("inspect_schematic", dict(arguments, **target))
    return expression.replace("aiInspectSchematic(", "aiBackgroundProbe(", 1)


def assess_background_probe(raw, *, target, expected_version):
    unavailable = dict(ok=False, available=False, code="background_unavailable",
                       method="inspect_schematic", automatic_resume_allowed=False)
    try:
        expected = _target(target)
        if not isinstance(raw, (bytes, str)) or len(raw if isinstance(raw, bytes) else raw.encode()) > MAX_PROBE_BYTES:
            raise ValueError("Probe exceeds limit")
        record = strict_json(raw)
        if record.get("protocol") != PROTOCOL or record.get("target") != expected:
            raise ValueError("Probe identity mismatch")
        if not expected_version or record.get("virtuoso_version") != expected_version:
            raise ValueError("Probe version mismatch")
        if record.get("ok") is not True:
            reasons = {"explicit_target_required", "unsupported_view_type", "api_unavailable",
                       "cellview_open_failed", "view_type_mismatch", "writable_handle", "modified_target",
                       "technology_unavailable", "master_unavailable", "dependency_limit", "cdf_unavailable",
                       "unsupported_dependency", "inspection_failed", "close_failed", "probe_failed"}
            return dict(unavailable, reason=record.get("reason") if record.get("reason") in reasons else "probe_failed")
        if (record.get("open_mode") != "r" or record.get("modified") is not False
                or record.get("window_required") is not False or record.get("closed") is not True
                or record.get("callbacks_executed") is not False
                or not isinstance(record.get("technology_library"), str) or not record["technology_library"]):
            raise ValueError("Probe read-only conditions missing")
        dependencies = record.get("dependencies")
        if (not isinstance(dependencies, list) or len(dependencies) > 200
                or type(record.get("instance_count")) is not int
                or record["instance_count"] != len(dependencies)):
            raise ValueError("Invalid dependency evidence")
        masters = {}
        for item in dependencies:
            if (not isinstance(item, dict) or item.get("open_mode") != "r"
                    or item.get("modified") is not False
                    or not isinstance(item.get("instance"), str) or not item["instance"]
                    or item["instance"] in masters
                    or not isinstance(item.get("master"), dict)
                    or not all(isinstance(item["master"].get(k), str) and item["master"][k]
                               for k in ("lib", "cell", "view"))
                    or not isinstance(item.get("technology_library"), str) or not item["technology_library"]
                    or type(item.get("cdf_present")) is not bool
                    or type(item.get("cdf_parameter_count")) is not int
                    or not 0 <= item["cdf_parameter_count"] <= 512
                    or (not item["cdf_present"] and item["cdf_parameter_count"] != 0)):
                raise ValueError("Invalid master/CDF evidence")
            masters[item["instance"]] = item["master"]
        inspection = record.get("inspection")
        if not isinstance(inspection, dict) or inspection.get("ok") is not True:
            return dict(unavailable, reason="inspection_failed")
        if (inspection.get("kind") != "schematic" or inspection.get("cellview") != expected
                or inspection.get("modified") is not False
                or not all(isinstance(inspection.get(k), list) for k in ("instances", "nets", "terminals"))):
            raise ValueError("Inspection identity or read-only evidence mismatch")
        instances = inspection["instances"]
        if (len(instances) > len(dependencies)
                or type(inspection.get("truncated")) is not bool
                or (not inspection["truncated"] and len(instances) != len(dependencies))):
            raise ValueError("Inspection dependency count mismatch")
        seen = set()
        for item in instances:
            if (not isinstance(item, dict) or not isinstance(item.get("name"), str)
                    or item["name"] in seen or item["name"] not in masters
                    or {k: item.get(k) for k in ("lib", "cell", "view")} != masters[item["name"]]):
                raise ValueError("Inspection master mismatch")
            seen.add(item["name"])
        return dict(ok=True, available=True, protocol=PROTOCOL, method="inspect_schematic",
                    target=expected, snapshot_scope="saved_oa", foreground_memory_visible=False,
                    automatic_resume_allowed=False, evidence=record)
    except (TypeError, ValueError, KeyError):
        return dict(unavailable, reason="invalid_probe")
