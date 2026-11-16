"""Inject thread assertions at real blocking boundaries without importing Qt in services."""

import functools
import threading


class ThreadGuard:
    def __init__(self, monkeypatch):
        self.owner = threading.get_ident()
        self.monkeypatch = monkeypatch
        self.calls = []

    def protect(self, target, name):
        original = getattr(target, name)

        @functools.wraps(original)
        def checked(*args, **kwargs):
            current = threading.get_ident()
            assert current != self.owner, "Blocking boundary on GUI thread: " + name
            self.calls.append((name, current))
            return original(*args, **kwargs)

        self.monkeypatch.setattr(target, name, checked)
        return checked
