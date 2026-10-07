"""Persistent template -> task-bound symbol preview/create/inspection MCP adapter."""

from __future__ import annotations

import json
import sqlite3

from .circuit_schema import string_schema, tool
from .circuit_spec_schema import ID
from .skill_diagnostics import carry_output
from .skill_result import call_skill
from .template_catalog import TemplateCatalog
from .template_circuit_schema import NET_SCOPE_MAP
from .template_schema import REF, TemplateError, TemplateUnavailable, validate
from .template_symbol_plan import make_plan, write_preview

NAME = string_schema(96, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
REQUEST = string_schema(96, pattern=r"^[A-Za-z0-9_-]+$")
TASK = string_schema(128, pattern=r"^task:[A-Za-z0-9_-]+$")
PREVIEW = string_schema(128, pattern=r"^template-symbol-preview:[A-Za-z0-9_-]+$")
SYMBOL = string_schema(128, pattern=r"^template-symbol:[A-Za-z0-9_-]+$")
DIGEST = string_schema(64, pattern=r"^[a-f0-9]{64}$")
TOOLS = [
    tool(
        "preview_template_symbol",
        "Plan a symbol from a persistent template. Select style=template_outline to preserve "
        "captured gate/device outlines; grid pin anchors are adjusted so terminal stubs touch "
        "the actual body and remain <=5 grid steps. Missing schematic ports get explicit pins. "
        "Default rectangular_module preserves compatibility. "
        "Validate the saved schematic's full interface, global nets and confirmed task mode. "
        "Requires an absent symbol in a writable design library; the captured source cell "
        "may receive its own missing symbol, otherwise use a separate work library. "
        "No OA writes. Returns "
        "preview_ref/digest and SVG. Port renaming is explicit; bus indices/order are preserved. "
        "For a circuit localized through boundary pins, pass the same net_scope_map and "
        "port_map to check the target schematic against its local interface.",
        {
            "template_ref": REF,
            "task_ref": TASK,
            "request_id": REQUEST,
            "library": NAME,
            "cell": NAME,
            "style": {
                "type": "string",
                "enum": ["rectangular_module", "template_outline"],
                "default": "rectangular_module",
            },
            "outline_scale": {"type": "number", "minimum": 0.25, "maximum": 32, "default": 1},
            "port_roles": {
                "type": "object",
                "maxProperties": 128,
                "additionalProperties": {"type": "string", "enum": ["power", "ground", "signal"]},
                "default": {},
                "description": "Source terminal roles for outline placement. Power pins go above, "
                "ground pins below. Defaults from sig_type=supply/ground; explicitly map roles "
                "when captured metadata is signal/unknown. Does not change electrical sigType.",
            },
            "net_scope_map": NET_SCOPE_MAP,
            "port_map": {
                "type": "object",
                "additionalProperties": string_schema(96),
                "maxProperties": 128,
                "default": {},
            },
        },
        ("template_ref", "task_ref", "request_id", "library", "cell"),
    ),
    tool(
        "create_template_symbol",
        "Create the previewed NEW symbol view; never overwrite. Revalidate target "
        "interface and library, build pins/labels/selection box, save and read back. Inherits "
        "confirmed task mode; foreground window remains open. Reuse the identical request after "
        "unknown transport status. Failures retain partial targets and status. "
        "Replay returns retained status; inspect revalidates.",
        {"preview_ref": PREVIEW, "preview_digest": DIGEST, "request_id": REQUEST},
        ("preview_ref", "preview_digest", "request_id"),
        True,
    ),
    tool(
        "draw_symbol",
        "Draw a NEW symbol from a template_outline preview_ref/digest. First build the standard "
        "symbol, then remove its generated shapes/pins and redraw captured body/stubs, explicit "
        "terminal pins, selection box, stick-font cdsName/partName/cellName/cdsTerm labels. "
        "Never overwrite existing views. Reuses create_template_symbol lifecycle, idempotency, "
        "task mode and inspection.",
        {"preview_ref": PREVIEW, "preview_digest": DIGEST, "request_id": REQUEST},
        ("preview_ref", "preview_digest", "request_id"),
        True,
    ),
    tool(
        "get_symbol_binding",
        "Read and revalidate a retained created Symbol, returning a design master row and "
        "per-instance geometry row for generic circuit/TB planning. Includes actual pin centers "
        "and outward escapes, including power above/ground below; no name-based pin guesses. "
        "Merge rows into the project's bindings/geometry, then run the ordinary previews and "
        "prepare_circuit_creation. Requires static flat scalar single-pin terminals and no "
        "parameterized CDF or callbacks. Does not enumerate PDKs, write OA, or create a TB.",
        {"symbol_ref": SYMBOL, "master_id": ID, "instance_id": ID},
        ("symbol_ref", "master_id", "instance_id"),
    ),
    tool(
        "inspect_template_symbol",
        "Read and revalidate a created symbol's interface, geometry, provenance, "
        "target schematic interface and retained foreground window. "
        "Does not save or reopen GUI windows.",
        {"symbol_ref": SYMBOL},
        ("symbol_ref",),
    ),
]
TOOL_NAMES = frozenset(t["name"] for t in TOOLS)


def skill_literal(value):
    if isinstance(value, list):
        return "list(" + " ".join(skill_literal(v) for v in value) + ")"
    if value is None or value is False:
        return "nil"
    if value is True:
        return "t"
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def call_symbol(name, values, *, workspace, client):
    schema = next(t["inputSchema"] for t in TOOLS if t["name"] == name)
    args = validate(values, schema)
    if client is None:
        raise TemplateUnavailable("symbol workflow requires a live Virtuoso client")
    try:
        plan, artifacts = None, None
        if name == "preview_template_symbol":
            catalog = TemplateCatalog(workspace=workspace)
            record = catalog.get(args["template_ref"])
            plan = make_plan(record, args)
            # Publish first so an I/O error cannot hide a successful live preview.
            artifacts = write_preview(plan, catalog.ensure_private_destination() / "previews")
            function = "aiTemplateSymbolPreview"
            arguments = [
                args["task_ref"],
                args["request_id"],
                plan["template_ref"],
                plan["preview_digest"],
                args["library"],
                args["cell"],
                plan["source"]["lib"],
                plan["source"]["library_path"],
                plan["rows"],
                plan["width"],
                plan["height"],
            ]
            if plan["style"] == "template_outline":
                arguments.append(
                    [
                        plan["body"],
                        plan["stubs"],
                        plan["selection_box"],
                        plan["bootstrap_rows"],
                        plan["pin_labels"],
                    ]
                )
            else:
                arguments.append(None)
            arguments.append(plan["source"]["cell"])
        elif name in {"create_template_symbol", "draw_symbol"}:
            function = "aiDrawSymbol" if name == "draw_symbol" else "aiTemplateSymbolCreate"
            arguments = [args[k] for k in ("preview_ref", "preview_digest", "request_id")]
        elif name == "get_symbol_binding":
            function, arguments = "aiTemplateSymbolBinding", [args["symbol_ref"]]
        else:
            function, arguments = "aiTemplateSymbolInspect", [args["symbol_ref"]]
        code = function + "(" + " ".join(skill_literal(v) for v in arguments) + ")"
        ok, result = call_skill(client, code, native=True)
        if not ok:
            return {**result, "ok": False}
        if name == "get_symbol_binding" and result.get("ok"):
            from .template_symbol_binding import binding_result

            return carry_output(binding_result(result, args), result)
        if plan is not None:
            result = {**result, "plan": plan, "artifacts": artifacts}
        return result
    except (TemplateError, TemplateUnavailable):
        raise
    except (OSError, sqlite3.Error, KeyError, TypeError, ValueError) as exc:
        raise TemplateUnavailable(str(exc)) from exc
