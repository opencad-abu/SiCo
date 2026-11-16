from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOT = ROOT.parents[1]
PYTHON = ROOT / "python"
if str(PYTHON) not in sys.path:
    sys.path.insert(0, str(PYTHON))
COMMON = ROOT.parent / "common/python"
if str(COMMON) not in sys.path:
    sys.path.insert(0, str(COMMON))


def _configure_production_environment() -> None:
    """Pin current interpreter names; retired spellings are never test inputs."""

    for retired in ("CAD_HOME", "CAD_PYTHON_ROOT", "CAD_PYTHON"):
        os.environ.pop(retired, None)
    os.environ.setdefault("SICO_HOME", str(PRODUCT_ROOT))
    os.environ.setdefault(
        "SICO_PYTHON", str(PRODUCT_ROOT / "runtime/python/bin/python3")
    )


_configure_production_environment()
