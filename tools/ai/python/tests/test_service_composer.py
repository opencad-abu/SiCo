"""Composer transport helpers and the GUI-input ownership boundary."""

from __future__ import annotations

from contextlib import contextmanager
import queue
import threading
from types import SimpleNamespace

import pytest

from sico.service.composer import run_composer, save_draft, send_text
from sico.transport.quick_codec import encode_text


def test_send_text_uses_the_quick_wire_encoding_and_one_line():
    sent = []
    send_text(sent.append, '中文 "quote" \\\nsecond')
    assert sent == ["@text " + encode_text('中文 "quote" \\\nsecond') + "\n"]
    assert sent[0].endswith("\n") and "\n" not in sent[0][:-1]


def test_save_draft_writes_utf8_and_leaves_existing_draft_untouched_when_empty(tmp_path):
    save_draft(tmp_path, "第一行\n第二行")
    path = tmp_path / "draft.txt"
    assert path.read_text(encoding="utf-8") == "第一行\n第二行"
    save_draft(tmp_path, "")
    assert path.read_text(encoding="utf-8") == "第一行\n第二行"


def test_run_composer_delegates_inside_gui_input(monkeypatch, tmp_path):
    events = []

    @contextmanager
    def gui_input(launch_dir):
        events.append(("enter", launch_dir))
        yield
        events.append(("exit", launch_dir))

    monkeypatch.setattr("sico.service.gui_input.gui_input", gui_input)
    monkeypatch.setattr("sico.service.composer._run_composer",
                        lambda args: events.append(("run", args.launch_dir)) or 7)
    args = SimpleNamespace(launch_dir=tmp_path)
    assert run_composer(args) == 7
    assert events == [
        ("enter", tmp_path),
        ("run", tmp_path),
        ("exit", tmp_path),
    ]


def test_run_composer_handles_termination_and_cleans_owned_resources(monkeypatch, tmp_path):
    import PyQt5.QtCore as qtcore
    import PyQt5.QtWidgets as qtwidgets
    import sico_ui.appearance_runtime as appearance
    import sico_ui.branding as branding
    import sico_ui.composer as ui_composer
    import sico_ui.qt_translation as translation
    import sico.service.composer as composer

    events = []

    class Signal:
        def __init__(self):
            self.callback = None

        def connect(self, callback):
            self.callback = callback

        def fire(self):
            if self.callback:
                self.callback()

    class Timer:
        def __init__(self, _window):
            self.timeout = Signal()
            Application.timer = self

        def start(self, _milliseconds):
            pass

        def stop(self):
            events.append("timer.stop")

    class Input:
        def toPlainText(self):
            return "保留草稿"

    class Window:
        def __init__(self, _initial, _submit, _query, object_request=None):
            self.input = Input()
            self.finished = False
            self.object_request = object_request

        def restore(self):
            events.append("window.restore")

        def receive(self, message):
            events.append(("window.receive", message))

        def reject(self):
            self.finished = True
            events.append("window.reject")

        def disconnected(self):
            events.append("window.disconnected")

    class Output:
        def submit(self, function, *args):
            function(*args)

        def shutdown(self, wait):
            events.append(("output.shutdown", wait))

    class Reader:
        def __init__(self, *_args, **_kwargs):
            self.lines = queue.Queue()
            self.closed = threading.Event()

        def next(self, timeout):
            events.append(("reader.next", timeout))
            return {"kind": "open", "source": "source", "destination": "destination"}

        def close(self):
            events.append("reader.close")

    class Termination:
        def __init__(self):
            self.received = threading.Event()

        def install(self):
            self.received.set()
            events.append("termination.install")
            return self

        def restore(self):
            events.append("termination.restore")

    class Application:
        aboutToQuit = Signal()
        timer = None

        @classmethod
        def instance(cls):
            return None

        def __init__(self, _args):
            pass

        def exec_(self):
            self.timer.timeout.fire()
            return 7

    monkeypatch.setattr(composer, "desktop_environment",
                        lambda *_args: {"TMPDIR": str(tmp_path)})
    monkeypatch.setattr(composer, "StdioLines", Reader)
    monkeypatch.setattr(composer, "ThreadPoolExecutor", lambda **_kwargs: Output())
    monkeypatch.setattr(composer, "TerminationSignal", Termination)
    monkeypatch.setattr(qtcore, "QTimer", Timer)
    monkeypatch.setattr(qtwidgets, "QApplication", Application)
    monkeypatch.setattr(branding, "install_logo", lambda _app: None)
    monkeypatch.setattr(appearance, "apply_project_appearance",
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(translation, "install_chinese_qt_translations", lambda _app: None)
    monkeypatch.setattr(ui_composer, "QuickComposer", Window)

    assert composer._run_composer(SimpleNamespace(launch_dir=tmp_path)) == 7
    assert (tmp_path / "draft.txt").read_text(encoding="utf-8") == "保留草稿"
    assert events[:3] == [
        ("reader.next", 20),
        "window.restore",
        "termination.install",
    ] or events[:3] == [
        "termination.install",
        ("reader.next", 20),
        "window.restore",
    ]
    assert "window.reject" in events
    assert "termination.restore" in events
    assert "reader.close" in events
    assert ("output.shutdown", True) in events


def test_run_composer_invalid_initialization_still_restores_resources(monkeypatch, tmp_path):
    import PyQt5.QtCore as qtcore
    import PyQt5.QtWidgets as qtwidgets
    import sico_ui.appearance_runtime as appearance
    import sico_ui.branding as branding
    import sico_ui.composer as ui_composer
    import sico_ui.qt_translation as translation
    import sico.service.composer as composer

    events = []

    class Signal:
        def connect(self, _callback):
            pass

    class Timer:
        def __init__(self, _window):
            self.timeout = Signal()

        def start(self, _milliseconds):
            pass

        def stop(self):
            events.append("timer.stop")

    class Output:
        def submit(self, *_args):
            pass

        def shutdown(self, wait):
            events.append(("output.shutdown", wait))

    class Reader:
        def __init__(self, *_args, **_kwargs):
            self.lines = queue.Queue()
            self.closed = threading.Event()

        def next(self, timeout):
            return {"kind": "invalid"}

        def close(self):
            events.append("reader.close")

    class Termination:
        def install(self):
            return self

        def restore(self):
            events.append("termination.restore")

    class Application:
        aboutToQuit = Signal()

        @classmethod
        def instance(cls):
            return None

        def __init__(self, _args):
            pass

        def exec_(self):
            pytest.fail("invalid initialization must not enter the Qt loop")

    monkeypatch.setattr(composer, "desktop_environment",
                        lambda *_args: {"TMPDIR": str(tmp_path)})
    monkeypatch.setattr(composer, "StdioLines", Reader)
    monkeypatch.setattr(composer, "ThreadPoolExecutor", lambda **_kwargs: Output())
    monkeypatch.setattr(composer, "TerminationSignal", Termination)
    monkeypatch.setattr(qtcore, "QTimer", Timer)
    monkeypatch.setattr(qtwidgets, "QApplication", Application)
    monkeypatch.setattr(branding, "install_logo", lambda _app: None)
    monkeypatch.setattr(appearance, "apply_project_appearance",
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(translation, "install_chinese_qt_translations", lambda _app: None)
    monkeypatch.setattr(ui_composer, "QuickComposer", lambda *_args, **_kwargs: None)

    with pytest.raises(ValueError, match="Invalid quick composer initialization"):
        composer._run_composer(SimpleNamespace(launch_dir=tmp_path))
    assert "termination.restore" in events
    assert "reader.close" in events
    assert ("output.shutdown", True) in events
