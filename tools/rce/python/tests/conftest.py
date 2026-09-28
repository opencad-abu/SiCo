from __future__ import annotations

import sys
from pathlib import Path

import pytest

PYTHON_ROOT = Path(__file__).resolve().parents[1]
CAD_ROOT = Path(__file__).resolve().parents[3]
COMMON_PYTHON_ROOT = CAD_ROOT / "common" / "python"
for root in (PYTHON_ROOT, COMMON_PYTHON_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


@pytest.fixture(autouse=True)
def _clear_rce_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep process-level RCE policy variables from leaking into unit tests."""
    monkeypatch.delenv("RCE_QUANTUS_BLOCK_CELL_FILE", raising=False)
    monkeypatch.delenv("RCE_QREDUCE", raising=False)

# Internal source-contract helpers are not runtime dependencies.
UTILITY_ROOT = Path(__file__).resolve().parents[3] / "utility"
if str(UTILITY_ROOT) not in sys.path:
    sys.path.insert(0, str(UTILITY_ROOT))
