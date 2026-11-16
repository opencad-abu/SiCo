"""SiCo reads SVG from .cad for display; tool reports contain only file metadata."""

import json
import os
from functools import partial
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from agent_gui_wait import receipts
from PyQt5.QtWidgets import QApplication
from test_circuit_preview import Client, receipt, svg

from cadai.circuit_preview import attach_svg_preview
from sico.core.contracts import ToolCall, ToolResult
from sico.service.detail_content import DetailContent
from sico.service.workbench_data import WorkbenchService
from sico.storage.journal import SessionJournal
from sico_ui.tool_results import ToolResults
from sico_ui.workbench_view import WorkbenchDetail


def test_python_file_read_ui_render_and_checksum_failure(tmp_path):
    app = QApplication.instance() or QApplication([])
    raw = svg('cdsParam(1) 电路预览')
    creation = attach_svg_preview(receipt(), Client(raw), tmp_path)
    result = ToolResult(data=dict(last_response=creation)).record()
    assert "<svg" not in json.dumps(result)
    with SessionJournal(tmp_path, "preview") as journal:
        service = WorkbenchService()
        view = ToolResults(partial(service.tool_result, journal))
        try:
            call = ToolCall("create", "execute_circuit_operation", {})
            view.receive("tool.started", call.record())
            view.receive("tool.finished", dict(id=call.id, result=result))
            receipts(view)
            assert view.svg_preview.renderer().isValid()
            assert view.details.tabText(2) == "原理图预览"
            view.resize(1100, 700)
            view.details.setCurrentIndex(2)
            view.show()
            app.processEvents()
            assert view.grab().save(str(tmp_path / "sico-svg-preview.png"))
            # A published report obtains the same bytes from its creation evidence.
            class Index:
                reader = journal
                session_id = "preview"
                reports, data, stages, works = {}, {}, {}, {}

                def verify_evidence(self, refs):
                    pass

                def evidence_value(self, key):
                    return result

            detail = DetailContent(Index())
            content = detail.report(dict(kind="report", id="report", key="report", version=1,
                                         outcome="completed", evidence=[dict(id="creation")],
                                         next_steps="", markdown="完成"))
            assert detail.images == [raw]
            report_view = WorkbenchDetail()
            try:
                report_view.current = ("report", "report")
                report_view._render_detail(report_view._detail_generation,
                                           dict(content, images=detail.images))
                assert report_view.image_sizes, report_view.notice.text()
            finally:
                report_view.close()
            path = Path(creation["svg_preview"]["artifact"]["path"])
            path.write_bytes(raw.replace(b"cdsParam", b"CDSPARAM"))
            view.select(view.list.currentItem())
            receipts(view)
            assert not view.svg_preview.renderer().isValid()
            assert "校验失败" in view.preview_notice.text()
            path.write_bytes(b"x" * len(raw))
            view.select(view.list.currentItem())
            receipts(view)
            assert not view.svg_preview.renderer().isValid()
            assert "校验失败" in view.preview_notice.text()
        finally:
            view.close()
            service.close()
            assert service.wait(5)
