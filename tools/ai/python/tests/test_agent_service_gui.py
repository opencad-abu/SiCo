"""Service attachment and window lifetime preserve Qt response and remote ownership."""

import threading
from concurrent.futures import CancelledError
from contextlib import ExitStack

import pytest
from agent_gui_wait import wait_until
from agent_service_business_fixtures import Provider, business_service, exchange, open_session
from agent_service_gui_fixtures import remote_window, startup_args
from agent_worker_fixtures import heartbeat
from PyQt5.QtWidgets import QLabel
from sico.service.frontend_attach import FrontendAttach
from sico.service.frontend_startup import FrontendStartup
from sico.service.remote_frontend import RemoteFrontend
from sico.service.service_messages import ServiceRequest
from sico_ui.startup import StartupView


def test_window_submit_and_close_preserve_service_task(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    provider = Provider(entered, release)
    with remote_window(tmp_path, monkeypatch, provider=provider) as (service, window):
        window.input.setPlainText("accepted remote draft")
        window.actions.submit()
        wait_until(entered.is_set)
        wait_until(lambda: not window.input.toPlainText())
        heartbeat(window)
        assert not release.is_set()
        window.request_quit()
        assert window.api.detached
        heartbeat(window)
        controller = service.owner.controllers["live"]
        assert controller.busy and not controller.closing
        assert exchange(service, ServiceRequest()).pending.sessions == 1
        release.set()
        assert controller.wait(5)
        assert provider.seen == ["accepted remote draft"]
        assert controller.journal.events()[-1]["kind"] == "task.completed"


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("phase", ["discovery", "attach"])
def test_startup_pause_keeps_qt_alive_and_cancel_cannot_stop_service(
        tmp_path, monkeypatch, phase, cancel):
    import sico.service.service_startup as discovery

    entered, release = threading.Event(), threading.Event()
    with business_service(tmp_path, monkeypatch) as service:
        open_session(service)
        target, attr = ((discovery, "discover_project") if phase == "discovery"
                        else (FrontendAttach, "execute"))
        original = getattr(target, attr)

        def slow(*args, **kwargs):
            entered.set()
            assert release.wait(5)
            return original(*args, **kwargs)

        monkeypatch.setattr(target, attr, slow)
        startup = FrontendStartup(startup_args(tmp_path, "service-gui"), lambda *_a, **_k: None,
                                  environment={})
        view = StartupView(startup, None, None)
        try:
            wait_until(entered.is_set)
            assert view.window is None
            heartbeat(view.progress)
            if cancel:
                view.progress.cancel.click()
                heartbeat(view.progress)
                assert view.control.closing
            assert not release.is_set()
            release.set()
            if cancel:
                assert startup.wait(5)
                view.ready.poll()
                assert view.window is None
                with pytest.raises(CancelledError):
                    startup.ready.result()
            else:
                wait_until(lambda: view.window is not None)
                wait_until(lambda: view.window.presentation.state is not None)
                assert isinstance(view.window.api, RemoteFrontend)
            assert service.owner.controllers["live"].closing is False
        finally:
            release.set()
            view.close()
            assert startup.wait(5)


def test_ready_after_close_cannot_create_window(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch):
        startup = FrontendStartup(startup_args(tmp_path), lambda *_a, **_k: None, environment={})
        view = StartupView(startup, None, None)
        view.ready.timer.stop()
        result = startup.ready.result(5)
        view.close()
        view._ready(result)
        assert view.window is None and startup.wait(5)
        assert result.frontend.api.detached


@pytest.mark.parametrize("identity", ["service_id", "runtime_id"])
@pytest.mark.parametrize("mode", ["service-gui", "desktop-worker"])
def test_stale_wake_identity_cannot_attach_a_new_runtime(tmp_path, monkeypatch, identity, mode):
    errors = []
    monkeypatch.setattr("sico_ui.startup.notice",
                        lambda *args, **kwargs: errors.append(args))
    with business_service(tmp_path, monkeypatch) as service:
        address = open_session(service)
        args = startup_args(tmp_path, mode)
        args.service_id = address.service_id
        args.runtime_id = address.session.runtime_id
        setattr(args, identity, "f" * 32)
        startup = FrontendStartup(args, lambda *_a, **_k: None, environment={})
        if mode == "desktop-worker":
            startup.start(dict(context=service.owner.controllers["live"].current.record(),
                               bridge=dict(instance_id="instance", generation="generation",
                                           bridge_id="b" * 32, router_id="r" * 32)))
        view = StartupView(startup, None, None)
        try:
            wait_until(lambda: view.control.closing)
            assert errors and view.window is None
            assert not service.owner.controllers["live"].closing
        finally:
            view.close()
            assert startup.wait(5)


def test_completed_preparation_discard_retires_server_subscription(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service:
        address = open_session(service)
        api = RemoteFrontend(service.descriptor, tmp_path, "live")
        widget = QLabel("retiring")
        try:
            api.open_initial("attach", dict(session_id="live")).result(5)
            receipt = api.prepare_replay(address.session, 8, 200)
            ready = receipt.result(5)
            receipt.discard()
            heartbeat(widget)
            with pytest.raises(CancelledError):
                receipt.result()
            with pytest.raises(ValueError):
                ready.stream.validate_current()
            wait_until(lambda: not service.dispatch.frontend.replay._feeds)
        finally:
            api.close_desktop()
            widget.close()


def test_ended_session_archives_to_the_history_block(tmp_path, monkeypatch):
    """结束会话后它归档到下块：仍可操作，带“已结束”，不再算打开着的会话。"""
    from PyQt5.QtCore import Qt
    from sico_ui.service_connection import attach_service_connection

    def row_block(window, session_id):
        for name, tree in (("opened", window.navigation.current),
                           ("history", window.navigation.list)):
            for index in range(tree.topLevelItemCount()):
                item = tree.topLevelItem(index)
                if item.data(0, Qt.UserRole) == session_id:
                    return name, item
        return "missing", None

    with remote_window(tmp_path, monkeypatch) as (_service, window):
        attach_service_connection(window)
        wait_until(lambda: row_block(window, "live")[0] == "opened")
        window.service_connection.ending.request(window.page.session)
        wait_until(lambda: not window.service_connection.ending.pending)
        wait_until(lambda: row_block(window, "live")[0] == "history")

        _name, item = row_block(window, "live")
        assert not item.data(0, Qt.UserRole + 2)
        assert item.flags() & Qt.ItemIsEnabled and item.flags() & Qt.ItemIsSelectable
        assert "（已结束）" in item.text(0) and "当前会话" not in item.text(0)
        assert "已归档到下方历史栏" not in window.status.text()
        assert "已归档到下方历史栏" in window.navigation.notices.messages["live"][1]
        window.renderer.refresh_status()
        assert window.status.text() == "会话已结束"
        assert not window.input.isEnabled()
        heartbeat(window)


def test_session_from_a_previous_window_shows_in_history_until_continued(tmp_path, monkeypatch):
    """上次运行遗留（服务里仍活着、本窗口没打开过）的会话在下块，继续后才进上块。"""
    from agent_service_gui_fixtures import ServiceTestWindow
    from PyQt5.QtCore import Qt
    from sico.service.frontend_session import FrontendSession
    from sico_ui.service_connection import attach_service_connection
    from test_agent_service_control import attach

    def block(window, session_id):
        for name, tree in (("opened", window.navigation.current),
                           ("history", window.navigation.list)):
            for index in range(tree.topLevelItemCount()):
                item = tree.topLevelItem(index)
                if item.data(0, Qt.UserRole) == session_id:
                    return name
        return "missing"

    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service, "old")            # 上一次运行留下的活会话
        previous, _token = attach(stack, service, tmp_path, "old")
        previous.close_desktop()                # 旧窗口关闭：只 detach，会话留在服务里
        wait_until(lambda: previous.detached)
        open_session(service, "fresh")          # 新窗口启动后新建的会话
        api, fresh = attach(stack, service, tmp_path, "fresh")
        window = ServiceTestWindow(FrontendSession(api, fresh))
        stack.callback(window.deleteLater)
        stack.callback(window.request_quit)
        attach_service_connection(window)
        wait_until(lambda: not window.page.opening and window.presentation.state is not None)

        wait_until(lambda: block(window, "old") == "history")
        assert block(window, "fresh") == "opened"
        window.page.activate_session("old")          # 右键“继续”走的就是这条路径
        wait_until(lambda: block(window, "old") == "opened")
        heartbeat(window)


def test_project_service_status_is_centered_and_the_badge_stays_right(tmp_path, monkeypatch):
    """项目服务状态固定在状态栏中间；右侧只保留“基座/模型”标签。"""
    from sico_ui.service_connection import attach_service_connection

    with remote_window(tmp_path, monkeypatch) as (_service, window):
        attach_service_connection(window)
        window.show()
        wait_until(lambda: window.service_connection.label.text() != "")

        center_box = window.service_status
        assert center_box.isAncestorOf(window.service_connection.label)
        assert center_box.isAncestorOf(window.service_connection.control.label)
        assert center_box.isAncestorOf(window.service_connection.ending.label)
        assert not center_box.isAncestorOf(window.model_status)
        # 平台/会话在左下角分两行；中间是服务状态，右侧是基座/模型。
        for label in window.statusBar().findChildren(QLabel):
            assert (
                label is window.model_status
                or label is window.status
                or label is window.platform_menu.label
                or center_box.isAncestorOf(label)
            ), label.text()
        platform = window.platform_menu.label
        assert not window.pages["conversation"].isAncestorOf(platform)
        assert platform.parentWidget() is window.status.parentWidget()
        assert platform.geometry().bottom() < window.status.geometry().top()
        assert platform.font() == window.model_status.font()
        assert window.status.font() == platform.font()
        from PyQt5.QtGui import QPalette
        assert platform.palette().color(QPalette.WindowText) == window.model_status.palette().color(
            QPalette.WindowText)
        assert window.service_connection.label.text() == "项目服务已连接"
        # 中间的服务状态与右侧标签同色（红棕色）。
        from sico_ui.theme import ACCENT

        accent_rule = window.styleSheet().split("QWidget#serviceStatus QLabel {", 1)[1].split(
            "}", 1
        )[0]
        assert f"color: {ACCENT}" in accent_rule

        bar = window.statusBar()
        label_center = window.service_connection.label.mapTo(
            bar, window.service_connection.label.rect().center()
        ).x()
        badge_center = window.model_status.mapTo(
            bar, window.model_status.rect().center()
        ).x()
        # 中间：容差按状态栏宽度取 12%；右侧：基座/模型落在最右边四分之一。
        assert abs(label_center - bar.width() / 2) <= bar.width() * 0.12
        assert badge_center > bar.width() * 0.75
