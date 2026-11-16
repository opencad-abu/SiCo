"""The live wire path: a real service answers the catalog and the viewer opens."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from agent_gui_wait import receipts
from agent_service_business_fixtures import business_service, open_session
from PyQt5.QtWidgets import QApplication
from sico.service.remote_frontend import RemoteFrontend
from sico_ui.preview_catalog import open_preview_catalog
from test_image_viewer_gui import close, svg, viewer_window


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application
    application.processEvents()


def test_remote_catalog_lists_and_opens_an_unregistered_product(tmp_path, monkeypatch, app):
    with business_service(tmp_path, monkeypatch) as service:
        open_session(service)
        api = RemoteFrontend(service.descriptor, tmp_path, "live")
        try:
            api.open_initial("attach", dict(session_id="live")).result(5)
            preview = api.preview_session("live", 1, 200).result(5)
            source = api.preview_source(preview)
            from cadai.circuit_preview_files import preview_directory

            directory = preview_directory(tmp_path, create=True)
            (directory / "tb_inv_p3_g7.svg").write_bytes(svg("manual"))
            dialog = open_preview_catalog(None, source)
            try:
                receipts(dialog)
                rows = {dialog.list.topLevelItem(index).text(0): dialog.list.topLevelItem(index)
                        for index in range(dialog.list.topLevelItemCount())}
                assert "tb_inv_p3_g7.svg" in rows, dialog.notice.text()
                # The row's open gesture: double-click reaches the dialog's handler.
                item = rows["tb_inv_p3_g7.svg"]
                dialog.list.setCurrentItem(item)
                dialog.list.itemDoubleClicked.emit(item, 0)
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline and not [
                        w for w in dialog.children() if w.objectName() == "imageViewer"]:
                    app.processEvents()
                    receipts(dialog)
                    time.sleep(0.005)
                viewer = viewer_window(dialog)
                assert viewer.view.renderer().isValid(), dialog.notice.text()
                assert "未登记" in viewer.notice.text()
                close(viewer, app)
            finally:
                close(dialog, app)
        finally:
            api.close_desktop()
