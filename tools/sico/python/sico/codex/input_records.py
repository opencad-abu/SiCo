"""Project native questions and approvals into task-bound decision records.

Codex owns child attribution and its input budget; the service validates evidence
and persists the returned record. This projector owns no mailbox or lifecycle.
"""

import uuid

from ..core.review_classes import BLOCKING, review_class


def native_record(incoming, task_id, context, current, source_digest, child, expected_turn, audits,
                  pending_count, child_scope, *, review_class_value=BLOCKING):
    row = {
        "id": "a_" + uuid.uuid4().hex, "title": incoming[0]["header"],
        "questions": incoming, "recommendation": "", "rationale": "",
        "task_id": task_id,
        "review_class": review_class(review_class_value),
        "work_id": current["work_id"], "stage_id": current["stage_id"],
        "context": context,
        "evidence": [{"id": current["source"],
                      "digest": source_digest}],
    }
    if child is not None:
        existing = [r for r in audits
                    if r["task_id"] == task_id]
        if len(existing) >= 32 or pending_count >= 4:
            raise ValueError("Child input limit reached")
        row.update({"child_thread_id": child.thread_id,
                    "parent_thread_id": child.parent_thread_id,
                    "child_turn_id": expected_turn,
                    "child_name": child.policy.name,
                    "title": child.policy.name + " · " + row["title"],
                    "input_origin": child_scope(child)})
    return row
