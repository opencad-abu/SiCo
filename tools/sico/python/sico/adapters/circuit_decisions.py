"""Project target observations and answered choices into host decisions."""

from copy import deepcopy
import uuid

from ..core.audit_questions import NEW_WINDOW_OPTION
from ..service.target_identity import KEY_FIELD, decision_key


def remember_created_target(client, data):
    """Record the task that wrote a target, including retained partial writes."""
    if not isinstance(data, dict):
        return
    receipt = data.get("receipt") if isinstance(data.get("receipt"), dict) else {}
    target = _target_list(data.get("target") or receipt.get("target"))
    task = data.get("task_ref") or receipt.get("task_ref")
    circuit = data.get("circuit_ref") or receipt.get("circuit_ref")
    if (
        target is not None and isinstance(task, str) and task
        and isinstance(circuit, str) and circuit
    ):
        client.created_targets[tuple(target)] = task


def _target_list(value):
    if isinstance(value, dict) and all(key in value for key in ("library", "cell", "view")):
        return [value["library"], value["cell"], value["view"]]
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return list(value)
    return None


def prepare_target_use(client, arguments, *, current_task, inspect):
    """Resolve target reuse at the write boundary, not during read-only inspection."""
    if "target_action" in arguments:
        return arguments, None, False
    target = _target_list((arguments.get("spec") or {}).get("target"))
    if target is None:
        return arguments, None, False
    observation = next(
        (
            deepcopy(row)
            for row in reversed(list(client.target_observations.values()))
            if _target_list(row.get("target")) == target
        ),
        None,
    )
    if observation is None:
        observation = inspect(target)
        if isinstance(observation, dict) and observation.get("target_ref"):
            client.target_observations[observation["target_ref"]] = deepcopy(observation)
    if not isinstance(observation, dict) or not observation.get("target_ref"):
        return arguments, None, False
    resolved = dict(arguments)
    if observation.get("content_state") == "empty":
        resolved.update(
            target_ref=observation["target_ref"], target_action="reuse_empty"
        )
        return resolved, None, True
    if (
        current_task
        and client.created_targets.get(tuple(target)) == current_task
        and observation.get("content_state") == "nonempty"
    ):
        resolved.update(
            target_ref=observation["target_ref"], target_action="continue"
        )
        return resolved, None, True
    if observation.get("content_state") == "nonempty":
        from cadai.circuit_create import target_decision

        return arguments, target_decision(observation), False
    return arguments, None, False


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
