from __future__ import annotations

import sys
from pathlib import Path

PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

UTILITY_ROOT = Path(__file__).resolve().parents[3] / "utility"
if str(UTILITY_ROOT) not in sys.path:
    sys.path.insert(0, str(UTILITY_ROOT))
