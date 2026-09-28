"""DRC selector application lifecycle and legacy public GUI imports.

The CLI imports this entry only for rule-select-gui. Re-exports hold no state;
remove them in the next incompatible API version once supported callers use
the rule_selection, model, view, dialog, style and lifecycle owners."""

from __future__ import annotations

import sys
from pathlib import Path
from cadgui.branding import logo_text
from cadgui.environment import check_xcb_runtime, prepare_qt_environment
from cadgui.lifecycle import finalize_gui_exit as _finalize_gui_exit, install_parent_monitor
from .rule_select import discover_rule_groups
from .rule_selection import RuleSelection, read_initial_selection, write_selection
from .rule_select_lifecycle import capture_parent_identity
from .rule_select_qt import QApplication
from .rule_select_model import RuleSelectModel
from .rule_select_view import RuleSelectFilterProxyModel
from .rule_select_dialog import RuleSelectDialog
from .rule_select_style import apply_application_style


def run_gui(
    rule_file: str | Path,
    *,
    output_path: str | Path | None = None,
    initial_path: str | Path | None = None,
    output: str | Path | None = None,
    initial: str | Path | None = None,
    calibre: str | Path | None = None,
    cache_dir: str | Path | None = None,
    timeout: float = 60.0,
    expand_tvf: bool = True,
    parent_pid: int | str | None = None,
) -> int:
    """Discover rules, display the selector, and publish only an Apply result."""
    if output_path is not None and output is not None:
        raise ValueError("Specify only one Rule Select output path")
    result_path = output_path if output_path is not None else output
    if result_path is None:
        raise ValueError("A Rule Select output path is required")
    if initial_path is not None and initial is not None:
        raise ValueError("Specify only one Rule Select initial-selection path")
    selection_path = initial_path if initial_path is not None else initial
    prepare_qt_environment()
    check_xcb_runtime()
    parent_identity = capture_parent_identity(parent_pid)
    initial_selection = read_initial_selection(selection_path)
    application = QApplication.instance() or QApplication(sys.argv[:1])
    application.setApplicationName("DRC Rule Select")
    application.setOrganizationName(logo_text())
    apply_application_style(application)

    groups = discover_rule_groups(
        rule_file,
        calibre=calibre,
        timeout=timeout,
        cache_dir=cache_dir,
        expand_tvf=expand_tvf,
    )
    dialog = RuleSelectDialog(
        groups,
        output_path=result_path,
        initial=initial_selection,
        rule_file=rule_file,
    )
    dialog.show()
    parent_monitor = install_parent_monitor(application, parent_identity, dialog.reject)
    # Keep the timer alive for the whole local event loop.
    dialog._parent_monitor = parent_monitor
    if not dialog.isVisible():
        return _finalize_gui_exit(0, parent_identity)
    status = application.exec_()
    return _finalize_gui_exit(status, parent_identity)


__all__ = [
    "RuleSelectDialog", "RuleSelectFilterProxyModel", "RuleSelectModel", "RuleSelection",
    "apply_application_style", "capture_parent_identity", "install_parent_monitor",
    "prepare_qt_environment", "read_initial_selection", "run_gui", "write_selection",
]
