"""Small typed doubles for project-resource settings boundary tests."""

from types import SimpleNamespace

from sico.agent_resources import snapshot as resource_snapshot
from sico.service.frontend_session import SessionToken
from sico.service.service_session_dto import SessionAddress

PROJECT = "a" * 32
SERVICE = "b" * 32
RUNTIME = "c" * 32
SESSION = "old"
VERSION = "d" * 64


class Owner:
    def __init__(self, project):
        self.project = project
        self.defaults = {"manifest": {"version": 1}, "source": "project",
                         "revision": None, "editable": True}
        self.default_calls = 0
        self.dependencies = {"environment": {}}
        self.environment = {}

    def resource_defaults(self):
        self.default_calls += 1
        return self.defaults

    def provider_for(self, session_id):
        return {"session": session_id}

    def dependencies_for(self, _session_id):
        return SimpleNamespace(**self.dependencies)

    def resource_settings(self):
        return SimpleNamespace(environment=self.environment)


class Worker:
    def __init__(self, value=None, error=None):
        self.value, self.error, self.calls = value, error, []

    def command(self, session, method):
        self.calls.append((session, method))
        if self.error:
            raise self.error
        return SimpleNamespace(result=lambda: self.value)


class Hub:
    def __init__(self, project):
        self.descriptor = SimpleNamespace(project_id=PROJECT, service_id=SERVICE)
        self.owner = Owner(project)
        self.workspace = SimpleNamespace(root=project, worker=Worker())
        self.controlled = []

    def controller(self, value):
        self.controlled.append(value)
        return "controlled"


def address(runtime_id=None):
    return SessionAddress(PROJECT, SERVICE, SessionToken(SESSION, runtime_id))


def reader(events):
    class Reader:
        directory = "/reader"

        def stream_events(self):
            return iter(events)

    return lambda *_args: Reader()


def selected_snapshot():
    return resource_snapshot.create(
        {"path": "/resources.json", "source": "project", "revision": None,
         "manifest": {"version": 1}},
        [],
        origin="defaults",
    )


def error_type(module, name):
    return getattr(module, name, None) or {
        "BlockingIOError": BlockingIOError,
        "ValueError": ValueError,
        "OSError": OSError,
        "RuntimeError": RuntimeError,
    }[name]
