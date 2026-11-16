"""Terminal launcher python regressions."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from pinyin_wrapper_support import product_layout

from terminal_fixtures import (
    PYQT_ROOT,
    REQUIRED_TERMINAL_PACKAGE_FILES,
)


def test_pyqt_launcher_reexecs_with_sico_python(tmp_path: Path) -> None:
    launcher = PYQT_ROOT / "sico-ai-terminal-pyqt"
    interpreter = tmp_path / "configured-python"
    log = tmp_path / "interpreter-argv"
    interpreter.write_text(
        '#!/bin/sh\nprintf \'%s\\n\' "$@" > "$CAD_AI_TEST_INTERPRETER_LOG"\nexit 17\n',
        encoding="ascii",
    )
    interpreter.chmod(0o755)
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("SICO_", "CAD_"))}
    environment.update(
        {
            "SICO_PYTHON": str(interpreter),
            "CAD_AI_TEST_INTERPRETER_LOG": str(log),
        }
    )

    result = subprocess.run(
        [str(launcher), "--invalid"],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )

    assert result.returncode == 17, result.stderr
    assert log.read_text(encoding="ascii").splitlines() == [
        str(launcher.resolve()),
        "--invalid",
    ]


def test_pyqt_launcher_sico_python_overrides_root_and_exports_installed_paths(
    tmp_path: Path,
) -> None:
    launcher_source = PYQT_ROOT / "sico-ai-terminal-pyqt"
    installed_root = product_layout(tmp_path) / "tools/ai"
    launcher = installed_root / "bin" / "sico-ai-terminal-pyqt"
    installed_python = installed_root / "python"
    installed_package = installed_python / "cad_ai_terminal"
    installed_library = installed_root / "lib"
    python_runtime_library = tmp_path / "python-runtime-lib"
    interpreter = tmp_path / "configured-python"
    ignored = tmp_path / "ignored-root" / "bin" / "python3"
    log = tmp_path / "interpreter-environment"
    launcher.parent.mkdir(parents=True)
    installed_package.mkdir(parents=True)
    installed_library.mkdir()
    python_runtime_library.mkdir()
    ignored.parent.mkdir(parents=True)
    launcher.write_bytes(launcher_source.read_bytes())
    launcher.chmod(0o755)
    ignored.write_text("#!/bin/sh\nexit 99\n", encoding="ascii")
    ignored.chmod(0o755)
    interpreter.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n%s\\n%s\\n%s\\n\' "$1" "$PYTHONPATH" '
        '"$LD_LIBRARY_PATH" "$PYTHONNOUSERSITE" '
        '> "$CAD_AI_TEST_INTERPRETER_LOG"\n'
        "exit 23\n",
        encoding="ascii",
    )
    interpreter.chmod(0o755)
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("SICO_", "CAD_"))}
    environment.update(
        {
            "SICO_PYTHON": str(interpreter),
            "SICO_PYTHON_ROOT": str(ignored.parents[1]),
            "CAD_AI_TEST_INTERPRETER_LOG": str(log),
            "SICO_AI_PYTHON_LIBRARY_PATH": str(python_runtime_library),
            "PYTHONPATH": "/existing/python",
            "LD_LIBRARY_PATH": "/existing/lib",
        }
    )

    result = subprocess.run(
        [str(launcher), "--invalid"],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )

    assert result.returncode == 23, result.stderr
    lines = log.read_text(encoding="ascii").splitlines()
    assert lines[0] == str(launcher.resolve())
    assert lines[1] == str(installed_python)
    assert lines[2].split(os.pathsep) == [
        str(installed_library),
        str(python_runtime_library),
    ]
    assert lines[3] == "1"


def test_installed_launcher_reexecs_even_when_sico_python_is_current(
    tmp_path: Path,
) -> None:
    from pinyin_wrapper_support import dispatcher_environment, product_layout

    launcher_source = PYQT_ROOT / "sico-ai-terminal-pyqt"
    installed_root = product_layout(tmp_path) / "tools/ai"
    launcher = installed_root / "bin" / "sico-ai-terminal-pyqt"
    package = installed_root / "python" / "cad_ai_terminal"
    launcher.parent.mkdir(parents=True)
    package.mkdir(parents=True)
    (installed_root / "lib").mkdir()
    launcher.write_bytes(launcher_source.read_bytes())
    launcher.chmod(0o755)
    for name in REQUIRED_TERMINAL_PACKAGE_FILES:
        (package / name).write_bytes((PYQT_ROOT / "cad_ai_terminal" / name).read_bytes())
    probe = tmp_path / "probe"
    probe.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    probe.chmod(0o755)
    environment = dispatcher_environment()
    environment.update(
        {
            "SICO_PYTHON": sys.executable,
            "LD_LIBRARY_PATH": "/existing/lib",
            "QT_QPA_PLATFORM": "offscreen",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    result = subprocess.run(
        [str(launcher), "--working-directory", str(tmp_path), "--", str(probe)],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )

    assert result.returncode == 2, result.stderr
    assert "PyQt5 and the QTermWidget 1.4 SIP binding are required" in result.stderr


def test_pyqt_launcher_uses_sico_python_root(tmp_path: Path) -> None:
    launcher = PYQT_ROOT / "sico-ai-terminal-pyqt"
    python_root = tmp_path / "python-root"
    interpreter = python_root / "bin" / "python3"
    log = tmp_path / "interpreter-root-argv"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text(
        '#!/bin/sh\nprintf \'%s\\n\' "$@" > "$CAD_AI_TEST_INTERPRETER_LOG"\nexit 19\n',
        encoding="ascii",
    )
    interpreter.chmod(0o755)
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("SICO_", "CAD_"))}
    environment.pop("SICO_PYTHON", None)
    environment.update(
        {
            "SICO_PYTHON_ROOT": str(python_root),
            "CAD_AI_TEST_INTERPRETER_LOG": str(log),
        }
    )

    result = subprocess.run(
        [str(launcher), "--invalid"],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )

    assert result.returncode == 19, result.stderr
    assert log.read_text(encoding="ascii").splitlines() == [
        str(launcher.resolve()),
        "--invalid",
    ]
