"""Read a published standard directory through the legacy session binding adapter."""

from copy import deepcopy
from pathlib import Path

from ..pdk_errors import PdkUnavailable
from ..pdk_normalize import context
from .jsonio import fail
from .publication import require
from .provenance import verify
from .workspace import divergence, select


def selection_status(workspace, library, environment):
    """Light standard-package fact for preparation: select, never verify content.

    The full publication gate stays in ``prepared``/``get_pdk_data``. This
    read exists so a design session can tell "no published package" apart
    from "published packages disagree" without first attempting a write path.
    """
    if not workspace:
        return {"status": "workspace_required"}
    from . import baseline_choice
    choice = divergence(workspace, library, environment)
    if choice and not baseline_choice.matches(baseline_choice.load(workspace), choice):
        return {
            "status": "decision_required",
            "reason": "pdk_baseline_sync_required",
            "message": "Workspace and SICO_PDK_DATA standard baselines differ",
            "baseline_ref": choice["baseline_ref"],
            "question": baseline_choice.question(choice),
            "workspace": choice["workspace"],
            "environment": choice["environment"],
            "next_action": "ask_user_to_resolve_pdk_baseline",
        }
    try:
        package = select(workspace, library, environment)
    except PdkUnavailable as exc:
        conflict = exc.code in {"pdk_update_conflict", "pdk_baseline_sync_required"}
        return {
            "status": "conflict" if conflict else "unavailable",
            "reason": exc.code,
            "message": str(exc),
            "next_action": "resolve_conflict" if conflict else "prepare_pdk_data",
        }
    return {
        "status": "selected",
        "revision": package.revision,
        "path": str((package.overlay or package.base.root) / "package.json"),
    }


def annotate_candidates(result, workspace, environment):
    """Attach standard readiness and the one user decision that gates design."""
    for candidate in result["candidates"]:
        candidate["standard"] = selection_status(workspace, candidate["library"], environment)
        status = candidate["standard"]["status"]
        if status == "conflict":
            result["issues"].append({
                "library": candidate["library"],
                "code": "pdk_update_conflict",
                "message": candidate["standard"]["message"],
                "next_action": "resolve_conflict",
            })
        elif status == "decision_required":
            result["issues"].append({
                "library": candidate["library"],
                "code": "pdk_baseline_sync_required",
                "message": candidate["standard"]["message"],
                "next_action": "ask_user_to_resolve_pdk_baseline",
            })
    targets = (
        {result["bound_library"]} if result["bound_library"]
        else {result["candidates"][0]["library"]} if len(result["candidates"]) == 1
        else set()
    )
    conflicts = [
        candidate for candidate in result["candidates"]
        if candidate["library"] in targets and candidate["standard"]["status"] == "conflict"
    ]
    decisions = [
        candidate for candidate in result["candidates"]
        if candidate["library"] in targets and candidate["standard"]["status"] == "decision_required"
    ]
    if conflicts:
        result["status"] = "conflict"
        result["next_action"] = "resolve_conflict"
    elif decisions:
        decision = decisions[0]["standard"]
        result.update(
            status="decision_required",
            user_input_required=True,
            baseline_sync_required=True,
            baseline_ref=decision["baseline_ref"],
            baseline_sync_question=decision["question"],
            next_action="ask_user_to_resolve_pdk_baseline",
        )
    elif any(candidate["standard"]["status"] == "selected"
             for candidate in result["candidates"]):
        result["next_action"] = "get_pdk_data"
    return result


def prepared(data, library, raw_context):
    if not data.workspace:
        return None
    try:
        package = select(data.workspace, library, data.environment)
        require(package)
    except PdkUnavailable as exc:
        if exc.code in {'pdk_standard_unavailable', 'pdk_generation_incomplete',
                        'pdk_acceptance_required'}:
            return None
        raise
    verify(package)
    live = next(row for row in context(raw_context)['libraries'] if row['name'] == library)
    items = []
    for row in package.document('device.json')['items'].values():
        lib = package.manifest['libraries'][row['library']]
        if lib['name'] != library:
            continue
        root = package.roots.get(lib['root'])
        if not root or (Path(root) / lib['path']).resolve() != Path(live['resolved_path']).resolve():
            fail('Published PDK library differs from live session', 'pdk_source_changed')
        if not any(row['use'][p] == 'allow' for p in ('circuit', 'testbench')):
            continue
        cdf = package.document(row['dir'] + '/cdf.json')
        items.append({'identity': {
            'target': {'library': library, 'cell': row['cell'], 'view': row['view']},
            'source': 'published_standard', 'available_views': [row['view']]},
            'cdf_summary': {'status': 'complete', 'presence': cdf['presence'],
                            'names': list(cdf['parameters'])}})
    from ..pdk_data import binding
    from .lifecycle import status
    return {'binding': binding(context(raw_context), library), 'source': 'published_standard',
            'capture': {'context': deepcopy(raw_context), 'data': {
                'items': items, 'status': 'complete', 'truncated': False, 'issues': []}},
            'path': str(package.base.root / 'package.json'), 'digest': package.revision,
            'standard': status(data.workspace, library, data.environment)}
