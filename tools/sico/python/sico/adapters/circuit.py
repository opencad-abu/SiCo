"""Shared business tools with captured-source, preflight and journalled write gates."""

import json
from copy import deepcopy
from pathlib import Path

from cadai.circuit_spec_schema import CircuitSpecError, canonical, validate
from cadai.pdk_schema import PDK_NAMES
from cadai.pdk_schema import arguments as pdk_arguments
from cadai.project_schema import OPERATIONS, PROJECT_NAMES
from cadai.project_tools import call_project

from ..core.contracts import CircuitCallError, NeedsReconcile, ToolResult
from ..core.tools import Tool
from ..transport.circuit import WRITES, literal_call
from .project import RECONCILE_STATES, ProjectReadClient, operation_status, register_project
from .results import NAMES as RESULT_NAMES
from .circuit_reads import EVIDENCE_READS, evidence_dependencies
from .circuit_decisions import settled_target_decision, target_decision
from .results import register_results

# Generic planning and finite lifecycle only. No sample-circuit recipes or arbitrary eval.
NAMES = frozenset(
    {
        "begin_circuit_task",
        "prepare_cdf_update",
        "preview_circuit_spec",
        "preview_circuit_geometry",
        "prepare_template_circuit",
        "prepare_circuit_template_plan",
        "prepare_circuit_edit",
        "execute_circuit_edit",
        "preview_template_placement",
        "prepare_circuit_creation",
        "inspect_circuit_target",
        "inspect_created_circuit",
        "query_device_catalog",
        "query_circuit_templates",
        "get_circuit_template",
        "match_circuit_template",
        "preview_template_symbol",
        "inspect_template_symbol",
        "get_symbol_binding",
        "inspect_circuit_config",
        "preview_simulation_recipe",
        "inspect_simulation_setup",
        "get_simulation_recipe_status",
        "stop_simulation_recipe",
        "search_pdk_devices",
        "get_pdk_preparation",
        "prepare_pdk_data",
        "advance_pdk_collection",
        "cancel_pdk_collection",
        "get_pdk_device",
        "get_pdk_data",
        "collect_pdk_data",
        "import_pdk_facts",
        "collect_pdk_parameter_facts",
        "get_pdk_fact_report",
        "prepare_pdk_cdf_validation",
        "advance_pdk_cdf_validation",
        "get_pdk_cdf_validation",
        "cancel_pdk_cdf_validation",
        "finalize_pdk_cdf_validation",
        "prepare_pdk_parameter_draft",
        "get_pdk_parameter_draft",
        "submit_pdk_parameter_batch",
        "cancel_pdk_parameter_draft",
        "prepare_pdk_data_update",
        "apply_pdk_data_update",
        "revalidate_pdk_bindings",
        "bind_pdk_device",
        "list_project_extensions",
        "prepare_circuit_library",
    }
)

class PreflightRequired(CircuitSpecError):
    def __init__(self, message, missing=None):
        super().__init__(message)
        self.missing = missing or []


class CircuitClient(ProjectReadClient):
    def __init__(self, broker, context, workspace):
        super().__init__(broker, context)
        self.workspace, self.requirements = Path(workspace), None
        self.target_observations = {}

    def send(self, function, values, claim=None, expected=None):
        reply = self.broker.circuit_call(
            self.context,
            {
                "function": function,
                "values": values,
                "claim": claim,
                "expected": expected,
            },
        )
        data = reply.get("circuit")
        if not isinstance(data, dict):
            raise NeedsReconcile("Missing shared circuit reply; query original operation")
        from cadai.skill_diagnostics import carry_output

        data = carry_output(data, reply)
        if function == "aiCopilotDescribe" and data.get("ok") is False:
            raise CircuitCallError(data.get("code", "circuit_preflight_failed"),
                                   data.get("message", "Operation preflight failed"), data={
                **data, "function": function, "stage": "describe", "category": "preflight",
                "operation_dispatched": False, "automatic_resume_allowed": False,
            })
        return data

    def call(self, method, args):
        if method in {"begin_circuit_task", "inspect_circuit_config"}:
            from cadai.circuit_tools import build_circuit_skill

            code = build_circuit_skill(method, args)
        elif method in {"eval_skill_native", "eval_skill"} and set(args) == {"code"}:
            code = args["code"]
        else:
            raise CircuitSpecError("Unregistered shared native method")
        function, values = literal_call(code)
        if function in WRITES or function == "aiProjectClaim":
            raise CircuitSpecError("Writes require execute_circuit_operation")
        return True, {"value": json.dumps(self.send(function, values), ensure_ascii=False)}

    def pdk_entry_context(self):
        from cadai.entry_context import enrich
        from ..transport.methods import QueryUnavailable
        try:
            return enrich(self.broker.read(self.context, "get_entry_context", {}))
        except QueryUnavailable:
            return None

    def call_project_native(self, function, values):
        if function == "aiProjectClaim":
            return self.send(function, values)
        return super().call_project_native(function, values)

    def call_guarded_native(self, args, record):
        function, values = literal_call(args["code"])
        binding = record["adapter_binding"]
        if binding["source"] != self.context.record():
            raise NeedsReconcile("Original operation source differs")
        claim = [record["session"]["session_id"], record["key"], record["input_digest"]]
        result = self.send(function, values, claim, binding["expected"])
        return True, {"value": json.dumps(result, ensure_ascii=False)}

    def prepare_operation(self, operation, parameters, identity):
        if operation == "create_circuit_library":
            from cadai.project_tools import session_identity

            description = self.send("aiCopilotDescribe", [operation, parameters["prepare_ref"],
                                                         None, None])
            expected = description.get("binding")
            if (description.get("ok") is not True or not isinstance(expected, list)
                    or len(expected) != 7 or expected[3] != "library"
                    or expected[6] != str(self.workspace.resolve())
                    or session_identity(self) != identity):
                raise PreflightRequired("Library preparation or captured project changed")
            return {"source": self.context.record(), "expected": expected,
                    "task_ref": expected[0]}
        if not self.requirements:
            raise PreflightRequired("Run a successful project preflight for this source first")
        ref = parameters.get(
            "prepare_ref", parameters.get("preview_ref", parameters.get("setup_ref"))
        )
        target = None
        if operation == "create_circuit_config":
            target = [parameters["library"], parameters["cell"], parameters.get("view", "config")]
        elif operation == "create_simulation_setup":
            target = [parameters["recipe"]["target"][k] for k in ("library", "cell", "view")]
        description = self.send(
            "aiCopilotDescribe", [operation, ref, parameters.get("task_ref"), target]
        )
        expected = description.get("binding")
        if (
            description.get("ok") is not True
            or not isinstance(expected, list)
            or len(expected) != 7
        ):
            raise PreflightRequired(description.get("message") or "Native operation binding unavailable")
        task, mode, target, view_type, _, library_path, run_dir = expected
        requirements = deepcopy(self.requirements)
        required_target = dict(zip(("library", "cell", "view"), target))
        required_target.update(
            intent=description.get("target_intent", "read" if operation == "run_simulation_setup" else "create"),
            view_type=view_type
        )
        missing = []
        if mode != requirements["presentation"] or required_target not in requirements["targets"]:
            missing.append(dict(field="targets/presentation", expected=required_target,
                                presentation=mode,
                                message="Preflight must cover the exact output target, intent and task mode"))
        library_match = any(
            row["library"] == target[0]
            and Path(row["expected_path"]).resolve() == Path(library_path).resolve()
            for row in requirements["libraries"]
        )
        if not library_match:
            missing.append(dict(field="libraries", expected=dict(library=target[0], expected_path=library_path),
                                message="Preflight must explicitly cover output library resolution"))
        if not any(
            d["purpose"] == "project" and Path(d["path"]).resolve() == self.workspace.resolve()
            for d in requirements["directories"]
        ):
            missing.append(dict(field="directories", purpose="project", path=str(self.workspace.resolve()),
                                message="Preflight must include this captured project directory"))
        model_files = set(description.get("model_files") or [])
        if operation == "create_simulation_setup":
            recipe = parameters["recipe"]
            model_files.update(
                m["path"]
                for t in [*recipe["tests"], *recipe.get("corners", [])]
                for m in t["models"]
            )
        if not model_files <= set(requirements["model_files"]):
            missing.append(dict(field="model_files", paths=sorted(model_files - set(requirements["model_files"])),
                                message="Preflight must include all explicit recipe model files"))
        if operation == "run_simulation_setup":
            if not requirements.get("simulator_executable") or not any(
                d["purpose"] == "run" and Path(d["path"]).resolve() == Path(run_dir).resolve()
                for d in requirements["directories"]
            ):
                missing.append(dict(field="directories/simulator_executable", purpose="run", path=run_dir,
                                    simulator_executable="absolute path to the intended simulator",
                                    message="Run preflight must cover actual projectDir and simulator executable"))
        if missing:
            raise PreflightRequired("; ".join(row["message"] for row in missing), missing)
        fresh = call_project("preflight_circuit_project", requirements, self, self.workspace, {})
        if not fresh.get("checks_passed") or fresh["session"] != identity:
            self.requirements = None
            raise PreflightRequired("Project preflight changed or failed before dispatch")
        return {
            "source": self.context.record(),
            "expected": expected,
            "preflight": fresh,
            "task_ref": task,
        }


def register_circuit(registry, broker, workspace):
    from cadai.mcp import _TOOLS_BY_NAME, McpServer

    clients, servers = {}, {}

    def client_for(context):
        cwd = context.snapshot.get("cwd")
        if not isinstance(cwd, str) or Path(cwd).resolve() != Path(workspace).resolve():
            raise NeedsReconcile("Project workspace differs from captured task source")
        key = canonical(context.record())
        if key not in clients:
            clients[key] = CircuitClient(broker, context, workspace)
            servers[key] = McpServer(clients[key], workspace=Path(workspace))
        return clients[key], servers[key]

    register_project(registry, broker, workspace, client_for=client_for)
    register_results(registry, client_for)
    discoverable = sorted(NAMES | RESULT_NAMES | PROJECT_NAMES | set(OPERATIONS))
    operation_schema = {
        "type": "object",
        "properties": {"operation": {"type": "string", "enum": discoverable}},
        "required": ["operation"],
        "additionalProperties": False,
    }
    registry.register(
        Tool(
            "get_circuit_operation_schema",
            "Read the complete input schema, including nested fields, for a registered shared "
            "circuit, PDK, project or result tool. operation is that tool's name. Read this when "
            "the model's tool signature omits nested fields. Mutations still require "
            "execute_circuit_operation; discovery does not expose direct write dispatch.",
            operation_schema,
            lambda args: validate(args, operation_schema),
            lambda args, context: ToolResult(
                data={
                    "ok": True,
                    "definition": deepcopy(_TOOLS_BY_NAME[args["operation"]]),
                    "dispatch_tool": (
                        "execute_circuit_operation"
                        if args["operation"] in OPERATIONS else args["operation"]
                    ),
                }
            ),
            execution_domain="local",
        )
    )

    def handler(name):
        def execute(arguments, context):
            client, server = client_for(context)
            try:
                if name == "prepare_circuit_creation" and arguments.get("target_action") in {"continue", "replace"}:
                    observed = client.target_observations.get(arguments.get("target_ref"))
                    validator = getattr(registry, "target_choice_validator", None)
                    if observed is None:
                        return ToolResult("preflight_failed", "先检查目标原理图的当前内容。")
                    if not validator or not validator(arguments["target_action"], observed, context):
                        return ToolResult(data=observed)
                validator = getattr(registry, "pdk_choice_validator", None)
                if validator and (name in PDK_NAMES or server._pdk_session is not None):
                    server.pdk_session().preparation.choice_validator = (
                        lambda library, info: validator(library, info, context)
                    )
                if name in {"prepare_pdk_data_update", "apply_pdk_data_update",
                             "submit_pdk_parameter_batch"}:
                    update_validator = getattr(registry, "pdk_update_validator", None)
                    updates = server.pdk_session().standard.updates
                    if updates:
                        updates.confirmation = (lambda info: update_validator(info, context)) if update_validator else None
                if name == "execute_circuit_operation":
                    selection = (None if arguments.get("operation") == "apply_cdf_update"
                                 else server.pdk_selection_guard(name))
                    if selection is not None:
                        return ToolResult(data=selection)
                    data = call_project(
                        name, arguments, client, workspace, _TOOLS_BY_NAME, server.pdk_bindings()
                    )
                    if isinstance(data, dict) and data.get("code") == "circuit_requires_target":
                        return ToolResult(data=target_decision(data))
                    summary = circuit_summary(name, data) or data.get("dispatch_diagnostic", "")
                    return ToolResult(operation_status(data), summary, data=data)
                reply = server.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": "copilot",
                        "method": "tools/call",
                        "params": {"name": name, "arguments": (
                            {"response_mode": "compact", **arguments}
                            if name == "bind_pdk_device" else arguments
                        )},
                    }
                )
                if "error" in reply:
                    return ToolResult("invalid_arguments", reply["error"]["message"],
                                      data=reply["error"].get("data"))
                result = reply["result"]
                data = json.loads(result["content"][0]["text"])
                if isinstance(data, dict) and data.get("code") == "circuit_requires_target":
                    return ToolResult(data=target_decision(data))
                if isinstance(data, dict) and data.get("decision_required"):
                    lookup = getattr(registry, "settled_target_action", None)
                    action = lookup(data, context) if lookup else None
                    if action:
                        # The answer is on record for these exact contents: hand
                        # the model the settled action instead of asking again.
                        data = settled_target_decision(data, action)
                if name == "inspect_circuit_target" and data.get("ok") and data.get("target_ref"):
                    client.target_observations[data["target_ref"]] = deepcopy(data)
                if data.get("state") in RECONCILE_STATES or (
                    name == "stop_simulation_recipe" and result.get("isError")
                ):
                    return ToolResult("needs_reconcile", circuit_summary(name, data), data=data)
                return ToolResult("tool_error" if result.get("isError") else "ok",
                                  circuit_summary(name, data), data=data)
            except PreflightRequired as exc:
                return ToolResult("preflight_failed", str(exc), {
                    "code": "preflight_failed", "category": "preflight",
                    "stage": "prepare_operation",
                    "operation": arguments.get("operation"),
                    "operation_request_id": arguments.get("arguments", {}).get("request_id"),
                    "operation_dispatched": False, "automatic_resume_allowed": False,
                    "missing_requirements": exc.missing,
                    "next_action": "preflight_circuit_project",
                })
            except (CircuitCallError, NeedsReconcile) as exc:
                if name == "execute_circuit_operation":
                    exc.data.update(
                        operation=arguments.get("operation"),
                        operation_request_id=arguments.get("arguments", {}).get("request_id"),
                    )
                raise

        return execute

    for name in sorted(NAMES | {"execute_circuit_operation"}):
        definition = _TOOLS_BY_NAME[name]
        schema = definition["inputSchema"]
        subject = EVIDENCE_READS.get(name)

        def checked(args, schema=schema, name=name):
            if name in PDK_NAMES:
                pdk_arguments(name, args)
                return
            validate(
                {**args, "arguments": {}} if name == "execute_circuit_operation" else args, schema
            )

        registry.register(
            Tool(
                name,
                definition["description"],
                schema,
                checked,
                handler(name),
                annotations=definition["annotations"],
                effect="read" if subject is not None else "unknown",
                input_dependencies=evidence_dependencies(subject) if subject is not None else None,
            )
        )



def circuit_summary(name, data):
    """Keep continuation information available even when the full plan is an artifact."""
    from .project import operation_summary

    if name in {"execute_circuit_operation", "get_circuit_operation"}:
        return operation_summary(data)
    if data.get("code") == "task_source_mismatch":
        return "该任务属于其他逻辑会话或已失效的绑定代次；请沿用已确认的执行模式建立新任务，再只读检查目标。原任务和写请求不会重放。"
    if name == "bind_pdk_device" and data.get("ok"):
        # Keep the binding token in structured tool data for the agent, but do
        # not repeat this implementation detail in user-facing summaries.
        return "器件完整参数已保存在后端，后续步骤会自动继续；无需用户提供内部信息。"
    if data.get("code", "").startswith("device_support_"):
        target = data.get("target", {})
        return ("器件 " + "/".join(target.get(k, "") for k in ("library", "cell"))
                + " 的自动参数处理尚未通过检查。请读取具体 CDF 和内置回调执行器诊断，"
                + "区分回调语法、过程未加载、参数约束和上下文变化；无需用户注册适配器。")
    if name in {"preview_circuit_spec", "preview_circuit_geometry"}:
        summary = {k: data[k] for k in (
            "next_action", "geometry_preview_ready", "preview_digest",
            "geometry_plan_digest", "user_input_required",
        ) if k in data}
        # The full plan usually travels as an artifact, so echo the acceptance
        # numbers that reviewers ask for (and that the model must quote).
        routing = (data.get("plan") or {}).get("routing")
        if isinstance(routing, dict):
            summary["routing"] = {
                "mode": routing.get("mode"),
                "engine": routing.get("engine"),
                "edges": routing.get("edges"),
                "pg_style": routing.get("pg_style"),
                "pg_stub_terminals": routing.get("pg_stub_terminals"),
                "metrics": routing.get("metrics"),
                "fallbacks": len(routing.get("fallbacks") or []),
                "junctions": len(routing.get("junctions") or []),
                "interior_escapes": len(routing.get("interior_escapes") or []),
            }
        review = (data.get("plan") or {}).get("layout_review")
        if review:
            summary["layout_review"] = [row.get("code") for row in review]
        return json.dumps(summary, ensure_ascii=False)
    if name in {"prepare_circuit_edit", "execute_circuit_edit"}:
        review = data.get("review") or {}
        summary = {k: data[k] for k in (
            "ok", "stage", "next_action", "user_input_required", "prepare_ref",
            "circuit_ref", "geometry_plan_digest", "review_digest", "target",
            "saved", "readback_verified", "check", "user_approved", "edited", "error",
        ) if k in data}
        if review:
            summary["review"] = {
                "mode": review.get("mode"),
                "instances": len(review.get("instances") or []),
                "ports": len(review.get("ports") or []),
                "wires": review.get("wires"),
            }
        if data.get("selection_question"):
            summary["selection_question"] = data["selection_question"]
        return json.dumps(summary, ensure_ascii=False)
    if name == "preview_template_placement":
        report = data.get("report") or {}
        return json.dumps({
            "rows": [row.get("members") for row in report.get("rows") or []],
            "mirrored_pairs": [
                [pair.get("a"), pair.get("b")] for pair in report.get("mirrored_pairs") or []
            ],
            "spacing": report.get("spacing"),
            "ports": report.get("ports"),
            "port_rows": report.get("port_rows"),
            "gaps": [gap.get("code") for gap in report.get("gaps") or []],
            "geometry_review": report.get("geometry_review"),
            "placement_digest": data.get("placement_digest"),
        }, ensure_ascii=False)
    return ""
