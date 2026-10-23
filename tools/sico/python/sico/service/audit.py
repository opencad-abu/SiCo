"""Durable engineering decisions with compatibility question exports; only the worker sends RPC answers."""

from __future__ import annotations

import uuid

from ..codex.elicitation import Elicitations
from ..codex.input_records import native_record
from ..codex.input_delivery import InputDelivery, send_controls
from ..core.contracts import NeedsReconcile, identifier
from ..core.review_classes import BLOCKING, effective
from .audit_decisions import (
    answered_target_action,
    confirmed_pdk_choice,
    confirmed_target_choice,
    matched_target_row,
    normalize_answers,
    pdk_record,
    target_record,
)
from .audit_destructive import DestructiveReviewMixin

# Compatibility imports retain the historical module entry; migrate imports to codex.audit_instructions.
from ..codex.audit_instructions import INSTRUCTIONS as INSTRUCTIONS

# Compatibility imports retain the historical module entry; migrate imports to core.audit_questions.
from ..core.audit_questions import NEW_WINDOW_OPTION as NEW_WINDOW_OPTION
from ..core.audit_questions import QUESTION as QUESTION
from ..core.audit_questions import bounded as bounded
from ..core.audit_questions import questions as questions
from ..core.audit_questions import same_host_questions as same_host_questions
from ..core.target_validation import TargetUnconfirmed
from .audit_records import (
    host_content,
    matching_preparation,
    native_binding,
    prepared_record,
    prepared_result,
)


class AuditService(DestructiveReviewMixin):
    def __init__(self, *, index, active_work, evidence, publish, runtime, requests):
        self.index, self.active_work, self.evidence, self.publish = index, active_work, evidence, publish
        self.runtime, self.requests = runtime, requests
        self.elicitations = Elicitations(
            runtime=self.runtime, requests=self.requests, index=self.index,
            active_work=self.active_work, emit=lambda *a, **kw: self.emit(*a, **kw),
            withdraw=self.withdraw_inputs, end=self.end_inputs)

    @property
    def pending(self):
        return self.requests.snapshot()

    def open_input(self, key, pending, payload, *, emit=None, claim=None):
        publish = emit or self.emit
        self.requests.open(key, pending, lambda: publish("opened", payload), claim=claim)

    def open_host(self, record, source):
        self.requests.connect(required=False)
        declared = effective(record.get("review_class"), True)
        self.open_input(record["id"], {
            "rpc_id": None, "binding": None, "blocking": True,
            "review_class": declared, "source": "host",
            "host_connection_id": self.requests.connection_id,
            "host_thread_id": self.runtime.snapshot().thread_id,
        }, {"id": record["id"], "source": source, "review_class": declared})

    def emit(self, kind, payload):
        row = self.index.audits.get(payload.get("id"), {})
        if kind == "invalidated" and row and row["task_id"] != self.runtime.snapshot().task_id:
            self.requests.ignore("old_task_mailbox_discarded")
            return
        if row.get("input_origin") and "input_origin" not in payload:
            payload = {**payload, "input_origin": row["input_origin"]}
        self.publish("workbench.audit." + kind, payload)

    def waiting_title(self):
        return "；".join(
            self.index.audits[key]["title"] for key, pending in self.pending.items()
            if pending.get("blocking", True)
        )

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
        with self.runtime.lock:
            self.index.sync()
            return any(
                pending["rpc_id"] is None
                and self.index.audits[key]["status"] == "answer_received"
                for key, pending in self.pending.items()
            )

    def prepare(self, args, context):
        self.runtime.require("native_input")
        with self.runtime.lock:
            current = self.active_work()
            if not current["stage_id"]:
                raise ValueError("Select an engineering stage before preparing an audit")
            record = prepared_record(
                args, context, self.evidence,
                current, self.runtime.snapshot().task_id, normalize_questions=questions,
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
        self.runtime.require("pdk_selection")
        with self.runtime.lock:
            current = self.active_work()
            if not self.runtime.admitted():
                return
            for row in self.index.audits.values():
                if row["task_id"] != self.runtime.snapshot().task_id:
                    continue
                if (row.get("pdk_selection_ref") == data["selection_ref"]
                        and row["context"] == context.record()
                        and row["status"] in {"pending", "answer_received", "answered"}):
                    return
            if self.pending:
                self.withdraw_inputs("PDK 候选或待答复事项已变化，请核对后重新发起任务。", send=False)
                self.runtime.invalidate("PDK selection changed while awaiting user input")
                return
            evidence = self.index.resolve("data", evidence_id)
            if (evidence["task_id"] != self.runtime.snapshot().task_id
                    or self.index.evidence_value(evidence_id).get("data") != data):
                raise ValueError("PDK question requires its original tool evidence")
            record = pdk_record(
                data, evidence_id, evidence, context, self.runtime.snapshot().task_id, current,
                normalize_questions=questions,
            )
            self.emit("prepared", record)
            self.open_host(record, "pdk_preparation")

    def require_pdk_update(self, data, evidence_id, context):
        from .pdk_data_confirmation import require
        return require(self, data, evidence_id, context)

    def pdk_update_confirmation(self, data, context):
        from .pdk_data_confirmation import answer
        return answer(self, data, context)

    def pdk_choice(self, library, info, context):
        self.runtime.require("pdk_selection")
        with self.runtime.lock:
            if not self.runtime.admitted():
                return False
            self.index.sync()
            return confirmed_pdk_choice(
                self.index.audits, self.runtime.snapshot().task_id, context,
                library, info, self.index.verify_evidence,
            )

    def require_target_choice(self, data, evidence_id, context):
        with self.runtime.lock:
            if not self.runtime.admitted():
                return
            current = self.active_work()
            # Re-inspecting unchanged contents is the same decision: a retained
            # question or answer for those contents already covers this one.
            if matched_target_row(
                self.index.audits.values(), self.runtime.snapshot().task_id, context, data,
                {"pending", "answer_received", "answered"},
            ) is not None:
                return
            if self.pending:
                raise NeedsReconcile("Existing design changed while another decision is pending")
            evidence = self.index.resolve("data", evidence_id)
            if (evidence["task_id"] != self.runtime.snapshot().task_id
                    or self.index.evidence_value(evidence_id).get("data") != data):
                raise ValueError("Target question requires its original tool evidence")
            record = target_record(
                data, evidence_id, evidence, context, self.runtime.snapshot().task_id, current,
                normalize_questions=questions,
            )
            self.emit("prepared", record)
            self.open_host(record, "circuit_target")

    def target_choice(self, action, info, context):
        from cadai.circuit_create import TARGET_CHOICES

        with self.runtime.lock:
            if not self.runtime.admitted():
                return False
            self.index.sync()
            return confirmed_target_choice(
                self.index.audits, self.runtime.snapshot().task_id, context,
                action, info, self.index.verify_evidence, TARGET_CHOICES,
            )

    def settled_target_action(self, info, context):
        """The action an already answered decision authorizes for this observation.

        Returning the action lets the admitting layer skip a question whose
        answer is on record instead of asking the user the same thing again.
        """
        from cadai.circuit_create import TARGET_CHOICES

        with self.runtime.lock:
            if not self.runtime.admitted():
                return None
            self.index.sync()
            return answered_target_action(
                self.index.audits, self.runtime.snapshot().task_id, context,
                info, self.index.verify_evidence, TARGET_CHOICES,
            )

    def open(self, event, *, child=None):
        self.runtime.require("native_input")
        with self.runtime.lock:
            reply = self._open(event, child=child)
        if isinstance(reply, InputDelivery):
            reply.send()
        elif reply:
            send_controls([reply])

    def _open(self, event, *, child=None):
        params = event["params"]
        incoming = questions(params["questions"])
        item_id, expected_thread, expected_turn, binding = native_binding(
            params, child, bounded=bounded)
        self.requests.connect()
        binding["connection_id"] = self.requests.connection_id
        if child is not None:
            self.runtime.child_current(child.thread_id, turn_id=expected_turn)
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
                declared = row.get("review_class", BLOCKING)
                classification = effective(declared, params["isBlocking"])
                bound = dict(rpc_id=event["id"], binding=binding,
                             blocking=classification == BLOCKING,
                             review_class=classification, source="native", questions=incoming)
                self.requests.bind(key, bound, lambda: self.emit("bound", {
                    "id": key, "review_class": classification, **binding}),
                                   claim=claim)
                return
            if pending["binding"] == binding and pending["rpc_id"] == event["id"]:
                if pending.get("questions", self.index.audits[key]["questions"]) != incoming:
                    raise ValueError("Input request identity changed")
                return
        for key in self.requests.delivered_hosts() if child is None else ():
            row = self.index.audits.get(key)
            if row is None or row["task_id"] != self.runtime.snapshot().task_id:
                self.requests.take_host(key)
                continue
            if not same_host_questions(row["questions"], incoming):
                continue
            self.index.verify_evidence(row["evidence"])
            response = self.requests.take_host(key)
            self.requests.remember(claim)
            connection = self.requests.connection
            delivery = InputDelivery(
                key, dict(rpc_id=event["id"], binding=binding), connection,
                {"answers": response}, self.emit, runtime=self.runtime, requests=self.requests,
                row=row, status=lambda: self.index.audits[key]["status"])
            self.emit("bound", {"id": key, **binding})
            self.emit("dispatching", {"id": key, "reply_id": row["reply"]["reply_id"]})
            return delivery
        # A host prerequisite owns the decision surface. Codex sometimes
        # emits a later blocking request that combines that question with
        # another clarification (for example PDK plus execution mode).
        # Do not turn the combined payload into a second audit: reject it
        # so the model can retry with the exact host question while the
        # original answer remains actionable.
        if child is None and (
                any(pending["rpc_id"] is None for pending in self.pending.values())
                or self.requests.delivered_hosts()) and any(
                    set(q["id"] for q in self.index.audits[key]["questions"])
                    & set(q["id"] for q in incoming)
                    for key in set(self.pending) | set(self.requests.delivered_hosts())):
            raise ValueError("Input conflicts with the host-opened audit; "
                             "reuse its original questions")
        current = self.active_work()
        # Reuse the prepared evidence and displayed options when native
        # input changes only presentation, leaving no orphan preparation.
        row = next(
            (candidate for candidate in self.index.audits.values()
             if candidate["task_id"] == self.runtime.snapshot().task_id
             and candidate.get("child_thread_id", "") == (child.thread_id if child else "")
             and candidate["status"] == "prepared"
             and same_host_questions(candidate["questions"], incoming)),
            None,
        )
        if row is None:
            if any(candidate["task_id"] == self.runtime.snapshot().task_id
                   and candidate.get("child_thread_id", "") == (
                       child.thread_id if child else "")
                   and candidate["status"] == "prepared"
                   and {q["id"] for q in candidate["questions"]}
                   & {q["id"] for q in incoming}
                   for candidate in self.index.audits.values()):
                raise ValueError("Input conflicts with the prepared audit; "
                                 "reuse its original questions")
            row = native_record(
                incoming, self.runtime.snapshot().task_id, self.runtime.snapshot().context,
                current, self.index.data[current["source"]]["digest"], child, expected_turn,
                self.index.audits.values(), len(self.pending),
                self.runtime.child_scope if child is not None else None,
                review_class_value=BLOCKING,
            )
            self.emit("prepared", row)
        self.index.verify_evidence(row["evidence"])
        classification = effective(row.get("review_class"), params["isBlocking"])
        pending = {
            "rpc_id": event["id"], "binding": binding,
            "blocking": classification == BLOCKING, "review_class": classification,
            "source": "native", "questions": incoming,
            "child_thread_id": child.thread_id if child is not None else "",
            "child_turn_id": expected_turn if child is not None else "",
        }
        opened = {"id": row["id"], **binding, "itemId": item_id,
                  "review_class": classification}
        if child is not None:
            opened["child_name"] = child.policy.name
        self.open_input(row["id"], pending, opened, claim=claim)
        if row["id"] in self.pending:
            if classification == BLOCKING:
                self.runtime.activity("waiting_user", "等待答复：" + row["title"])
            else:
                self.runtime.activity("supplementary", "补充事项（可继续）：" + row["title"])

    def answer(self, audit_id, answers, reply_id, actor, task_id):
        with self.runtime.lock:
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
                    or not self.runtime.admitted()):
                raise ValueError("此事项已结束或失效，答复未执行")
            self.emit("reply_received", {
                "id": audit_id, "reply_id": reply_id,
                "answers": normalized, "actor": bounded(actor, 200),
            })
            return True

    def answer_supplementary(self):
        """Release supplementary native questions so the model may decide."""
        from .audit_supplementary import answer
        return answer(self)

    def deliver(self):
        from .audit_delivery import deliver
        return deliver(self)

    def native_current(self, key):
        from .audit_delivery import native_current
        return native_current(self, key)

    def resolved(self, event):
        from .audit_continuation import retain_resolved
        return self.requests.resolved(event, retain=lambda key, pending: retain_resolved(self, key, pending))

    def retain_turn_reviews(self, turn_id):
        from .audit_continuation import retain
        retain(self, turn_id)

    def continue_turn(self, turn_id):
        from .audit_continuation import submit
        return submit(self, turn_id)

    def end_inputs(self, reason, *, child_id=None, keys=None, send=True):
        replies = self.withdraw_inputs(reason, child_id=child_id, keys=keys, send=send)
        send_controls(replies)

    def withdraw_inputs(self, reason, *, child_id=None, keys=None, send=True):
        """Consume mailboxes under lock; return captured, never retried control replies."""
        replies, failure = [], None
        with self.runtime.lock:
            selected = [(key, pending) for key, pending in self.pending.items()
                        if (keys is None or key in keys)
                        and (child_id is None or pending.get("child_thread_id") == child_id)
                        ]
            for key, pending in selected:
                elicitation = pending.get("source") == "elicitation"
                emit = self.elicitations.emit if elicitation else self.emit
                try:
                    consumed = self.requests.cancel(
                        key, lambda: emit("invalidated", {"id": key, "reason": reason}))
                    if send and consumed is not None:
                        replies.extend(self.requests.cancellation(pending))
                except (OSError, ValueError, RuntimeError) as exc:
                    failure = failure or exc
        if failure:
            raise failure
        return replies

    def deliver_host(self):
        """Validate and consume an answer that arrived before native input binding."""
        replies = []
        with self.runtime.lock:
            self.index.sync()
            for key, pending in self.pending.items():
                row = self.index.audits[key]
                if (key not in self.pending or pending["rpc_id"] is not None
                        or row["status"] != "answer_received"):
                    continue
                if not self.runtime.admitted():
                    return replies
                try:
                    scope = self.runtime.snapshot()
                    if pending.get("host_connection_id") and (
                            not self.requests.is_current()
                            or pending["host_connection_id"] != self.requests.connection_id
                            or pending["host_thread_id"] != self.runtime.snapshot().thread_id):
                        raise ValueError("Host input connection changed")
                    self.index.verify_evidence(row["evidence"])
                    if row["context"] != self.runtime.snapshot().context:
                        raise ValueError("Captured target changed")
                    self.runtime.validate_target()
                    self.index.verify_evidence(row["evidence"])
                    if not self.runtime.admitted() or self.runtime.snapshot() != scope:
                        raise ValueError("Task or target changed during validation")
                    content = host_content(row)
                    if content is None:
                        continue
                except TargetUnconfirmed as exc:
                    self.runtime.activity("waiting_user", str(exc))
                    continue
                except (ValueError, OSError, TypeError, KeyError, NeedsReconcile) as exc:
                    self.requests.cancel(key, lambda: self.emit("invalidated", {"id": key,
                                              "reason": "目标或证据已变化，答复未执行。"}))
                    self.runtime.invalidate(str(exc) or "Audit target or evidence invalid; no continuation")
                    return replies
                scope = self.runtime.snapshot()
                response = {q: {"answers": [v for v in (a["choice"], a["text"]) if v]}
                            for q, a in row["reply"]["answers"].items()}
                self.requests.resolve(key, lambda: self.emit(
                    "resumed", {"id": key, "reply_id": row["reply"]["reply_id"]}), host_response=response)
                if not self.runtime.admitted() or self.runtime.snapshot() != scope:
                    self.requests.take_host(key)
                    return replies
                replies.append({"role": "user", "content": content})
        return replies

    def deliver_local(self):
        """Return host answers as provider messages for the Python backend.

        The legacy provider loop uses a local message list instead of a native
        RPC response. Keep that path on the same evidence and target checks as
        the Codex backend.
        """
        return self.deliver_host()

    def finish(self):
        self.requests.finish()
