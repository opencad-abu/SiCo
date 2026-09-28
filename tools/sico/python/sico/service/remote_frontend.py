"""GUI facade over project service connections and immutable local publications."""

import threading
import uuid
from pathlib import Path

from sicostate import project_directory

from ..transport.framing import ProtocolError
from .frontend_assets import input_value
from .frontend_session import FrontendSession
from .frontend_transport import FrontendLane
from .remote_catalog import RemoteCatalog
from .remote_control import RemoteControl
from .remote_queries import RemoteSource
from .remote_recovery import RemoteRecovery
from .remote_replay import RemoteFeed, publish_replay
from .remote_sessions import RemoteSessions
from .service_client import ServiceClient
from .service_messages import ServiceRequest
from .service_protocol import exact_fields
from .service_session_dto import SessionAddress


class RemoteFrontend:
    def __init__(self, descriptor, project, initial_id):
        self.descriptor = descriptor
        self.initial_id = initial_id
        self.launch_directory = Path(project)
        self.layout_path = project_directory(self.launch_directory, "ai/agent") / "workspace.ini"
        self._closed = threading.Event()
        self._sessions = RemoteSessions(descriptor)
        self._reconnect = None
        self._force_stop = None
        self.catalog = RemoteCatalog(self)
        client = uuid.uuid4().hex
        self._attach = FrontendLane(descriptor, client, "frontend.attach")
        self._events = FrontendLane(descriptor, client, "frontend.events")
        self._queries = FrontendLane(descriptor, client, "frontend.query")
        self._commands = FrontendLane(descriptor, client, "frontend.command")
        self._admin = ServiceClient(descriptor, client_id=client)
        self.control = RemoteControl(self)
        self.recovery = RemoteRecovery(self)
        from .remote_platforms import RemotePlatforms

        self.platforms = RemotePlatforms(self)

    @property
    def detached(self):
        return self._closed.is_set()

    @property
    def connection_state(self):
        if self.detached:
            return "detached"
        states = [lane.state for lane in
                  (self._attach, self._events, self._queries, self._commands)]
        return next((state for state in ("disconnected", "reconnecting", "connecting")
                     if state in states), "connected")

    def open_home(self):
        def publish(row):
            exact_fields(row, {"platforms"})
            if row["platforms"] != ["CDNS-IC"]:
                raise ProtocolError("Unsupported platform service")
            return row
        return self._queries.request("home", {}, publish)

    def open_initial(self, operation, params, *, observe_creation=None):
        def publish(row):
            exact_fields(row, {"session", "created"})
            if type(row["created"]) is not bool:
                raise ProtocolError("Invalid session creation observation")
            if observe_creation is not None:
                observe_creation(row["created"])
            row = row["session"]
            address = SessionAddress.from_record(row["address"])
            if (address.session.session_id != self.initial_id
                    and operation not in {"open", "platform_open"}):
                raise ProtocolError("Initial session changed")
            token = self._sessions.publish(row)
            self.initial_id = token.session_id
            return FrontendSession(self, token)
        return self._attach.request(operation, params, publish)

    def owns(self, token):
        if token is None:
            return False
        current = self._sessions.get(token.session_id)
        return not self.detached and current is not None and current.address.session == token

    def handle(self, token):
        if token is None or not self.owns(token):
            raise ValueError("会话运行实例已变化，请重新连接")
        return token

    def session(self, session_id):
        row = self._sessions.get(session_id)
        return row.address.session if row else None

    def view(self, token):
        return self._sessions.get(self.handle(token).session_id).view

    def connection(self, token):
        context = self.view(token).context
        return context.instance_id, context.generation

    def is_closing(self, token):
        return not self.owns(token) or self._sessions.get(token.session_id).closing

    def activity(self, token):
        return self._sessions.get(self.handle(token).session_id).activity

    def display_name(self, token):
        return self._sessions.get(self.handle(token).session_id).display_name

    def opened_sessions(self):
        return self._sessions.tokens()

    def open_session(self, session_id):
        def publish(row):
            exact_fields(row, {"session", "created"})
            if row["created"] is not False:
                raise ProtocolError("Attachment cannot create a session")
            row = row["session"]
            if SessionAddress.from_record(row["address"]).session.session_id != session_id:
                raise ProtocolError("Attached session changed")
            return self._sessions.publish(row)
        return self._attach.request("attach", dict(session_id=session_id), publish)

    def create_session(self, context):
        def publish(row):
            exact_fields(row, {"session", "control"})
            token = self._sessions.publish(row["session"])
            self.control.created(token, row["control"])
            return token
        candidates = [token for token in self.opened_sessions()
                      if token.runtime_id is not None and self.view(token).context == context]
        source = next((token for token in reversed(candidates) if self.can_control(token)), None)
        if source is None:
            # A live source also supports an explicitly selected new target.
            initial = self.session(self.initial_id)
            if initial is not None and self.can_control(initial):
                source = initial
        if source is None:
            source = next((token for token in reversed(candidates) if self.is_closing(token)), None)
        if source is None:
            raise ValueError("没有可用的会话创建来源，请重新打开项目")
        return self.command(source, "create", context, _publish=publish)

    def command(self, token, name, *args, _publish=lambda value: value, **kwargs):
        self.handle(token)
        if token.runtime_id is None:
            raise ValueError("历史只读；请明确恢复后获取控制权")
        address = self._sessions.get(token.session_id).address
        return self._commands.request(name, lambda: dict(address=address.record(), args=args,
                                                        kwargs=kwargs), _publish,
                                     control=self.control.proof(token))

    def end_session(self, token, operation_id, *, observe=False):
        return self.control.end(token, operation_id, observe=observe)

    def address(self, token):
        return self._sessions.get(self.handle(token).session_id).address

    def can_control(self, token):
        return self.control.writable(token)

    def rename_session(self, session_id, name):
        token = self.session(session_id)
        if token is not None and token.runtime_id is not None and not self.is_closing(token):
            return self.command(token, "rename", name)
        return self._record_command(session_id, "rename", name)

    def review_deletion(self, session_id):
        from .recovery_contract import validate_review

        return self._queries.request("deletion_review", dict(session_id=session_id),
                                     lambda row: validate_review(row, session_id))

    def delete_session(self, session_id, *, review_version=None):
        return self._record_command(session_id, "delete", None, review_version=review_version)

    def _record_command(self, session_id, action, name, *, review_version=None):
        token = self.session(session_id)
        params = dict(session_id=session_id, runtime_id=token.runtime_id if token else None,
                      action=action, name=name)
        if review_version is not None:
            params["review_version"] = review_version
        return self._commands.request("record_command", params)

    def prepare_replay(self, token, activation, messages):
        self.handle(token)
        if token.runtime_id is None:
            return self._prepare(token.session_id, None, activation, messages, True)
        resume, self._reconnect = self._reconnect, None
        if resume is not None and resume.address.session != token:
            resume = None
        return self._prepare(token.session_id, token.runtime_id, activation, messages, False,
                             resume=resume)

    def reconnect_session(self, token, feed):
        """Rebuild the displayed window from its captured durable start on a new connection."""
        self.handle(token)
        if (not isinstance(feed, RemoteFeed) or feed._api is not self
                or feed._wire_cursor.address.session != token or feed._wire_cursor.readonly):
            raise ValueError("重连回放属于其他页面或运行实例")
        self._reconnect = feed._wire_cursor

    def _prepare(self, session_id, runtime, activation, messages, readonly, *, resume=None):
        params = dict(session_id=session_id, runtime_id=runtime, activation=activation,
                      messages=messages, readonly=readonly,
                      resume=None if resume is None else resume.record())
        return self._events.request("prepare", params,
            lambda row: publish_replay(self, params, row),
            discard=lambda prepared: prepared.stream.close())

    def preview_session(self, session_id, activation=0, messages=0):
        token = self.session(session_id)
        runtime = token.runtime_id if token and not self.is_closing(token) else None
        return self._prepare(session_id, runtime,
                             activation, messages, True)

    def replay_matches(self, prepared, token, activation):
        return (self.owns(token) and prepared.stream.identity[1] == token.runtime_id
                and self.preview_matches(prepared, token.session_id, activation))

    def preview_matches(self, prepared, session_id, activation):
        stream = prepared.stream
        if not isinstance(stream, RemoteFeed) or stream._api is not self:
            return False
        try:
            stream.validate_current()
            return stream.identity[0] == session_id and stream.activation == activation
        except ValueError:
            return False

    def source(self, token, events=None, scope=None):
        self.handle(token)
        return RemoteSource(self, token, scope)

    def preview_source(self, prepared):
        return RemoteSource(self, scope=prepared.stream)

    def event_batch(self, feed):
        return feed.batch()

    def event_detail(self, feed, key, offset=0):
        return RemoteSource(self, scope=feed)._request("event_detail",
                                                     lambda: dict(key=key, offset=offset))

    def input_detail(self, feed, sequence, index=None):
        return RemoteSource(self, scope=feed)._request("input_detail",
            lambda: dict(sequence=sequence, index=index), input_value)

    def chat_page(self, feed, *, before=0, after=0, end):
        return RemoteSource(self, scope=feed)._request("chat_page",
            lambda: dict(before=before, after=after, end=end))

    def catalog_snapshot(self, version=None):
        return self.catalog.snapshot(version)

    def released_targets(self, token):
        return ()  # Host bridge owns release notices; a reader must not consume another's queue.

    def interrupt(self, token):
        pass  # A failed observer stream cannot cancel a service task.

    def target_source(self, token, **kwargs):
        return None  # Target mutation requires AS-05 control authority.

    def background_request(self, token, method, *args, **kwargs):
        if method in {'submit_background', 'cancel_background'}:
            return self.command(token, method, *args, **kwargs)
        return self._session_query(token, "background", (method, *args), kwargs)

    def tool_result(self, token, result, *, context=False):
        return self._session_query(token, "tool_result", (result,), dict(context=context))

    def _session_query(self, token, operation, args, kwargs):
        self.handle(token)
        address = self._sessions.get(token.session_id).address
        def publish(value):
            self.handle(token)
            return value
        return self._queries.request(operation, lambda: dict(address=address.record(),
                                    args=args, kwargs=kwargs), publish)

    def accept_quick(self, token, message):
        raise ValueError("旧桌面提交入口已关闭，请从宿主 Ask 提交；文字已保留")

    def stop_service(self):
        return self._admin.request(ServiceRequest("service.stop"))

    def force_stop_service(self):
        from .service_force_stop import ForceServiceStop

        if self.detached:
            raise RuntimeError("窗口已与项目服务分离")
        if self._force_stop is None or self._force_stop.ready.done():
            self._force_stop = ForceServiceStop(self.launch_directory, self.descriptor)
        return self._force_stop.ready

    def service_status(self):
        return self._admin.request(ServiceRequest("service.status"))

    def query_input(self, token, operation_id):
        self.handle(token)
        address = self._sessions.get(token.session_id).address
        return self._admin.request(ServiceRequest("session.input", operation_id,
                                                  dict(address=address.record())))

    def close_desktop(self):
        self._closed.set()
        for lane in (self._attach, self._events, self._queries, self._commands):
            lane.close()
        self._admin.close()

    @property
    def desktop_busy(self):
        return False

    def wait_desktop(self, timeout=0):
        if timeout:
            raise ValueError("Frontend shutdown polling must be nonblocking")
        return all(not lane.thread.is_alive()
                   for lane in (self._attach, self._events, self._queries, self._commands))
