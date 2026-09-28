"""Project target observations and answered choices into host decisions."""

import uuid

from ..service.audit import NEW_WINDOW_OPTION
from ..service.target_identity import KEY_FIELD, decision_key


def settled_target_decision(data, action):
    """Reuse an answered decision: continue instead of asking the same question."""
    settled = {
        key: value
        for key, value in data.items()
        if key not in {"decision_required", "user_input_required", "selection_question"}
    }
    settled["target_action_confirmed"] = action
    settled["decision_reused"] = True
    settled["next_action"] = (
        "allocate_new_target" if action == "new" else "prepare_circuit_creation"
    )
    key = decision_key(data)
    if key:
        settled[KEY_FIELD] = key
    return settled


def target_decision(data):
    """Turn a "no captured design target" reply into a target-choice question."""
    candidates = (
        data.get("candidates") if isinstance(data.get("candidates"), list) else []
    )
    options = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        label = " / ".join(
            str(candidate.get(key, "")).strip() for key in ("library", "cell", "view")
        ).strip(" /")
        if not label or label in {option["label"] for option in options}:
            continue
        options.append(
            {
                "label": label[:200],
                "description": "已打开 · "
                + ("可编辑" if candidate.get("editable") else "只读"),
            }
        )
        if len(options) >= 5:
            break
    options.append(
        {
            "label": NEW_WINDOW_OPTION,
            "description": "从库中选择一个 cellview 打开并绑定",
        }
    )
    return {
        "target_ref": "target:" + uuid.uuid4().hex,
        "decision_required": True,
        "selection_question": {
            "id": "design_target",
            "header": "绑定设计目标",
            "question": "当前会话还没有捕获设计窗口，请选择这次工作绑定到哪个目标。",
            "options": options,
        },
        "candidates": candidates,
        "family": data.get("family", "schematic"),
        "untrusted": True,
    }
