"""The settings session view re-checks one captured session and shows its status."""

from concurrent.futures import Future
from types import SimpleNamespace

import pytest
from agent_gui_wait import wait_until
from agent_service_gui_fixtures import remote_window
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication
from sico.codex.backend import CodexBackend
from sico.core.tools import ToolRegistry
from test_codex_resources_runtime import make_resources


def attach_resources(controller, manifest):
    provider = SimpleNamespace(options={"external_resources": str(manifest)}, environment={})
    loop = CodexBackend(provider, ToolRegistry(), controller.loop.journal, controller.current)
    loop.resources.prepare()
    controller.loop.resources = loop.resources
    return loop


def open_settings(window):
    action = next(a for a in window.settings_menu.actions() if a.text() == "智能体设置…")
    action.trigger()
    return window.agent_settings_dialog


@pytest.mark.parametrize("width", [700, 1000])
def test_settings_session_view_rechecks_worker_resources_without_runtime(
        tmp_path, monkeypatch, width):
    manifest, _ = make_resources(tmp_path)
    app = QApplication.instance() or QApplication([])
    with remote_window(tmp_path, monkeypatch) as (service, window):
        controller = service.owner.controllers["live"]
        loop = attach_resources(controller, manifest)
        dialog = None
        try:
            window.show()
            wait_until(lambda: not window.page.opening)
            assert [a.text() for a in window.settings_menu.actions()] == [
                "外观设置…", "智能体设置…", "Codex 特性…", "工程记忆…"]
            assert "外部技能与插件…" not in [a.text() for a in window.workspace_menu.actions()]
            dialog = open_settings(window)
            wait_until(lambda: not dialog.busy)
            dialog.ui.mode.setCurrentIndex(2)
            dialog.resize(width, 700)
            app.processEvents()
            assert dialog.ui.categories.geometry().right() < dialog.ui.stack.geometry().left()
            wait_until(lambda: dialog.ui.recheck.isEnabled())
            assert dialog.ui.session_name.text().startswith("会话：")
            assert "只读" in dialog.ui.session_name.text()
            dialog.ui.recheck.click()
            wait_until(lambda: not dialog.busy and dialog.ui.recheck.isEnabled())
            assert "等待会话进程启动" in dialog.ui.session_status.text()
            skills, mcp = dialog.ui.pages[2].tree, dialog.ui.pages[1].tree
            assert [skills.topLevelItem(i).text(0).splitlines()[0]
                    for i in range(skills.topLevelItemCount())] == ["user", "cap"]
            assert "读取" in skills.topLevelItem(0).child(0).text(2)
            assert mcp.topLevelItem(0).text(0) == "sico_ic"
            plugin = mcp.topLevelItem(1)
            assert plugin.text(0).splitlines()[0] == "b-plugin"
            assert "加载和调用" in plugin.child(0).text(2)
            snapshot = controller.backend_commands.resource_status()
            assert len(snapshot["rows"]) == 3
            snapshot["status"] = "checked"
            snapshot["rows"][0].update(status="available", message="已发现 1 项技能")
            snapshot["rows"][1].update(
                status="dependency_missing", message="插件已安装，工具连接尚未就绪",
                servers=[{"name": "proof-server", "status": "failed", "tools": 0}])
            snapshot["rows"][2].update(status="selected", message="已绑定当前线程，按需读取")
            snapshot["skills"] = [{"name": "project-analysis", "enabled": True,
                                   "path": str(manifest), "pluginId": None}]
            snapshot["dependencies"] = [{"skill": "project-analysis", "name": "proof-server",
                                         "status": "dependency_missing"}]
            snapshot["issues"] = ["部分技能无法解析，请核对 SKILL.md 格式和目录权限"]
            dialog.session_rechecked(snapshot)
            dialog.render()
            app.processEvents()
            plugin = mcp.topLevelItem(1)
            assert "依赖未就绪" in plugin.text(2)
            assert plugin.child(1).text(0) == "proof-server"
            group = next(skills.topLevelItem(i) for i in range(skills.topLevelItemCount())
                         if skills.topLevelItem(i).text(0) == "会话已发现的技能")
            assert group.child(0).text(0) == "project-analysis"
            assert any(skills.topLevelItem(i).text(2).startswith("依赖未就绪")
                       for i in range(skills.topLevelItemCount()))
            assert "部分技能无法解析" in dialog.ui.session_status.text()
            assert dialog.grab().save(str(tmp_path / "settings-session-status.png"))
            dialog.response["session"]["snapshot"]["busy"] = True
            dialog.render()
            assert "任务执行中 · 最近检查结果" in dialog.ui.session_status.text()
            assert loop.runtime is None
            assert not any(e["kind"] == "task.started" for e in controller.loop.journal.events())
            assert set(service.owner.controllers) == {"live"}
        finally:
            if dialog is not None:
                dialog.discard_close()
            loop.close()


def test_settings_session_view_discards_late_result_after_switch(tmp_path, monkeypatch):
    manifest, _ = make_resources(tmp_path)
    with remote_window(tmp_path, monkeypatch) as (service, window):
        controller = service.owner.controllers["live"]
        loop = attach_resources(controller, manifest)
        dialog = None
        try:
            window.show()
            wait_until(lambda: not window.page.opening)
            dialog = open_settings(window)
            wait_until(lambda: not dialog.busy)
            dialog.ui.mode.setCurrentIndex(2)
            wait_until(lambda: dialog.ui.recheck.isEnabled())

            pending = Future()
            pending.set_running_or_notify_cancel()
            original = window.api.command
            def command(token, name, *args, **kwargs):
                if name == "resource_status" and token is dialog.token:
                    return pending
                return original(token, name, *args, **kwargs)
            monkeypatch.setattr(window.api, "command", command)
            dialog.ui.recheck.click()
            assert dialog.busy
            window.page.activation += 1
            pending.set_result({"rows": [{"id": "foreign"}], "status": "checked"})
            QTest.qWait(50)
            dialog.receipt.poll()
            assert dialog.stale
            assert "切换" in dialog.ui.message.text()
            assert not dialog.ui.recheck.isEnabled()
            assert "foreign" not in dialog.ui.pages[2].tree.topLevelItem(0).text(0)
        finally:
            if dialog is not None:
                dialog.discard_close()
            loop.close()
