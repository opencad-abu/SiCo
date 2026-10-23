"""Bind user messages to native turns and fold a verified history-only revert."""

from __future__ import annotations

from sico.core.cad_ref import target_from_snapshot


class TranscriptTurns:
    """Projection-owned turn identity; storage and Qt adapters stay outside."""

    def _init_turns(self):
        self._task_id = None
        self._task_message = None
        self._task_target = None
        self._turn_messages = {}

    def _start_task_message(self, event, message):
        """Remember the visible prompt that may receive a native turn later."""
        payload = event.get("payload") or {}
        context = payload.get("context") or {}
        self._task_target = target_from_snapshot(context.get("snapshot") or {})
        self._task_id = event.get("task_id")
        origin = payload.get("origin")
        self._task_message = message if origin != "thread_operation" else None
        if message is not None:
            message["task_id"] = event.get("task_id", "")
            message["input_id"] = (event.get("payload") or {}).get("input_id", "")

    def _bind_turn(self, event):
        """Attach the task's first native turn to its visible user message."""
        payload = event.get("payload") or {}
        native = payload.get("native")
        if not isinstance(native, dict):
            return
        message = self._task_message
        turn_id, thread_id = native.get("turn_id"), native.get("thread_id")
        if (
            message is None
            or message.get("role") != "user"
            or event.get("task_id") != self._task_id
            or not isinstance(turn_id, str)
            or not turn_id
            or not isinstance(thread_id, str)
            or not thread_id
        ):
            return
        if message.get("turn_id") == turn_id and message.get("thread_id") == thread_id:
            return
        message["turn_id"] = turn_id
        message["thread_id"] = thread_id
        self._turn_messages[(thread_id, turn_id)] = message["message_id"]
        self._changed(message)

    def _mark_rewindable(self, task_id):
        """A completed task boundary is safe to expose; execution is not."""
        message = self._task_message
        if (
            message is None
            or task_id != self._task_id
            or not message.get("turn_id")
            or message.get("rewindable")
        ):
            return
        message["rewindable"] = True
        self._changed(message)

    def _apply_revert(self, event):
        """Fold every displayed message at or after the verified native boundary."""
        payload = event.get("payload") or {}
        before_turn_id = payload.get("before_turn_id")
        thread_id = payload.get("thread_id")
        boundary = None
        if isinstance(before_turn_id, str) and isinstance(thread_id, str):
            boundary = self._turn_messages.get((thread_id, before_turn_id))
            if boundary is None:
                boundary = next(
                    (
                        message["message_id"]
                        for message in self.messages
                        if message.get("turn_id") == before_turn_id
                        and message.get("thread_id") == thread_id
                    ),
                    None,
                )
        if boundary is None:
            # The target predates this bounded projection. Every retained message
            # belongs to the removed suffix; the journal remains the source for
            # anything earlier.
            removed = list(self.messages)
            self.messages = []
        else:
            index = next(
                (
                    index
                    for index, message in enumerate(self.messages)
                    if message["message_id"] >= boundary
                ),
                len(self.messages),
            )
            removed = self.messages[index:]
            self.messages = self.messages[:index]
        removed_ids = {id(message) for message in removed}
        for message in removed:
            self.removed(message)
        if self.streaming is not None and id(self.streaming) in removed_ids:
            self.streaming = None
        if self.activity is not None and id(self.activity) in removed_ids:
            self.activity = None
        if self.tools is not None and id(self.tools) in removed_ids:
            self.tools = None
        if self._task_message is not None and id(self._task_message) in removed_ids:
            self._task_message = None
        self.native_records = {
            key: message
            for key, message in self.native_records.items()
            if id(message) not in removed_ids
        }
        self.supplements = {
            key: message
            for key, message in self.supplements.items()
            if id(message) not in removed_ids
        }
        self.audits = {
            key: row
            for key, row in self.audits.items()
            if id(row.get("message")) not in removed_ids
        }
        self.calls.clear()
        if boundary is not None:
            self._turn_messages = {
                key: message_id
                for key, message_id in self._turn_messages.items()
                if message_id < boundary
            }
            self.reverted(boundary)
        self.dirty = True

    def reverted(self, before_message_id):
        """Observe a suffix removal; storage adapters override this hook."""
