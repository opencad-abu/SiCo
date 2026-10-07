"""Command-line entry point for the PyQt5 terminal."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from cadgui.environment import check_xcb_runtime, prepare_qt_environment
from sicoenv import value as environment_value

from .protocol import parse_launch_options
from sicotemp import initialize

def _restore_qt_plugin_path() -> None:
    """Expose an explicitly configured production Qt plugin root."""
    candidates: list[Path] = []
    configured = environment_value(os.environ, "SICO_AI_QT_PLUGIN_PATH",
                                   ("CAD_AI_QT_PLUGIN_PATH",), "").strip()
    if configured:
        for value in configured.split(os.pathsep):
            candidate = Path(value).expanduser()
            if (
                candidate.is_absolute()
                and candidate.is_dir()
                and candidate not in candidates
            ):
                candidates.append(candidate)
    if candidates:
        os.environ["QT_PLUGIN_PATH"] = os.pathsep.join(map(str, candidates))


def main(arguments: list[str] | None = None) -> int:
    values = sys.argv[1:] if arguments is None else arguments
    try:
        initialize()
        options = parse_launch_options(values)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"sico-ai-terminal: {exc}", file=sys.stderr)
        return 2

    prepare_qt_environment()
    _restore_qt_plugin_path()
    try:
        check_xcb_runtime()
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"sico-ai-terminal: {exc}", file=sys.stderr)
        return 2

    from .qt_frontend import run_frontend

    from sico_pinyin import input_session

    with input_session():
        return run_frontend(options)


if __name__ == "__main__":
    raise SystemExit(main())
