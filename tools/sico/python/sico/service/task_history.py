"""Bounded task summaries; interrupted inputs are evidence, never a replay queue."""

from __future__ import annotations

import os
from collections import OrderedDict

from ..core.contracts import BoundContext, json_copy
from ..storage.journal import open_private
from ..transport.framing import strict_json


class TaskHistory:
    def __init__(self, journal, inbox):
        self.rows = OrderedDict()
        self.task_inputs = {}
        for event in journal.events():
            self.event(event)
        # Old inbox files may outlive a crash between execution and status persistence.
        for path in sorted(inbox.directory.glob("*.json"), key=lambda p: p.stat().st_mtime)[-200:]:
            with os.fdopen(open_private(path, os.O_RDONLY), "rb") as stream:
                record = strict_json(stream.read(262145))
            message = record["message"]
            if path.stem != record["id"] or message["id"] != record["id"]:
                raise ValueError("Invalid saved input identity")
            BoundContext.from_record(message["context"])
            if not isinstance(message["text"], str):
                raise ValueError("Invalid saved input text")
            if "attachments" in message and not isinstance(message["attachments"], list):
                raise ValueError("Invalid saved input attachments")
            existing = self.rows.get(record["id"])
            if existing:
                existing["origin"] = record.get("origin", "quick")
            else:
                self.record(record)
        for row in self.rows.values():
            row["status"] = {
                "queued": "not_started",
                "executing": "needs_reconcile",
                "waiting_user": "needs_reconcile",
            }.get(
                row["status"], row["status"]
            )

    def _put(self, key, row):
        self.rows[key] = row
        if len(self.rows) > 200:
            removed, _ = self.rows.popitem(last=False)
            self.task_inputs = {k: v for k, v in self.task_inputs.items() if v != removed}

    def record(self, record):
        message = record["message"]
        self._put(
            record["id"],
            {
                "id": record["id"],
                "task_id": record.get("task_id", ""),
                "text": message["text"],
                "context": message["context"],
                "attachments": json_copy(message.get("attachments", [])),
                "inputs": json_copy(message.get("inputs", [])),
                "turn_options": json_copy(record.get(
                    "turn_options", message.get("turn_options", {}))),
                "status": record["status"],
                "origin": record.get("origin", "quick"),
                "diagnostic": "",
            },
        )

    def event(self, event):
        kind, payload, task_id = event["kind"], event["payload"], event.get("task_id", "")
        if kind == "task.started":
            key = payload.get("input_id") or task_id
            old = self.rows.get(key, {})
            self._put(
                key,
                {
                    "id": key,
                    "task_id": task_id,
                    "text": payload["text"],
                    "context": payload["context"],
                    "attachments": json_copy(payload.get("attachments", [])),
                    "inputs": json_copy(payload.get("inputs", [])),
                    "turn_options": json_copy(payload.get("turn_options", {})),
                    "status": "executing",
                    "origin": payload.get("origin", old.get("origin", "chat")),
                    "diagnostic": "",
                },
            )
            self.task_inputs[task_id] = key
        elif kind.startswith("task."):
            row = self.rows.get(self.task_inputs.get(task_id))
            if row:
                row.update(status=kind[5:], diagnostic=payload.get("message", ""))
        elif kind in {"workbench.audit.opened", "workbench.audit.resumed"}:
            row = self.rows.get(self.task_inputs.get(task_id))
            if row:
                row["status"] = "waiting_user" if kind.endswith("opened") else "executing"

    def snapshot(self):
        return json_copy(list(self.rows.values()))
