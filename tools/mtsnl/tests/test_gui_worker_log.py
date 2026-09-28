from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt5")

from PyQt5.QtWidgets import QApplication  # noqa: E402

from mtsnetlistor.gui.controller import CATALOG_LOG_PREFIX  # noqa: E402
from mtsnetlistor.gui.main_window import MtsMainWindow  # noqa: E402


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("channel", ("OCEAN", "dbAccess"))
def test_replay_echo_is_hidden_but_diagnostics_remain(application, channel):
    window = MtsMainWindow(session=None)
    prefix = CATALOG_LOG_PREFIX if channel == "dbAccess" else ""
    transcript = (
        "\\p > \n"
        "\\i simulator('spectre)\n"
        '\\i design("source" "cell" "schematic" "r")\n'
        "\\i modelFile(\n"
        '\\i   \'("/pdk/model.scs" "tt")\n'
        "\\i )\n"
        "\\i procedure(mtsDefaultsSafeCurrentSession()\n"
        '\\i   error("not an actual failure")\n'
        "\\i )\n"
        '\\i printf("MTS_NETLISTOR_RESULT_BEGIN\\n")\n'
        "\\i mtsNetlist=createNetlist(?recreateAll t ?display nil)\n"
        '  \\a printf("INFO input echo")\n'
        "\\t mtsDefaultsSafeCurrentSession\n"
        '\\r "INFO return echo"\n'
        "\\o INFO createNetlist started\n"
        "\\o Loading PDK interface libInit.il\n"
        "\\w *WARNING* Model section missing\n"
        "\\w Select another section before retrying.\n"
        "\\e *Error* design: cannot open cellview\n"
        "\\e Check the library mapping.\n"
    )
    try:
        # Prefixes and echoed source may be split across worker output chunks.
        for offset in range(0, len(transcript), 7):
            window._receive_worker_log(prefix + transcript[offset : offset + 7])
            window._drain_worker_logs()
        window._drain_worker_logs(flush_tail=True)
        rendered = window.log.toPlainText()
        for hidden in (
            "simulator('",
            "design(",
            "modelFile(",
            "procedure(",
            "printf(",
            "mtsNetlist=",
            "not an actual failure",
            "input echo",
            "return echo",
            "mtsDefaultsSafeCurrentSession",
            "MTS_NETLISTOR_RESULT_BEGIN",
        ):
            assert hidden not in rendered
        for visible in (
            "INFO createNetlist started",
            "*WARNING* Model section missing",
            "Select another section before retrying.",
            "*Error* design: cannot open cellview",
            "Check the library mapping.",
        ):
            assert f"{channel}: {visible}" in rendered
        assert f"{channel}: >" not in rendered
        # Flush a final runtime line without newline, as task completion does.
        result = (
            "catalog completed"
            if channel == "dbAccess"
            else "MTS_NETLISTOR_RAW=/tmp/private/input.scs"
        )
        window._receive_worker_log(prefix + "\\o " + result)
        window._drain_worker_logs(flush_tail=True)
        assert f"{channel}: {result}" in window.log.toPlainText()
    finally:
        window.close()
        window.controller.close()
