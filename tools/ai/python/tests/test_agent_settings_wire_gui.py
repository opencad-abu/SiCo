"""Real service settings receipts in home and session windows."""

import threading
from contextlib import ExitStack

from agent_gui_wait import wait_until
from agent_service_business_fixtures import business_service
from agent_service_gui_fixtures import remote_window
from agent_worker_fixtures import heartbeat
from PyQt5.QtWidgets import QApplication
from sico.agent_resources import store
from sico.codex.backend import CodexBackend
from sico.core.tools import ToolRegistry
from sico.storage.journal import SessionJournal
from sico_ui.agent_settings import AgentSettingsDialog
from sico_ui.home_window import HomeWindow
from test_agent_codex_native import CONTEXT
from test_agent_settings_gui import add_skill, close
from test_agent_settings_service import client


def test_two_home_clients_conflict_with_blocked_service_write(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = store.save_project
    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        windows, dialogs = [], []
        for _ in range(2):
            api = stack.enter_context(client(service, tmp_path))
            window = HomeWindow(tmp_path, tmp_path / "workspace.ini", lambda _f: None)
            window.api = api
            windows.append(window)
            dialogs.append(AgentSettingsDialog(window))
        try:
            wait_until(lambda: all(not d.busy for d in dialogs))
            a, b = dialogs
            a.show()
            b.show()
            add_skill(a, tmp_path / "a", "a")
            add_skill(b, tmp_path / "b", "b")
            monkeypatch.setattr(store, "save_project", slow)
            a.save()
            wait_until(entered.is_set)
            heartbeat(windows[0])
            assert not release.is_set() and a.busy
            release.set()
            wait_until(lambda: not a.busy and not a.draft.dirty)
            b.save()
            wait_until(lambda: not b.busy)
            assert b.draft.blocked and b.draft.manifest["skill_roots"][0]["id"] == "b"
            assert store.read_project(tmp_path)["manifest"]["skill_roots"][0]["id"] == "a"
            b.reload()
            wait_until(lambda: not b.busy)
            b.compare()
            QApplication.instance().processEvents()
            assert b.grab().save(str(tmp_path / "conflict.png"))
            assert b.diff_dialog.grab().save(str(tmp_path / "conflict-diff.png"))
            b.choose_latest()
            b.confirmation.pick("continue")
            assert not b.draft.dirty and b.draft.manifest == a.draft.manifest
            assert not service.owner.controllers and not service.provider.seen
        finally:
            release.set()
            for window, dialog in zip(windows, dialogs):
                close(window, dialog)


def test_lost_save_reply_reconciles_committed_defaults(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service, client(service, tmp_path) as api:
        window = HomeWindow(tmp_path, tmp_path / "workspace.ini", lambda _f: None)
        window.api = api
        dialog = AgentSettingsDialog(window)
        original = api.agent_settings.save
        def lost(*args):
            future = original(*args)
            def fail(_value):
                raise RuntimeError("保存回执丢失")
            from concurrent.futures import Future

            receipt = Future()
            def settled(_future):
                try:
                    fail(future.result())
                except Exception as exc:
                    receipt.set_exception(exc)
            future.add_done_callback(settled)
            return receipt
        try:
            dialog.show()
            wait_until(lambda: not dialog.busy)
            add_skill(dialog, tmp_path / "skills")
            monkeypatch.setattr(api.agent_settings, "save", lost)
            dialog.save()
            wait_until(lambda: not dialog.busy)
            assert dialog.draft.dirty and dialog.draft.blocked
            assert store.read_project(tmp_path)["manifest"] == dialog.draft.manifest
            dialog.reload()
            wait_until(lambda: not dialog.busy)
            assert dialog.draft.latest is not None and dialog.draft.blocked
            dialog.choose_latest()
            dialog.confirmation.pick("continue")
            assert not dialog.draft.dirty and not dialog.draft.blocked
        finally:
            close(window, dialog)


def test_session_menu_and_unopened_history_read_fixed_choices(tmp_path, monkeypatch):
    from sico.codex.settings import CodexSettings

    provider = CodexSettings(
        {"backend": "codex", "provider": "responses",
         "base_url": "http://127.0.0.1:1/v1", "model": "test",
         "api_key_env": "SICO_API_KEY"},
        {"SICO_API_KEY": "controlled"},
    )
    provider.release, provider.seen = None, []
    with SessionJournal(tmp_path, "history") as journal:
        history = CodexBackend(provider, ToolRegistry(), journal, CONTEXT)
        history.resources.prepare()
    with remote_window(tmp_path, monkeypatch, provider=provider) as (service, window):
        controller = service.owner.controllers["live"]
        controller.loop.resources.prepare()
        dialogs = []
        try:
            window.show()
            wait_until(lambda: not window.page.opening)
            assert [a.text() for a in window.settings_menu.actions()] == [
                "外观设置…", "智能体设置…", "Codex 特性…", "工程记忆…"]
            assert "智能体设置…" not in [a.text() for a in window.workspace_menu.actions()]
            action = next(a for a in window.settings_menu.actions() if a.text() == "智能体设置…")
            action.trigger()
            dialog = window.agent_settings_dialog
            dialogs.append(dialog)
            wait_until(lambda: not dialog.busy)
            assert dialog.response["defaults_scope"] == "session"
            assert not dialog.response["session"]["recorded"]
            add_skill(dialog, tmp_path / "new")
            dialog.save()
            wait_until(lambda: not dialog.busy and not dialog.draft.dirty)
            dialog.reload()
            wait_until(lambda: not dialog.busy)
            dialog.ui.mode.setCurrentIndex(2)
            assert dialog.response["session"]["snapshot"]["configuration"]["effective"][
                "manifest"]["skill_roots"] == []
            assert "1 项差异" in dialog.ui.session_status.text()
            assert dialog.ui.recheck.isEnabled()
            assert controller.loop.runtime is None
            window.page.preview_session("history")
            wait_until(lambda: not window.page.opening)
            assert window.api.session("history") is None
            action.trigger()
            history_dialog = window.agent_settings_dialog
            dialogs.append(history_dialog)
            wait_until(lambda: not history_dialog.busy)
            assert history_dialog.token == "history"
            assert history_dialog.response["session"]["recorded"]
            history_dialog.ui.mode.setCurrentIndex(2)
            assert "历史检查结果" in history_dialog.ui.session_status.text()
            assert not history_dialog.ui.recheck.isEnabled()
            history_dialog.resize(700, 620)
            QApplication.instance().processEvents()
            assert history_dialog.grab().save(str(tmp_path / "history.png"))
            page = history_dialog.ui.pages[0]
            assert page.tree.geometry().bottom() < page.add_buttons[0][0].geometry().top()
            assert set(service.owner.controllers) == {"live"}
            assert not service.provider.seen
        finally:
            for dialog in dialogs:
                dialog.discard_close()
