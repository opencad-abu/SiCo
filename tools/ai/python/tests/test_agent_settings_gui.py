"""Settings use async service receipts and retain drafts across failures and page changes."""

from concurrent.futures import Future
from copy import deepcopy
from types import SimpleNamespace

import pytest
from agent_gui_wait import wait_until
from agent_service_business_fixtures import business_service
from PyQt5.QtWidgets import QApplication
from sico.agent_resources import store
from sico.agent_resources.schema import upgrade_manifest
from sico.service.remote_frontend import RemoteFrontend
from sico_ui.agent_settings import AgentSettingsDialog, show_agent_settings
from sico_ui.home_window import HomeWindow
from test_agent_resource_store import config


def response(project, *, selected=None, session=None):
    document = store.read_project(project)
    document["manifest"] = upgrade_manifest(document["manifest"])
    pdk = project / "pdk"
    return dict(project=str(project), defaults_scope="service",
                supported_kinds=["skill_roots", "plugins", "capability_roots"],
                project_defaults={"status": "available", **document},
                selected_defaults=selected or {"status": "available", **document}, session=session,
                pdk_workflow={"status": "available", "pdk_workflow": "design",
                              "revision": None, "path": str(project) + "/.sico/ai/agent-mode.json"},
                model_settings={"status": "available", "backend": "codex",
                                "provider": "responses", "revision": None,
                                "path": str(project) + "/.sico/ai/agent-model.json"},
                data_sources={
                    "status": "available", "path": str(project) + "/.sico/ai/data-sources.json",
                    "revision": None, "editable": True,
                    "project": {"pdk_data": None, "circuit_templates": None},
                    "effective": {
                        "pdk_data": {"path": str(pdk), "source": "environment",
                                     "environment": "SICO_PDK_DATA",
                                     "status": "available", "message": ""},
                        "circuit_templates": {"path": None, "source": "none",
                                              "environment": "SICO_CIRCUIT_TEMPLATES_DIR",
                                              "status": "unconfigured", "message": "未配置"},
                    },
                    "session": None,
                })


def done(value):
    future = Future()
    future.set_result(value)
    return future


def fake_window(project, read, save=None, save_mode=None, save_model=None,
                save_data_sources=None):
    window = HomeWindow(project, project / "workspace.ini", lambda _f: None)
    window.api = SimpleNamespace(
        launch_directory=project,
        agent_settings=SimpleNamespace(
            read=read, save=save, save_mode=save_mode, save_model=save_model,
            save_data_sources=save_data_sources),
    )
    # The fake exercises settings only; catalog/platform lifecycle has separate real-wire tests.
    window.timer.stop()
    window.platform_menu.timer.stop()
    return window


def test_model_settings_default_to_codex_and_save_python(tmp_path):
    calls = []

    def save_model(backend, provider, revision, token):
        calls.append((backend, provider, revision))
        return done({"outcome": "saved", "document": {
            "status": "available", "backend": backend, "provider": provider,
            "revision": "b" * 64, "path": str(tmp_path) + "/.sico/ai/agent-model.json"}})

    window = fake_window(
        tmp_path, lambda _token: done(response(tmp_path)), save_model=save_model
    )
    dialog = AgentSettingsDialog(window)
    try:
        wait_until(lambda: not dialog.busy)
        assert dialog.ui.backend.currentData() == "codex"
        assert dialog.ui.provider.currentData() == "responses"
        assert not dialog.ui.apply_model.isEnabled()
        dialog.ui.backend.setCurrentIndex(1)
        assert dialog.ui.provider.currentData() == "anthropic"
        assert dialog.ui.apply_model.isEnabled()
        dialog.ui.apply_model.click()
        wait_until(lambda: not dialog.busy)
        assert calls == [("python", "anthropic", None)]
        assert dialog.model_saved == ("python", "anthropic")
        assert "新会话" in dialog.ui.message.text()
    finally:
        close(window, dialog)


def test_data_sources_edit_and_save(tmp_path):
    calls = []

    def save_data_sources(pdk, templates, revision, token):
        calls.append((pdk, templates, revision))
        return done({"outcome": "saved", "document": {
            "status": "available", "path": str(tmp_path) + "/.sico/ai/data-sources.json",
            "revision": "c" * 64, "editable": True,
            "project": {"pdk_data": pdk, "circuit_templates": templates},
            "effective": {
                "pdk_data": {"path": pdk, "source": "project",
                             "environment": "SICO_PDK_DATA",
                             "status": "available", "message": ""},
                "circuit_templates": {"path": templates, "source": "project",
                                      "environment": "SICO_CIRCUIT_TEMPLATES_DIR",
                                      "status": "available", "message": ""},
            },
            "session": None,
        }})

    window = fake_window(
        tmp_path, lambda _token: done(response(tmp_path)),
        save_data_sources=save_data_sources)
    dialog = AgentSettingsDialog(window)
    pdk, templates = tmp_path / "pdk", tmp_path / "templates"
    pdk.mkdir()
    templates.mkdir()
    try:
        wait_until(lambda: not dialog.busy)
        dialog.ui.categories.setCurrentRow(3)
        dialog.edit_data_source("pdk_data")
        editor = dialog.editor
        editor.path.setText(str(pdk))
        editor.submit()
        wait_until(lambda: dialog.editor is None)
        assert dialog.data_edits["pdk_data"] == str(pdk)
        dialog.edit_data_source("circuit_templates")
        editor = dialog.editor
        editor.path.setText(str(templates))
        editor.submit()
        wait_until(lambda: dialog.editor is None)
        assert dialog.ui.data_page.save.isEnabled()
        dialog.ui.data_page.save.click()
        wait_until(lambda: not dialog.busy)
        assert calls == [(str(pdk), str(templates), None)]
        assert dialog.data_saved == {
            "pdk_data": str(pdk), "circuit_templates": str(templates)}
        assert not dialog.data_edits
        assert "新会话" in dialog.ui.message.text()
    finally:
        close(window, dialog)


def close(window, dialog):
    dialog.discard = True
    dialog.close()
    window.finish()
    QApplication.instance().processEvents()


def add_skill(dialog, root, name="design"):
    dialog.ui.categories.setCurrentRow(2)
    dialog.ui.pages[2].add_buttons[0][0].click()
    editor = dialog.editor
    assert editor is not None
    editor.identity.setText(name)
    editor.path.setText(str(root))
    editor.submit()
    assert dialog.editor is None
    assert dialog.draft.dirty


@pytest.mark.parametrize("width", [700, 1000])
def test_home_three_pages_edit_save_and_read_only_override(tmp_path, monkeypatch, width):
    with business_service(tmp_path, monkeypatch) as service:
        api = RemoteFrontend(service.descriptor, tmp_path, "")
        window = HomeWindow(tmp_path, tmp_path / "workspace.ini", lambda _f: None)
        dialog = None
        try:
            window.show()
            assert not window.settings_action.isEnabled()
            window.ready(api)
            assert window.settings_action.isEnabled()
            assert [action.text() for action in window.settings_menu.actions()] == [
                "外观设置…", "智能体设置…", "Codex 特性（工程默认）…"]
            assert "智能体设置…" not in [action.text()
                                         for action in window.workspace_menu.actions()]
            assert not window.workspace_menu.menuAction().isVisible()
            window.settings_button.click()
            dialog = window.agent_settings_dialog
            dialog.resize(width, 720)
            wait_until(lambda: dialog.ui.reload.isEnabled())
            assert [dialog.ui.categories.item(i).text() for i in range(4)] == [
                "知识源", "MCP 与插件", "技能", "数据源"]
            assert dialog.ui.stack.currentWidget() is dialog.ui.pages[0]
            assert dialog.ui.pages[0].add_buttons[0][0].isEnabled()
            assert dialog.ui.pages[1].add_buttons[1][0].isEnabled()
            skills = dialog.ui.pages[2].add_buttons
            assert "SKILL.md" in skills[0][0].toolTip()
            assert "新建会话" in skills[1][0].toolTip()
            for index, name in enumerate(("knowledge", "mcp", "skills")):
                dialog.ui.categories.setCurrentRow(index)
                QApplication.instance().processEvents()
                assert dialog.ui.stack.currentWidget() is dialog.ui.pages[index]
                assert dialog.grab().save(str(tmp_path / (name + "-empty.png")))
            add_skill(dialog, tmp_path / "skills")
            dialog.ui.save.click()
            wait_until(lambda: not dialog.busy and not dialog.draft.dirty)
            wait_until(lambda: dialog.ui.reload.isEnabled())
            assert store.read_project(tmp_path)["manifest"]["skill_roots"][0]["id"] == "design"
            assert not service.owner.controllers and not service.provider.seen
            assert dialog.grab().save(str(tmp_path / "skills-saved.png"))
            dialog.ui.mode.setCurrentIndex(1)
            assert not dialog.ui.pages[2].add_buttons[0][0].isEnabled()
            assert not dialog.ui.save.isEnabled()
            assert not dialog.ui.data_page.edit.isEnabled()
            assert not dialog.ui.data_page.save.isEnabled()
        finally:
            if dialog:
                close(window, dialog)
            else:
                window.finish()
            api.close_desktop()
            wait_until(lambda: api.wait_desktop(0))


def test_conflict_retains_draft_until_explicit_rebase(tmp_path):
    latest = [response(tmp_path)]
    writes = []
    def save(manifest, revision, token):
        writes.append((deepcopy(manifest), revision))
        return done({"outcome": "conflict", "message": "其他窗口已修改，草稿保留"})
    window = fake_window(tmp_path, lambda _token: done(latest[0]), save)
    dialog = AgentSettingsDialog(window)
    try:
        dialog.show()
        wait_until(lambda: not dialog.busy)
        add_skill(dialog, tmp_path / "mine", "mine")
        draft = deepcopy(dialog.draft.manifest)
        dialog.save()
        wait_until(lambda: not dialog.busy)
        assert dialog.draft.manifest == draft and dialog.draft.blocked
        assert not dialog.ui.save.isEnabled()
        store.save_project(tmp_path, config(tmp_path, "theirs"), expected_revision=None)
        latest[0] = response(tmp_path)
        dialog.reload()
        wait_until(lambda: not dialog.busy)
        assert dialog.draft.manifest == draft and dialog.draft.latest is not None
        dialog.ui.rebase.click()
        assert dialog.draft.blocked
        assert dialog.diff_dialog.isVisible()
        dialog.confirmation.pick("continue")
        assert not dialog.draft.blocked and dialog.ui.save.isEnabled()
        assert dialog.draft.manifest == draft
        assert dialog.draft.document["revision"] == latest[0]["project_defaults"]["revision"]
    finally:
        close(window, dialog)


@pytest.mark.parametrize("outcome", ["rejected", "uncertain", "transport"])
def test_save_failure_keeps_draft_and_requires_read_after_unknown(tmp_path, outcome):
    def save(*_args):
        if outcome == "transport":
            future = Future()
            future.set_exception(RuntimeError("连接中断"))
            return future
        return done({"outcome": outcome, "message": "保存未完成"})
    window = fake_window(tmp_path, lambda _token: done(response(tmp_path)), save)
    dialog = AgentSettingsDialog(window)
    try:
        wait_until(lambda: not dialog.busy)
        add_skill(dialog, tmp_path / "mine")
        before = deepcopy(dialog.draft.manifest)
        dialog.save()
        wait_until(lambda: not dialog.busy)
        assert dialog.draft.manifest == before and dialog.draft.dirty
        assert dialog.draft.blocked == (outcome != "rejected")
    finally:
        close(window, dialog)


def test_late_result_after_session_change_cannot_replace_draft(tmp_path):
    pending = Future()
    pending.set_running_or_notify_cancel()
    window = fake_window(tmp_path, lambda _token: pending)
    token = SimpleNamespace(session_id="a", runtime_id="r")
    window.page = SimpleNamespace(activation=1, reviewing=None, session=token, opening=False)
    window.api.owns = lambda _token: True
    dialog = AgentSettingsDialog(window)
    try:
        window.page.activation += 1
        pending.set_result(response(tmp_path))
        dialog.receipt.poll()
        assert dialog.stale and dialog.draft.manifest is None
        assert not dialog.ui.save.isEnabled()
        assert "切换" in dialog.ui.message.text()
    finally:
        close(window, dialog)


def test_busy_session_and_default_differences_have_read_only_view(tmp_path):
    original = upgrade_manifest(config(tmp_path, "old"))
    store.save_project(tmp_path, config(tmp_path, "new"), expected_revision=None)
    session = {"status": "available", "recorded": False, "snapshot": {
        "busy": True, "rows": [],
        "configuration": {"state": "frozen", "effective": {
            "manifest": original, "source": "project",
            "path": "/project/resources.json"}, "drift": []}}}
    window = fake_window(tmp_path, lambda _token: done(response(tmp_path, session=session)))
    dialog = AgentSettingsDialog(window)
    try:
        wait_until(lambda: not dialog.busy)
        dialog.ui.mode.setCurrentIndex(2)
        dialog.ui.categories.setCurrentRow(2)
        assert dialog.ui.pages[2].tree.topLevelItem(0).text(0).startswith("old")
        assert "执行中" in dialog.ui.session_status.text()
        assert "2 项差异" in dialog.ui.session_status.text()
        assert not dialog.ui.session_bar.isHidden()
        assert not dialog.ui.recheck.isEnabled()
        assert not dialog.ui.pages[2].edit.isEnabled()
        dialog.ui.compare.click()
        assert dialog.diff_dialog.isVisible()
        QApplication.instance().processEvents()
        assert dialog.grab().save(str(tmp_path / "effective.png"))
    finally:
        close(window, dialog)


def test_close_confirmation_and_reopen_reuses_live_draft(tmp_path):
    window = fake_window(tmp_path, lambda _token: done(response(tmp_path)))
    show_agent_settings(window)
    dialog = window.agent_settings_dialog
    try:
        wait_until(lambda: not dialog.busy)
        add_skill(dialog, tmp_path / "draft")
        show_agent_settings(window)
        assert window.agent_settings_dialog is dialog
        dialog.close()
        assert dialog.confirmation is not None and dialog.isVisible()
        dialog.confirmation.pick("cancel")
        assert dialog.draft.dirty and dialog.isVisible()
    finally:
        close(window, dialog)
