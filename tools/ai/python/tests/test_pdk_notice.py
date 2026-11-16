"""A relocation notice is visible once without interrupting the task."""
from sico_ui.transcript import Transcript
from PyQt5.QtWidgets import QTextBrowser


def test_pdk_relocation_notice_is_visible_and_deduplicated():
    transcript = Transcript()
    message = "PDK pdk_a 路径发生变更：/old/pdk → /shared/pdk。继续使用已采集数据。"
    for index in range(2):
        transcript.receive({"kind": "tool.finished", "task_id": "task", "sequence": index,
            "timestamp": "2026-09-16T08:00:00Z", "session_id": "session",
            "payload": {"id": str(index), "name": "prepare_pdk_data",
                "result": {"status": "ok", "summary": "", "data": {
                    "status": "ready", "notices": [{"code": "pdk_path_changed", "message": message}]}}}})
    notices = [row for row in transcript.messages if row["role"] == "notice"]
    assert len(notices) == 1 and notices[0]["text"] == message
    view = QTextBrowser()
    transcript.render(view)
    assert view.toPlainText().count(message) == 1
