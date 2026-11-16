"""Test-only in-process frontend_port harness for historical FB regression."""

from dataclasses import dataclass

from agent_query_harness import FrontendQueries
from sico.service.frontend_receipt import project_receipt
from sico.service.frontend_session import SessionToken


@dataclass(frozen=True)
class QuickReceipt:
    session: object
    created: bool
    accepted: bool


class FrontendPort:
    def __init__(self, worker):
        self.__worker = worker
        self.__tokens = worker._tokens
        self.__queries = FrontendQueries(worker)

    def handle(self, token):
        self.__tokens.check(token)
        self.__tokens.resolve(token)
        return token

    def owns(self, token):
        return self.__tokens.current(token)

    def session(self, session_id):
        return self.__tokens.session(session_id)

    def view(self, token):
        return self.__tokens.resolve(token).frontend_view

    def connection(self, token):
        context = self.__tokens.resolve(token).current
        return context.instance_id, context.generation

    def is_closing(self, token):
        if not self.owns(token):
            return True
        owner = self.__tokens.resolve(token)
        return owner.closing or owner._shutdown.is_set()

    def activity(self, token):
        return self.__tokens.resolve(token).activity()

    def opened_sessions(self):
        return tuple(self.handle(SessionToken(owner.session_id, owner.runtime_id))
                     for owner in tuple(self.__worker.sessions.controllers.values()))

    def display_name(self, token):
        return self.__tokens.resolve(token).display_name

    @property
    def initial_id(self):
        return self.__worker.sessions.initial_id

    @property
    def layout_path(self):
        return self.__worker.sessions.root / "workspace.ini"

    @property
    def launch_directory(self):
        return self.__worker.sessions.root.parents[2]

    @property
    def desktop_busy(self):
        return self.__worker.sessions.busy

    def close_desktop(self):
        self.__worker.sessions.close()

    def wait_desktop(self, timeout=0):
        if timeout != 0:
            raise ValueError("Frontend shutdown polling must be nonblocking")
        return self.__worker.sessions.wait(0)

    def catalog_snapshot(self, version=None):
        return self.__worker.sessions.catalog.snapshot(version)

    def open_session(self, session_id):
        return project_receipt(self.__worker.open_session(session_id),
                               lambda owner: self.handle(SessionToken(owner.session_id, owner.runtime_id)))

    def create_session(self, context):
        return project_receipt(self.__worker.create_session(context),
                               lambda owner: self.handle(SessionToken(owner.session_id, owner.runtime_id)))

    def command(self, token, name, *args, **kwargs):
        self.handle(token)
        if name == "attach_targets":
            if args or kwargs:
                raise TypeError("Target registration uses the desktop's broker")
            return self.__worker.attach_targets(token)
        return self.__worker.command(token, name, *args, **kwargs)

    def accept_quick(self, token, message):
        self.handle(token)

        def publish(receipt):
            return QuickReceipt(SessionToken(receipt.controller.session_id,
                                             receipt.controller.runtime_id),
                                receipt.created, receipt.accepted)

        return project_receipt(self.__worker.accept_quick(token, message), publish)

    def rename_session(self, session_id, name):
        return self.__worker.rename_session(session_id, name)

    def delete_session(self, session_id):
        return self.__worker.delete_session(session_id)

    def prepare_replay(self, token, activation, messages):
        self.handle(token)
        return self.__queries.replay(token, activation, messages)

    def preview_session(self, session_id, activation=0, messages=0):
        return self.__queries.preview(session_id, activation, messages)

    def replay_matches(self, prepared, token, activation):
        return self.owns(token) and self.__queries.matches(
            prepared, token.session_id, activation, token)

    def preview_matches(self, prepared, session_id, activation):
        return self.__queries.matches(prepared, session_id, activation)

    def source(self, token, events=None, scope=None):
        self.handle(token)
        if events is not None:
            raise TypeError("Frontend sources do not accept raw events")
        return self.__queries.source(token, scope)

    def preview_source(self, prepared):
        return self.__queries.preview_source(prepared)

    def event_batch(self, feed):
        return self.__queries.batch(feed)

    def event_detail(self, feed, key, offset=0):
        return self.__queries.detail(feed, key, offset)

    def chat_page(self, feed, *, before=0, after=0, end):
        return self.__queries.chat_page(feed, before, after, end)

    def input_detail(self, feed, sequence, index=None):
        return self.__queries.input_detail(feed, sequence, index)

    def tool_result(self, token, result, *, context=False):
        self.handle(token)
        return self.__worker.session_query(token, "tool_result", result, context=context)

    def target_source(self, token, *, task_id="", audit_id=""):
        owner = self.__tokens.resolve(token)
        sessions = self.__worker.sessions
        if sessions.broker is None:
            return None
        return sessions.targets.bind(
            sessions.broker, owner, task_id=task_id, audit_id=audit_id,
            context=self.view(token).context, validate=lambda: self.__tokens.resolve(token),
        )

    def background_request(self, token, method, *args, **kwargs):
        self.handle(token)
        return self.__worker.session_query(token, "background", method, *args, **kwargs)

    def released_targets(self, token):
        return self.__tokens.resolve(token).bindings.released_targets()

    def interrupt(self, token):
        if self.owns(token):
            self.__tokens.resolve(token).interrupt()


def frontend(worker, controller=None):
    from sico.service.frontend_session import FrontendSession

    token = (SessionToken(controller.session_id, controller.runtime_id) if controller is not None
             else worker._tokens.session(worker.sessions.initial_id))
    worker._tokens.resolve(token)
    return FrontendSession(FrontendPort(worker), token)
