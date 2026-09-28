"""SKILL IPC child for quick input; creates no Agent session until submission."""

from __future__ import annotations

import os
import queue
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..storage.journal import open_private
from ..transport.framing import strict_json
from ..transport.quick_codec import encode_text
from ..transport.relay import StdioLines
from .launch import desktop_environment


def send_text(send, text):
    send("@text " + encode_text(text) + "\n")


def save_draft(directory, text):
    if text:
        fd = open_private(Path(directory) / "draft.txt", os.O_CREAT | os.O_WRONLY | os.O_TRUNC)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())


def run_composer(args):
    from sico.service.gui_input import gui_input

    with gui_input(args.launch_dir):
        return _run_composer(args)


def _run_composer(args):
    # Preserve locale, XMODIFIERS, QT_IM_MODULE and the user's input-method bus.
    # Set private temp/cache paths before QApplication or input plugins load.
    environment = desktop_environment(args.launch_dir, "quick-" + uuid.uuid4().hex)
    os.environ.clear()
    os.environ.update(environment)
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication

    from sico_ui.branding import install_logo
    from sico_ui.composer import QuickComposer

    reader = StdioLines(sys.stdin.buffer, decode=strict_json)
    output = ThreadPoolExecutor(max_workers=1, thread_name_prefix="copilot-composer")
    app = QApplication.instance() or QApplication([])
    install_logo(app)

    def send(line):
        sys.stdout.write(line)
        sys.stdout.flush()

    try:
        initial = reader.next(timeout=20)
        if initial.get("kind") != "open" or any(
            not isinstance(initial.get(key), str) for key in ("source", "destination")
        ):
            raise ValueError("Invalid quick composer initialization")
        window = QuickComposer(initial, lambda text: output.submit(send_text, send, text))
        timer = QTimer(window)

        def closed():
            try:
                output.submit(send, "@closed\n")
            except (OSError, ValueError):
                pass

        app.aboutToQuit.connect(closed)

        def poll():
            try:
                for _ in range(16):
                    try:
                        message = reader.lines.get_nowait()
                    except queue.Empty:
                        break
                    window.receive(message)
                if reader.closed.is_set() and not window.finished:
                    timer.stop()
                    # The owning Virtuoso is gone. Preserve a private draft,
                    # then exit rather than leave an orphaned composer process.
                    output.submit(save_draft, os.environ["TMPDIR"], window.input.toPlainText())
                    window.finished = True
                    window.reject()
            except (ValueError, KeyError, AttributeError):
                timer.stop()
                window.disconnected()

        timer.timeout.connect(poll)
        timer.start(50)
        window.restore()
        # 位置已由 SiWindowMixin 在构造时按“右半屏”摆好，见 chrome.place_on_launch_half。
        result = app.exec_()
        timer.stop()
        return result
    finally:
        reader.close()
        output.shutdown(wait=True)
