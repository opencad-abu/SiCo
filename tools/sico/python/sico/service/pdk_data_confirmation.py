"""Host-owned answers for exact, retained PDK data update previews."""

import uuid

from cadai.pdk_errors import PdkUnavailable

from ..core.contracts import NeedsReconcile
from .audit_questions import questions
from .pdk_update_review import preview, semantic_preview


def require(audit, data, evidence_id, context):
    with audit.loop.lock:
        if not audit.loop.active or audit.loop.cancelled() or audit.loop.stale:
            return
        current = audit.workbench.active()
        task = audit.loop.state.task["id"]
        for row in audit.index.audits.values():
            if (row["task_id"] == task and row.get("pdk_update_ref") == data["update_ref"]
                    and row["context"] == context.record()
                    and row["status"] in {"pending", "answer_received", "answered"}):
                return
        if audit.pending:
            raise NeedsReconcile("Finish the current question before another PDK update")
        evidence = audit.index.resolve("data", evidence_id)
        if evidence["task_id"] != task or audit.index.evidence_value(evidence_id).get("data") != data:
            raise ValueError("PDK update confirmation requires the original change preview")
        record = {"id": "a_" + uuid.uuid4().hex, "title": "确认 PDK 数据更新",
                  "questions": questions([data["selection_question"]]), "recommendation": "",
                  "rationale": "请核对工具预览中的具体字段差异；确认仅覆盖所示对象和修订。",
                  "context": context.record(), "task_id": task,
                  "work_id": current["work_id"], "stage_id": current["stage_id"],
                  "evidence": [{"id": evidence_id, "digest": evidence["digest"]}],
                  "pdk_update_ref": data["update_ref"], "pdk_update_revision": data["revision"]}
        audit.emit("prepared", record)
        audit.pending[record["id"]] = {
            "rpc_id": None, "binding": None, "blocking": True, "source": "host",
            "host_rpc": getattr(getattr(audit.loop, "runtime", None), "rpc", None),
            "host_connection_id": getattr(audit.loop, "connection_id", ""),
            "host_thread_id": getattr(audit.loop, "thread_id", None)}
        audit.loop.state.task["waiting_audits"] = list(audit.pending)
        audit.emit("opened", {"id": record["id"], "source": "pdk_data_update"})


def answer(audit, data, context):
    with audit.loop.lock:
        if not audit.loop.active or audit.loop.cancelled() or audit.loop.stale:
            return None
        audit.index.sync()
        for row in audit.index.audits.values():
            if (row["task_id"] == audit.loop.state.task["id"] and row["status"] == "answered"
                    and row["context"] == context.record()
                    and row.get("pdk_update_ref") == data["update_ref"]
                    and row.get("pdk_update_revision") == data["revision"]):
                if semantic_preview(preview(audit.index, row)) != semantic_preview(data):
                    raise PdkUnavailable(
                        "pdk_update_conflict", "PDK confirmation must match the exact reviewed changes")
                reply = row["reply"]["answers"]["pdk_data_update"]
                choice = reply["choice"] or reply["text"]
                # Free text is not interpreted as consent or an edit to the frozen preview.
                return {"decision": "confirm" if choice == "确认更新" and not (
                            reply["choice"] and reply["text"]) else "cancel",
                        "actor": row["reply"]["actor"], "evidence_ref": row["id"]}
        return None
