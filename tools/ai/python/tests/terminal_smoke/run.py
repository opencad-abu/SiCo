"""Private source acceptance entry for the actual QTermWidget binding."""

from pathlib import Path

from PyQt5 import QtWidgets
from terminal_smoke.popup import verify_popup
from terminal_smoke.rename import verify_rename
from terminal_smoke.sessions import verify_sessions


class TestApplication(QtWidgets.QApplication):
    def exec_(self):
        window = next(
            item for item in self.topLevelWidgets() if item.objectName() == "cadAiTerminalWindow"
        )
        terminal, surface, tabs = verify_popup(self, window)
        verify_rename(self, window, tabs)
        return verify_sessions(self, window, terminal, surface, tabs)


QtWidgets.QApplication = TestApplication
from cad_ai_terminal.protocol import LaunchOptions  # noqa: E402
from cad_ai_terminal.qt_frontend import run_frontend  # noqa: E402

options = LaunchOptions(
    Path.cwd(), Path("/bin/sh"), ("-c", "printf 'right-click-test selection text\n'; sleep 10")
)
raise SystemExit(run_frontend(options))
