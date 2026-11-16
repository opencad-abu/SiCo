"""Test-only in-process sessions harness for historical FB regression."""

from __future__ import annotations

import time
import uuid
from contextlib import ExitStack, contextmanager

from sico.core.contracts import BoundContext, RunState, identifier
from sico.service.backend import create_backend
from sico.service.background_service import BackgroundService
from sico.service.catalog import CatalogService
from sico.service.controller import SessionController
from sico.service.event_service import EventService
from sico.service.metadata import MetadataService
from sico.service.quick_routing import QuickInputRouting
from sico.service.targets import TargetService
from sico.service.workbench_data import WorkbenchService
from sico.service.worker import WorkerApi
from sico.storage.history import SessionReader
from sico.storage.journal import SessionJournal


class DesktopSessions:
    def __init__(self, controller, *, tools_factory=None, resolve_context=None, broker=None,
                 background=None):
        self.initial_id = controller.session_id
        self.root = controller.journal.root
        self.provider = controller.loop.provider
        self.tools_factory = (
            tools_factory
            or getattr(controller.loop.tools, "session_factory", None)
            or (lambda _journal: controller.loop.tools)
        )
        self.resolve_context = resolve_context
        self.broker = broker
        self.controllers = {controller.session_id: controller}
        self.quick_input = QuickInputRouting(self)
        self._journals = {}
        with ExitStack() as pending:
            def own(service):
                pending.callback(self._close_service, service)
                return service

            self.data = own(WorkbenchService())
            self.events = own(EventService())
            self.background = own(background or BackgroundService(self.root / 'background'))
            self.targets = own(TargetService())
            self.catalog = own(CatalogService(self.root, lambda: tuple(self.controllers.values())))
            self.metadata = own(MetadataService(lambda: tuple(self.controllers.values())))
            # Start the command owner last: only a complete desktop may own
            # controller/backend cleanup. Earlier failures retire services here.
            self.worker = WorkerApi(self)
            pending.pop_all()

    @staticmethod
    def _close_service(service):
        service.close()
        service.wait()

    def open(self, session_id):
        identifier(session_id)
        if session_id in self.controllers:
            controller = self.controllers[session_id]
            if controller.closing:
                raise ValueError("此会话已结束，请重新打开 Silicon Copilot")
            return controller
        if self.tools_factory is None:
            raise ValueError("当前工具配置不支持恢复历史会话")
        journal = SessionJournal(self.root.parents[2], session_id)
        loop = None
        try:
            if journal.deleted:
                raise ValueError("此会话已删除")
            state = journal.state
            if state is None:
                raise ValueError("此会话没有可恢复的记录")
            saved = state.context
            context = self.resolve_context(saved) if self.resolve_context else saved
            loop = create_backend(self.provider, self.tools_factory(journal), journal, saved)
            if context.record() != saved.record():
                loop.state.context = BoundContext.from_record(context.record())
                loop._event("session.rebound", {
                    "previous_context": saved.record(), "context": context.record(),
                    "message": "会话已连接当前工程；历史设计目标需重新读取和预检。",
                })
            resume = getattr(loop, "resume_thread", None)
            if callable(resume) and getattr(loop, "thread_id", None):
                resume()
            controller = SessionController(loop)
            if self.broker is not None:
                controller.bindings.attach_targets(self.broker)
        except BaseException:
            if loop is not None and hasattr(loop, "close"):
                loop.close()
            journal.close()
            raise
        self.controllers[session_id] = controller
        self._journals[session_id] = journal
        self.catalog.request()
        return controller

    def create(self, context, *, session_id=None):
        """Create a session in this desktop process and return its controller."""

        context = BoundContext.from_record(context.record())
        session_id = session_id or uuid.uuid4().hex
        identifier(session_id)
        if session_id in self.controllers:
            raise ValueError("会话编号已存在")
        journal = SessionJournal(self.root.parents[2], session_id)
        loop = None
        try:
            if journal.state is not None:
                raise ValueError("会话编号已存在")
            journal.append(
                "session.created",
                {"entry": context.snapshot.get("entry", "menu")},
                RunState(context),
            )
            loop = create_backend(self.provider, self.tools_factory(journal), journal, context)
            controller = SessionController(loop)
            if self.broker is not None:
                controller.bindings.attach_targets(self.broker)
        except BaseException:
            if loop is not None and hasattr(loop, "close"):
                loop.close()
            journal.close()
            raise
        self.controllers[session_id] = controller
        self._journals[session_id] = journal
        self.catalog.request()
        return controller

    def preview(self, session_id):
        reader = SessionReader(self.root, session_id)
        controller = self.controllers.get(session_id)
        # A complete file line may still be awaiting fsync. An owned journal
        # publishes records only after commit, under the append lock.
        events = controller.journal.events() if controller is not None else reader.events()
        if any(event["kind"] == "codex.thread.deleted" for event in events):
            raise ValueError("此会话已删除")
        context = {}
        for event in events:
            if event["kind"] in {"session.created", "task.started"}:
                context = event["payload"].get("context", context)
        return reader, events, context

    @contextmanager
    def _transient(self, session_id):
        """Metadata-only backend for a session this window has not opened.

        Renaming and deleting never activate a session: the saved context is
        used as-is (no rebinding), no target transport is attached, and the
        backend is not registered as a window-owned controller. It exists only
        to issue the Codex RPC that the app-server requires.
        """

        identifier(session_id)
        if self.tools_factory is None:
            raise ValueError("当前工具配置不支持恢复历史会话")
        journal = SessionJournal(self.root.parents[2], session_id)
        loop = None
        try:
            if journal.deleted:
                raise ValueError("此会话已删除")
            state = journal.state
            if state is None:
                raise ValueError("此会话没有可恢复的记录")
            loop = create_backend(
                self.provider,
                self.tools_factory(journal),
                journal,
                state.context,
                workbench=False,
            )
            yield loop
        finally:
            if loop is not None and hasattr(loop, "close"):
                loop.close()
            journal.close()

    def rename(self, session_id, name):
        """Rename one session without switching to or activating it."""

        controller = self.controllers.get(session_id)
        if controller is not None:
            controller.rename(name)
        else:
            with self._transient(session_id) as loop:
                rename = getattr(loop, "set_thread_name", None)
                if not callable(rename):
                    raise ValueError("当前会话后端不支持重命名")
                rename(name)
        self.catalog.request()

    def delete(self, session_id):
        """Delete one session without switching to or activating it."""

        controller = self.controllers.get(session_id)
        if controller is not None:
            controller.delete()
        else:
            with self._transient(session_id) as loop:
                delete = getattr(loop, "delete_thread", None)
                if not callable(delete):
                    raise ValueError("当前会话后端不支持删除会话")
                delete()
        self.catalog.request()

    @property
    def busy(self):
        return (self.worker.busy or self.data.busy or self.events.busy
                or self.targets.busy or self.background.busy
                or any(controller.busy for controller in tuple(self.controllers.values())))

    def close(self):
        self.background.close()
        self.metadata.close()
        self.data.close()
        self.events.close()
        self.targets.close()
        self.worker.close()

    def _close_controllers(self):
        self.background.close()
        self.background.wait()
        self.data.close()
        self.data.wait()
        self.events.close()
        self.events.wait()
        self.targets.close()
        # A target lookup may currently be blocked in the external bridge.
        # Its Future is detached by the picker on close; do not make desktop
        # teardown wait for the bridge's operation timeout.
        self.targets.wait(0)
        for controller in tuple(self.controllers.values()):
            controller.close()
        for controller in tuple(self.controllers.values()):
            controller.wait()
            journal = self._journals.pop(controller.session_id, None)
            if journal is not None:
                journal.close()

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        stopped = self.worker.wait(timeout)
        remaining = None if deadline is None else max(0, deadline - time.monotonic())
        catalog_stopped = self.catalog.wait(remaining)
        remaining = None if deadline is None else max(0, deadline - time.monotonic())
        return self.metadata.wait(remaining) and catalog_stopped and stopped
