"""Authenticated background preview, cancellation ownership and Qt draft lifetimes."""

import sys
import threading
from concurrent.futures import Future
from pathlib import Path

import pytest
from agent_gui_wait import wait_until
from agent_service_business_fixtures import business_service
from knowledge_fixtures import local, manifest
from PyQt5.QtWidgets import QApplication
from sico.agent_resources import store
from sico.service import knowledge_checks
from sico_ui.agent_settings import AgentSettingsDialog
from sico_ui.knowledge_editor import KnowledgeEditor
from test_agent_settings_gui import close, done, fake_window, response
from test_agent_settings_service import client


def test_preview_build_search_read_without_saving_or_opening_a_session(tmp_path, monkeypatch):
    source = local(tmp_path / "docs")
    (Path(source["path"]) / "manual.md").write_text("器件 nmos_1v8 的薄栅氧电压限制。")
    value = manifest(source)
    with business_service(tmp_path, monkeypatch) as service, client(service, tmp_path) as api:
        service.owner._default_environment = {"SICO_PYTHON": sys.executable}
        assert (
            api.agent_settings.knowledge("build", value, "build", {"source_id": "local"}).result(
                10
            )["status"]
            == "checked"
        )
        search = api.agent_settings.knowledge(
            "search", value, "search", {"query": "薄栅氧", "limit": 4}
        ).result(10)
        hit = search["result"]["hits"][0]
        proof = api.agent_settings.knowledge(
            "read", value, "read", {"source_id": "local", "evidence_id": hit["evidence_id"]}
        ).result(10)
        assert proof["result"]["verified"]
        assert not service.owner.controllers and not service.provider.seen
        assert store.read_project(tmp_path)["revision"] is None


def test_cancellation_is_client_scoped_and_survives_cancel_before_start(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    observed = []

    def hold(self, query, limit, cancelled):
        entered.set()
        release.wait(4)
        observed.append(cancelled())
        return {"hits": []}

    monkeypatch.setattr(knowledge_checks.Knowledge, "search", hold)
    value = manifest(local(tmp_path / "docs"))
    with (
        business_service(tmp_path, monkeypatch) as service,
        client(service, tmp_path) as a,
        client(service, tmp_path) as b,
    ):
        try:
            future = a.agent_settings.knowledge(
                "same", value, "search", {"query": "MCP", "limit": 2}
            )
            assert entered.wait(3)
            b.agent_settings.cancel_knowledge("same").result(3)
            release.set()
            assert future.result(5)["status"] == "checked" and observed == [False]
            a.agent_settings.cancel_knowledge("pre-cancel").result(3)
            assert (
                a.agent_settings.knowledge("pre-cancel", value, "status", {}).result(3)["status"]
                == "cancelled"
            )
            entered.clear()
            release.clear()
            future = a.agent_settings.knowledge(
                "owned", value, "search", {"query": "MCP", "limit": 2}
            )
            assert entered.wait(3)
            a.agent_settings.cancel_knowledge("owned").result(3)
            release.set()
            assert future.result(5)["status"] == "cancelled" and observed[-1]
        finally:
            release.set()


@pytest.mark.parametrize("kind", ["local", "remote"])
def test_knowledge_editor_narrow_layout_stages_typed_definition(tmp_path, kind):
    editor = KnowledgeEditor(None, kind=kind)
    try:
        editor.resize(610, 490)
        editor.fields["id"].setText(kind)
        editor.fields["name"].setText("本地工艺说明" if kind == "local" else "OpenAI 官方文档")
        if kind == "local":
            editor.fields["path"].setText(str(tmp_path / "docs"))
        editor.show()
        QApplication.instance().processEvents()
        assert editor.grab().save(str(tmp_path / (kind + "-editor.png")))
        editor.submit()
        assert editor.value["type"] == kind
        assert editor.value.get("adapter", "openai_docs_mcp") == "openai_docs_mcp"
    finally:
        editor.close()


def test_knowledge_editor_stages_jedai_adapter_and_category(tmp_path):
    editor = KnowledgeEditor(None, kind="remote")
    try:
        editor.fields["id"].setText("jedai-ask")
        editor.fields["name"].setText("JedAI 官方文档")
        editor.adapter.setCurrentIndex(editor.adapter.findData("jedai_aai_mcp"))
        assert editor.collections.currentWidget() is editor.collection_text
        editor.fields["url"].setText("http://127.0.0.1:8080/api/aai/mcp/")
        editor.collection_text.setText("cdn_virtuoso-ask-26.10")
        editor.fields["credential_ref"].setText("JEDAI_ACCESS_TOKEN")
        editor.show()
        QApplication.instance().processEvents()
        # 适配器切换容器只占单行高度，不再吞掉表单多余的竖向空间。
        single_line = editor.fields["url"].height() + 8
        assert editor.collections.height() <= single_line
        editor.adapter.setCurrentIndex(editor.adapter.findData("openai_docs_mcp"))
        QApplication.instance().processEvents()
        assert editor.collections.currentWidget() is editor.collection
        assert editor.collections.height() <= single_line
        editor.adapter.setCurrentIndex(editor.adapter.findData("jedai_aai_mcp"))
        QApplication.instance().processEvents()
        assert editor.grab().save(str(tmp_path / "jedai-editor.png"))
        editor.submit()
        assert editor.value["adapter"] == "jedai_aai_mcp"
        assert editor.value["collection"] == "cdn_virtuoso-ask-26.10"
        assert editor.value["credential_ref"] == "JEDAI_ACCESS_TOKEN"
        empty = KnowledgeEditor(None, kind="remote")
        try:
            empty.adapter.setCurrentIndex(empty.adapter.findData("jedai_aai_mcp"))
            empty.fields["id"].setText("jedai-ask")
            empty.fields["name"].setText("JedAI")
            empty.fields["url"].setText("http://127.0.0.1:8080/api/aai/mcp/")
            empty.submit()
            assert empty.value is None and "类别名" in empty.message.text()
        finally:
            empty.close()
    finally:
        editor.close()


def test_preview_discards_late_result_after_draft_change_and_keeps_single_dialog(tmp_path):
    source = local(tmp_path / "docs")
    reply = response(tmp_path)
    reply["project_defaults"]["manifest"]["knowledge_sources"] = [source]
    window = fake_window(tmp_path, lambda _token: done(reply))
    pending, cancelled = Future(), []
    window.api.agent_settings.knowledge = lambda *_args: pending
    window.api.agent_settings.cancel_knowledge = lambda identity: (
        cancelled.append(identity) or done({})
    )
    dialog = AgentSettingsDialog(window)
    try:
        wait_until(lambda: not dialog.busy)
        dialog.show()
        dialog.preview_knowledge()
        preview = dialog.knowledge_dialog
        dialog.preview_knowledge()
        assert dialog.knowledge_dialog is preview
        dialog.draft.toggle("knowledge_sources", 0)
        preview.check_scope()
        pending.set_result({"status": "checked", "result": {"sources": [], "hits": []}})
        wait_until(lambda: not preview.active)
        assert cancelled and not preview.search.isEnabled() and "改变" in preview.message.text()
    finally:
        close(window, dialog)


def test_preview_shows_search_citation_and_read_text(tmp_path):
    source = local(tmp_path / "docs")
    reply = response(tmp_path)
    reply["project_defaults"]["manifest"]["knowledge_sources"] = [source]
    window = fake_window(tmp_path, lambda _token: done(reply))
    hit = {
        "source_id": "local",
        "evidence_id": "a" * 64,
        "title": "工艺说明.md",
        "excerpt": "薄栅氧电压限制",
        "uri": "file:///docs/manual.md#L1",
        "version": "b" * 64,
        "location": {"start": 1, "end": 3, "page": None},
    }

    def knowledge(_identity, _manifest, action, _arguments, _token):
        result = (
            {"sources": [{"source_id": "local", "status": "ready"}], "hits": [hit]}
            if action == "search"
            else {**hit, "text": "薄栅氧电压限制（已核验）", "verified": True}
            if action == "read"
            else {"sources": [{"source_id": "local", "status": "pending_index"}]}
        )
        return done({"status": "checked", "result": result})

    window.api.agent_settings.knowledge = knowledge
    window.api.agent_settings.cancel_knowledge = lambda _identity: done({})
    dialog = AgentSettingsDialog(window)
    try:
        wait_until(lambda: not dialog.busy)
        dialog.preview_knowledge()
        preview = dialog.knowledge_dialog
        wait_until(lambda: not preview.active)
        preview.resize(700, 580)
        preview.query.setText("薄栅氧电压")
        preview.search.click()
        wait_until(lambda: not preview.active)
        preview.rows.setCurrentItem(preview.rows.topLevelItem(1))
        preview.read.click()
        wait_until(lambda: not preview.active)
        assert "已核验" in preview.output.toPlainText()
        assert "行号：1–3" in preview.output.toPlainText()
        assert not dialog.draft.dirty
        assert preview.grab().save(str(tmp_path / "knowledge-preview.png"))
    finally:
        close(window, dialog)
