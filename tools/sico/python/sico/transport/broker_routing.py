"""Admission of a bound request into one instance router."""

from .circuit import METHOD as CIRCUIT_METHOD


def execute_bound(context, method, params, *, call_context, binding_lock, bindings,
                  router, request_bound, dispatch, operation_timeout):
    identity = {
        key: call_context[key]
        for key in ("session_id", "task_id", "tool_call_id")
        if call_context.get(key) is not None
    }
    if call_context.get("session_id"):
        with binding_lock:
            identity["binding_id"] = bindings.require(
                call_context["session_id"], context).binding_id
    resources = None
    if (call_context.get("session_id")
            and context.snapshot.get("native_binding") == "cad_ai_native_binding.v1"
            and method == CIRCUIT_METHOD
            and (params.get("expected") is not None
                 or params["function"] == "aiCopilotOpenTarget")):
        # Every call that declares an output target authenticates the
        # resolved resource row before dispatch. Creation/open/write
        # bindings always declare one; a guarded read carries the
        # operation's task/target expectation and is reserved here as
        # well, so the final native gate compares identical rows.
        preflight = dict(function="aiCopilotResolveResources",
            values=[params["function"], params["values"], params["expected"]],
            claim=None, expected=None)
        observed = request_bound(context, CIRCUIT_METHOD, preflight)
        resources = observed["circuit"]["resources"]
    return router.execute(
        method,
        lambda request_id: dispatch(context, method, params, request_id, resources),
        # The caller is already a desktop worker thread. Waiting here
        # preserves FIFO execution while progress publishes queued
        # state to the session journal instead of blocking Qt.
        wait=True,
        execution_timeout=operation_timeout,
        cancelled=call_context.get("cancelled"),
        progress=call_context.get("progress"),
        target_id=context.target_id,
        **identity,
    )


def validate_request_id(request_id):
    if (
        not isinstance(request_id, str)
        or len(request_id) != 32
        or any(char not in "0123456789abcdef" for char in request_id)
    ):
        raise ValueError("Invalid router request ID")
