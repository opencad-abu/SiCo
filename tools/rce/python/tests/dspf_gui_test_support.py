from __future__ import annotations

import time
from pathlib import Path
from typing import Callable


_APPLICATION = None

SMALL_DSPF = """*|DSPF 1.3
.SUBCKT top A B
*|GROUND_NET 0
*|NET A 2p
*|P (A I 0 0 0)
*|S (A:1 0 0)
R1 A A:1 10
C1 A:1 0 1p
C2 A:1 B:1 0.25p
*|NET B 1p
*|P (B O 0 10 0)
*|S (B:1 10 0)
R2 B B:1 20
C3 B:1 0 0.5p
.ENDS top
"""


def write_small_dspf(path: Path) -> Path:
    path.write_text(SMALL_DSPF, encoding="ascii")
    return path


def application():
    from PyQt5.QtWidgets import QApplication

    global _APPLICATION
    _APPLICATION = QApplication.instance() or QApplication([])
    return _APPLICATION


def wait_until(predicate: Callable[[], bool], *, timeout: float = 8.0) -> bool:
    app = application()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return bool(predicate())
