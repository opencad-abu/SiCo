"""Durable engineering decisions with compatibility question exports; only the worker sends RPC answers."""

from __future__ import annotations

import uuid
from contextlib import nullcontext

from ..codex.elicitation import Elicitations
from ..codex.input_requests import InputRequests
from ..core.contracts import NeedsReconcile, identifier
from .audit_decisions import (
    answered_target_action,
    confirmed_pdk_choice,
    confirmed_target_choice,
    matched_target_row,
    normalize_answers,
    pdk_record,
    target_record,
)

# Compatibility imports retain the historical module entry; migrate imports to audit_instructions.
from .audit_instructions import INSTRUCTIONS as INSTRUCTIONS

# Compatibility imports retain the historical module entry; migrate imports to audit_questions.
from .audit_questions import NEW_WINDOW_OPTION as NEW_WINDOW_OPTION
from .audit_questions import QUESTION as QUESTION
from .audit_questions import bounded as bounded
from .audit_questions import questions as questions
from .audit_questions import same_host_questions as same_host_questions
from .audit_records import (
    host_content,
    matching_preparation,
    native_binding,
    native_record,
    prepared_record,
    prepared_result,
)


class AuditService:
    def __init__(self, workbench):
        self.workbench, self.loop = workbench, workbench.loop
        self.pending = {}
        # Host answers can be delivered before Codex emits its native input
        # item. Retain those rows long enough to bind a late exact-match item.
        self._host_delivered = {}
        self.requests = InputRequests(self)
        self.elicitations = Elicitations(self)

    @property
    def index(self):
        return self.workbench.index

    def emit(self, kind, payload):
        row = self.index.audits.get(payload.get("id"), {})
        if kind == "invalidated" and row and row["task_id"] != self.loop.state.task.get("id"):
            self.loop._event("codex.input_ignored", {"reason": "old_task_mailbox_discarded"})
            return
        if row.get("input_origin") and "input_origin" not in payload:
            payload = {**payload, "input_origin": row["input_origin"]}
        self.workbench.emit("workbench.audit." + kind, payload)

    def pending_title(self):
        return "；".join(self.index.audits[key]["title"] for key in self.pending)

    def waiting_for_input(self):
        return any(
            pending["rpc_id"] is not None and pending.get("blocking", True)
            for pending in self.pending.values()
        )

    def waiting_for_nonblocking_input(self):
        """Return true when Codex has an unanswered non-blocking input item."""
        return any(
            pending["rpc_id"] is not None and not pending.get("blocking", True)
            for pending in self.pending.values()
        )

    def waiting_for_host(self):
        """Return true while a host-opened question has no native RPC binding."""
        return any(pending["rpc_id"] is None for pending in self.pending.values())

    def host_answer_ready(self):
        with self.loop.lock:
            self.index.sync()
            return any(
                pending["rpc_id"] is None
                and self.index.audits[key]["status"] == "answer_received"
                for key, pending in self.pending.items()
            )

    def prepare(self, args, context):
        with getattr(self.loop, "lock", nullcontext()):
            current = self.workbench.active()
            if not current["stage_id"]:
                raise ValueError("Select an engineering stage before preparing an audit")
            record = prepared_record(
                args, context, self.workbench.evidence,
                current, self.loop.state.task["id"], normalize_questions=questions,
            )
            existing = matching_preparation(self.index.audits.values(), record)
            if existing is not None:
                return self.prepared_result(existing)
            record["id"] = "a_" + uuid.uuid4().hex
            self.emit("prepared", record)
            return self.prepared_result(record)

    def prepared_result(self, record):
        return prepared_result(record, self.index.session_id)

    def require_pdk_choice(self, data, evidence_id, context):
        """Expose a host prerequisite before Codex opens its native input RPC."""
        with self.loop.lock:
            current = self.workbench.active()
            if not self.loop.active or self.loop.cancelled() or self.loop.stale:
                return
            for row in self.index.audits.values():
                if row["task_id"] != self.loop.state.task["id"]:
                    continue
                if (row.get("pdk_selection_ref") == data["selection_ref"]
                        and row["context"] == context.record()
                        and row["status"] in {"pending", "answer_received", "answered"}):
                    return
            if self.pending:
                for key in self.pending:
                    self.emit("invalidated", {"id": key,
                        "reason": "PDK 候选或待答复事项已变化，请核对后重新发起任务。"})
                self.loop.stale = True
                self.loop.stale_reason = "PDK selection changed while awaiting user input"
                return
            evidence = self.index.resolve("data", evidence_id)
            if (evidence["task_id"] != self.loop.state.task["id"]
                    or self.index.evidence_value(evidence_id).get("data") != data):
                raise ValueError("PDK question requires its original tool evidence")
            record = pdk_record(
                data, evidence_id, evidence, context, self.loop.state.task["id"], current,
                normalize_questions=questions,
            )
            self.emit("prepared", record)
            self.pending[record["id"]] = {
                "rpc_id": None, "binding": None, "blocking": True, "source": "host",
                "host_rpc": getattr(getattr(self.loop, "runtime", None), "rpc", None),
                "host_connection_id": getattr(self.loop, "connection_id", ""),
                "host_thread_id": getattr(self.loop, "thread_id", None),
            }
            self.loop.state.task["waiting_audits"] = list(self.pending)
            self.emit("opened", {"id": record["id"], "source": "pdk_preparation"})

    def require_pdk_update(self, data, evidence_id, context):
        from .pdk_data_confirmation import require
        return require(self, data, evidence_id, context)

    def pdk_update_confirmation(self, data, context):
        from .pdk_data_confirmation import answer
        return answer(self, data, context)

    def pdk_choice(self, library, info, context):
        with self.loop.lock:
            if not self.loop.active or self.loop.cancelled() or self.loop.stale:
                return False
            self.index.sync()
            return confirmed_pdk_choice(
                self.index.audits, self.loop.state.task["id"], context,
                library, info, self.index.verify_evidence,
            )

    def require_target_choice(self, data, evidence_id, context):
        with self.loop.lock:
            if not self.loop.active or self.loop.cancelled() or self.loop.stale:
                return
            current = self.workbench.active()
            # Re-inspecting unchanged contents is the same decision: a retained
            # question or answer for those contents already covers this one.
            if matched_target_row(
                self.index.audits.values(), self.loop.state.task["id"], context, data,
                {"pending", "answer_received", "answered"},
            ) is not None:
                return
            if self.pending:
                raise NeedsReconcile("Existing design changed while another decision is pending")
            evidence = self.index.resolve("data", evidence_id)
            if (evidence["task_id"] != self.loop.state.task["id"]
                    or self.index.evidence_value(evidence_id).get("data") != data):
                raise ValueError("Target question requires its original tool evidence")
            record = target_record(
                data, evidence_id, evidence, context, self.loop.state.task["id"], current,
                normalize_questions=questions,
            )
            self.emit("prepared", record)
            self.pending[record["id"]] = {
                "rpc_id": None, "binding": None, "blocking": True, "source": "host",
                "host_rpc": getattr(getattr(self.loop, "runtime", None), "rpc", None),
                "host_connection_id": getattr(self.loop, "connection_id", ""),
                "host_thread_id": getattr(self.loop, "thread_id", None),
            }
            self.loop.state.task["waiting_audits"] = list(self.pending)
            self.emit("opened", {"id": record["id"], "source": "circuit_target"})

    def target_choice(self, action, info, context):
        from cadai.circuit_create import TARGET_CHOICES

        with self.loop.lock:
            if not self.loop.active or self.loop.cancelled() or self.loop.stale:
                return False
            self.index.sync()
            return confirmed_target_choice(
                self.index.audits, self.loop.state.task["id"], context,
                action, info, self.index.verify_evidence, TARGET_CHOICES,
            )

    def settled_target_action(self, info, context):
        """The action an already answered decision authorizes for this observation.

        Returning the action lets the admitting layer skip a question whose
        answer is on record instead of asking the user the same thing again.
        """
        from cadai.circuit_create import TARGET_CHOICES

        with self.loop.lock:
            if not self.loop.active or self.loop.cancelled() or self.loop.stale:
                return None
            self.index.sync()
            return answered_target_action(
                self.index.audits, self.loop.state.task["id"], context,
                info, self.index.verify_evidence, TARGET_CHOICES,
            )

    def open(self, event, *, child=None):
        with self.loop.lock:
            params = event["params"]
            incoming = questions(params["questions"])
            item_id, expected_thread, expected_turn, binding = native_binding(
                params, child, bounded=bounded)
            self.requests.connect()
            binding["connection_id"] = self.requests.connection_id
            if child is not None:
                self.loop.child_inputs.current(child.thread_id, turn_id=expected_turn)
                binding.update({"child_thread_id": child.thread_id,
                                "parent_thread_id": child.parent_thread_id,
                                "child_turn_id": expected_turn})
            claim = self.requests.check(event["id"], expected_thread, [binding, incoming])
            if claim is None:
                return
            for key, pending in self.pending.items():
                if pending["rpc_id"] is None:
                    if child is not None:
                        continue
                    row = self.index.audits[key]
                    if not same_host_questions(row["questions"], incoming):
                        continue
                    self.index.verify_evidence(row["evidence"])
                    pending.update(
                        rpc_id=event["id"], binding=binding,
                        blocking=params["isBlocking"], source="native", questions=incoming,
                    )
                    self.emit("bound", {"id": key, **binding})
                    self.requests.remember(claim)
                    return
                if pending["binding"] == binding and pending["rpc_id"] == event["id"]:
                    if pending.get("questions", self.index.audits[key]["questions"]) != incoming:
                        raise ValueError("Input request identity changed")
                    return
            for key in list(self._host_delivered) if child is None else ():
                row = self.index.audits.get(key)
                if row is None or row["task_id"] != self.loop.state.task["id"]:
                    self._host_delivered.pop(key, None)
                    continue
                if not same_host_questions(row["questions"], incoming):
                    continue
                self.index.verify_evidence(row["evidence"])
                pending = self._host_delivered.pop(key)
                response = pending.pop("response")
                self.loop.runtime.rpc.send(
                    {"id": event["id"], "result": {"answers": response}}
                )
                self.emit("bound", {"id": key, **binding})
                self.requests.remember(claim)
                self.loop._activity("waiting_model", "等待 LLM 响应")
                return
            # A host prerequisite owns the decision surface. Codex sometimes
            # emits a later blocking request that combines that question with
            # another clarification (for example PDK plus execution mode).
            # Do not turn the combined payload into a second audit: reject it
            # so the model can retry with the exact host question while the
            # original answer remains actionable.
            if child is None and (
                    any(pending["rpc_id"] is None for pending in self.pending.values())
                    or self._host_delivered) and any(
                        set(q["id"] for q in self.index.audits[key]["questions"])
                        & set(q["id"] for q in incoming)
                        for key in set(self.pending) | set(self._host_delivered)):
                raise ValueError("Input conflicts with the host-opened audit; "
                                 "reuse its original questions")
            current = self.workbench.active()
            # Reuse the prepared evidence and displayed options when native
            # input changes only presentation, leaving no orphan preparation.
            row = next(
                (candidate for candidate in self.index.audits.values()
                 if candidate["task_id"] == self.loop.state.task["id"]
                 and candidate.get("child_thread_id", "") == (child.thread_id if child else "")
                 and candidate["status"] == "prepared"
                 and same_host_questions(candidate["questions"], incoming)),
                None,
            )
            if row is None:
                if any(candidate["task_id"] == self.loop.state.task["id"]
                       and candidate.get("child_thread_id", "") == (
                           child.thread_id if child else "")
                       and candidate["status"] == "prepared"
                       and {q["id"] for q in candidate["questions"]}
                       & {q["id"] for q in incoming}
                       for candidate in self.index.audits.values()):
                    raise ValueError("Input conflicts with the prepared audit; "
                                     "reuse its original questions")
                row = native_record(
                    incoming, self.loop.state.task["id"], self.loop.state.context.record(),
                    current, self.index.data[current["source"]]["digest"], child, expected_turn,
                    self.index.audits.values(), len(self.pending),
                    self.loop.child_inputs.scope if child is not None else None,
                )
                self.emit("prepared", row)
            self.index.verify_evidence(row["evidence"])
            self.pending[row["id"]] = {
                "rpc_id": event["id"], "binding": binding,
                "blocking": params["isBlocking"], "source": "native", "questions": incoming,
                "child_thread_id": child.thread_id if child is not None else "",
                "child_turn_id": expected_turn if child is not None else "",
            }
            self.loop.state.task["waiting_audits"] = list(self.pending)
            opened = {"id": row["id"], **binding, "itemId": item_id}
            if child is not None:
                opened["child_name"] = child.policy.name
            self.emit("opened", opened)
            self.requests.remember(claim)
            self.loop._activity("waiting_user", "等待答复：" + row["title"])

    def answer(self, audit_id, answers, reply_id, actor, task_id):
        with getattr(self.loop, "lock", nullcontext()):
            self.index.sync()
            row = self.index.resolve("audit", audit_id)
            if "elicitation" in row:
                raise ValueError("此事项须通过原 MCP 交互表单答复")
            if row["task_id"] != task_id:
                raise ValueError("答复不属于此执行任务")
            if row.get("child_thread_id"):
                self.native_current(audit_id)
            identifier(reply_id)
            normalized = normalize_answers(answers, row["questions"], row.get("pdk_candidates"), bounded=bounded)
            from ..codex.approval_requests import validate_answer
            validate_answer(row, normalized)
            if row.get("reply"):
                if row["reply"]["answers"] == normalized:
                    return False
                raise ValueError("此事项已答复，不能覆盖原决定")
            if (audit_id not in self.pending or row["status"] != "pending"
                    or not self.loop.active or self.loop.cancelled() or self.loop.stale):
                raise ValueError("此事项已结束或失效，答复未执行")
            self.emit("reply_received", {
                "id": audit_id, "reply_id": reply_id,
                "answers": normalized, "actor": bounded(actor, 200),
            })
            return True

    def deliver(self):
        from .audit_delivery import deliver
        return deliver(self)

    def native_current(self, key):
        from .audit_delivery import native_current
        return native_current(self, key)

    def end_inputs(self, reason, *, child_id=None, keys=None, send=True):
        """Invalidate original RPC mailboxes without binding them to another task."""
        with self.loop.lock:
            selected = [key for key, pending in self.pending.items()
                        if (keys is None or key in keys)
                        and (child_id is None or pending.get("child_thread_id") == child_id)
                        and pending["rpc_id"] is not None]
            failure = None
            try:
                self.elicitations.end(reason, keys=selected, send=send)
            except (OSError, ValueError, RuntimeError) as exc:
                failure = exc
            for key in selected:
                pending = self.pending.pop(key, None)
                if pending is None:
                    continue
                try:
                    self.emit("invalidated", {"id": key, "reason": reason})
                    if (send and self.loop.runtime
                            and self.loop.runtime.rpc is self.requests.connection
                            and pending["binding"].get("connection_id") == self.loop.connection_id):
                        self.requests.connection.send({"id": pending["rpc_id"], "error": {
                            "code": -32800, "message": "Input request is no longer active"}})
                except (OSError, ValueError, RuntimeError) as exc:
                    failure = failure or exc
            if self.loop.state.task:
                self.loop.state.task["waiting_audits"] = list(self.pending)
            if failure:
                raise failure

    def deliver_host(self):
        """Validate and consume an answer that arrived before native input binding."""
        replies = []
        with self.loop.lock:
            self.index.sync()
            for key in list(self.pending):
                pending = self.pending[key]
                row = self.index.audits[key]
                if pending["rpc_id"] is not None or row["status"] != "answer_received":
                    continue
                if self.loop.cancelled() or self.loop.stale:
                    return replies
                try:
                    self.index.verify_evidence(row["evidence"])
                    if row["context"] != self.loop.state.context.record():
                        raise ValueError("Captured target changed")
                    self.loop.validate_audit_target()
                    content = host_content(row)
                    if content is None:
                        continue
                except (ValueError, OSError, TypeError, KeyError, NeedsReconcile) as exc:
                    self.emit("invalidated", {"id": key,
                                              "reason": "目标或证据已变化，答复未执行。"})
                    self.loop.stale = True
                    self.loop.stale_reason = (str(exc)
                        or "Audit target or evidence invalid; no continuation")
                    return replies
                replies.append({"role": "user", "content": content})
                self.pending.pop(key)
                self._host_delivered[key] = {
                    **pending,
                    "response": {
                        q: {"answers": [v for v in (a["choice"], a["text"]) if v]}
                        for q, a in row["reply"]["answers"].items()
                    },
                }
                self.loop.state.task["waiting_audits"] = list(self.pending)
                self.emit("resumed", {"id": key, "reply_id": row["reply"]["reply_id"]})
        return replies

    def deliver_local(self):
        """Return host answers as provider messages for the Python backend.

        The legacy provider loop uses a local message list instead of a native
        RPC response. Keep that path on the same evidence and target checks as
        the Codex backend.
        """
        return self.deliver_host()

    def finish(self):
        self.pending.clear()
        self._host_delivered.clear()
        self.loop.state.task.pop("waiting_audits", None)
