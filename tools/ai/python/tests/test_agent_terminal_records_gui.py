"""Read-only terminal record dialog: list, page and selected output only."""

import base64
from concurrent.futures import Future
from types import SimpleNamespace

import pytest
from agent_gui_wait import wait_until
from PyQt5.QtWidgets import QApplication, QWidget
from sico_ui.terminal_records import TerminalRecordsPanel, display_text, output_text


def command_row(name, command, *, timestamp="2026-10-18T07:00:00+00:00",
                phase="exited", mode="pty", cancel_requested=False):
    return dict(
        ref={"session_id": "session", "terminal_id": "terminal", "command_id": name},
        timestamp=timestamp, phase=phase, exit_code=0 if phase == "exited" else None,
        signal=None, cancel_requested=cancel_requested,
        output_complete=phase == "exited", output_bytes=5,
        output_sequence=1, input_bytes=0, reason=None, reason_truncated=False,
        actor="model", mode=mode, requested_cwd=None, execution_cwd="/work",
        task_id=None, command=command, command_truncated=False,
    )


class Api:
    def __init__(self):
        self.calls = []
        self.read_phase = "exited"

    def command(self, handle, name, *args, **kwargs):
        self.calls.append((handle, name, args, kwargs))
        future = Future()
        if name == "terminal_history":
            after = kwargs.get("after", 0)
            if after == 0:
                future.set_result({
                    "contract": "sico.terminal.commands.v2",
                    "commands": [command_row("one", "pwd")],
                    "next_after": 7, "observed_through": 7,
                    "integrity": "metadata_only"})
            else:
                future.set_result({
                    "contract": "sico.terminal.commands.v2",
                    "commands": [command_row("two", "printf done")],
                    "next_after": None, "observed_through": 12,
                    "integrity": "metadata_only"})
        elif name == "terminal_read":
            assert kwargs["command"]["command_id"] in {"one", "two"}
            offset = kwargs["offset"]
            data = b"hello\n"
            future.set_result({
                "contract": "sico.terminal.output.page.v1",
                "command": command_row(kwargs["command"]["command_id"], "pwd",
                                       phase=self.read_phase),
                "offset": offset, "next_offset": len(data), "has_more": False,
                "fragments": [{"stream": "pty", "offset": offset, "encoding": "base64",
                               "data": base64.b64encode(data[offset:]).decode()}],
                "integrity": "returned_chunks_verified"})
        elif name in {"terminal_take_input", "terminal_write_input", "terminal_cancel"}:
            assert kwargs["command"]["command_id"] == "one"
            future.set_result({"terminal": {"phase": "running"}})
        else:
            future.set_exception(ValueError("unexpected command " + name))
        return future


class Window(QWidget):
    def __init__(self, api=None):
        super().__init__()
        self.api = api or Api()
        self.page = SimpleNamespace(session="session", activation=0)


class FailingApi(Api):
    def command(self, handle, name, *args, **kwargs):
        self.calls.append((handle, name, args, kwargs))
        future = Future()
        future.set_exception(ValueError("Session command terminal is not qualified"))
        return future


@pytest.fixture
def app():
    application = QApplication.instance() or QApplication([])
    yield application
    application.processEvents()


def test_dialog_reads_history_and_selected_output_without_control(app):
    window = Window()
    dialog = TerminalRecordsPanel(window.api, window.page.session)
    dialog.show()
    try:
        wait_until(lambda: dialog.list.topLevelItemCount() == 1)
        item = dialog.list.topLevelItem(0)
        assert item.text(2) == "pwd"
        assert "已结束" in item.text(1)
        wait_until(lambda: "hello" in dialog.detail.toPlainText())
        history = [call for call in window.api.calls if call[1] == "terminal_history"]
        reads = [call for call in window.api.calls if call[1] == "terminal_read"]
        assert history and reads
        assert reads[0][3] == {
            "command": {"session_id": "session", "terminal_id": "terminal",
                        "command_id": "one"},
            "offset": 0, "limit": 16384}
        assert not any(call[1] in {"terminal_exec", "terminal_cancel",
                                   "terminal_write_input", "terminal_take_input"}
                       for call in window.api.calls)
    finally:
        dialog.close()
        window.close()


def test_dialog_pages_commands_without_clearing_the_list(app):
    window = Window()
    dialog = TerminalRecordsPanel(window.api, window.page.session)
    dialog.show()
    try:
        wait_until(lambda: dialog.list.topLevelItemCount() == 1)
        assert dialog.next.isEnabled()
        dialog.next.click()
        wait_until(lambda: dialog.list.topLevelItemCount() == 2)
        history = [call for call in window.api.calls if call[1] == "terminal_history"]
        assert history[-1][3] == {"after": 7, "limit": 20}
        assert dialog.next.isEnabled() is False
        assert dialog.list.topLevelItem(1).text(2) == "printf done"
    finally:
        dialog.close()
        window.close()


def test_dialog_reports_unavailable_terminal(app):
    window = Window(FailingApi())
    dialog = TerminalRecordsPanel(window.api, window.page.session)
    dialog.show()
    try:
        wait_until(lambda: "暂不可读" in dialog.status.text())
        assert "not qualified" in dialog.status.text()
        assert dialog.status.isHidden()
        assert dialog.list.topLevelItemCount() == 0
    finally:
        dialog.close()
        window.close()


def test_dialog_takes_sends_and_cancels_the_selected_command(app):
    window = Window()
    window.api = Api()
    window.api.read_phase = "running"
    original = window.api.command

    def command(handle, name, *args, **kwargs):
        if name == "terminal_history":
            future = Future()
            future.set_result({
                "contract": "sico.terminal.commands.v2",
                "commands": [command_row("one", "cat", phase="running")],
                "next_after": None, "observed_through": 3,
                "integrity": "metadata_only"})
            window.api.calls.append((handle, name, args, kwargs))
            return future
        return original(handle, name, *args, **kwargs)

    window.api.command = command
    dialog = TerminalRecordsPanel(window.api, window.page.session)
    dialog.show()
    try:
        wait_until(lambda: dialog.list.topLevelItemCount() == 1)
        wait_until(dialog.take.isEnabled)
        assert dialog.send_input.isEnabled() is False
        dialog.take.click()
        wait_until(lambda: "已取得" in dialog.status.text())
        assert dialog.send_input.isEnabled()
        dialog.input.setText("hello")
        dialog.send_input.click()
        wait_until(lambda: "输入已发送" in dialog.status.text())
        writes = [call for call in window.api.calls if call[1] == "terminal_write_input"]
        assert writes[-1][3]["data"] == base64.b64encode(b"hello").decode()
        assert dialog.input.text() == ""
        dialog.cancel_command.click()
        wait_until(lambda: "已请求取消" in dialog.status.text())
        assert any(call[1] == "terminal_cancel" for call in window.api.calls)
        assert dialog.cancel_command.isEnabled() is False
    finally:
        dialog.close()
        window.close()


class LiveApi(Api):
    def __init__(self):
        super().__init__()
        self.reads = 0

    def command(self, handle, name, *args, **kwargs):
        if name == "terminal_history":
            future = Future()
            future.set_result({
                "contract": "sico.terminal.commands.v2",
                "commands": [command_row("one", "cat", phase="running")],
                "next_after": None, "observed_through": 3,
                "integrity": "metadata_only"})
            self.calls.append((handle, name, args, kwargs))
            return future
        if name == "terminal_read":
            self.reads += 1
            offset = kwargs["offset"]
            if self.reads == 1:
                data, phase = b"first\n", "running"
            else:
                data, phase = b"second\n", "exited"
            future = Future()
            future.set_result({
                "contract": "sico.terminal.output.page.v1",
                "command": command_row("one", "cat", phase=phase),
                "offset": offset, "next_offset": offset + len(data), "has_more": False,
                "fragments": [{"stream": "pty", "offset": offset, "encoding": "base64",
                               "data": base64.b64encode(data).decode()}],
                "integrity": "returned_chunks_verified"})
            self.calls.append((handle, name, args, kwargs))
            return future
        return super().command(handle, name, *args, **kwargs)


def test_dialog_live_appends_pty_output_and_stops_at_exit(app):
    window = Window(LiveApi())
    dialog = TerminalRecordsPanel(window.api, window.page.session)
    dialog.show()
    try:
        wait_until(lambda: dialog.list.topLevelItemCount() == 1)
        wait_until(lambda: "first" in dialog.detail.toPlainText())
        wait_until(lambda: "second" in dialog.detail.toPlainText())
        wait_until(lambda: "命令已结束" in dialog.status.text())
        assert window.api.reads >= 2
        assert dialog.cancel_command.isEnabled() is False
        assert dialog.take.isEnabled() is False
        assert dialog.live.isActive() is False
    finally:
        dialog.close()
        window.close()


def test_output_text_marks_invalid_fragment_encoding():
    page = {"fragments": [
        {"stream": "stdout", "encoding": "base64", "data": "aGk="},
        {"stream": "pty", "encoding": "base64", "data": "not base64"},
    ]}
    text = output_text(page)
    assert text.startswith("[stdout]\nhi")
    assert "[输出片段编码无效]" in text


def test_display_text_strips_ansi_and_applies_carriage_return_and_backspace():
    data = b"\x1b[31mred\x1b[0m\rxy\bZ\n"
    page = {"fragments": [
        {"stream": "pty", "encoding": "base64",
         "data": base64.b64encode(data).decode()},
    ]}
    assert output_text(page) == "xZ\n"
    assert display_text("a\tb\r") == ""
