"""Re-exec a source entry with the installation-owned Python interpreter."""

from __future__ import annotations

import os
from pathlib import Path
import sys


def ensure_runtime(entry, root):
    """Exec ``$SICO_HOME/runtime/python/bin/python3`` unless already selected."""
    entry = Path(entry).resolve()
    if os.environ.pop("SICO_RUNTIME_BOOTSTRAP", None) == str(entry):
        return
    root = Path(root).resolve()
    python = root / "runtime/python/bin/python3"
    if not python.is_file() or not os.access(python, os.X_OK):
        print(f"SiCo Python is unavailable: {python}", file=sys.stderr)
        raise SystemExit(2)
    if os.path.realpath(python) == os.path.realpath(sys.executable):
        return
    environment = dict(os.environ)
    for name in ("PYTHONHOME", "PYTHONPATH", "LD_PRELOAD", "LD_AUDIT"):
        environment.pop(name, None)
    environment.update(
        SICO_PYTHON=str(python),
        SICO_PYTHON_ROOT=str(root / "runtime/python"),
        SICO_SYSTEM_LD_LIBRARY_PATH=os.pathsep.join(
            (str(root / "runtime/python/lib"), str(root / "lib"))
        ),
        PYTHONNOUSERSITE="1",
        PYTHONDONTWRITEBYTECODE="1",
        SICO_RUNTIME_BOOTSTRAP=str(entry),
    )
    os.execve(str(python), [str(python), "-s", str(entry), *sys.argv[1:]], environment)
