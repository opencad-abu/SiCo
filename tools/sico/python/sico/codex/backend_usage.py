"""backend usage projections with explicit inputs."""


def context_usage(params):
    """Latest context estimate, distinct from cumulative billed token usage."""
    usage = params.get("tokenUsage") if isinstance(params, dict) else None
    if not isinstance(usage, dict):
        return None
    last = usage.get("last")
    used = last.get("totalTokens") if isinstance(last, dict) else None
    capacity = usage.get("modelContextWindow")
    if type(used) is not int or used < 0 or type(capacity) is not int or capacity <= 0:
        return None
    return {"used": used, "capacity": capacity}


def token_totals(params, turns):
    usage = params.get("tokenUsage") if isinstance(params, dict) else None
    last = usage.get("last") if isinstance(usage, dict) else None
    total = usage.get("total") if isinstance(usage, dict) else None
    if not isinstance(last, dict):
        return
    input_tokens = last.get("inputTokens", 0)
    output_tokens = last.get("outputTokens", 0)
    if (
        type(input_tokens) is not int
        or input_tokens < 0
        or type(output_tokens) is not int
        or output_tokens < 0
    ):
        return
    turn_id = params.get("turnId")
    if not isinstance(turn_id, str) or not turn_id:
        turn_id = "unknown"
    if (
        isinstance(total, dict)
        and type(total.get("inputTokens")) is int
        and total["inputTokens"] >= 0
        and type(total.get("outputTokens")) is int
        and total["outputTokens"] >= 0
    ):
        total_input = total["inputTokens"]
        total_output = total["outputTokens"]
    else:
        turns[turn_id] = (input_tokens, output_tokens)
        total_input = sum(value[0] for value in turns.values())
        total_output = sum(value[1] for value in turns.values())
    return total_input, total_output
