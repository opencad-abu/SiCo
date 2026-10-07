"""MCP adaptation for AIVW recipe execution and candidate feedback."""

from .aivw_tool import AivwExecutionError, run_aivw_recipe


def dispatch_aivw(name, arguments, *, client, workspace, get_ledger):
    recipe_arguments = dict(arguments)
    candidate_sha256 = recipe_arguments.pop("candidate_sha256", None)
    try:
        detail = run_aivw_recipe(recipe_arguments, client=client, workspace=workspace)
    except AivwExecutionError as exc:
        return False, {"code": "aivw_execution_error", "message": str(exc)}
    if candidate_sha256 is not None:
        feedback = get_ledger().attach_feedback(candidate_sha256, detail)
        detail = dict(detail)
        detail["candidate_feedback"] = feedback
    return True, detail
