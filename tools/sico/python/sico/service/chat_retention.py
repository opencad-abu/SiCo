"""Bounded display retention; durable history is read through backend views."""


class TranscriptHistory:
    def trim_history(self):
        if len(self.messages) > self.RETAIN_MESSAGES:
            del self.messages[:-self.RETAIN_MESSAGES]
        # References must not keep an evicted bubble (and its payload) alive.
        if self._next_message_id % 32:
            return
        visible = {id(message) for message in self.messages}
        for mapping in (self.native_records, self.supplements):
            for key, message in list(mapping.items()):
                if id(message) not in visible:
                    del mapping[key]
        for key, row in list(self.audits.items()):
            if (id(row.get("message")) not in visible
                    and row.get("status") not in {"prepared", "pending", "answer_received",
                                                  "dispatching", "unconfirmed"}):
                del self.audits[key]
        if len(self.pdk_notices) > self.RETAIN_MESSAGES:
            self.pdk_notices.clear()
