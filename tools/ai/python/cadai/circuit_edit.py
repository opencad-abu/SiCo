"""Approved in-place edit of an existing schematic cellview.

The agent may rewrite the current cellview - including deleting and re-creating
instances, wires, labels, ports and nets - but only after the user reviewed the
exact plan: :func:`prepare_edit` returns a review plus a question, and the write
only starts when the execution carries the digest of that reviewed plan while the
cellview still holds the contents the review was based on (the inspected target
record proves both natively).
"""

from .circuit_create import DIGEST, GEOMETRY_ARGS, REF, REQUEST, payload
from .circuit_schema import tool
from .circuit_spec_plan import preview_circuit
from .circuit_spec_schema import CircuitSpecError, digest, validate
from .pdk_binding import guards, require_service
from .pdk_binding_schema import BINDING_REFS, SELECTED_SPEC
from .skill_result import decode_skill_result
from .template_circuit_schema import TEMPLATE_USE

EDIT_MODES = ("rebuild", "reroute", "patch")
CONFIRM_CHOICES = {"approved": "执行这次修改", "rejected": "取消"}

EDIT_TOOLS = [
    tool(
        "prepare_circuit_edit",
        "Prepare an approved in-place edit of an existing schematic: recompute the plan, "
        "compare it with the inspected target and return a review for the user. "
        "Nothing is written here; the edit only runs through execute_circuit_edit with the "
        "review digest after the user agreed. mode=rebuild replaces every instance, wire, "
        "label, port and net of the current cellview with the planned circuit (the AI owns "
        "the cellview once approved); the inspected target_ref and the reviewed contents "
        "are re-verified natively before the first deletion. Retain prepare_ref, "
        "review_digest and request_id; reuse identical request_id after unknown status.",
        {
            **GEOMETRY_ARGS,
            "spec": SELECTED_SPEC,
            "binding_refs": BINDING_REFS,
            "geometry_plan_digest": DIGEST,
            "task_ref": REF,
            "request_id": REQUEST,
            "target_ref": REF,
            "target_action": {"type": "string", "enum": ["edit"]},
            "edit": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": list(EDIT_MODES)},
                    "drops": {
                        "type": "array", "maxItems": 128,
                        "items": {"type": "string", "maxLength": 256},
                    },
                    "nets": {
                        "type": "array", "maxItems": 32,
                        "items": {"type": "string", "maxLength": 128},
                    },
                    "delete": {
                        "type": "object",
                        "properties": {
                            "instances": {
                                "type": "array", "maxItems": 64,
                                "items": {"type": "string", "maxLength": 128},
                            },
                            "ports": {
                                "type": "array", "maxItems": 128,
                                "items": {"type": "string", "maxLength": 128},
                            },
                            "drops": {
                        "type": "array", "maxItems": 128,
                        "items": {"type": "string", "maxLength": 256},
                    },
                    "nets": {
                                "type": "array", "maxItems": 256,
                                "items": {"type": "string", "maxLength": 128},
                            },
                            "wires": {"type": "boolean"},
                        },
                        "required": [],
                        "additionalProperties": False,
                    },
                },
                "required": ["mode"],
                "additionalProperties": False,
            },
            "template_use": TEMPLATE_USE,
        },
        ("spec", "preview_digest", "layout", "geometry_plan_digest", "task_ref",
         "request_id", "target_ref", "target_action", "edit"),
    ),
    tool(
        "execute_circuit_edit",
        "Execute a prepared circuit edit after the user approved its review. "
        "Requires the prepare_ref, the reviewed plan digest and the approval digest returned "
        "by prepare_circuit_edit; the native side re-checks that the cellview still holds the "
        "reviewed contents, then clears and rebuilds it, runs schCheck, verifies the declared "
        "instances/ports/connections, saves and reopens for readback. "
        "Identical request_id returns the retained status without repeating writes.",
        {
            "prepare_ref": REF,
            "geometry_plan_digest": DIGEST,
            "request_id": REQUEST,
            "approval_digest": DIGEST,
        },
        ("prepare_ref", "geometry_plan_digest", "request_id", "approval_digest"),
        True,
    ),
]
EDIT_NAMES = frozenset(entry["name"] for entry in EDIT_TOOLS)
EXECUTE_NAME = "execute_circuit_edit"
OPERATION = "edit_circuit"


def edit_plan(args, review_digest):
    """Native edit plan: mode, reviewed approval digest and the delete lists."""
    edit = args["edit"]
    if edit["mode"] == "reroute":
        if not edit.get("nets"):
            raise CircuitSpecError("a single-net re-route names the nets it redraws")
    if edit["mode"] == "patch":
        block = edit.get("delete") or {}
        if not (block.get("instances") or block.get("ports") or block.get("nets")
                or block.get("wires")):
            raise CircuitSpecError(
                "patch edits must delete something: name instances, ports or nets, or set wires"
            )
    deletes = edit.get("delete") or {}
    if edit["mode"] == "reroute":
        deletes = {**deletes, "nets": list(edit.get("nets") or []), "wires": False}
    return [
        ["mode", edit["mode"]],
        ["approval", review_digest],
        ["drops", list(edit.get("drops") or [])],
        [
            "delete",
            [
                ["instances", list(deletes.get("instances") or [])],
                ["ports", list(deletes.get("ports") or [])],
                ["nets", list(deletes.get("nets") or [])],
                ["wires", True if deletes.get("wires") else None],
            ],
        ],
    ]


def review(args, workspace=None):
    """Describe exactly what the approved write will do, for the user to accept."""
    plan = payload(args, workspace=workspace)
    target, instances, ports, wires, notes = plan
    preview = preview_circuit(args["spec"], args["bindings"])
    spec = preview["plan"]["spec"]
    edges = sum(1 for row in wires if len(row) == 4 and row[3])
    deletes = args["edit"].get("delete") or {}
    body = {
        "target": target,
        "mode": args["edit"]["mode"],
        "clears": {
            "rebuild": "every instance, wire, label, port and net of the reviewed cellview",
            "reroute": "only the wires and labels of the re-routed nets; devices, ports "
                       "and every other net stay exactly as they are",
            "patch": "only the listed objects; everything else stays as reviewed",
        }[args["edit"]["mode"]],
        "reroutes": list(args["edit"].get("nets") or []),
        "drops": list(args["edit"].get("drops") or []),
        "deletes": {
            "instances": list(deletes.get("instances") or []),
            "ports": list(deletes.get("ports") or []),
            "nets": list(deletes.get("nets") or []),
            "all_wires": bool(deletes.get("wires")),
        },
        "modifies": ("a declared instance that already exists is re-placed and "
                     "re-parameterized in place"),
        "instances": [row[0] for row in instances],
        "ports": [row[0] for row in ports],
        "notes": len(notes),
        "wires": {
            "drawn": len(wires) - edges,
            "connections": edges,
        },
        "spec_nets": [net["name"] for net in spec["nets"]],
    }
    return body, digest(body)


def question(review_body, review_digest):
    target = "/".join(review_body["target"])
    return {
        "id": "circuit_edit",
        "header": "电路修改",
        "question": (
            f"将按审阅过的计划重画 {target}：清空现有内容后重建 "
            f"{len(review_body['instances'])} 个器件、{len(review_body['ports'])} 个端口、"
            f"{review_body['wires']['connections']} 条连接"
            f"（审阅摘要 {review_digest[:12]}…）。是否执行？"
        ),
        "options": [
            {"label": CONFIRM_CHOICES["approved"], "description": "按审阅内容修改当前 cellview。"},
            {"label": CONFIRM_CHOICES["rejected"], "description": "放弃本次修改，保持现状。"},
        ],
    }


def edit_values(args, workspace):
    if ("target_ref" in args) != ("target_action" in args):
        raise CircuitSpecError("Use the inspected target_ref and target_action together")
    if args["target_action"] != "edit":
        raise CircuitSpecError("Approved circuit edits use target_action=edit")
    if args["edit"]["mode"] not in EDIT_MODES:
        raise CircuitSpecError("Unsupported edit mode: " + args["edit"]["mode"])
    if (args["edit"]["mode"] == "rebuild"
            and args["layout"].get("routing", {}).get("mode") == "stub"):
        raise CircuitSpecError("An approved rebuild draws connections; drop routing.mode=stub")
    if args["edit"]["mode"] == "reroute":
        ground = {net["name"] for net in args["spec"]["nets"] if net["scope"] == "ground"}
        if ground & set(args["edit"].get("nets") or []):
            raise CircuitSpecError(
                "Ground nets are physically pin-linked; they are not re-routed alone"
            )
    return [
        args["task_ref"],
        args["request_id"],
        args["geometry_plan_digest"],
        payload(args, workspace=workspace),
    ]


def execute_edit(args, client, workspace=None, pdk_bindings=None):
    """Run the approved edit; the approval digest ties it to the reviewed plan."""
    validate(
        args,
        next(entry["inputSchema"] for entry in EDIT_TOOLS if entry["name"] == EXECUTE_NAME),
    )
    from .circuit_create import skill_literal

    values = [args["prepare_ref"], args["geometry_plan_digest"], args["request_id"]]
    base = list(values)
    first = "aiCreateExecute(" + " ".join(
        skill_literal(v) for v in base + [None, args["approval_digest"]]
    ) + ")"
    ok, response = client.call("eval_skill_native", {"code": first})
    if not ok:
        return decode_skill_result(response, transport_ok=False)
    ok, result = decode_skill_result(response)
    if result.get("code") == "pdk_validation_required":
        guard = result["pdk_guard"]
        require_service(pdk_bindings).validate_execute(guard)
        code = "aiCreateExecute(" + " ".join(
            skill_literal(v) for v in base + [guard, args["approval_digest"]]
        ) + ")"
        ok, response = client.call("eval_skill_native", {"code": code})
        if not ok:
            return decode_skill_result(response, transport_ok=False)
        ok, result = decode_skill_result(response)
    from .circuit_annotations import decode_text_layout
    from .circuit_preview import attach_svg_preview

    if result.get("text_layout"):
        result["text_layout"] = decode_text_layout(result["text_layout"])
    return ok, attach_svg_preview(result, client, workspace)


def call_edit(name, args, client, workspace=None, pdk_bindings=None):
    if name == EXECUTE_NAME or name == OPERATION:
        if "approval_digest" not in args and "approval" in args:
            args = {**args, "approval_digest": args["approval"]["review_digest"]}
        return execute_edit(args, client, workspace=workspace, pdk_bindings=pdk_bindings)
    validate(args, next(entry["inputSchema"] for entry in EDIT_TOOLS if entry["name"] == name))
    if "binding_refs" in args and pdk_bindings is not None:
        args = pdk_bindings.resolve_request(args)
    elif "binding_refs" in args:
        raise CircuitSpecError("Selected device data expired; bind the selected devices again")
    if args["edit"]["mode"] == "reroute" and not args["edit"].get("nets"):
        raise CircuitSpecError("a single-net re-route names the nets it redraws")
    missing = sorted(set(GEOMETRY_ARGS) - set(args))
    if missing:
        raise CircuitSpecError(
            "Edit inputs missing: " + ", ".join(missing)
            + ". Expand binding_refs through prepare_circuit_edit before compiling the edit"
        )
    body, review_digest = review(args, workspace=workspace)
    if guards(args):
        require_service(pdk_bindings)
    from .circuit_create import skill_literal

    values = edit_values(args, workspace)
    if args["edit"]["mode"] == "reroute":
        nets = set(args["edit"].get("nets") or [])
        rows = values[3]
        values[3] = rows[:3] + [[row for row in rows[3] if row[0] in nets]] + [rows[4]]
    values.extend([guards(args) or None, [args["target_ref"], args["target_action"]]])
    values.append(edit_plan(args, review_digest))
    code = "aiCreatePrepare(" + " ".join(skill_literal(v) for v in values) + ")"
    ok, response = client.call("eval_skill_native", {"code": code})
    if not ok:
        return decode_skill_result(response, transport_ok=False)
    ok, result = decode_skill_result(response)
    if not ok:
        return ok, result
    result.update({
        "stage": "edit_review",
        "next_action": "ask_user_to_approve_circuit_edit",
        "user_input_required": True,
        "selection_question": question(body, review_digest),
        "review": body,
        "review_digest": review_digest,
        "operation": OPERATION,
    })
    return True, result
