"""Compatibility test entry for LSF GUI source acceptance.

Case modules use ``*_cases.py`` names so directory collection executes each
case once. Retire this entry after test commands migrate to those modules.
"""

from cadlsf_gui_fixtures import application as application

from cadlsf_gui_models_cases import (
    test_table_models_expose_load_values_progress_and_recommendation as test_table_models_expose_load_values_progress_and_recommendation,
    test_cpu_progress_threshold_boundaries as test_cpu_progress_threshold_boundaries,
    test_memory_progress_threshold_boundaries as test_memory_progress_threshold_boundaries,
    test_memory_without_total_keeps_available_value_without_alert as test_memory_without_total_keeps_available_value_without_alert,
)

from cadlsf_gui_window_cases import (
    test_host_filter_and_progress_delegate_render_nonblank as test_host_filter_and_progress_delegate_render_nonblank,
    test_job_model_filter_and_window_tab_layout as test_job_model_filter_and_window_tab_layout,
    test_window_refresh_pause_failure_and_close_lifecycle as test_window_refresh_pause_failure_and_close_lifecycle,
)

from cadlsf_gui_jobs_cases import (
    test_job_kill_button_maps_proxy_row_confirms_and_refreshes as test_job_kill_button_maps_proxy_row_confirms_and_refreshes,
    test_job_kill_failure_preserves_snapshot as test_job_kill_failure_preserves_snapshot,
    test_failed_job_refresh_preserves_rows_and_unavailable_status as test_failed_job_refresh_preserves_rows_and_unavailable_status,
)

from cadlsf_gui_selector_cases import (
    test_selector_publishes_only_latest_verified_auto_or_host as test_selector_publishes_only_latest_verified_auto_or_host,
    test_selector_cancel_and_stale_snapshot_do_not_publish as test_selector_cancel_and_stale_snapshot_do_not_publish,
    test_selector_rejects_selection_changed_during_live_validation as test_selector_rejects_selection_changed_during_live_validation,
)

from cadlsf_gui_refresh_cases import (
    test_refresh_controller_discards_stale_result as test_refresh_controller_discards_stale_result,
    test_refresh_controller_publishes_cached_snapshot_before_topology_refresh as test_refresh_controller_publishes_cached_snapshot_before_topology_refresh,
)
import os
import subprocess
import sys
from pathlib import Path


def test_family_style_follows_the_project_appearance(
        application, tmp_path, monkeypatch):
    from PyQt5.QtCore import QSettings
    from PyQt5.QtGui import QColor, QPalette

    from cadgui.appearance import (
        AppearancePreferences,
        apply_application_appearance,
        save_appearance,
    )
    from cadgui.chrome import apply_family_style
    from cadgui.palette import DARK, active
    from cadlsf.gui.app import apply_application_style

    state = tmp_path / ".sico"
    state.mkdir(mode=0o700)
    path = state / "ai/agent/workspace.ini"
    path.parent.mkdir(parents=True)
    settings = QSettings(str(path), QSettings.IniFormat)
    save_appearance(settings, AppearancePreferences("dark", 16, 10))
    monkeypatch.setenv("SICO_TEMP_DIR", str(state))
    point = application.font().pointSize()
    try:
        apply_application_style(application, QColor, QPalette)
        assert active().name == "dark"
        assert DARK.background in application.styleSheet()
        apply_family_style(application)
        assert application.palette().color(QPalette.ToolTipBase).name() == \
            DARK.background.lower()
        assert application.palette().color(QPalette.ToolTipText).name() == \
            DARK.text.lower()
    finally:
        apply_application_appearance(
            application, AppearancePreferences("light", point, point))


def test_gui_bootstrap_defers_qt_until_the_monitor_command():
    root = Path(__file__).resolve().parents[3]
    environment = dict(
        os.environ,
        PYTHONPATH=str(root / "common/python"),
        PYTHONNOUSERSITE="1",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            (
                "import cadlsf.gui.app as app\n"
                "try:\n"
                "    app._import_qt()\n"
                "except RuntimeError as exc:\n"
                "    assert 'PyQt5 is unavailable' in str(exc)\n"
                "else:\n"
                "    raise SystemExit('PyQt5 unexpectedly available')\n"
            ),
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
