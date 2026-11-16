"""Shared launcher/input fixtures for pinyin wrapper source tests."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import shlex
import sys
import time
from pathlib import Path

DISPATCHER_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "terminal"
    / "scripts"
    / "sico-ai-terminal-dispatcher.sh"
)
PINYIN_SOURCE = DISPATCHER_SOURCE.parents[2] / "bin" / "sico-ai-pinyin"
PYTHON_ADAPTER = (
    '#!/bin/bash\nif [[ ${1:-} == -E ]]; then exec '
    + shlex.quote(sys.executable) + ' "$@"; fi\n'
)


def write_executable(path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="ascii")
    path.chmod(0o755)


def product_layout(tmp_path):
    from sicopaths import IDENTITY_BYTES, MARKER
    root = tmp_path / "product"
    (root / MARKER).parent.mkdir(parents=True)
    (root / MARKER).write_bytes(IDENTITY_BYTES)
    (root / "bin").mkdir()
    shell = root / "tools/common/sh/sico-installation.sh"
    shell.parent.mkdir(parents=True)
    source = DISPATCHER_SOURCE.parents[4] / "tools/common/sh/sico-installation.sh"
    shutil.copyfile(source, shell)
    shutil.copyfile(source.with_name("sico-temporary.sh"), shell.with_name("sico-temporary.sh"))
    common = root / "tools/common/python"
    common.mkdir()
    for name in ("sicoenv", "sicopaths", "sicoresources", "sicostate", "sicomigration",
                 "sicotemp", "sicoprocess", "cadenv"):
        shutil.copyfile(source.parents[1] / "python" / (name + ".py"), common / (name + ".py"))
    shutil.copytree(source.parents[1] / "python/cadgui", common / "cadgui",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(source.parents[1] / "python/sico_pinyin", common / "sico_pinyin",
                    ignore=shutil.ignore_patterns("__pycache__"))
    write_executable(root / "bin/test-python", PYTHON_ADAPTER + 'exec /bin/bash "$@"\n')
    return root


def install_dispatcher(tmp_path: Path) -> Path:
    product_root = product_layout(tmp_path)
    install_root = product_root / "tools/ai"
    launcher = install_root / "bin" / "sico-ai-terminal"
    launcher.parent.mkdir(parents=True)
    shutil.copy2(DISPATCHER_SOURCE, launcher)
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
    pinyin = launcher.with_name("sico-ai-pinyin")
    shutil.copy2(PINYIN_SOURCE, pinyin)
    pinyin.chmod(pinyin.stat().st_mode | stat.S_IXUSR)

    terminal = launcher.with_name("sico-ai-terminal-pyqt")
    write_executable(terminal, "#!/bin/bash\nexit 0\n")
    data = product_root / "share" / "qtermwidget5"
    (data / "kb-layouts").mkdir(parents=True)
    (data / "color-schemes").mkdir()
    (data / "kb-layouts" / "default.keytab").write_text("keymap\n", encoding="ascii")
    (data / "color-schemes" / "BreezeModified.colorscheme").write_text(
        "scheme\n", encoding="ascii"
    )
    for platform in ("rhel7", "rhel8"):
        runtime = install_root / "runtime" / platform
        (runtime / "python").mkdir(parents=True)
        (runtime / "lib").mkdir()
        (runtime / "python" / "QTermWidget.abi3.so").write_bytes(b"binding")
        (runtime / "lib" / "libqtermwidget5.so.1").write_bytes(b"library")
    return launcher


def dispatcher_environment(**overrides: str) -> dict[str, str]:
    environment = os.environ.copy()
    for name in (
        "SICO_HOME", "CAD_HOME", "SICO_ROOT", "CAD_AGENT_ROOT",
        "SICO_AI_PLATFORM",
        "SICO_AI_OS_RELEASE_FILE",
        "CAD_TEMP_DIR",
        "SICO_TEMP_DIR", "SICO_PYTHON", "SICO_PYTHON_ROOT",
        "TMPDIR",
        "TMP",
        "TEMP",
        "SQLITE_TMPDIR",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_RUNTIME_DIR",
        "GSETTINGS_BACKEND",
        "PYTHONDONTWRITEBYTECODE",
        "SICO_PYTHON",
        "SICO_PYTHON_ROOT",
        "DBUS_SESSION_BUS_ADDRESS",
        "IBUS_ADDRESS",
        "IBUS_ADDRESS_FILE",
        "IBUS_USE_PORTAL",
        "QT_IM_MODULE",
        "XMODIFIERS",
        "LSB_JOBID",
    ):
        environment.pop(name, None)
    environment.update(
        SICO_AI_PLATFORM="rhel8",
        SICO_PYTHON=overrides.pop("SICO_PYTHON", "/bin/bash"),
        **overrides,
    )
    return environment


def run_dispatcher(
    launcher: Path,
    *arguments: str,
    pass_fds: tuple[int, ...] = (),
    **overrides: str,
) -> subprocess.CompletedProcess[str]:
    overrides.setdefault("SICO_PYTHON", str(launcher.parents[3] / "bin/test-python"))
    return subprocess.run(
        [str(launcher), *arguments],
        check=False,
        text=True,
        capture_output=True,
        env=dispatcher_environment(**overrides),
        cwd=launcher.parents[3].parent,
        pass_fds=pass_fds,
        timeout=10,
    )
