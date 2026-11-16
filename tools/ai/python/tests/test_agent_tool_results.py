"""Tool evidence is replayable, escaped, and reads large results from its own journal."""

from __future__ import annotations

import json
import os
from functools import partial

import pytest
from agent_gui_wait import receipts

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtGui import QTextDocument
from PyQt5.QtWidgets import QApplication

from sico.core.contracts import ToolCall, ToolResult
from sico.core.tools import tool_message
from sico.service.workbench_data import WorkbenchService
from sico.storage.journal import SessionJournal
from sico_ui.presentation import data_html
from sico_ui.tool_results import ToolResults, result_html


def setup_data():
    return {
        "valid": True,
        "cellview": {"lib": "testLib", "cell": "tb", "view_name": "maestro"},
        "contract": "ade_setup.v1",
        "tests": [
            {
                "name": "nominal",
                "enabled": True,
                "testbench": {"lib": "testLib", "cell": "tb", "view": "schematic"},
                "simulator": "spectre",
                "output_count": 1,
                "outputs": [
                    {"name": "gain", "expression": "<img src='file:///secret'>", "signal": ""}
                ],
            }
        ],
        "global_variables": [{"name": "VDD", "value": "1.2"}],
        "corners": ["Nominal"],
        "bound_history": "Interactive.1",
        "truncated": True,
    }


def test_tool_tables_escape_values_and_state_query_limits():
    rendered = result_html({"data": setup_data(), "summary": "test"})
    assert "<img" not in rendered and "&lt;img" in rendered
    assert "当前 ADE 设置" in rendered and "只显示部分数据" in rendered
    assert "不代表该 History" in rendered and "1.2" in rendered
    assert "Testbench" in rendered and "全局变量" in rendered
    history = result_html(
        {"data": {"contract": "ade_history.v1", "histories": [], "cellview": {"lib": "testLib"}}}
    )
    assert "尚无 History" in history and "计数不代表仿真通过" in history
    assert "暂时无法整理" in result_html({"data": {"contract": "ade_setup.v1", "tests": [False]}})


@pytest.mark.parametrize("data", [
    {
        "cellview": {"lib": "testLib", "cell": "tb", "view_name": "schematic"},
        "value": 0, "valid": False, "unit": "V", "missing": None,
    },
    [{"value": 0, "valid": False, "unit": "V", "missing": None}],
])
def test_unknown_tool_data_keeps_zero_false_and_missing_distinct(data):
    app = QApplication.instance() or QApplication([])
    document = QTextDocument()
    document.setHtml(result_html({"status": "ok", "data": data}))
    plain = document.toPlainText()
    assert "0" in plain and "否" in plain and "未提供" in plain and "V" in plain
    assert not any(symbol in plain for symbol in ("{", "}", "[", "]"))
    assert app


def test_data_fields_escape_bound_and_never_render_truncated_json_preview():
    value = {
        "truncated": True, "preview": '{"raw_secret_field":',
        "artifact": {"path": "artifacts/data.json", "size": 120},
        "nested": {"name": "<img src='file:///secret'>"},
    }
    rendered = data_html(value)
    assert "<img" not in rendered and "&lt;img" in rendered
    assert "raw_secret_field" not in rendered and "artifacts/data.json" in rendered
    bounded = data_html({"value": "x" * 10000, "rows": list(range(300))})
    assert bounded.count("<tr>") == 160 and "部分字段" in bounded


def test_tool_view_restores_artifact_and_reports_missing_evidence(tmp_path):
    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "tools") as journal:
        service = WorkbenchService()
        view = ToolResults(partial(service.tool_result, journal))
        view.resize(1000, 660)
        call = ToolCall("read1", "read_ade_setup", {})
        data = setup_data()
        data["extra"] = "x" * 9000
        message = tool_message(
            call, ToolResult(summary="ADE 配置", data=data), 4000, journal.artifact
        )
        result = json.loads(message["content"])
        assert result["truncated"]
        view.receive("tool.started", call.record())
        view.receive("tool.finished", {"id": call.id, "result": result})
        receipts(view)
        assert "testLib" in view.summary.toPlainText()
        assert "<img src='file:///secret'>" in view.summary.toPlainText()
        assert "当前 ADE 设置" in view.summary.toPlainText()
        assert view.details.tabText(1) == "数据明细"
        assert "原始 JSON" not in view.fields.toPlainText()
        assert view.records["0:read1"] == result
        view.show()
        app.processEvents()
        view.grab().save(str(tmp_path / "ade-tools.png"))
        item = view.list.currentItem()
        assert item.text(1) == "完成"
        path = journal.root / result["artifact"]["path"]
        path.write_text("corrupt")
        view.select(item)
        receipts(view)
        assert "暂不可读" in view.summary.toPlainText()
        view.receive("tool.started", ToolCall("read2", "read_ade_history", {}).record())
        view.receive("task.needs_reconcile", {})
        assert "中断" in view.list.currentItem().text(1)
        view.receive("task.started", {})
        view.receive("tool.started", call.record())
        assert len(view.items) == 3
        assert view.summary.loadResource(2, None) is None
        view.close()
        service.close()
        assert service.wait(5)
