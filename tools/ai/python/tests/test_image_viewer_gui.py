"""Image viewer window and preview catalog dialog, all offscreen."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from agent_gui_wait import receipts
from PyQt5.QtCore import QBuffer, QIODevice
from PyQt5.QtGui import QColor, QImage, QPainter
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QPushButton
from sico.service.preview_catalog import read_preview
from sico.service.workbench_data import WorkbenchService
from sico.storage.journal import SessionJournal
from sico_ui.image_viewer import PreviewImage, show_image
from sico_ui.preview_catalog import open_preview_catalog
from sico_ui.workbench_view import WorkbenchDetail
from test_circuit_preview import Client, receipt, svg

from cadai.circuit_preview import attach_svg_preview
from cadai.circuit_preview_files import preview_directory


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application
    application.processEvents()


def png(width=120, height=80):
    image = QImage(width, height, QImage.Format_RGB32)
    image.fill(QColor("#3c78d8"))
    painter = QPainter(image)
    painter.setPen(QColor("#ffffff"))
    painter.drawText(10, 20, "SiCo")
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    assert image.save(buffer, "PNG")
    return bytes(buffer.data())


def viewer_window(dialog):
    """The newest image viewer opened on this owner; closed ones can still be children."""
    viewers = [widget for widget in dialog.children() if widget.objectName() == "imageViewer"]
    assert viewers, "no image viewer window"
    return viewers[-1]


def close(dialog, app):
    """Close and finish WA_DeleteOnClose cleanup before the next assertion."""
    dialog.close()
    for _ in range(20):
        app.processEvents()
        if dialog not in QApplication.topLevelWidgets():
            break


def test_ready_svg_opens_with_working_zoom_controls(app):
    data = svg("cdsParam(1) 电路预览")
    dialog = show_image(None, PreviewImage(data, name="auto.svg", sha256="a75ace6e" + "0" * 56))
    try:
        view = dialog.view
        assert view.renderer().isValid()
        assert view.provider == "svg"
        assert dialog.windowTitle().startswith("auto.svg")
        assert "a75ace6e" in dialog.windowTitle()
        assert dialog.notice.text() == ""
        fitted = view.displayed_size()
        view.actual()
        assert view.zoom_label() == "100%" and view.displayed_size().width() == 500
        view.zoom_in()
        assert view.displayed_size().width() == 625 and view.zoom_label() == "125%"
        view.zoom_out()
        assert view.zoom_label() == "100%"
        view.fit()
        assert view.zoom_label() == "适应窗口"
        shown = view.canvas.pixmap().size()
        assert shown.width() <= fitted.width() + 1 and shown.width() > 0
        view.viewport.resize(200, 150)
        app.processEvents()
        assert view.displayed_size().width() <= 200
        view.viewport.resize(80, 60)
        app.processEvents()
        assert 0 < view.displayed_size().width() <= 80
    finally:
        close(dialog, app)


def test_raster_image_opens_and_broken_bytes_keep_a_notice(app):
    image = png()
    dialog = show_image(None, PreviewImage(image, name="report.png"))
    try:
        assert dialog.view.provider == "raster"
        assert dialog.view.displayed_size().width() > 0
    finally:
        close(dialog, app)
    broken = show_image(None, PreviewImage(b"not an image", name="broken.svg",
                                           notice="该预览产物暂不可读或校验失败；文件保留在预览目录。"))
    try:
        assert broken.view.provider == ""
        assert "校验失败" in broken.notice.text()
    finally:
        close(broken, app)
    unreadable = show_image(None, PreviewImage(b"x" * 32))
    try:
        assert unreadable.view.provider == ""
        assert "无法显示" in unreadable.notice.text()
    finally:
        close(unreadable, app)


def test_viewer_buttons_clear_the_title_rule(app):
    """标题下的粗线与按钮边框之间保留 2-3 像素，避免两条线看起来重合。"""
    dialog = show_image(None, PreviewImage(svg("rule"), name="auto.svg"))
    try:
        dialog.resize(700, 420)
        dialog.show()
        app.processEvents()
        rule = dialog.title_bar.rule.geometry()
        button = dialog.findChildren(QPushButton)[0]
        gap = button.mapTo(dialog, button.rect().topLeft()).y() - rule.bottom() - 1
        assert gap >= 2, (gap, rule, button.geometry())
    finally:
        close(dialog, app)


def test_viewer_takes_the_right_half_like_every_sico_tool_window(app):
    dialog = show_image(None, PreviewImage(svg("half"), name="auto.svg"))
    try:
        screen = dialog.screen() or QApplication.primaryScreen()
        area = screen.availableGeometry()
        geometry = dialog.geometry()
        assert geometry.width() <= area.width() // 2 + 1, geometry
        assert geometry.left() >= area.left() + area.width() // 2 - 1, (geometry, area)
    finally:
        close(dialog, app)


def test_slow_request_explains_the_session_load(app, monkeypatch):
    """超过 1s 的等待要说明原因（冷启动时会话数据仍在载入）；落地后提示消失。"""
    from concurrent.futures import Future

    import sico_ui.preview_catalog as catalog_module
    from sico.service.preview_catalog import PreviewIndex

    monkeypatch.setattr(catalog_module, "SLOW_REQUEST_MS", 30)

    class SlowSource:
        def __init__(self):
            self.future = Future()

        def preview_artifact(self, name=""):
            return self.future

    source = SlowSource()
    dialog = open_preview_catalog(None, source)
    try:
        assert dialog.notice.text() == "正在读取预览产物…"
        assert not dialog.wait_hint.isVisible()
        QTest.qWait(150)
        assert dialog.wait_hint.isVisible()
        assert dialog.wait_hint.text() == "会话数据正在载入，稍候"
        source.future.set_result(PreviewIndex(()))
        receipts(dialog)
        assert not dialog.wait_hint.isVisible()
        assert dialog.notice.text() == "预览目录里还没有图片。"
    finally:
        close(dialog, app)


def test_fast_request_keeps_the_hint_hidden(app, monkeypatch):
    from concurrent.futures import Future

    import sico_ui.preview_catalog as catalog_module
    from sico.service.preview_catalog import PreviewIndex

    monkeypatch.setattr(catalog_module, "SLOW_REQUEST_MS", 30)

    class FastSource:
        def preview_artifact(self, name=""):
            future = Future()
            future.set_result(PreviewIndex(()))
            return future

    dialog = open_preview_catalog(None, FastSource())
    try:
        receipts(dialog)
        QTest.qWait(60)
        assert not dialog.wait_hint.isVisible()
    finally:
        close(dialog, app)


def test_closing_while_the_hint_is_up_is_clean(app, monkeypatch):
    """关闭时必须走 QDialog 自己的 done(int)，辅助方法不能占用该名字。"""
    from concurrent.futures import Future

    import sico_ui.preview_catalog as catalog_module

    monkeypatch.setattr(catalog_module, "SLOW_REQUEST_MS", 30)

    class SlowSource:
        def preview_artifact(self, name=""):
            return Future()

    dialog = open_preview_catalog(None, SlowSource())
    dialog.resize(600, 380)
    dialog.show()
    QTest.qWait(120)
    assert dialog.wait_hint.isVisible()
    dialog.close()
    app.processEvents()
    assert not dialog.isVisible()


def test_catalog_dialog_lists_and_opens_registered_and_manual_products(tmp_path, app):
    creation = attach_svg_preview(receipt(), Client(svg("auto")), tmp_path)
    artifact = creation["svg_preview"]["artifact"]
    auto = artifact["path"].rsplit("/", 1)[-1]
    (preview_directory(tmp_path) / "tb_inv_p3_g7.svg").write_bytes(svg("manual"))
    service = WorkbenchService()
    journal = SessionJournal(tmp_path, "preview")
    source = service.source(journal)
    try:
        dialog = open_preview_catalog(None, source)
        assert dialog is not None
        try:
            receipts(dialog)
            names = {dialog.list.topLevelItem(index).text(0)
                     for index in range(dialog.list.topLevelItemCount())}
            assert names == {auto, "tb_inv_p3_g7.svg"}
            item = dialog.rows["tb_inv_p3_g7.svg"]
            assert "未登记" in item.text(1)
            assert auto in {dialog.list.topLevelItem(index).text(0)
                            for index in range(dialog.list.topLevelItemCount())}
            manual_item = dialog.rows["tb_inv_p3_g7.svg"]
            dialog.open(manual_item)
            receipts(dialog)
            viewer = viewer_window(dialog)
            assert viewer.view.renderer().isValid()
            assert "未登记" in viewer.notice.text()
            close(viewer, app)
            auto_item = dialog.rows[auto]
            dialog.open(auto_item)
            receipts(dialog)
            viewer = viewer_window(dialog)
            assert viewer.view.renderer().isValid()
            assert viewer.notice.text() == ""
            close(viewer, app)
            # A tampered file reports the checksum failure instead of opening.
            path = preview_directory(tmp_path) / auto
            path.write_bytes(svg("tampered"))
            dialog.list.setCurrentItem(auto_item)
            dialog.open(auto_item)
            receipts(dialog)
            assert "校验失败" in dialog.notice.text()
            assert viewer_window(dialog).isVisible() is False
        finally:
            close(dialog, app)
    finally:
        journal.close()
        service.close()
        assert service.wait(5)


def test_report_image_button_opens_the_viewer_with_the_service_digest(tmp_path, app):
    creation = attach_svg_preview(receipt(), Client(svg("report")), tmp_path)
    artifact = creation["svg_preview"]["artifact"]
    name = artifact["path"].rsplit("/", 1)[-1]
    journal = SessionJournal(tmp_path, "preview")

    class Index:
        reader, session_id = journal, "preview"

    view = WorkbenchDetail()
    try:
        view.index = Index()
        view.current = ("report", "report")
        view._detail_generation = 1
        view._render_detail(1, {"title": "报告", "markdown": "完成",
                                "images": [svg("report")],
                                "svg_preview": {"schema": "cad.schematic.svg-preview.v1",
                                                "artifact": artifact}})
        assert view.images_gallery.button.isEnabled()
        assert view.image_sizes
        viewer = view.images_gallery.open()
        assert viewer is not None and viewer.view.renderer().isValid()
        content = read_preview(journal, name)
        assert content.readable and viewer.windowTitle() == (
            name + "  ·  " + content.sha256[:12])
        close(viewer, app)
    finally:
        view.deleteLater()
        app.processEvents()
        journal.close()
