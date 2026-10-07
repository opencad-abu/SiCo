"""Compile verified generic plans into fixed native creation lifecycle calls."""

import json

from .circuit_geometry import preview_geometry
from .circuit_geometry_schema import GEOMETRY, LAYOUT
from .circuit_schema import string_schema, tool
from .circuit_spec_plan import preview_circuit
from .circuit_spec_schema import BINDINGS, SPEC, TARGET, CircuitSpecError, validate
from .pdk_binding import guards, require_service
from .pdk_binding_schema import BINDING_REFS, SELECTED_SPEC, binding_sources
from .skill_result import decode_skill_result
from .template_circuit_schema import TEMPLATE_USE

REQUEST = string_schema(80, pattern=r"^[A-Za-z0-9_-]+$")
REF = string_schema(128, pattern=r"^[A-Za-z0-9:_-]+$")
DIGEST = string_schema(64, pattern=r"^[a-f0-9]{64}$")
GEOMETRY_ARGS = {
    "spec": SPEC,
    "bindings": BINDINGS,
    "preview_digest": DIGEST,
    "geometry": GEOMETRY,
    "layout": LAYOUT,
}
CREATE_TOOLS = [
    tool(
        "inspect_circuit_target",
        "Inspect the exact intended schematic, including unsaved contents in the captured editor. "
        "An existing empty view can be reused. Existing content requires the user's decision to "
        "continue, replace (with backup), or use another target. Retain target_ref for "
        "preparation; changed content invalidates it. Inspect the entry schematic before "
        "allocating a new target.",
        {"target": TARGET, "task_ref": REF, "request_id": REQUEST},
        ("target", "task_ref", "request_id"),
    ),
    tool(
        "prepare_circuit_creation",
        "Recompute a generic geometry plan and validate its exact "
        "selected masters, CDF and pin geometry in the current session. Retain a task-bound "
        "prepare_ref without creating OA data. A design-unit TB requires its DUT symbol first: "
        "inspect/reuse it, or "
        "create, save and verify the missing symbol before preparation. Never copy or flatten "
        "the DUT schematic into the TB. Requires static symbols, supported scalar "
        "parameters and the standard 0.0625 schematic grid. CDF callbacks/hooks require an "
        "installed device support validated automatically in the current session. "
        "Prefer binding_refs from bind_pdk_device to reuse complete bindings and geometry. "
        "Never ask users for internal registration or data references. "
        "Does not discover or classify PDKs. Supply template_use for a template-backed plan. "
        "Reuse identical request_id after unknown status.",
        {
            **GEOMETRY_ARGS,
            "spec": SELECTED_SPEC,
            "binding_refs": BINDING_REFS,
            "geometry_plan_digest": DIGEST,
            "task_ref": REF,
            "request_id": REQUEST,
            "template_use": TEMPLATE_USE,
            "target_ref": REF,
            "target_action": {"type": "string", "enum": ["reuse_empty", "continue", "replace"]},
        },
        ("spec", "preview_digest", "layout", "geometry_plan_digest", "task_ref", "request_id"),
    ),
    tool(
        "create_circuit_from_plan",
        "Create or populate the inspected schematic using a retained prepare_ref. "
        "Revalidate live dependencies and exact target contents. Evaluate built-in CDF callbacks "
        "for all instances before opening the destination for writing; reject conflicting "
        "requested values. Reuse evaluated parameters without repeating those callbacks, then "
        "create instances/pins/stubs/notes, "
        "schCheck, verify connectivity/parameters, save and reopen for readback. Foreground "
        "windows remain open. Existing content requires an evidence-bound decision; replacement "
        "saves a backup first. Identical request_id returns retained status "
        "without repeating writes, including after partial failure. The final report includes "
        "svg_preview with a workspace SVG path and checksum; SVG source stays outside model "
        "context; native Cadence expressions "
        "may remain as text. Preview export failure does not invalidate saved readback. "
        "Does not create Maestro.",
        {"prepare_ref": REF, "geometry_plan_digest": DIGEST, "request_id": REQUEST},
        ("prepare_ref", "geometry_plan_digest", "request_id"),
        True,
    ),
    tool(
        "inspect_created_circuit",
        "Read the retained creation status and revalidate saved "
        "geometry, connections, parameters, work-library path and bound foreground window. "
        "Does not save or check user edits. Failed creation returns its retained stage.",
        {"circuit_ref": REF},
        ("circuit_ref",),
    ),
    tool(
        "release_created_circuit",
        "Detach a completed creation record; keep foreground window. "
        "Failed or modified background references are retained. Does not save, delete or close "
        "user windows. Released records remain available for idempotent status retrieval.",
        {"circuit_ref": REF},
        ("circuit_ref",),
        True,
    ),
]
CREATE_NAMES = frozenset(t["name"] for t in CREATE_TOOLS)


def skill_literal(value):
    if value is None or value is False:
        return "nil"
    if value is True:
        return "t"
    if isinstance(value, list):
        return "list(" + " ".join(skill_literal(v) for v in value) + ")"
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


binding_sources(next(t["inputSchema"] for t in CREATE_TOOLS
                     if t["name"] == "prepare_circuit_creation"), geometry=True)


def payload(args, workspace=None):
    preview = preview_circuit(args["spec"], args["bindings"])
    optional = {"template_use": args["template_use"]} if "template_use" in args else {}
    result = preview_geometry(
        **{k: args[k] for k in GEOMETRY_ARGS}, **optional, workspace=workspace
    )
    if result["geometry_plan_digest"] != args["geometry_plan_digest"]:
        raise CircuitSpecError("geometry_plan_digest differs; preview exact geometry again")
    plan = result["plan"]
    if not plan["geometry_checks_passed"]:
        raise CircuitSpecError("resolve geometry conflicts before creation preparation")
    if args["layout"]["grid"] != 0.0625:
        raise CircuitSpecError("initial live creation backend requires 0.0625 schematic grid")
    if not args["layout"].get("routing"):
        raise CircuitSpecError(
            "Confirm the wiring choice before creating: pass layout.routing "
            "(stub is the recommended default; the Copilot question asks the user and "
            'records layout.routing.decision = {"source": "user"|"timeout", "chosen": ...})'
        )
    spec = preview["plan"]["spec"]
    if any(i["unconnected"] for i in spec["instances"]):
        raise CircuitSpecError("explicit unconnected terminals require a later schCheck policy")
    masters = {m["id"]: m for m in args["bindings"]["masters"]}
    geometry = {r["instance"]: r for r in args["geometry"]["instances"]}
    positions = {r["id"]: r for r in plan["instances"]}
    instance_names = {instance["id"]: instance["name"] for instance in spec["instances"]}
    ground_nets = {n["name"] for n in spec["nets"] if n["scope"] == "ground"}
    instances = []
    for inst in spec["instances"]:
        master, geo, pos = masters[inst["master"]], geometry[inst["id"]], positions[inst["id"]]
        params = {p["name"]: p for p in master["parameters"]}
        order = master["callbacks"].get("order")
        if master["callbacks"].get("extension_ref") == "builtin:cdf-callbacks:v1":
            if (not order or len(order) != len(set(order))
                    or set(inst["parameters"]) - set(order)):
                raise CircuitSpecError("Built-in CDF creation requires an explicit callback order")
        names = ([n for n in order if n in inst["parameters"]]
                 if order else sorted(inst["parameters"]))
        instances.append(
            [
                inst["name"],
                [master["target"][k] for k in ("library", "cell", "view")],
                master["library_path"],
                pos["xy"],
                pos["orientation"],
                [[n, params[n]["type"], inst["parameters"][n]] for n in names],
                [
                    [n, "gnd!" if v in ground_nets else v]
                    for n, v in sorted(inst["connections"].items())
                ],
                [[p["name"], p["direction"]] for p in master["terminals"]],
                geo["occupied_bbox"],
                [[t["name"], [a["xy"] for a in t["anchors"]]] for t in geo["terminals"]],
                master["kind"],
                master["callbacks"].get("extension_ref"),
            ]
        )

    def reference(endpoint):
        if "port" in endpoint:
            return ["port", endpoint["port"]]
        return [
            "inst",
            instance_names.get(endpoint["instance"], endpoint["instance"]),
            endpoint["terminal"],
        ]

    def wire_row(wire):
        from .circuit_wire_payload import draw_points

        if wire.get("kind") == "cadence_route":
            # The native router resolves both real pin centres and lays the path;
            # the row carries the endpoints, their escape vectors and the stub
            # length used when a route fails. The net name travels with every
            # edge so a refused edge can label its fallback stubs; the trailing
            # flag keeps one label per net on the successful path.
            return [
                wire["net"],
                [],
                None,
                [
                    reference(wire["refs"][0]),
                    reference(wire["refs"][1]),
                    wire["escapes"][0],
                    wire["escapes"][1],
                    wire["stub_length"],
                    True if wire.get("label") else None,
                ],
            ]
        return [
            wire["net"] if wire["label"] else None,
            draw_points(wire["points"]),
            wire["label"]["xy"] if wire["label"] else None,
        ]

    # Coincident same-net pins can require identical labelled stubs. Preserve all
    # logical endpoints in the verified preview, but draw each exact native row
    # once: schCreateWire returns nil for an already-drawn path. This does not
    # merge partial overlaps, differently labelled wires, or native router edges.
    wires, drawn = [], set()
    for wire in plan["wires"]:
        row = wire_row(wire)
        key = json.dumps([wire["net"], row])
        if wire.get("kind") != "cadence_route":
            if key in drawn:
                continue
            drawn.add(key)
        wires.append(row)

    # Port rows keep the stub direction and the pin-master orientation as separate
    # fields: ``escape`` only draws the wire, ``orientation`` only places the pin.
    # The native builder must never derive one from the other.
    compiled = [
        [spec["target"][k] for k in ("library", "cell", "view")],
        instances,
        [
            [p["name"], p["direction"], p["xy"], p["escape"], p["orientation"]]
            for p in plan["ports"]
        ],
        wires,
        [[n["rendered_text"], n["bbox"]] for n in plan["notes"]],
    ]
    if plan["routing"].get("engine") == "template":
        from .template_routing_receipt import receipt

        compiled.append(receipt(plan))
    return compiled


def build_create_call(name, args, workspace=None):
    schema = next(t["inputSchema"] for t in CREATE_TOOLS if t["name"] == name)
    validate(args, schema)
    if name == "inspect_circuit_target":
        function, values = "aiCrInspectTarget", [
            args["task_ref"],
            args["request_id"],
            [args["target"][k] for k in ("library", "cell", "view")],
        ]
    elif name == "prepare_circuit_creation":
        if not set(GEOMETRY_ARGS) <= set(args) or "binding_refs" in args:
            missing = sorted(set(GEOMETRY_ARGS) - set(args))
            raise CircuitSpecError(
                "Creation inputs missing: " + ", ".join(missing)
                + ". Pass binding_refs from bind_pdk_device through prepare_circuit_creation "
                "to expand bindings and geometry before native compilation"
            )
        function = "aiCreatePrepare"
        values = [
            args["task_ref"],
            args["request_id"],
            args["geometry_plan_digest"],
            payload(args, workspace=workspace),
        ]
        if ("target_ref" in args) != ("target_action" in args):
            raise CircuitSpecError("Use the inspected target_ref and target_action together")
        if "target_ref" in args:
            values.extend([guards(args) or None, [args["target_ref"], args["target_action"]]])
        elif guards(args):
            values.append(guards(args))
    elif name == "create_circuit_from_plan":
        function, values = (
            "aiCreateExecute",
            [args[k] for k in ("prepare_ref", "geometry_plan_digest", "request_id")],
        )
    else:
        function = "aiCreateInspect" if name == "inspect_created_circuit" else "aiCreateRelease"
        values = [args["circuit_ref"]]
    code = function + "(" + " ".join(skill_literal(v) for v in values) + ")"
    if len(code.encode("utf-8")) > 65536:
        raise CircuitSpecError("creation payload exceeds 64 KiB native request budget")
    return code


def call_create(name, args, client, workspace=None, pdk_bindings=None):
    from .circuit_create_feedback import attach_creation_feedback

    ok, result = _call_create(name, args, client, workspace, pdk_bindings)
    return ok, attach_creation_feedback(result, name, args, ok=ok)


def _call_create(name, args, client, workspace, pdk_bindings):
    validate(args, next(t["inputSchema"] for t in CREATE_TOOLS if t["name"] == name))
    if name == "prepare_circuit_creation" and pdk_bindings is not None:
        args = pdk_bindings.resolve_request(args)
    code = build_create_call(name, args, workspace=workspace)
    if name == "prepare_circuit_creation":
        if guards(args):
            require_service(pdk_bindings)
        if pdk_bindings is not None:
            pdk_bindings.validate_prepare(args)
    ok, response = client.call(
        "eval_skill_native", {"code": code}
    )
    if not ok:
        return decode_skill_result(response, transport_ok=False)
    ok, result = decode_skill_result(response)
    if name == "inspect_circuit_target" and ok:
        result = target_decision(result)
    if name == "create_circuit_from_plan" and result.get("code") == "pdk_validation_required":
        guard = result["pdk_guard"]
        require_service(pdk_bindings).validate_execute(guard)
        # Native retained status wins on retries; otherwise this marker is required before writes.
        ok, response = client.call("eval_skill_native", {
            "code": code[:-1] + " " + skill_literal(guard) + ")",
        })
        if not ok:
            return decode_skill_result(response, transport_ok=False)
        ok, result = decode_skill_result(response)
        if not ok:
            return False, result
    if result.get("template_routing"):
        from .template_routing_receipt import decode_receipt

        result["template_routing"] = decode_receipt(result["template_routing"])
    if result.get("text_layout"):
        from .circuit_annotations import decode_text_layout

        result["text_layout"] = decode_text_layout(result["text_layout"])
    from .circuit_preview import attach_svg_preview

    return ok, attach_svg_preview(result, client, workspace)


TARGET_CHOICES = {"continue": "继续完善现有设计", "replace": "备份后重新设计", "new": "另建单元"}


def target_decision(result):
    contents = result.get("contents")
    if isinstance(contents, list) and len(contents) == 3:
        result = {**result, "contents": {
            "instances": [
                dict(
                    zip(
                        (
                            "name", "master", "xy", "orientation", "bbox",
                            "connections", "properties",
                        ),
                        row,
                    )
                )
                for row in contents[0] or []
            ],
            "ports": [dict(zip(("name", "direction", "net"), row)) for row in contents[1] or []],
            "shapes": [dict(zip(("type", "layer", "bbox", "points", "text", "net"), row))
                       for row in contents[2] or []],
        }}
    if result.get("content_state") != "nonempty":
        return result
    target = "/".join(result["target"])
    return {**result, "decision_required": True, "user_input_required": True,
            "selection_question": {"id": "circuit_target", "header": "现有设计",
                "question": f"{target} 中已有设计内容，本次如何继续？",
                "options": [
                    {
                        "label": TARGET_CHOICES["continue"],
                        "description": "保留当前内容，在其基础上补充设计。",
                    },
                    {
                        "label": TARGET_CHOICES["replace"],
                        "description": "先备份当前内容，再在该视图重新设计。",
                    },
                    {
                        "label": TARGET_CHOICES["new"],
                        "description": "保留当前视图，在另一个单元中设计。",
                    },
                ],
            },
            "next_action": "ask_user_about_existing_design"}
