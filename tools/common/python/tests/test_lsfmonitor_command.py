from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


SICO_TOOLS_ROOT = Path(__file__).resolve().parents[3]
LAUNCHER = SICO_TOOLS_ROOT.parent / "bin/lsfmonitor"
ENTRY_RELATIVE = "tools/common/python/sico-lsf"


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


def _installation(tmp_path: Path, *, entry=True):
    root = tmp_path / "sico"
    (root / "bin").mkdir(parents=True)
    (root / "etc/config").mkdir(parents=True)
    (root / "tools/common/sh").mkdir(parents=True)
    shutil.copy2(LAUNCHER, root / "bin/lsfmonitor")
    shutil.copy2(
        SICO_TOOLS_ROOT / "common/sh/sico-installation.sh",
        root / "tools/common/sh/sico-installation.sh",
    )
    shutil.copy2(
        SICO_TOOLS_ROOT.parent / "etc/config/sico-install.json",
        root / "etc/config/sico-install.json",
    )
    if entry:
        target = root / ENTRY_RELATIVE
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="ascii")
    return root


def _environment(root: Path, **overrides: str) -> dict[str, str]:
    environment = {
        "PATH": "/usr/bin:/bin",
        "SICO_HOME": str(root),
        "LD_LIBRARY_PATH": "/eda/tool/lib",
        "PYTHONHOME": "/bad/home",
        "PYTHONPATH": "/bad/path",
        "LD_PRELOAD": "/bad/preload.so",
        "LD_AUDIT": "/bad/audit.so",
    }
    environment.update(overrides)
    return environment


def test_lsfmonitor_uses_only_the_installation_python(tmp_path: Path) -> None:
    root = _installation(tmp_path)
    python = _interpreter(root / "runtime/python/bin/python3")

    result = subprocess.run(
        [str(root / "bin/lsfmonitor"), "--queue", "queue with spaces",
         "--refresh-interval", "30"],
        env=_environment(
            root,
            SICO_PYTHON="/stale/python3",
            SICO_PYTHON_ROOT="/stale/python",
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
        f"ld={root}/runtime/python/lib:{root}/lib",
        "original_ld=/eda/tool/lib",
        "dontwrite=1",
        "nousersite=1",
        "ld_preload=unset",
        "ld_audit=unset",
        "argument=-s",
        f"argument={root / ENTRY_RELATIVE}",
        "argument=monitor",
        "argument=--queue",
        "argument=queue with spaces",
        "argument=--refresh-interval",
        "argument=30",
    ]


def test_lsfmonitor_rejects_missing_installation_python(tmp_path: Path) -> None:
    root = _installation(tmp_path)

    result = subprocess.run(
        [str(root / "bin/lsfmonitor")],
        env=_environment(root, SICO_PYTHON="/bin/sh"),
        check=False,
        text=True,
        capture_output=True,
        timeout=10,
    )

    assert result.returncode == 2
    assert "SiCo Python is unavailable" in result.stderr


def test_lsfmonitor_preserves_existing_original_library_path(tmp_path: Path) -> None:
    root = _installation(tmp_path)
    _interpreter(root / "runtime/python/bin/python3")
    result = subprocess.run(
        [str(root / "bin/lsfmonitor")],
        env=_environment(
            root,
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
    root = _installation(tmp_path, entry=False)
    _interpreter(root / "runtime/python/bin/python3")
    expected = root / ENTRY_RELATIVE

    result = subprocess.run(
        [str(root / "bin/lsfmonitor")],
        env=_environment(root),
        check=False,
        text=True,
        capture_output=True,
        timeout=10,
    )

    assert result.returncode == 2
    assert result.stderr.strip().endswith(
        f"lsfmonitor: Python entry is unavailable: {expected}"
    )
