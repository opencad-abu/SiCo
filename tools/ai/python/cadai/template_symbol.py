"""Persistent template -> task-bound symbol preview/create/inspection MCP adapter."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

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
        "Plan a symbol. style=auto prefers a captured outline or registered family; rectangular "
        "fallback reports fallback_reason. family_ref selects a reusable family. No OA writes.",
        {
            "template_ref": REF,
            "task_ref": TASK,
            "request_id": REQUEST,
            "library": NAME,
            "cell": NAME,
            "style": {
                "type": "string",
                "enum": ["auto", "rectangular_module", "template_outline", "symbol_family"],
                "default": "auto",
            },
            "family_ref": {
                "type": "string",
                "maxLength": 128,
            },
            "slot_map": {
                "type": "object",
                "additionalProperties": string_schema(96),
                "maxProperties": 128,
                "default": {},
            },
            "outline_scale": {"type": "number", "minimum": 0.25, "maximum": 32, "default": 1},
            "label_policy": {
                "type": "string",
                "enum": ["strict", "drop", "preserve"],
                "default": "strict",
            },
            "port_roles": {
                "type": "object",
                "maxProperties": 128,
                "additionalProperties": {"type": "string", "enum": ["power", "ground", "signal"]},
                "default": {},
                "description": "Source terminal roles for outline placement. Power pins go above, "
                "ground pins below. Defaults from sig_type=supply/ground; explicitly map roles "
                "when captured metadata is signal/unknown. Preview returns "
                "port_role_suggestions; suggestions are not applied until passed back here. "
                "Does not change electrical sigType.",
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
        help_text=(
            "For a circuit localized through boundary pins, pass the same net_scope_map and "
            "port_map to check the target schematic against its local interface. family_ref "
            "loads a private symbol family; slot_map binds target terminals to family slots. "
            "label_policy=strict refuses unrenderable source labels; drop omits and reports "
            "them in dropped_labels; preserve renders literal plain text and cdsParam "
            "placeholders as non-evaluated labels and refuses conditional expressions. "
            "port_role_suggestions are review hints only and require explicit port_roles."
        ),
    ),
    tool(
        "create_template_symbol",
        "Create the previewed NEW symbol view; never overwrite. Uses the preview's effective "
        "style and preserves fallback_reason. Revalidates target interface/library, saves and "
        "reads back. Reuse the identical request after unknown status; inspect revalidates.",
        {"preview_ref": PREVIEW, "preview_digest": DIGEST, "request_id": REQUEST},
        ("preview_ref", "preview_digest", "request_id"),
        True,
    ),
    tool(
        "draw_symbol",
        "Draw a NEW symbol from a template_outline or symbol_family preview_ref/digest. First "
        "build the standard symbol, then remove its generated shapes/pins and redraw captured "
        "body/stubs, explicit terminal pins, selection box, fixed annotations and preserved "
        "labels. "
        "Never overwrite existing views. Reuses create_template_symbol lifecycle, idempotency, "
        "task mode and inspection.",
        {"preview_ref": PREVIEW, "preview_digest": DIGEST, "request_id": REQUEST},
        ("preview_ref", "preview_digest", "request_id"),
        True,
    ),
    tool(
        "get_symbol_binding",
        "Read and revalidate a retained created Symbol: a design master row and per-instance "
        "geometry row for generic circuit/TB planning. Includes actual pin centers and outward "
        "escapes (power above/ground below); no name-based pin guesses. "
        "Merge rows into the project's bindings/geometry, then run the ordinary previews. "
        "Requires static flat scalar single-pin terminals and no "
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


def annotation_rows(plan):
    """Flatten preserved labels into the native renderer's literal row format."""
    return [
        [
            item["text"],
            item["xy"],
            item["bbox"],
            item["justify"],
            item["orient"],
            item["font"],
            item["height"],
            item["label_type"],
        ]
        for item in plan.get("annotations", [])
    ]


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
            family_ref = args.get("family_ref")
            family = None
            if family_ref:
                if Path(family_ref).name != family_ref:
                    raise TemplateUnavailable("family_ref must be a plain file name")
                from .template_locations import template_root

                family_path = template_root(workspace) / "symbol_families" / (family_ref + ".json")
                if not family_path.is_file():
                    raise TemplateUnavailable("symbol family not found: " + family_ref)
                try:
                    family = json.loads(family_path.read_text())
                except (OSError, ValueError) as exc:
                    raise TemplateUnavailable("cannot read symbol family: " + str(exc)) from exc
                if family.get("format") != "sico.symbol.family.v1":
                    raise TemplateUnavailable("unsupported symbol family format")
            elif args.get("style", "auto") in {"auto", "symbol_family"}:
                from .template_locations import template_root
                from .template_symbol_family import select_family
                from .template_symbol_family_registry import read_registry

                family_root = template_root(workspace) / "symbol_families"
                try:
                    families, hints = read_registry(family_root)
                except (OSError, TemplateError) as exc:
                    raise TemplateUnavailable(
                        "cannot read symbol family registry: " + str(exc)
                    ) from exc
                selected = select_family(record, families, hints)
                if selected:
                    family = selected[1]
            if family is not None:
                args = {**args, "family": family}
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
            if plan["style"] in {"template_outline", "symbol_family"}:
                arguments.append(
                    [
                        plan["body"],
                        plan["stubs"],
                        plan["selection_box"],
                        plan["bootstrap_rows"],
                        plan["pin_labels"],
                        annotation_rows(plan),
                    ]
                )
            else:
                arguments.append(None)
            arguments.extend([plan["source"]["cell"], plan["style"]])
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
