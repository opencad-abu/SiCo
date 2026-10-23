"""MCP-facing generic circuit/TB preview; live PDK discovery is an external tool."""

from __future__ import annotations

import json

from .circuit_geometry import preview_geometry
from .circuit_geometry_schema import GEOMETRY, LAYOUT
from .circuit_schema import tool
from .circuit_spec_plan import preview_circuit
from .circuit_spec_schema import BINDINGS, REF, CircuitSpecError, digest, validate
from .pdk_binding_schema import BINDING_REFS, SELECTED_SPEC, binding_sources
from .template_circuit_schema import TEMPLATE_USE

GENERIC_NAMES = frozenset({"preview_circuit_spec", "preview_circuit_geometry"})
GENERIC_TOOLS = [
    tool(
        "preview_circuit_spec",
        "Validate a circuit/testbench spec against a PDK binding snapshot and return a read-only "
        "topology/layout plan. Prefer binding_refs from bind_pdk_device; do not reconstruct "
        "masters. Explicit bindings must come from the current project session. No callbacks, "
        "OA writes or readiness claims. Optional template_use adds endpoint checks and "
        "placement provenance.",
        {"spec": SELECTED_SPEC, "bindings": BINDINGS, "binding_refs": BINDING_REFS,
         "template_use": TEMPLATE_USE},
        ("spec",),
    ),
    tool(
        "preview_circuit_geometry",
        "Preview grid-aligned labelled routing for explicit placements and master-local pin "
        "anchors; no OA write. Use a preview_digest plus geometry or binding_refs. mode and "
        "ports control internal and external routing; template_use is required for "
        "template-backed plans. Escapes must point into the circuit; layout_review reports "
        "outward stubs.",
        {
            "spec": SELECTED_SPEC,
            "bindings": BINDINGS,
            "binding_refs": BINDING_REFS,
            "preview_digest": REF,
            "geometry": GEOMETRY,
            "layout": LAYOUT,
            "template_use": TEMPLATE_USE,
        },
        ("spec", "preview_digest", "layout"),
        help_text=(
            "Follow geometry_preview_ready from the spec preview, not creation_ready. mode=stub "
            "keeps all stubs. Template port sides/positions take priority over default bands; "
            "missing positions use power/signal/ground ordering, and terminal-row alignment "
            "applies only to continuously routed ports without template positions. Checks occupied "
            "bounds, foreign-net pin/stub contacts and TB/pin placement rules; the escape draws "
            "the wire only while orientation follows the direction and facing edge. Does not "
            "certify label rendering or current session bindings."),
    ),
]

for _definition in GENERIC_TOOLS:
    binding_sources(_definition["inputSchema"],
                    geometry=_definition["name"] == "preview_circuit_geometry")


def checked_preview(args, workspace=None):
    required = {"spec", "bindings"}
    if (
        not isinstance(args, dict)
        or not required <= set(args)
        or set(args) - required - {"template_use"}
    ):
        raise CircuitSpecError(
            "preview_circuit_spec requires spec, bindings and optional template_use"
        )
    result = preview_circuit(args["spec"], args["bindings"])
    if "template_use" in args:
        from .template_circuit import attach_template

        result = attach_template(result, args["template_use"], workspace=workspace)
    return _bounded(result)


# 预览的内联页：请求回显（instances/nets/ports 与整份 bindings 副本）不再重复占用
# 结果预算——它们在请求里已经存在，超限截断只会迫使智能体转去 shell 读 artifact。
# 权威增量保留：binding_snapshot、每个 master 的 revision、以及被省略部分的摘要。
SPEC_ECHO_FIELDS = ("instances", "nets", "ports")
MASTER_PAGE_FIELDS = ("id", "target", "revision", "kind", "classification", "pdk_binding")


def spec_preview_page(result):
    plan = result.get("plan")
    if not isinstance(plan, dict):
        return result
    spec = plan.get("spec")
    if isinstance(spec, dict):
        page = {key: value for key, value in spec.items() if key not in SPEC_ECHO_FIELDS}
        page.update(status="request_echo_omitted", omitted=list(SPEC_ECHO_FIELDS),
                    digest=digest(spec))
        plan["spec"] = page
    bindings = plan.get("bindings")
    if isinstance(bindings, dict):
        page = {key: value for key, value in bindings.items() if key != "masters"}
        page["masters"] = [
            {key: master[key] for key in MASTER_PAGE_FIELDS if key in master}
            for master in bindings.get("masters") or []
        ]
        page.update(status="request_echo_omitted", digest=digest(bindings))
        plan["bindings"] = page
    return result


def call_generic(name, args, workspace=None, pdk_bindings=None):
    validate(args, next(t["inputSchema"] for t in GENERIC_TOOLS if t["name"] == name))
    if "binding_refs" in args and pdk_bindings is None:
        raise CircuitSpecError("Selected device data expired; bind the selected devices again")
    if pdk_bindings is not None:
        args = pdk_bindings.resolve_request(args)
    if name == "preview_circuit_spec":
        return spec_preview_page(checked_preview(args, workspace=workspace))
    required = {"spec", "bindings", "preview_digest", "geometry", "layout"}
    if (
        name != "preview_circuit_geometry"
        or not isinstance(args, dict)
        or not required <= set(args)
        or set(args) - required - {"template_use"}
    ):
        missing = sorted(required - set(args))
        extra = sorted(set(args) - required - {"template_use"})
        raise CircuitSpecError("geometry arguments: missing " + ", ".join(missing)
                               + "; unsupported " + ", ".join(extra))
    if "template_use" in args:
        validate(args["template_use"], TEMPLATE_USE, "template_use")
    return _bounded(preview_geometry(**args, workspace=workspace))


def _bounded(result):
    # Leave room for escaped JSON in the existing 1 MiB MCP envelope.
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if len(json.dumps(text, ensure_ascii=False).encode("utf-8")) > 900000:
        raise CircuitSpecError("preview response exceeds transport budget; preview a smaller block")
    return result
