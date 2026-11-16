from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

PYQT_ROOT = Path(__file__).resolve().parents[2] / "terminal" / "pyqt"

TERMINAL_CMAKE = PYQT_ROOT.parent / "CMakeLists.txt"

PROTOCOL_PATH = PYQT_ROOT / "cad_ai_terminal" / "protocol.py"

SPEC = importlib.util.spec_from_file_location("cad_ai_terminal_protocol", PROTOCOL_PATH)

assert SPEC is not None and SPEC.loader is not None

protocol = importlib.util.module_from_spec(SPEC)

sys.modules[SPEC.name] = protocol

SPEC.loader.exec_module(protocol)

sys.path.insert(0, str(PYQT_ROOT))

from cad_ai_terminal import qt_frontend as frontend  # noqa: E402,F401
from cad_ai_terminal import terminal_session as terminal_session  # noqa: E402

REQUIRED_TERMINAL_PACKAGE_FILES = (
    "__init__.py",
    "appearance.py",
    "control_channel.py",
    "input_behavior.py",
    "main.py",
    "protocol.py",
    "qt_frontend.py",
    "resources.py",
    "session_context.py",
    "shell_environment.py",
    "tab_renamer.py",
    "terminal_session.py",
    "terminal_widget.py",
    "terminal_window.py",
    "output_capture.py",
    "toolbar.py",
    "window_close.py",
    "window_shutdown.py",
)

def _stage_test_terminal_package(root: Path, exit_code: int) -> Path:
    package = root / "cad_ai_terminal"
    package.mkdir(parents=True)
    for name in REQUIRED_TERMINAL_PACKAGE_FILES:
        source = ""
        if name == "main.py":
            source = f"def main():\n    return {exit_code}\n"
        (package / name).write_text(source, encoding="ascii")
    return package


def _stage_test_terminal_launcher(directory: Path) -> Path:
    launcher = directory / "sico-ai-terminal-pyqt"
    directory.mkdir(parents=True)
    launcher.write_bytes((PYQT_ROOT / "sico-ai-terminal-pyqt").read_bytes())
    launcher.chmod(0o755)
    return launcher


def _run_test_terminal_launcher(
    launcher: Path,
    *,
    cwd: Path,
    pyqt_root: str | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("SICO_", "CAD_"))}
    for name in (
        "SICO_AI_PYQT_ROOT",
        "SICO_AI_PYTHON_LIBRARY_PATH",
        "SICO_SYSTEM_LD_LIBRARY_PATH",
        "SICO_PYTHON_ROOT",
        "PYTHONHOME",
    ):
        environment.pop(name, None)
    environment.update(
        {
            "SICO_PYTHON": sys.executable,
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    if pyqt_root is not None:
        environment["SICO_AI_PYQT_ROOT"] = pyqt_root
    return subprocess.run(
        [str(launcher)],
        cwd=cwd,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )


class _FakeFont:
    Monospace = 1

    def __init__(self, family: str) -> None:
        self.family = family
        self.style_hint = None
        self.fixed_pitch = False

    def setStyleHint(self, hint: int) -> None:
        self.style_hint = hint

    def setFixedPitch(self, enabled: bool) -> None:
        self.fixed_pitch = enabled


class _FakeFontInfo:
    def __init__(self, font: _FakeFont) -> None:
        self.font = font

    def fixedPitch(self) -> bool:
        return self.font.family == "System Mono"


class _FakeFontDatabase:
    FixedFont = 1
    system_family = "System Sans"
    available_families = ["System Sans", "Liberation Mono"]

    @classmethod
    def systemFont(cls, _kind: int) -> _FakeFont:
        return _FakeFont(cls.system_family)

    def families(self) -> list[str]:
        return list(self.available_families)

    def isFixedPitch(self, family: str) -> bool:
        return family.endswith("Mono")
