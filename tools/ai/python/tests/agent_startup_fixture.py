"""Capture test-owned startup resources without exposing them in ready results."""

from types import SimpleNamespace
from weakref import WeakKeyDictionary

_resources = WeakKeyDictionary()


def capture_startup(monkeypatch):
    from agent_startup_harness import DesktopStartup

    original = DesktopStartup._sessions

    def sessions(self, resources, provider, broker, journal, context, **kwargs):
        controller, desktop = original(self, resources, provider, broker, journal, context,
                                       **kwargs)
        _resources[self] = SimpleNamespace(controller=controller, sessions=desktop, broker=broker)
        return controller, desktop

    monkeypatch.setattr(DesktopStartup, "_sessions", sessions)


def startup_backend(startup):
    startup.ready.result(5)
    return _resources[startup]


def window_backend(window):
    sessions = getattr(window, "sessions", None)
    if sessions is not None:
        return sessions
    for resources in tuple(_resources.values()):
        token = resources.sessions.worker._tokens.session(window.page.session.session_id)
        if token == window.page.session:
            return resources.sessions
    return None
