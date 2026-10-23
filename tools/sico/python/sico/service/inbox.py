"""Durable per-session input queue; accepted requests never retarget an active task."""

from __future__ import annotations

import json
import os
import time
from collections import deque

from ..core.contracts import BoundContext, identifier, json_copy
from ..core.ask_queue import PENDING_LIMIT, QueueFull, ask_sequence, pending_order
from ..storage.inbox_records import read_input
from ..storage.journal import open_private, private_dir, sync_directory
from ..transport.targets import submission
from .ask_diagnostics import queue_event


class SessionInbox:
    def __init__(self, journal, initial):
        self.journal = journal
        self.directory = journal.directory / "inbox"
        private_dir(self.directory)
        self.initial = initial
        self.pending = deque()
        self.active = None
        self.sealed = False
        # Prior accepted/started files are evidence, never automatically replayed.
        sync_directory(journal.directory)

    def _save(self, record):
        target = self.directory / (record["id"] + ".json")
        temporary = target.with_suffix(".part")
        fd = open_private(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC)
        with os.fdopen(fd, "w") as stream:
            json.dump(record, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        sync_directory(self.directory)

    def accept(self, message, *, turn_options=None, service_address=None,
               attachment_text=None, submission_digest=None):
        context = submission(message)
        # ServiceInputs verifies durable identity before creating attachment assets.
        message = dict(message)
        if attachment_text is not None:
            message["attachments"] = [self.journal.text_attachment(attachment_text)]
        if submission_digest is not None:
            message["submission_digest"] = submission_digest
        return self.enqueue(message, context, "quick", turn_options=turn_options,
                            service_address=service_address)

    def enqueue(self, message, context, origin="chat", *, turn_options=None, service_address=None):
        identifier(message["id"])
        if (context.instance_id, context.generation) != (
            self.initial.instance_id,
            self.initial.generation,
        ):
            raise ValueError("Input belongs to another Virtuoso instance")
        old = read_input(self.directory, message["id"], confirm=True)
        if old is not None:
            if old["message"] != message or old.get("service_address") != service_address:
                raise ValueError("Input ID conflicts with an earlier request")
            return False
        sequence = message.get("ask_sequence")
        if sequence is not None:
            ask_sequence(sequence)
            for path in self.directory.glob("*.json"):
                prior = read_input(self.directory, path.stem)
                if prior["message"].get("ask_sequence") == sequence:
                    raise ValueError("Ask sequence belongs to an earlier input ID")
        if len(self.pending) >= PENDING_LIMIT:
            raise QueueFull("The session already has 16 waiting requests")
        record = {
            "id": message["id"],
            "status": "queued",
            "message": json_copy(message),
            "origin": origin,
            "accepted_at": time.time(),
        }
        if turn_options:
            # Host settings do not change the identity of a retried quick-input envelope.
            record["turn_options"] = json_copy(turn_options)
        if service_address is not None:
            record["service_address"] = json_copy(service_address)
        self._save(record)
        self.pending.append(record)
        self.pending = deque(sorted(self.pending, key=pending_order))
        for status in ("accepted", "queued"):
            queue_event(status, message["id"], sequence,
                        session_id=self.journal.session_id, pending=len(self.pending))
        return True

    def take(self):
        if self.active or not self.pending:
            return None
        self.active = self.pending.popleft()
        self.active["status"] = "executing"
        self.active["started_at"] = time.time()
        self._save(self.active)
        message = self.active["message"]
        queue_event("started", message["id"], message.get("ask_sequence"),
                    session_id=self.journal.session_id, pending=len(self.pending))
        return message["text"], BoundContext.from_record(message["context"]), message["id"]

    def finish(self, task):
        if self.active:
            self.active.update(status=task.get("status", "needs_reconcile"), task_id=task.get("id"))
            self._save(self.active)
            self.active = None

    def close(self):
        for record in self.pending:
            record["status"] = "not_started"
            self._save(record)
        self.pending.clear()
        self.sealed = True

    def recover(self, input_id):
        """Explicitly stage one durable queued record; never runs it implicitly."""
        record = read_input(self.directory, input_id, confirm=True)
        if record is None:
            raise ValueError("没有可恢复的持久输入")
        if record["status"] != "queued":
            raise ValueError("该输入已执行或需要先核对原操作")
        if any(item["id"] == input_id for item in self.pending):
            return False
        if self.active is not None or len(self.pending) >= PENDING_LIMIT:
            raise ValueError("当前会话队列不可恢复")
        self.pending.append(record)
        self.pending = deque(sorted(self.pending, key=pending_order))
        return True
