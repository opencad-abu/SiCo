"""Owner of the live projection and its separate read-only replay transcript."""

from sico.core.contracts import BoundContext

from .transcript import Transcript


class SessionPresentation:
    def __init__(self, context):
        self.displayed_context = context
        self.submission_context = context
        self.reset()

    def reset(self):
        self.state = None
        self.transcript = Transcript()
        self.replay = None
        self.last_sequence = 0
        self.source_detached = False
        self._contexts = None

    def update(self, state, contexts=None):
        if contexts is not None:
            self._contexts = state, contexts
        elif self._contexts is not None and self._contexts[0] is state:
            contexts = self._contexts[1]
        if contexts is None:
            contexts = (BoundContext.from_record(state["context"]),
                        BoundContext.from_record(state.get("default_context", state["context"])))
        changed = contexts[0] != self.displayed_context
        self.state = state
        self.displayed_context = contexts[0]
        self.submission_context = contexts[1] if state.get("binding_state") else contexts[0]
        return changed

    def receive(self, event):
        sequence = event.get("sequence")
        if sequence is not None:
            if sequence <= self.last_sequence:
                return False
            self.last_sequence = sequence
        self.transcript.receive(event)
        if event["kind"] == "task.started":
            self.source_detached = False
        elif event["kind"] == "context.detached":
            self.source_detached = True
        return True

    def preview(self):
        self.replay = Transcript(read_only=True)
        return self.replay

    def cancel_render(self):
        self.transcript.cancel_render()
        if self.replay is not None:
            self.replay.cancel_render()

    def current_transcript(self, reviewing):
        return self.transcript if reviewing is None else self.replay
