"""Chat-link routing: only preview products open the viewer; other links stay put."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QWidget
from sico_ui import detail_navigation, preview_catalog
from sico_ui.preview_catalog import preview_link


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application
    application.processEvents()


class Notices:
    def __init__(self):
        self.warnings = []

    def warning(self, title, message):
        self.warnings.append((title, message))


class ChatScroll:
    def shown(self):
        return None


class Ui:
    def __init__(self):
        self.notices = Notices()
        self.tabs = None
        self.conversation = object()
        self.parent = type("Parent", (), {"chat_scroll": ChatScroll()})()


class Page:
    def chat_scroll(self):
        raise AssertionError("a preview link must not need the transcript")


def navigation(monkeypatch, opened):
    ui = Ui()

    def open_preview_link(window, source, name):
        opened.append((window, source, name))
        return True

    monkeypatch.setattr(detail_navigation, "open_preview_link", open_preview_link)
    page = type("Page", (), {"source": "journal-source"})()
    stub = detail_navigation.DetailNavigation(
        ui, detail=None, centers=page, page=None, presentation=None,
        show_panel=lambda key: None, input_detail=None, event_detail=None,
        reconcile=None,
    )
    # The transcript is only needed by tool receipts; preview links open first.
    return stub, ui


def test_only_preview_products_are_preview_links():
    path = "/workarea/projs/proj4/xh/.sico/ai/circuit_previews/tb_inv_p3_g7.svg"
    assert preview_link(path) == "tb_inv_p3_g7.svg"
    assert preview_link("preview:" + path) == "tb_inv_p3_g7.svg"
    assert preview_link("preview:tb_inv_p3_g7.svg") == "tb_inv_p3_g7.svg"
    assert preview_link("http://example.com/logo.svg") == ""
    assert preview_link("https://example.com/circuit_previews/a.svg") == ""
    assert preview_link("tools-data:data-1") == ""
    assert preview_link("/tmp/notes/other.svg") == ""
    assert preview_link("") == ""
    assert preview_link(None) == ""


def test_dialog_parent_never_hands_a_non_widget_to_qt(app):
    """The crash was a non-widget parent; Qt aborts the process from inside its slot."""
    from PyQt5.QtWidgets import QWidget
    from sico_ui.conversation_widgets import ConversationWidgets
    from sico_ui.preview_catalog import dialog_parent

    window = QWidget()
    holder = ConversationWidgets(
        parent=window, notices=None, session_notices=None, input=None, display=None,
        target=None, token_usage=None, status=None, status_bar=None, send=None, stop=None,
        resume=None, abandon=None, latest=None, resume_action=None, return_action=None,
        host_notice_status=None, tabs=None, conversation=None, task_panel=None,
    )
    try:
        assert dialog_parent(holder) is window  # the conversation's own shape
        assert dialog_parent(window) is window
        # Junk with nothing to resolve to: None, never a non-widget.
        assert dialog_parent("not a widget") is None
    finally:
        window.deleteLater()
        app.processEvents()


def test_holder_composition_resolves_the_window_parent(app, monkeypatch):
    """The conversation hands its widget holder over; the dialog gets the window."""
    from sico_ui.conversation_widgets import ConversationWidgets

    window = QWidget()
    holder = ConversationWidgets(
        parent=window, notices=None, session_notices=None, input=None, display=None,
        target=None, token_usage=None, status=None, status_bar=None, send=None, stop=None,
        resume=None, abandon=None, latest=None, resume_action=None, return_action=None,
        host_notice_status=None, tabs=None, conversation=None, task_panel=None,
    )
    opened = []

    class Centers:
        source = "journal-source"

    def open_preview_link(target, source, name):
        opened.append((target, source, name))
        return True

    monkeypatch.setattr(detail_navigation, "open_preview_link", open_preview_link)
    stub = detail_navigation.DetailNavigation(
        holder, detail=None, centers=Centers(), page=None, presentation=None,
        show_panel=lambda key: None, input_detail=None, event_detail=None, reconcile=None,
    )
    try:
        assert stub.open_preview("tb_inv_p3_g7.svg") is True
        assert opened == [(window, "journal-source", "tb_inv_p3_g7.svg")]
    finally:
        window.deleteLater()
        app.processEvents()


def test_double_click_on_a_preview_link_opens_the_viewer(app, monkeypatch):
    opened = []
    stub, ui = navigation(monkeypatch, opened)
    try:
        assert stub.open_tool_reference(
            "/zone.disk/ssd1/workarea/projs/proj4/xh/.sico/ai/circuit_previews/"
            "tb_inv_p3_g7.svg") is None
        assert opened == [(ui.parent, "journal-source", "tb_inv_p3_g7.svg")]
        assert ui.notices.warnings == []
    finally:
        pass


def test_other_links_keep_their_own_handler(app, monkeypatch):
    opened = []
    stub, ui = navigation(monkeypatch, opened)
    # A tools: receipt needs the transcript; a preview link must not open instead.
    assert stub.open_tool_reference("tools-head:message-1") is None
    assert opened == []


def test_unavailable_preview_directory_reports_a_notice(app, monkeypatch):
    ui = Ui()

    def unavailable(window, source, name):
        return False

    monkeypatch.setattr(detail_navigation, "open_preview_link", unavailable)
    page = type("Page", (), {"source": None})()
    stub = detail_navigation.DetailNavigation(
        ui, detail=None, centers=page, page=None, presentation=None,
        show_panel=lambda key: None, input_detail=None, event_detail=None,
        reconcile=None,
    )
    assert stub.open_preview("a.svg") is False
    assert ui.notices.warnings and "预览产物" in ui.notices.warnings[0][0]


def test_catalog_initial_name_opens_that_product(app, monkeypatch, tmp_path):
    from sico.service.workbench_data import WorkbenchService
    from sico.storage.journal import SessionJournal

    from cadai.circuit_preview_files import preview_directory

    def svg(text):
        return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 500 200">'
                '<text x="10" y="20">' + text + '</text></svg>').encode()

    (preview_directory(tmp_path, create=True) / "tb_inv_p3_g7.svg").write_bytes(svg("manual"))
    journal = SessionJournal(tmp_path, "preview")
    service = WorkbenchService()
    source = service.source(journal)
    dialog = None
    try:
        dialog = preview_catalog.open_preview_catalog(None, source, "tb_inv_p3_g7.svg")
        from agent_gui_wait import receipts

        receipts(dialog)
        viewers = [w for w in dialog.children() if w.objectName() == "imageViewer"]
        assert viewers, dialog.notice.text()
        assert viewers[-1].view.renderer().isValid()
    finally:
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()
        journal.close()
        service.close()
        assert service.wait(5)
        app.processEvents()
