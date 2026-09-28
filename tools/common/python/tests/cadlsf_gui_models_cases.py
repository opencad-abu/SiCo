"""LSF GUI models behavior cases."""

from __future__ import annotations

from cadlsf_gui_fixtures import application as application, _snapshot
import pytest
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication
from cadlsf.gui.models import (
    HostTableModel, QueueTableModel, PROGRESS_ROLE, PROGRESS_WARNING,
    PROGRESS_NORMAL, PROGRESS_CRITICAL,
)
from cadlsf.model import HostInfo


def test_table_models_expose_load_values_progress_and_recommendation(
    application: QApplication,
) -> None:
    queue_model = QueueTableModel()
    queue_model.set_rows(_snapshot().queues)
    assert queue_model.data(queue_model.index(0, 0)) == "normal"
    assert queue_model.data(queue_model.index(0, 4)) == "20"
    queue_model.sort(4, Qt.AscendingOrder)
    assert queue_model.data(queue_model.index(0, 0)) == "batch"

    host_model = HostTableModel()
    host_model.set_rows(_snapshot().hosts)
    assert host_model.data(host_model.index(1, 0)) == "1"
    assert host_model.data(host_model.index(0, 3), PROGRESS_ROLE) == (
        0.875,
        "14 / 16",
    )
    assert host_model.data(host_model.index(0, 8), PROGRESS_ROLE) == (
        0.8,
        "80%",
        PROGRESS_WARNING,
    )
    assert host_model.data(host_model.index(1, 8), PROGRESS_ROLE) == (
        0.2,
        "20%",
        PROGRESS_NORMAL,
    )
    memory_ratio, memory_text = host_model.data(
        host_model.index(1, 9), PROGRESS_ROLE
    )[:2]
    assert memory_ratio == pytest.approx(0.2)
    assert memory_text == "20%"
    assert host_model.data(host_model.index(0, 9), PROGRESS_ROLE) == (
        0.875,
        "88%",
        PROGRESS_CRITICAL,
    )
    assert host_model.data(host_model.index(0, 9), Qt.ToolTipRole) == (
        "4.0 GiB available of 32.0 GiB total"
    )
    assert "14 free slots" in host_model.data(host_model.index(1, 11))


@pytest.mark.parametrize(
    ("ratio", "expected"),
    (
        (0.499, PROGRESS_NORMAL),
        (0.5, PROGRESS_WARNING),
        (0.8, PROGRESS_WARNING),
        (0.801, PROGRESS_CRITICAL),
    ),
)
def test_cpu_progress_threshold_boundaries(
    application: QApplication, ratio: float, expected: str
) -> None:
    model = HostTableModel()
    model.set_rows((HostInfo("node", "ok", True, cpu_utilization=ratio),))

    assert model.data(model.index(0, 8), PROGRESS_ROLE)[2] == expected


@pytest.mark.parametrize(
    ("available", "expected"),
    (
        (51, PROGRESS_NORMAL),
        (50, PROGRESS_WARNING),
        (20, PROGRESS_WARNING),
        (19, PROGRESS_CRITICAL),
    ),
)
def test_memory_progress_threshold_boundaries(
    application: QApplication, available: int, expected: str
) -> None:
    model = HostTableModel()
    model.set_rows(
        (
            HostInfo(
                "node",
                "ok",
                True,
                memory_available_bytes=available,
                memory_total_bytes=100,
            ),
        )
    )

    assert model.data(model.index(0, 9), PROGRESS_ROLE)[2] == expected


def test_memory_without_total_keeps_available_value_without_alert(
    application: QApplication,
) -> None:
    model = HostTableModel()
    model.set_rows(
        (
            HostInfo(
                "node",
                "ok",
                True,
                memory_available_bytes=12 * 1024**3,
            ),
        )
    )
    index = model.index(0, 9)

    assert model.data(index) == "12.0 GiB"
    assert model.data(index, PROGRESS_ROLE) is None
    assert model.data(index, Qt.ToolTipRole) == (
        "12.0 GiB available; total memory unavailable"
    )
