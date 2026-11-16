"""Independent home and platform selection remain responsive without a design session."""

from concurrent.futures import Future
from types import SimpleNamespace

from agent_gui_wait import wait_until
from agent_service_business_fixtures import business_service
from PyQt5.QtWidgets import QApplication
from sico.service.remote_frontend import RemoteFrontend
from sico_ui.close_choice import PLATFORM_CLOSE_NOTE
from sico_ui.home_window import HomeWindow
from sico_ui.platform_picker import PlatformPicker


def test_home_opens_before_service_and_has_future_platform_entries(tmp_path):
    window = HomeWindow(str(tmp_path), tmp_path / "workspace.ini", lambda _frontend: None)
    try:
        window.show()
        assert window.api is None
        assert not window.connect_button.isEnabled()
        assert window.launch_button.text() == "启动 CDNS-IC…"
        assert not window.launch_button.isEnabled()
        assert window.stop_action.text() == "停止"
        assert not window.stop_action.isEnabled()
        actions = window.platform_menu.menu.actions()
        assert actions[0].text() == "连接 CDNS-IC…"
        assert actions[1].text() == "启动 CDNS-IC…"
        assert "EMPY-AE" in actions[2].text() and not actions[2].isEnabled()
        assert "SNPS-CC" in actions[3].text() and not actions[3].isEnabled()
        assert "尚未连接 IC 平台" in window.hint.text()
    finally:
        window.finish()
        window.deleteLater()


def test_picker_can_be_cancelled_during_discovery_without_late_selection(tmp_path):
    pending = Future()
    pending.set_running_or_notify_cancel()
    window = HomeWindow(str(tmp_path), tmp_path / "workspace.ini", lambda _frontend: None)
    picker = PlatformPicker(window, SimpleNamespace(instances=lambda: pending))
    selected = []
    picker.selected.connect(selected.append)
    try:
        picker.show()
        QApplication.instance().processEvents()
        assert picker.isVisible()
        picker.reject()
        pending.set_result([dict(id="a" * 64, instance="v1", version="IC23", project="test")])
        picker.receipt.poll()
        assert not selected and not picker.instances.topLevelItemCount()
    finally:
        picker.deleteLater()
        window.finish()
        window.deleteLater()


def test_service_ready_home_lists_history_without_creating_execution(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service:
        api = RemoteFrontend(service.descriptor, tmp_path, "")
        window = HomeWindow(str(tmp_path), tmp_path / "workspace.ini", lambda _frontend: None)
        try:
            window.show()
            window.ready(api)
            wait_until(lambda: api.connection_state == "connected")
            assert window.connect_button.isEnabled()
            assert not service.owner.controllers
            assert not service.provider.seen
            window.platform_menu.connect_action.trigger()
            picker = window.platform_menu.picker
            wait_until(lambda: picker.refresh_button.isEnabled())
            assert "未发现可连接实例" in picker.message.text()
            picker.reject()
            wait_until(lambda: window.platform_menu.picker is None)
            window.connect_button.click()
            assert window.platform_menu.picker is not None
            window.platform_menu.picker.reject()
        finally:
            window.finish()
            window.deleteLater()
            api.close_desktop()
            wait_until(lambda: api.wait_desktop(0))


def test_home_close_explains_that_platform_virtuoso_stays_open(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service:
        api = RemoteFrontend(service.descriptor, tmp_path, "")
        window = HomeWindow(str(tmp_path), tmp_path / "workspace.ini", lambda _frontend: None)
        try:
            window.show()
            window.ready(api)
            wait_until(lambda: api.connection_state == "connected")
            window.close()
            assert window._dialog is not None
            assert PLATFORM_CLOSE_NOTE in window._dialog.message.text()
            window._dialog.reject()
            window._dialog = None
        finally:
            window.finish()
            window.deleteLater()
            api.close_desktop()
            wait_until(lambda: api.wait_desktop(0))
