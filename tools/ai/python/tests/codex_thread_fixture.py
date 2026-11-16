"""Bind synthetic native receipts through the same transition as runtime RPCs."""


def bind_thread(backend, thread_id="thread", *, history_mode="legacy"):
    previous = backend.thread_id
    action = "start" if previous is None else "resume" if previous == thread_id else "fork"
    backend._bind_thread(action, {"id": thread_id, "historyMode": history_mode},
                         expected=previous)
