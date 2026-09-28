from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


SICO_TOOLS_ROOT = Path(__file__).resolve().parents[3]
LAUNCHER = SICO_TOOLS_ROOT.parent / "bin/lsfmonitor"
ENTRY = SICO_TOOLS_ROOT / "common/python/sico-lsf"


def _interpreter(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/sh\n"
        "printf 'interpreter=%s\\n' \"$0\"\n"
        "printf 'pythonhome=%s\\n' \"${PYTHONHOME-unset}\"\n"
        "printf 'pythonpath=%s\\n' \"${PYTHONPATH-unset}\"\n"
        "printf 'ld=%s\\n' \"${LD_LIBRARY_PATH-unset}\"\n"
        "printf 'original_ld=%s\\n' "
        "\"${SICO_LSF_MONITOR_ORIG_LD_LIBRARY_PATH-unset}\"\n"
        "printf 'dontwrite=%s\\n' \"${PYTHONDONTWRITEBYTECODE-unset}\"\n"
        "printf 'nousersite=%s\\n' \"${PYTHONNOUSERSITE-unset}\"\n"
        "printf 'ld_preload=%s\\n' \"${LD_PRELOAD-unset}\"\n"
        "printf 'ld_audit=%s\\n' \"${LD_AUDIT-unset}\"\n"
        "for argument in \"$@\"; do\n"
        "    printf 'argument=%s\\n' \"$argument\"\n"
        "done\n",
        encoding="ascii",
    )
    path.chmod(0o755)
    return path


def _environment(**overrides: str) -> dict[str, str]:
    environment = dict(os.environ)
    for name in (
        "SICO_LSF_MONITOR_ORIG_LD_LIBRARY_PATH",
        "SICO_PYTHON",
        "SICO_PYTHON_ROOT",
        "SICO_SYSTEM_LD_LIBRARY_PATH",
    ):
        environment.pop(name, None)
        environment.pop("CAD_" + name[5:], None)
    environment.pop("CAD_HOME", None)
    environment.pop("SICO_HOME", None)
    environment.update(overrides)
    return environment


def test_lsfmonitor_uses_sico_python_and_forwards_monitor_options(
    tmp_path: Path,
) -> None:
    python = _interpreter(tmp_path / "python")

    result = subprocess.run(
        [str(LAUNCHER), "--queue", "queue with spaces", "--refresh-interval", "30"],
        env=_environment(
            SICO_PYTHON=str(python),
            SICO_SYSTEM_LD_LIBRARY_PATH="/cad/python/lib",
            LD_LIBRARY_PATH="/eda/tool/lib",
            PYTHONHOME="/bad/home",
            PYTHONPATH="/bad/path",
            LD_PRELOAD="/bad/preload.so",
            LD_AUDIT="/bad/audit.so",
        ),
        check=False,
        text=True,
        capture_output=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"interpreter={python}",
        "pythonhome=unset",
        "pythonpath=unset",
        "ld=/cad/python/lib",
        "original_ld=/eda/tool/lib",
        "dontwrite=1",
        "nousersite=1",
        "ld_preload=unset",
        "ld_audit=unset",
        "argument=-s",
        f"argument={ENTRY}",
        "argument=monitor",
        "argument=--queue",
        "argument=queue with spaces",
        "argument=--refresh-interval",
        "argument=30",
    ]


def test_lsfmonitor_uses_sico_python_root(tmp_path: Path) -> None:
    python_root = tmp_path / "python-root"
    python = _interpreter(python_root / "bin/python3")

    result = subprocess.run(
        [str(LAUNCHER), "--help"],
        env=_environment(SICO_PYTHON_ROOT=str(python_root)),
        check=False,
        text=True,
        capture_output=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert f"interpreter={python}" in result.stdout.splitlines()
    assert result.stdout.splitlines()[-4:] == [
        "argument=-s",
        f"argument={ENTRY}",
        "argument=monitor",
        "argument=--help",
    ]


def test_lsfmonitor_preserves_existing_original_library_path(
    tmp_path: Path,
) -> None:
    python = _interpreter(tmp_path / "python")
    result = subprocess.run(
        [str(LAUNCHER)],
        env=_environment(
            SICO_PYTHON=str(python),
            SICO_LSF_MONITOR_ORIG_LD_LIBRARY_PATH="/eda/original/lib",
            LD_LIBRARY_PATH="/already/sanitized/lib",
        ),
        check=False,
        text=True,
        capture_output=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert "original_ld=/eda/original/lib" in result.stdout.splitlines()


def test_lsfmonitor_reports_missing_entry(tmp_path: Path) -> None:
    installed = tmp_path / "bin/lsfmonitor"
    installed.parent.mkdir(parents=True)
    shutil.copy2(LAUNCHER, installed)
    support = tmp_path / "tools/common/sh/sico-installation.sh"
    support.parent.mkdir(parents=True)
    shutil.copy2(SICO_TOOLS_ROOT / "common/sh/sico-installation.sh", support)
    marker = tmp_path / "etc/config/sico-install.json"
    marker.parent.mkdir(parents=True)
    shutil.copy2(SICO_TOOLS_ROOT.parent / "etc/config/sico-install.json", marker)
    expected = tmp_path / "tools/common/python/sico-lsf"

    result = subprocess.run(
        [str(installed)],
        env=_environment(SICO_PYTHON="/bin/sh"),
        check=False,
        text=True,
        capture_output=True,
        timeout=10,
    )

    assert result.returncode == 2
    assert result.stderr.strip() == (
        f"lsfmonitor: Python entry is unavailable: {expected}"
    )
