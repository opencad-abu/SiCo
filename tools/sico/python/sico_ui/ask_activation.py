"""Activate one accepted Ask in the existing desktop, then acquire its control."""

from sico.service.diagnostics import diagnostics
from .ask_wake import AskWake


class AskActivation:
    def __init__(self, window):
        self.window = window
        self._disconnect = None
        self._generation = 0
        self._closed = False
        self.wake = AskWake(self)

    def cancel(self):
        self._generation += 1
        if self._disconnect is not None:
            detach, self._disconnect = self._disconnect, None
            detach()

    def close(self):
        self._closed = True
        self.wake.close()
        self.cancel()

    def activate(self, session_id, input_id=None, ask_sequence=None):
        if ask_sequence is not None:
            self.wake.enqueue(session_id, input_id, ask_sequence)
        else:
            self._activate(session_id)

    def _activate(self, session_id, *, finished=lambda: None):
        if self._closed:
            finished()
            return
        self.cancel()
        generation = self._generation
        page = self.window.page
        starting, completed = True, False
        slots = []

        def detach():
            for signal, slot in slots:
                try:
                    signal.disconnect(slot)
                except (TypeError, RuntimeError):
                    pass
            slots.clear()
            if self._disconnect is detach:
                self._disconnect = None
            finished()

        def invalidated():
            if not starting:
                self.cancel()

        def failed(message):
            nonlocal completed
            completed = True
            detach()
            if generation == self._generation:
                self._report(message)

        def activated(*_args):
            nonlocal completed
            if completed or generation != self._generation:
                return
            completed = True
            detach()
            if (page.session.session_id == session_id and not getattr(page, "opening", False)
                    and getattr(page, "reviewing", None) is None):
                self._ensure_control(page.session, generation)

        try:
            self.window.restore()
            for name, slot in (("activated", activated), ("failed", failed),
                               ("invalidated", invalidated)):
                signal = getattr(page, name, None)
                if signal is not None:
                    signal.connect(slot)
                    slots.append((signal, slot))
            self._disconnect = detach
            page.activate_session(session_id)
            starting = False
            if page.session.session_id == session_id and not getattr(page, "opening", False):
                activated()
            self.window.focus_input()
        except Exception as exc:
            failed(str(exc))

    def _ensure_control(self, session, generation):
        page = self.window.page
        activation = getattr(page, "activation", None)

        def failed(exc):
            if (not self._closed and generation == self._generation and page.session == session
                    and getattr(page, "activation", None) == activation):
                self._report(str(exc))
                return None
            return False

        try:
            future = self.window.api.control.ensure(session)
            commands = getattr(self.window, "commands", None)
            if commands is None:
                raise RuntimeError("Attach control receipt channel is unavailable")
            commands.watch(future, failure=failed)
        except Exception as exc:
            failed(exc)

    def _report(self, message):
        diagnostics.submit(__name__, "Attach failed: " + str(message))
        notices = getattr(self.window, "session_notices", None)
        if notices is not None:
            notices.set("attach", "会话未取得控制", str(message), level="error")
        elif hasattr(self.window, "info_app"):
            self.window.info_app.warning("会话未取得控制", str(message))
