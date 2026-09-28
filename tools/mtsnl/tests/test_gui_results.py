from __future__ import annotations

from io import StringIO
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt5")

from PyQt5.QtWidgets import QApplication  # noqa: E402

from mtsnetlistor.errors import RequestValidationError  # noqa: E402
from mtsnetlistor.gui.host_requests import (  # noqa: E402
    emit_open_view_request,
    encode_open_view_request,
)
from mtsnetlistor.gui.result_browser import (  # noqa: E402
    ResultBrowserDialog,
    ResultRow,
    ResultView,
    open_netlist_file,
)


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


def test_open_view_request_is_restricted_and_flushed() -> None:
    stream = StringIO()

    record = emit_open_view_request(
        "target_lib",
        "top",
        "spectreText",
        stream=stream,
    )

    assert record == "MTS_OPEN_VIEW\ttarget_lib\ttop\tspectreText"
    assert stream.getvalue() == record + "\n"
    for invalid in ("layout", "schematic", "symbol\nhiQuit()"):
        with pytest.raises(RequestValidationError):
            encode_open_view_request("target_lib", "top", invalid)


def test_result_row_adds_only_confirmed_publication_views(tmp_path: Path) -> None:
    row = ResultRow("src", "top", "schematic", tmp_path / "top.spe")
    publication = SimpleNamespace(
        status="manual_cleanup_required",
        target_library="target",
        target_cell="top_mts",
        symbol=SimpleNamespace(status="succeeded"),
        text=None,
    )

    updated = row.with_publication(publication)

    assert updated.symbol_view == ResultView("target", "top_mts", "symbol")
    assert updated.netlist_view is None


def test_result_browser_lists_cells_and_dispatches_double_clicks(
    application: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_file = tmp_path / "first.spe"
    second_file = tmp_path / "second.sp"
    first_file.write_text("subckt first\nends first\n", encoding="ascii")
    second_file.write_text(".subckt second\n.ends second\n", encoding="ascii")
    rows = (
        ResultRow(
            "source",
            "first",
            "schematic",
            first_file,
            ResultView("target", "first_mts", "symbol"),
            ResultView("target", "first_mts", "spectreText"),
        ),
        ResultRow("source", "second", "schematic", second_file),
    )
    opened_files: list[str] = []
    opened_views: list[ResultView] = []
    errors: list[str] = []
    monkeypatch.setattr(
        "mtsnetlistor.gui.result_browser.open_netlist_file",
        lambda path: opened_files.append(str(path)),
    )
    dialog = ResultBrowserDialog(
        rows,
        open_view=opened_views.append,
        report_error=errors.append,
    )
    try:
        assert dialog.table.rowCount() == 2
        assert dialog.table.columnCount() == 4
        assert dialog.table.item(0, 0).text() == "source/first/schematic"
        assert dialog.table.item(0, 1).text() == str(first_file)
        assert dialog.table.item(0, 2).text() == "target/first_mts/symbol"
        assert dialog.table.item(0, 3).text() == "target/first_mts/spectreText"
        assert dialog.table.item(1, 2).text() == "-"
        assert dialog.table.item(1, 3).text() == "-"

        dialog._open_cell(0, 0)
        dialog._open_cell(0, 1)
        dialog._open_cell(0, 2)
        dialog._open_cell(1, 3)

        assert opened_files == [str(first_file)]
        assert opened_views == [ResultView("target", "first_mts", "symbol")]
        assert errors == []
    finally:
        dialog.close()


def test_netlist_editor_uses_argv_without_shell(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    netlist = tmp_path / "top.spe"
    netlist.write_text("subckt top\nends top\n", encoding="ascii")
    captured = {}

    def fake_popen(argv, **kwargs):
        captured.update(argv=argv, kwargs=kwargs)
        return SimpleNamespace(pid=42)

    monkeypatch.setattr(
        "mtsnetlistor.gui.result_browser.shutil.which",
        lambda name, path=None: "/cad/bin/runUserCmd" if name == "runUserCmd" else None,
    )
    monkeypatch.setattr(
        "mtsnetlistor.gui.result_browser.subprocess.Popen",
        fake_popen,
    )

    assert open_netlist_file(netlist, environ={"PATH": "/cad/bin"}) == 42
    assert captured["argv"] == [
        "/cad/bin/runUserCmd",
        "gvim",
        "--",
        str(netlist),
    ]
    assert "shell" not in captured["kwargs"]
    assert captured["kwargs"]["start_new_session"] is True
