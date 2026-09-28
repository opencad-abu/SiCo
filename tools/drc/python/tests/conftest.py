from __future__ import annotations

import sys
from pathlib import Path


PYTHON_ROOT = Path(__file__).resolve().parents[1]
CAD_ROOT = Path(__file__).resolve().parents[3]
RCE_PYTHON_ROOT = CAD_ROOT / "rce" / "python"
COMMON_PYTHON_ROOT = CAD_ROOT / "common" / "python"
for root in (PYTHON_ROOT, RCE_PYTHON_ROOT, COMMON_PYTHON_ROOT, CAD_ROOT / "utility"):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
