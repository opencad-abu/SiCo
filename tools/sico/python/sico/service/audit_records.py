"""Service audit evidence and reply projections with a retained native alias."""

# Compatibility export; native input records are owned by Codex.
from ..codex.input_records import native_record as native_record

from ..core.contracts import ToolResult, identifier
from ..core.review_classes import BLOCKING, review_class
from ..storage.workbench import object_link


def prepared_record(args, context, resolve_evidence, current, task_id, *, normalize_questions):
    record = {
        "request_id": identifier(args["request_id"]),
        "title": args["title"], "recommendation": args["recommendation"],
        "rationale": args["rationale"], "questions": normalize_questions(args["questions"]),
        "context": context.record(),
        "evidence": resolve_evidence(args["evidence_ids"], current),
        "work_id": current["work_id"], "stage_id": current["stage_id"],
        "task_id": task_id,
        "review_class": review_class(args.get("review_class"), default=BLOCKING),
    }
    return record


def host_content(row):
    if row.get("destructive_kind"):
        answer_id = row["destructive_answer_id"]
        answer = row["reply"]["answers"][answer_id]
        content = (
            "用户已答复删除审阅（"
            + row["destructive_kind"]
            + "，review_digest="
            + row["destructive_digest"]
            + "）："
            + (answer["choice"] or answer["text"])
        )
        if answer["choice"] and answer["text"]:
            content += "；附加说明：" + answer["text"]
    elif row.get("pdk_update_ref"):
        answer = row["reply"]["answers"]["pdk_data_update"]
        content = "用户已答复 PDK 数据更新 " + row["pdk_update_ref"] + "：" + (answer["choice"] or answer["text"])
        if answer["choice"] and answer["text"]:
            content += "；附加说明：" + answer["text"]
    elif row.get("pdk_selection_ref"):
        answer = row["reply"]["answers"]["pdk_library"]
        selected = answer["choice"] or answer["text"]
        if selected not in row.get("pdk_candidates", []):
            raise ValueError("PDK answer did not authorize the selected library")
        content = "用户已选择 PDK：" + selected
    elif row.get("circuit_target_ref"):
        answer = row["reply"]["answers"]["circuit_target"]
        content = "用户已决定现有设计：" + (answer["choice"] or answer["text"])
    else:
        return None
    return content


def native_binding(params, child, *, bounded):
    if not isinstance(params.get("isBlocking"), bool):
        raise ValueError("Missing input blocking policy")
    item_id = bounded(params["itemId"], 200)
    expected_thread = child.thread_id if child is not None else params.get("threadId")
    expected_turn = child.turn_id if child is not None else params.get("turnId")
    if (child is not None and (params.get("threadId") != expected_thread
            or not isinstance(params.get("turnId"), str)
            or params.get("turnId") != expected_turn)):
        raise ValueError("Input request does not belong to the registered child turn")
    binding = {k: params[k] for k in ("threadId", "turnId", "itemId", "isBlocking")}
    return item_id, expected_thread, expected_turn, binding


def matching_preparation(audits, record):
    for row in audits:
        if row["task_id"] != record["task_id"]:
            continue
        if row.get("request_id") == record["request_id"]:
            if any(row.get(k) != v for k, v in record.items()):
                raise ValueError("Audit request_id was already used with different content")
            return row
        if row["status"] in {"prepared", "pending", "answer_received"}:
            raise ValueError("Finish the current audit before preparing another")
    return None


def prepared_result(record, session_id):
    return ToolResult(data={
        "audit_id": record["id"], "questions": record["questions"],
        "review_class": record.get("review_class", BLOCKING),
        "url": object_link("audit", session_id, record["id"]),
    })
