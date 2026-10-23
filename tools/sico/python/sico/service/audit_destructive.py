"""Blocking deletion reviews and their host-approved execution status."""

from __future__ import annotations

from ..core.contracts import NeedsReconcile
from ..core.audit_questions import questions
from .audit_decisions import confirmed_destructive, destructive_record


class DestructiveReviewMixin:
    """Deletion-review operations over the service-owned audit state."""

    def require_destructive_review(self, data, evidence_id, context):
        """Open one blocking review before any destructive tool may dispatch."""
        with self.runtime.lock:
            if not self.runtime.admitted():
                return
            kind = data.get("review_kind")
            review_digest = data.get("review_digest")
            for row in self.index.audits.values():
                if (row["task_id"] == self.runtime.snapshot().task_id
                        and row["context"] == context.record()
                        and row.get("destructive_kind") == kind
                        and row.get("destructive_digest") == review_digest
                        and row["status"] in {"pending", "answer_received", "answered"}):
                    return
            if self.pending:
                raise NeedsReconcile("Deletion review changed while another decision is pending")
            current = self.active_work()
            evidence = self.index.resolve("data", evidence_id)
            if (evidence["task_id"] != self.runtime.snapshot().task_id
                    or self.index.evidence_value(evidence_id).get("data") != data):
                raise ValueError("Deletion review requires its original tool evidence")
            record = destructive_record(
                data, evidence_id, evidence, context, self.runtime.snapshot().task_id,
                current, normalize_questions=questions,
            )
            self.emit("prepared", record)
            self.open_host(record, "destructive_review")

    def destructive_decision(self, kind, review_digest, context):
        """Return missing/pending/rejected/approved without dispatching a write."""
        if not isinstance(kind, str) or not isinstance(review_digest, str):
            return "missing"
        with self.runtime.lock:
            if not self.runtime.admitted():
                return "unavailable"
            self.index.sync()
            return confirmed_destructive(
                self.index.audits, self.runtime.snapshot().task_id, context,
                kind, review_digest, self.index.verify_evidence,
            )


__all__ = ["DestructiveReviewMixin"]
