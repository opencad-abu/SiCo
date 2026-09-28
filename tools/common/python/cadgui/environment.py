"""Environment isolation for PyQt applications launched by EDA tools."""

from __future__ import annotations

import ctypes
import os
from typing import MutableMapping

from sicopaths import installation


QT_ENV_VARIABLES = (
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    "QT_PLUGIN_PATH",
    "QT_DIR",
    "QT_SELECT",
    "QML2_IMPORT_PATH",
    "QT_XCB_NO_XI2",
    "QT_XCB_NO_XI2_MOUSE",
)
_XCB_RUNTIME_HANDLE: object | None = None


def prepare_qt_environment(
    environ: MutableMapping[str, str] | None = None,
    *,
    variables: tuple[str, ...] = QT_ENV_VARIABLES,
) -> tuple[str, ...]:
    """Remove Qt settings that must not leak from Virtuoso or another tool."""
    target = os.environ if environ is None else environ
    removed = tuple(name for name in variables if name in target)
    for name in removed:
        target.pop(name, None)
    return removed


def _needs_xcb(environ: MutableMapping[str, str] | None = None) -> bool:
    target = os.environ if environ is None else environ
    platform = target.get("QT_QPA_PLATFORM", "").split(":", 1)[0].casefold()
    if platform:
        return platform == "xcb"
    return bool(target.get("DISPLAY"))


def check_xcb_runtime(environ: MutableMapping[str, str] | None = None) -> None:
    """Fail before Qt aborts when the XCB cursor runtime is unavailable."""
    global _XCB_RUNTIME_HANDLE

    if not _needs_xcb(environ):
        return
    target = os.environ if environ is None else environ
    candidates: list[str] = []
    if "SICO_HOME" in target or "CAD_HOME" in target:
        bundled = installation(target).path("lib/libxcb-cursor.so.0")
        if bundled.is_file():
            candidates.append(str(bundled))
    candidates.append("libxcb-cursor.so.0")

    errors: list[OSError] = []
    for candidate in candidates:
        try:
            _XCB_RUNTIME_HANDLE = ctypes.CDLL(candidate)
            return
        except OSError as exc:
            errors.append(exc)
    raise RuntimeError(
        "Qt X11 requires libxcb-cursor.so.0. SiCo GUI startup checked "
        "$SICO_HOME/lib first and then the system runtime. Install "
        "xcb-util-cursor (or libxcb-cursor0) for this host. Do not add an "
        "Ansys Qt library directory to LD_LIBRARY_PATH because that mixes "
        "Qt runtimes."
    ) from errors[-1]
