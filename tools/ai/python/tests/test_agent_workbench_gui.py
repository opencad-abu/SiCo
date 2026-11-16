"""Visible report/evidence navigation, filtering and immutable read-only history."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from agent_gui_wait import receipts, settle
from test_agent_workbench import STAGE, archived_result, backend_for, begin, call, publish

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtWidgets import QApplication

from sico.core.contracts import ToolResult
from sico.service.detail_content import DetailContent
from sico.service.workbench_data import WorkbenchService
from sico.storage.journal import SessionJournal
from sico.storage.workbench import object_link, parse_link
from sico_ui.centers import Centers
from agent_window_fixture import create_window as AssistantWindow
from sico_ui.workbench_view import WorkbenchDetail


def finish_report(backend, markdown=None):
    begin(backend)
    stage = call(backend, "set_copilot_stage", STAGE).data
    source, measured, result = archived_result(backend)
    more = {"markdown": markdown} if markdown else {}
    report = publish(backend, [source["id"], measured["id"]], **more)
    assert report.status == "ok", report
    backend._event("model.completed", {"text": "已发布归档复核报告。"})
    backend._finish("completed")
    return stage, source, measured, result, report.data


@pytest.mark.parametrize("width,height", [(800, 600), (1280, 820)])
def test_report_evidence_links_back_and_corruption(tmp_path, width, height):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    with SessionJournal(tmp_path, "workbench") as journal:
        backend = backend_for(journal)
        markdown = "## 结论\n\nslow_hot 点为 1.0 V；fast_cold 点为 1.2 V，超出规格。\n\n"
        markdown += "\n\n".join(f"检查项 {i}：仅复核已有归档，未运行仿真。" for i in range(24))
        stage, source, measured, result, report = finish_report(backend, markdown)
        window = AssistantWindow(backend)
        window.resize(width, height)
        window.show()
        try:
            settle(window)
            app.processEvents()
            assert window.width() <= width and window.height() <= height
            assert "已发布报告" in window.display.toPlainText()
            assert '{"' not in window.display.toPlainText()
            centers = window.centers
            centers.work_filter.setCurrentIndex(centers.work_filter.findData(stage["work"]["id"]))
            receipts(centers)
            centers.stage_filter.setCurrentIndex(
                centers.stage_filter.findData(stage["stage"]["id"])
            )
            receipts(centers)
            centers.reports.list.setCurrentItem(centers.reports.list.topLevelItem(0))
            centers.reports.open_button.click()
            receipts(window)
            app.processEvents()
            assert window.tabs.currentWidget() is window.detail
            assert window.detail.document.verticalScrollBar().value() == 0
            assert "1.2 V" in window.detail.document.toPlainText()
            assert "下一步" in window.detail.document.toPlainText()
            assert window.detail.document.toHtml().count("copilot://data/") == 2
            assert not window.detail.notice.text()
            window.grab().save(str(tmp_path / "workbench-report.png"))
            bar = window.detail.document.verticalScrollBar()
            bar.setValue(bar.maximum() // 2)
            position = bar.value()
            assert position > 0
            before = journal.events()
            link = object_link("data", journal.session_id, measured["id"])
            window.detail.document.anchorClicked.emit(QUrl(link))
            receipts(window)
            app.processEvents()
            text = window.detail.document.toPlainText()
            assert all(
                word in text
                for word in (
                    "CustomHistory.9",
                    "arbitrary_characterization",
                    "fast_cold",
                    "42",
                    "1.2",
                    "V",
                    "归档数据",
                    "引用此数据的报告",
                    "查询与执行条件",
                )
            )
            assert "{'" not in text
            window.grab().save(str(tmp_path / "workbench-evidence.png"))
            window.detail.go_back()
            receipts(window)
            app.processEvents()
            assert window.detail.current == parse_link(report["url"], journal.session_id)
            assert window.detail.document.verticalScrollBar().value() == position
            window.detail.go_back()
            assert window.tabs.currentWidget() is window.pages["conversation"]
            assert centers.reports.list.currentItem() is not None
            assert before == journal.events()

            centers.tabs.setCurrentWidget(centers.data)
            centers.data.category.setCurrentIndex(centers.data.category.findData("process"))
            receipts(centers)
            assert centers.data.list.topLevelItemCount() == 1
            centers.data.list.setCurrentItem(centers.data.list.topLevelItem(0))
            centers.data.search.setText("没有匹配记录")
            receipts(centers)
            assert not centers.data.open_button.isEnabled()
            assert centers.data.list.topLevelItemCount() == 0
            centers.data.search.clear()
            receipts(centers)
            assert centers.data.list.topLevelItemCount() == 1
            window.details.open_object_link(QUrl(report["url"]))
            receipts(window)
            unlisted = next(r for r in backend.workbench.index.data.values()
                            if r["data_type"] == "request")
            assert window.detail.open_url(
                object_link("data", journal.session_id, unlisted["id"])
            )
            receipts(window)
            assert "未登记" in window.detail.notice.text()
            assert window.detail.current == parse_link(report["url"], journal.session_id)
            for invalid in (
                "file:///etc/passwd",
                "https://example.invalid/image.png",
                "studio://data/another/d_1_input",
            ):
                assert not window.detail.open_url(invalid)
                assert window.detail.current == parse_link(report["url"], journal.session_id)
            assert journal.events() == before
            Path(result["artifact"]["path"]).write_text("{}")
            window.detail.reload()
            receipts(window)
            assert "校验失败" in window.detail.notice.text()
            assert "1.2 V" in window.detail.document.toPlainText()
            assert window.detail.open_url(link)
            receipts(window)
            assert "校验失败" in window.detail.notice.text()
            assert "引用此数据的报告" in window.detail.document.toPlainText()
            app.processEvents()
            assert window.detail.notice.height() >= window.detail.notice.heightForWidth(
                window.detail.notice.width()
            )
            window.grab().save(str(tmp_path / "workbench-unavailable.png"))
        finally:
            window.close()


def test_history_replay_and_live_detail_do_not_mix(tmp_path):
    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "old_work") as journal:
        old = finish_report(backend_for(journal))[-1]
    with SessionJournal(tmp_path, "live_work") as journal:
        backend = backend_for(journal)
        live = finish_report(backend)[-1]
        window = AssistantWindow(backend)
        window.show()
        try:
            settle(window)
            app.processEvents()
            window.details.open_object_link(QUrl(live["url"]))
            old_item = next(
                window.navigation.list.topLevelItem(n)
                for n in range(window.navigation.list.topLevelItemCount())
                if window.navigation.list.topLevelItem(n).data(0, Qt.UserRole) == "old_work"
            )
            # 双击历史行（itemDoubleClicked）才只读回放；单击仅选中。
            window.navigation.list.itemDoubleClicked.emit(old_item, 0)
            settle(window)
            assert not window.detail.current and window.detail.index.session_id == "old_work"
            window.details.open_object_link(QUrl(old["url"]))
            assert window.detail.current == parse_link(old["url"], "old_work")
            before = journal.events()
            begin(backend, "另一个工程任务")
            call(backend, "set_copilot_stage", {**STAGE, "work_title": "新任务"})
            backend._finish("completed")
            window.binding.poll()
            assert window.detail.index.session_id == "old_work"
            assert window.detail.current == parse_link(old["url"], "old_work")
            window.navigation.return_live()
            settle(window)
            assert window.detail.index.session_id == "live_work" and not window.detail.current
            settle(window)
            assert not window.detail.open_url(old["url"])
            assert len(window.centers.index.works) == 2
            assert len(journal.events()) > len(before)
        finally:
            window.close()


def test_all_data_search_and_paging_do_not_drop_old_records(tmp_path):
    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "paging") as journal:
        backend = backend_for(journal)
        begin(backend)
        call(backend, "set_copilot_stage", STAGE)
        for n in range(205):
            call(backend, "get_context", {}, result=ToolResult(data={"count": n}))
        index = backend.workbench.index
        service = WorkbenchService()
        centers = Centers(journal, service)
        listing = centers.data
        centers.show()
        try:
            receipts(centers)
            app.processEvents()
            assert listing.list.topLevelItemCount() == 200 and listing.next.isEnabled()
            listing.next.click()
            receipts(centers)
            assert listing.list.topLevelItemCount() == 7 and listing.previous.isEnabled()
            listing.search.setText("复核归档结果")
            receipts(centers)
            assert listing.list.topLevelItemCount() == 1 and not listing.previous.isEnabled()
            assert len(index.data) == 207
        finally:
            centers.close()
            service.close()
            assert service.wait(5)


def test_measurement_values_and_passive_markdown(tmp_path):
    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "passive") as journal:
        backend = backend_for(journal)
        report = finish_report(
            backend,
            "## 记录\n\n<script>bad()</script>\n\n"
            "![untrusted](file:///etc/passwd)\n\n"
            "[external](https://example.invalid)",
        )[-1]
        detail = WorkbenchDetail()
        service = WorkbenchService()
        _, snapshot = service.source(journal).snapshot().result(5)
        detail.bind(snapshot)
        detail.open_url(report["url"])
        detail.show()
        try:
            receipts(detail)
            app.processEvents()
            assert not detail.document.openExternalLinks()
            assert not detail.open_url("https://example.invalid")
            value = {
                "rows": [
                    {"test": "zero", "value": 0, "unit": "V", "qualified": False},
                    {"test": "missing", "value": None, "unit": None, "qualified": None},
                ]
            }
            detail.document.setHtml(DetailContent.material_html(value))
            assert all(v in detail.document.toPlainText() for v in ("0", "否", "未提供", "V"))
        finally:
            detail.close()
            service.close()
            assert service.wait(5)
