"""Page identity and asynchronous selection, independent of window widgets."""

from PyQt5.QtCore import QObject, pyqtSignal

PREVIEW_HINT = "正在浏览会话记录（只读）"


class NavigationState(QObject):
    invalidated = pyqtSignal()
    began = pyqtSignal()
    activated = pyqtSignal(object, object)
    previewReady = pyqtSignal(object, object)
    returned = pyqtSignal()
    failed = pyqtSignal(str)
    initializeRequested = pyqtSignal()

    def __init__(self, api, session, *, receipt, closing):
        super().__init__()
        self.api = api
        self.session = session
        self.view = api.view(session)
        self.receipt = receipt
        self.closing = closing
        self.activation = 0
        # 本窗口打开过的会话：导航上块只放这些，服务里别人留下的活动会话归下块。
        self.opened = {session.session_id: session}
        self.opening = False
        self.reviewing = None
        self.disposed = False

    def dispose(self):
        # QObject destruction must invalidate callbacks without rendering widgets.
        self.disposed = True
        self.activation += 1

    def invalidate(self):
        self.activation += 1
        self.invalidated.emit()

    def begin(self):
        self.invalidate()
        self.receipt.clear()
        self.opening = True
        self.began.emit()
        return self.activation, self.session, self.session.runtime_id

    def current(self, scope, *, replacing=False):
        activation, session, runtime = scope
        return (not self.disposed and activation == self.activation and session == self.session
                and (replacing or self.api.owns(session)) and runtime == session.runtime_id
                and not self.closing())

    def finish(self, scope):
        if not self.current(scope):
            return False
        self.opening = False
        return True

    def failure(self, scope, error):
        if self.finish(scope):
            self.failed.emit("会话未打开：" + str(error))

    def prepare(self, session, scope, *, initialize=False, replacing=False):
        from .session_binding import SessionBinding

        session = self.api.handle(session)

        def ready(prepared):
            if not self.current(scope, replacing=replacing):
                prepared.stream.close()
                return
            if not self.api.replay_matches(prepared, session, scope[0]):
                prepared.stream.close()
                raise ValueError("会话回放准备结果已失效，请重新选择会话")
            previous = self.session
            self.session = session
            self.opened[session.session_id] = session
            self.view = self.api.view(session)
            self.reviewing = None
            self.opening = False
            self.activated.emit(previous, prepared)
            if initialize:
                self.initializeRequested.emit()

        def failed(exc):
            if self.current(scope, replacing=replacing):
                self.opening = False
                self.failed.emit("会话未打开：" + str(exc))

        try:
            future = self.api.prepare_replay(session, scope[0], SessionBinding.REPLAY_MESSAGES)
            self.receipt.watch(future, ready, failed)
        except (ValueError, RuntimeError) as exc:
            failed(exc)

    def activate_session(self, session_id):
        session = self.api.session(session_id)
        if session is not None and self.api.is_closing(session):
            raise ValueError("此会话已结束，请重新打开 Silicon Copilot")
        if session == self.session:
            self.return_from_preview()
            return
        scope = self.begin()
        if session is not None:
            self.prepare(session, scope)
            return

        def opened(session):
            session = self.api.handle(session)
            if not self.current(scope):
                return
            if session.session_id != session_id or not self.api.owns(session):
                raise ValueError("打开的会话运行实例已失效")
            self.prepare(session, scope)

        try:
            self.receipt.watch(self.api.open_session(session_id), opened,
                               lambda exc: self.failure(scope, exc))
        except (ValueError, RuntimeError) as exc:
            self.failure(scope, exc)

    def activate(self):
        self.prepare(self.session, self.begin())

    def continue_session(self, session_id):
        scope = self.begin()

        def current():
            return self.current(scope, replacing=True)

        def opened(token):
            if current() and token.session_id == session_id:
                self.prepare(token, scope, replacing=True)

        def failed(error):
            if current():
                self.opening = False
                self.failed.emit("会话暂未打开：" + str(error))

        try:
            self.receipt.watch(self.api.recovery.continue_session(session_id), opened, failed)
        except (ValueError, RuntimeError) as exc:
            failed(exc)

    def activate_recovered(self, session_id):
        scope = self.begin()

        def current():
            return (not self.disposed and not self.closing() and self.activation == scope[0]
                    and self.session == scope[1])

        def opened(token):
            if current() and token.session_id == session_id:
                self.session = self.api.handle(token)
                self.view = self.api.view(token)
                self.prepare(token, (scope[0], token, token.runtime_id), replacing=True)

        def failed(error):
            if current():
                self.opening = False
                self.failed.emit("恢复会话未关联：" + str(error))

        self.receipt.watch(self.api.open_session(session_id), opened, failed)

    def preview_session(self, session_id):
        from .session_binding import SessionBinding

        session = self.api.session(session_id)
        # An ended page still names its old runtime. Read the retained record
        # through the preview API instead of returning to that retired binding.
        if (session_id == self.session.session_id and session is not None
                and not self.api.is_closing(session)):
            self.return_from_preview()
            return
        self.reviewing = session_id
        scope = self.begin()

        def failed(exc):
            if self.current(scope):
                self.return_from_preview()
                self.failed.emit("历史会话暂不可读：" + str(exc))

        def ready(prepared):
            if not self.current(scope):
                prepared.stream.close()
                return
            if not self.api.preview_matches(prepared, session_id, scope[0]):
                prepared.stream.close()
                raise ValueError("历史会话回放准备结果已失效")
            self.previewReady.emit(prepared, scope)

        try:
            future = self.api.preview_session(session_id, scope[0], SessionBinding.REPLAY_MESSAGES)
            self.receipt.watch(future, ready, failed)
        except (ValueError, RuntimeError) as exc:
            failed(exc)

    def return_from_preview(self):
        if self.reviewing is not None:
            self.reviewing = None
            self.activate()
            return
        self.invalidate()
        self.receipt.clear()
        self.opening = False
        self.returned.emit()

    def new_session(self, context):
        if self.closing() or self.opening:
            return
        scope = self.begin()

        def created(session):
            if self.current(scope):
                self.prepare(session, scope, initialize=True)

        try:
            self.receipt.watch(self.api.create_session(context), created,
                               lambda exc: self.failure(scope, exc))
        except (ValueError, RuntimeError) as exc:
            self.failure(scope, exc)
