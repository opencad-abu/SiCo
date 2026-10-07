"""Project checks and fail-closed, opt-in durable dispatch around generic tools."""

import json
import uuid

from .circuit_create import REQUEST, skill_literal
from .circuit_spec_schema import CircuitSpecError, canonical, digest, validate
from .project_contract import receipt as checked_receipt
from .project_contract import require, session
from .project_contract import target as checked_target
from .project_journal import OperationJournal, now
from .project_preflight import preflight
from .project_schema import PROJECT_TOOLS, operation_tool
from .simulation_recipe import call_simulation_native


def native(client, function, values):
    # Other frontends can supply a finite native-method transport. Business
    # validation, preflight and recovery remain shared with the terminal MCP.
    if hasattr(client, "call_project_native"):
        reply = client.call_project_native(function, values)
        ok = True
    else:
        ok, reply = call_simulation_native(client, function, values)
    if not ok or not isinstance(reply, dict) or reply.get("ok") is not True:
        raise CircuitSpecError("project backend unavailable: " + str(reply))
    return reply


def session_identity(client):
    return session(native(client, "aiProjectSession", [uuid.uuid4().hex]))


class GuardedClient:
    """Recheck session and exact claim in the SAME SKILL evaluation as each dispatch."""

    def __init__(self, client, record):
        self.client, self.record = client, record

    def call(self, method, args):
        if method != "eval_skill_native":
            raise CircuitSpecError("journalled operation requires fixed native dispatch")
        if hasattr(self.client, "call_guarded_native"):
            return self.client.call_guarded_native(args, self.record)
        values = [
            self.record["session"]["session_id"],
            self.record["key"],
            self.record["input_digest"],
        ]
        guard = "aiProjectAssertClaim(" + " ".join(skill_literal(v) for v in values) + ")"
        bindings = (
            "((aiProjectDispatchKey "
            + skill_literal(values[1])
            + ") "
            + "(aiProjectDispatchDigest "
            + skill_literal(values[2])
            + "))"
        )
        return self.client.call(
            method,
            {**args, "code": "progn(" + guard + " let(" + bindings + " " + args["code"] + "))"},
        )


def dispatch(operation, args, client, workspace, pdk_bindings):
    from .cdf_update import call_cdf_update
    from .circuit_config import build_config_skill
    from .circuit_create import call_create
    from .skill_result import call_skill
    from .simulation_recipe import call_simulation
    from .template_symbol import call_symbol
    from .circuit_library import call_library

    if operation == "apply_cdf_update":
        return call_cdf_update(operation, args, client)
    if operation == "create_circuit_library":
        return call_library(operation, args, client, workspace)
    if operation in {"create_simulation_setup", "run_simulation_setup"}:
        return call_simulation(operation, args, client)
    if operation == "create_circuit_from_plan":
        return call_create(operation, args, client, workspace, pdk_bindings)
    if operation == "edit_circuit":
        from .circuit_edit import call_edit
        return call_edit("edit_circuit", args, client, workspace, pdk_bindings)
    if operation in {"create_template_symbol", "draw_symbol"}:
        result = call_symbol(operation, args, workspace=workspace, client=client)
        return result.get("ok") is True, result
    return call_skill(client, build_config_skill(operation, args), native=True)


def exception_evidence(exc):
    data = getattr(exc, "data", None)
    return {
        **(data if isinstance(data, dict) else {}),
        "error_type": type(exc).__name__,
        "message": str(exc)[:4096],
        "automatic_resume_allowed": False,
    }


def report(record, client=None):
    result = {
        "ok": True,
        "schema": "cad.circuit.operation.status.v1",
        "operation": record["operation"],
        "request_id": record["request_id"],
        "input_digest": record["input_digest"],
        "recorded_session": record["session"],
        "input_summary": record.get("input_summary"),
        "recorded_at": record["created_at"],
        "record_state": record["state"],
        "last_observation": record.get("observation"),
        "last_response": record.get("response"),
        "last_response_ok": record.get("response_ok"),
        "state": "offline_record",
        "automatic_resume_allowed": False,
        "live_integrity_verified": False,
        "next_action": "inspect_original_session_and_exact_target_before_any_new_operation",
        **{k: record[k] for k in ("dispatch_error", "dispatch_diagnostic") if k in record},
    }
    if client is None:
        return result
    try:
        identity = session_identity(client)
        result["current_session"] = identity
        if identity != record["session"]:
            return {**result, "state": "session_mismatch", "ok": False}
        observed = native(
            client,
            "aiProjectReceipt",
            [identity["session_id"], record["key"], record["input_digest"], record["operation"]],
        )
        raw = observed.pop("receipt_json", None)
        if raw is None:
            failure = record.get("dispatch_error", {})
            if (failure.get("operation_dispatched") is False
                    and failure.get("category") in {"preflight", "validation", "binding"}):
                return {**result, "ok": False, "state": "dispatch_rejected",
                        "code": failure.get("code", "circuit_preflight_failed"),
                        "next_action": "inspect_rejection_and_target_before_a_new_operation"}
            return {**result, "ok": False, "state": "outcome_unknown"}
        require(isinstance(raw, str), "receipt_json must be text")
        receipt = checked_receipt(json.loads(raw), record)
        target = observed.get("target_observation")
        if target is not None:
            checked_target(target)
            if record["operation"] != "run_simulation_setup":
                require(target[0] == receipt.get("target"), "receipt target observation differs")
        # Native observations cannot replace the host's status schema or replay policy.
        result.update(
            {k: observed[k] for k in ("target_observation", "run_context_checked") if k in observed}
        )
        result["receipt"] = receipt
        result["state"] = "retained_receipt"
        result["next_action"] = "inspect_retained_reference_before_further_writes"
        if receipt.get("error") or receipt.get("ok") is False:
            result.update(state="partial_or_failed", next_action="inspect_retained_partial_target")
        if record["operation"] == "create_circuit_library":
            library = observed.get("library_observation")
            require(isinstance(library, list) and len(library) == 3,
                    "library observation missing")
            result["library_observation"] = library
            if receipt["saved"] and library != [receipt["library"], receipt["library_path"],
                                                receipt["technology_path"]]:
                result.update(state="target_changed", next_action="inspect_current_library")
        if target and (target[6] or (receipt.get("saved") and not target[1])):
            result["target_modified"] = target[6]
            result.update(
                state="partial_or_failed" if result["state"] == "partial_or_failed" else "target_changed",
                next_action="inspect_current_target_without_saving"
            )
        if record["operation"] == "run_simulation_setup":
            result["state"] = receipt["state"]
            result["next_action"] = "query_exact_history_and_results"
            if observed.get("run_context_checked") is not True and receipt["state"] != "failed":
                result.update(state="context_lost", ok=False)
            if result["state"] in {"start_unknown", "context_lost"}:
                result["next_action"] = "manually_reconcile_exact_ADE_history_and_scheduler_jobs"
        return result
    except Exception as exc:
        return {**result, "ok": False, "state": "outcome_unknown",
                "diagnostic": str(exc)[:4096], "recovery_error": exception_evidence(exc)}


def observe_and_record(journal, record, client):
    result = report(record, client)
    # Persist bounded recovery evidence, without recursively nesting earlier observations.
    record["observation"] = {
        **record.get("observation", {}),
        **{k: result[k] for k in ("state", "receipt", "target_observation", "library_observation",
                                 "recovery_error")
           if k in result},
    }
    record["observation"]["checked_at"] = now()
    try:
        journal.write(record)
    except (OSError, CircuitSpecError) as exc:
        result["observation_persisted"] = False
        result["persistence_diagnostic"] = str(exc)
    else:
        result["observation_persisted"] = True
    return result


def execute(args, client, workspace, definitions, pdk_bindings):
    operation, parameters = args["operation"], args["arguments"]
    validate(parameters, definitions[operation_tool(operation)]["inputSchema"])
    validate(parameters.get("request_id"), REQUEST)
    if len(canonical(parameters).encode()) > 196608:
        raise CircuitSpecError("operation input exceeds 192 KiB")
    journal = OperationJournal(workspace)
    key = journal.key(operation, parameters["request_id"])
    fingerprint = digest({"operation": operation, "arguments": parameters})
    with journal.lock(key):
        previous = journal.read(key)
        if previous:
            if previous["operation"] != operation or previous["input_digest"] != fingerprint:
                raise CircuitSpecError("operation request_id conflict; no dispatch")
            return observe_and_record(journal, previous, client)
        identity = session_identity(client)
        binding = None
        if hasattr(client, "prepare_operation"):
            binding = client.prepare_operation(operation, parameters, identity)
        summary = {
            k: parameters[k]
            for k in (
                "task_ref",
                "prepare_ref",
                "preview_ref",
                "setup_ref",
                "recipe_digest",
                "geometry_plan_digest",
                "preview_digest",
            )
            if k in parameters
        }
        if operation == "create_simulation_setup":
            summary["target"] = parameters["recipe"]["target"]
        if operation == "create_circuit_config":
            summary["target"] = {
                k: parameters.get(k, "config") for k in ("library", "cell", "view")
            }
        record = {
            "schema": "cad.circuit.operation.v1",
            "key": key,
            "operation": operation,
            "request_id": parameters["request_id"],
            "input_digest": fingerprint,
            "input_summary": summary,
            "session": identity,
            "created_at": now(),
            "state": "intent_recorded",
        }
        if binding is not None:
            record["adapter_binding"] = binding
        journal.write(record)
        # Intent precedes the claim. A crash here is UNKNOWN, not permission to retry.
        phase = "claim"
        try:
            native(client, "aiProjectClaim", [identity["session_id"], key, fingerprint])
            record["state"] = "dispatching"
            journal.write(record)
            phase = "dispatch"
            ok, response = dispatch(
                operation, parameters, GuardedClient(client, record), workspace, pdk_bindings
            )
            record.update(
                state="response_recorded", response_ok=ok, response=response, response_at=now()
            )
            journal.write(record)
        except Exception as exc:
            # Never re-dispatch, including after failure to persist a successful reply.
            record["dispatch_diagnostic"] = str(exc)[:4096]
            record["dispatch_error"] = {
                **exception_evidence(exc), "phase": phase, "operation": operation,
                "operation_request_id": parameters["request_id"],
            }
            persistence_error = None
            try:
                journal.write(record)
            except (OSError, CircuitSpecError) as failure:
                persistence_error = str(failure)[:4096]
            result = observe_and_record(journal, record, client)
            if persistence_error:
                result["dispatch_evidence_persistence_error"] = persistence_error
            return result
        return observe_and_record(journal, record, client)


def call_project(name, args, client, workspace, definitions, pdk_bindings=None):
    schema = next(t["inputSchema"] for t in PROJECT_TOOLS if t["name"] == name)
    if name == "execute_circuit_operation":
        # The inner object is checked against the actual registered tool schema, not a duplicate.
        if not isinstance(args, dict) or not isinstance(args.get("arguments"), dict):
            raise CircuitSpecError("execute requires an arguments object")
        validate({**args, "arguments": {}}, schema)
        return execute(args, client, workspace, definitions, pdk_bindings)
    validate(args, schema)
    if name == "get_circuit_operation":
        journal = OperationJournal(workspace)
        record = journal.read(journal.key(args["operation"], args["request_id"]))
        if not record:
            return {
                "ok": False, "code": "operation_not_found",
                "operation": args["operation"], "request_id": args["request_id"],
                "record_found": False, "automatic_resume_allowed": False,
                "message": (
                    "No execution record for this operation/request_id in the current workspace. "
                    "Preparation requests do not create execution records. Check the request_id "
                    "passed to execute_circuit_operation and inspect the target; a missing record "
                    "does not prove that no write occurred and does not authorize replay."
                ),
                "next_action": "check_execution_request_id_and_inspect_target",
            }
        if record["operation"] != args["operation"]:
            raise CircuitSpecError("operation name differs from the retained request")
        if not args.get("refresh", True):
            return report(record)
        with journal.lock(record["key"]):
            return observe_and_record(journal, journal.read(record["key"]), client)
    identity = session_identity(client)
    targets = [[t[k] for k in ("library", "cell", "view")] for t in args["targets"]]
    libs = sorted(
        {t["library"] for t in args["targets"]} | {t["library"] for t in args["libraries"]}
    )
    observed = native(client, "aiProjectObserve", [targets, libs, args["presentation"]])
    if session_identity(client) != identity:
        raise CircuitSpecError("Virtuoso session changed during preflight")
    return preflight(args, identity, observed)
