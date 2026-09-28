"""Host decision records, answer normalization and confirmed-choice evidence checks."""

import uuid

from ..core.contracts import NeedsReconcile
from .target_identity import decision_key


def pdk_record(data, evidence_id, evidence, context, task_id, current, *, normalize_questions):
    record = {
        "id": "a_" + uuid.uuid4().hex, "title": data["selection_question"]["header"],
        "questions": normalize_questions([data["selection_question"]]),
        "recommendation": data.get("recommended_library") or "",
        "rationale": "本次使用的工艺需要用户选择。",
        "context": context.record(), "task_id": task_id,
        "work_id": current["work_id"], "stage_id": current["stage_id"],
        "evidence": [{"id": evidence_id, "digest": evidence["digest"]}],
        "pdk_selection_ref": data["selection_ref"],
        "pdk_candidates": [r["library"] for r in data["candidates"]],
    }
    return record


def target_record(data, evidence_id, evidence, context, task_id, current, *, normalize_questions):
    record = {"id": "a_" + uuid.uuid4().hex, "title": data["selection_question"]["header"],
        "questions": normalize_questions([data["selection_question"]]),
        "recommendation": "继续完善现有设计",
        "rationale": "当前原理图已有内容，需要确定本次设计范围。",
        "context": context.record(), "task_id": task_id,
        "work_id": current["work_id"], "stage_id": current["stage_id"],
        "evidence": [{"id": evidence_id, "digest": evidence["digest"]}],
        "circuit_target_ref": data["target_ref"],
        "circuit_target_key": decision_key(data)}
    return record


def normalize_answers(answers, question_rows, pdk_candidates, *, bounded):
    expected = {q["id"] for q in question_rows}
    if not isinstance(answers, dict) or set(answers) != expected:
        raise ValueError("请答复本事项的全部问题")
    normalized = {}
    for q in question_rows:
        answer = answers[q["id"]]
        if not isinstance(answer, dict) or set(answer) != {"choice", "text"}:
            raise ValueError("答复格式无效")
        choice = bounded(answer["choice"], 200, empty=True)
        text = bounded(answer["text"], 4000, empty=True).strip()
        if choice and choice not in {o["label"] for o in q["options"]}:
            raise ValueError("所选方案不属于此问题")
        if not choice and not text:
            raise ValueError("请选择方案或填写答复")
        normalized[q["id"]] = {"choice": choice, "text": text}
    if pdk_candidates:
        reply = normalized["pdk_library"]
        selected = reply["choice"] or reply["text"]
        if (selected not in pdk_candidates
                or (reply["choice"] and reply["text"] in pdk_candidates
                    and reply["choice"] != reply["text"])):
            raise ValueError("请选择一个候选 PDK，或填写候选工艺库的精确名称")
    return normalized


def confirmed_pdk_choice(audits, task_id, context, library, info, verify_evidence):
    for row in audits.values():
        if (row["task_id"] == task_id
                and row["context"] == context.record()
                and row.get("pdk_selection_ref") == info["selection_ref"]
                and row["status"] == "answered"):
            try:
                verify_evidence(row["evidence"])
            except (ValueError, OSError, TypeError, KeyError) as exc:
                raise NeedsReconcile(
                    "PDK selection evidence changed; no continuation") from exc
            reply = row["reply"]["answers"]["pdk_library"]
            return library == (reply["choice"] or reply["text"])
    return False


def matched_target_row(rows, task_id, context, info, statuses):
    """The retained decision this observation belongs to, or ``None``.

    Contents decide: the same unchanged cellview is the same decision even when
    another inspection reported it under a new retained ref. Rows persisted
    before content keys existed keep matching by their retained ref.
    """
    key = decision_key(info)
    for row in rows:
        if (row["task_id"] == task_id
                and row["context"] == context.record()
                and row["status"] in statuses):
            recorded = row.get("circuit_target_key")
            if recorded:
                if key and recorded == key:
                    return row
            elif row.get("circuit_target_ref") == info.get("target_ref"):
                return row
    return None


def answered_target_row(audits, task_id, context, info, verify_evidence):
    """The answered decision for this observation, or ``None``."""
    row = matched_target_row(audits.values(), task_id, context, info, {"answered"})
    if row is not None:
        try:
            verify_evidence(row["evidence"])
        except (ValueError, OSError, TypeError, KeyError) as exc:
            raise NeedsReconcile(
                "Target selection evidence changed; inspect again") from exc
    return row


def confirmed_target_choice(audits, task_id, context, action, info, verify_evidence, target_choices):
    row = answered_target_row(audits, task_id, context, info, verify_evidence)
    if row is None:
        return False
    reply = row["reply"]["answers"]["circuit_target"]
    return (reply["choice"] or reply["text"]) == target_choices.get(action)


def answered_target_action(audits, task_id, context, info, verify_evidence, target_choices):
    """The action this observation's answered decision already authorizes."""
    row = answered_target_row(audits, task_id, context, info, verify_evidence)
    if row is None:
        return None
    reply = row["reply"]["answers"]["circuit_target"]
    answer = reply["choice"] or reply["text"]
    return next((action for action, label in target_choices.items() if label == answer), None)
