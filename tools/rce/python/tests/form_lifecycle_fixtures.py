from __future__ import annotations

import os
from pathlib import Path

CAD_ROOT = Path(__file__).resolve().parents[3]
RUN_PROBE = os.environ.get("RCE_RUN_SKILL_PROBE") == "1"


def _skill_literal(path: Path) -> str:
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _install_tree(tmp_path: Path) -> Path:
    install = tmp_path / "install"
    install.mkdir()
    (install / "tools").symlink_to(CAD_ROOT, target_is_directory=True)
    return install
