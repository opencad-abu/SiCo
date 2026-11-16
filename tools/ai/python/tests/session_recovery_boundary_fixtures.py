"""Small typed doubles for recovery inspection and ownership boundary tests."""

import threading
from contextlib import nullcontext
from types import SimpleNamespace

from sico.core.contracts import BoundContext
from sico.service.session_recovery import SessionRecovery

PROJECT = "a" * 32
SERVICE = "b" * 32
VERSION = "c" * 64
CURRENT = BoundContext("instance", "generation", "target", {"source": "simulated"})
OTHER = BoundContext("other", "generation", "target", {"source": "simulated"})


class Lock:
    def __init__(self):
        self.entered = 0

    def __enter__(self):
        self.entered += 1

    def __exit__(self, *_args):
        return False


class Owner:
    def __init__(self, project):
        self.project = project
        self.controllers = {}
        self._mutation = nullcontext()
        self.opened = []
        self.owned = []

    def open(self, key, descriptor, context, **kwargs):
        self.opened.append((key, descriptor, context, kwargs))
        return "controller"

    def own_resource(self, controller, callback):
        self.owned.append((controller, callback))


def facts(**overrides):
    value = dict(
        snapshot={"provider_config": None, "environment": {}, "bridge_identity": {},
                  "background": None},
        context=CURRENT,
        task={},
        version=VERSION,
        inputs=(),
        ends=(),
        deleted=False,
        operations=(),
        resources_released=False,
        archived=False,
        bridge_identity=None,
    )
    value.update(overrides)
    return SimpleNamespace(**value)


def recovery(project="project"):
    value = SessionRecovery.__new__(SessionRecovery)
    value.owner = Owner(project)
    value.descriptor = SimpleNamespace(project_id=PROJECT, service_id=SERVICE)
    value._unresolved = frozenset()
    value._scanning = False
    value._scan_lock = threading.Lock()
    value._retired = set()
    value._closed = threading.Event()
    return value


def params(operation_id="op", version=VERSION, credentials=None):
    return {"session_id": "old", "version": version,
            "credentials": {} if credentials is None else credentials}


def install_facts(monkeypatch, value):
    calls = []

    def read(_project, session_id, operation_id=None):
        calls.append((session_id, operation_id))
        return value

    monkeypatch.setattr("sico.service.session_recovery.read_facts", read)
    return calls


class Bridge:
    def __init__(self, context=CURRENT, descriptor=None):
        self.context_value = context
        self.descriptor = {} if descriptor is None else descriptor
        self.entered = 0
        self.exited = 0

    def __enter__(self):
        self.entered += 1
        return self

    def __exit__(self, *_args):
        self.exited += 1
        return False

    def context(self, _instance, _target):
        return self.context_value
