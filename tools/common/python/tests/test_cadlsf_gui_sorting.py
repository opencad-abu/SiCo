"""Ordering contracts for missing metrics and numeric job identifiers."""

import pytest

from cadlsf_gui_fixtures import application as application
from PyQt5.QtCore import Qt
from cadlsf.gui.models import (
    HostFilterProxyModel, HostTableModel, JobFilterProxyModel, JobTableModel,
)
from cadlsf.model import HostInfo, JobInfo


@pytest.mark.parametrize("order", [Qt.AscendingOrder, Qt.DescendingOrder])
@pytest.mark.parametrize("proxy_enabled", [False, True])
def test_missing_host_metrics_sort_last(application, order, proxy_enabled):
    model = HostTableModel()
    model.set_rows((
        HostInfo("unknown", "ok", True),
        HostInfo("high", "ok", True, cpu_utilization=0.9),
        HostInfo("low", "ok", True, cpu_utilization=0.1),
    ))
    visible = model
    if proxy_enabled:
        visible = HostFilterProxyModel()
        visible.setSourceModel(model)
    visible.sort(8, order)
    known = ["low", "high"] if order == Qt.AscendingOrder else ["high", "low"]
    assert [visible.index(row, 1).data() for row in range(3)] == known + ["unknown"]


def test_job_proxy_sorts_ids_numerically_and_searches_secondary_host(application):
    model = JobTableModel()
    model.set_rows(tuple(
        JobInfo(
            job_id, "demo", "RUN", "normal", "first", "first:secondary",
            2, "", "", 60, "job",
        )
        for job_id in ("100", "9", "22")
    ))
    proxy = JobFilterProxyModel()
    proxy.setSourceModel(model)
    proxy.set_query(" SECONDARY ")
    proxy.sort(0, Qt.AscendingOrder)
    assert [proxy.index(row, 0).data() for row in range(3)] == ["9", "22", "100"]
