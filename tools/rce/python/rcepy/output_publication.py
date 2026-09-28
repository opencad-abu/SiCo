"""Publish validated extraction outputs under their configured stable names."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable


def publish_outputs(
    publications: tuple[tuple[Path, Path], ...], info: Callable[[str], None],
) -> None:
    for native, published in publications:
        if native == published:
            continue
        if not native.is_file():
            raise RuntimeError(f"Cannot publish missing extracted netlist: {native}")
        published.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(native, published)
        except OSError as exc:
            raise RuntimeError(
                f"Cannot publish extracted netlist {native} as {published}: {exc}"
            )
        info(f"Published corner netlist: {published}")
