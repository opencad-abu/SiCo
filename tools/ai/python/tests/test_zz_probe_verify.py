"""Temporary probe: verify the mapping between the names and the buttons."""

from PyQt5.QtCore import QPoint
from PyQt5.QtWidgets import QApplication, QStyle
import sico_ui.close_choice as close_choice
from agent_service_gui_fixtures import remote_window
from sico_ui.service_connection import attach_service_connection


def test_probe(tmp_path, monkeypatch):
    seen = {}
    original = close_choice.add_choices

    def logged(box):
        choices = original(box)
        seen["hint"] = box.style().styleHint(QStyle.SH_DialogButtonLayout, None, box)
        seen["choices"] = [button.text() for button in choices]
        return choices

    monkeypatch.setattr(close_choice, "add_choices", logged)
    with remote_window(tmp_path, monkeypatch) as (_service, window):
        attach_service_connection(window)
        window.show()
        window.close_choice.ask()
        box = window.close_choice.dialog
        box.adjustSize()
        QApplication.instance().processEvents()
        shown = sorted(box.buttons(), key=lambda b: b.mapTo(box, QPoint(0, 0)).x())
        print("\nplatform:", QApplication.platformName(), "hint:", seen["hint"])
        print("returned:", seen["choices"])
        print("rendered:", [b.text() for b in shown])
        print("default :", box.defaultButton().text())


def test_probe_default_like_the_boundary_test(tmp_path, monkeypatch):
    """Replicate test_close_choice_default_only_closes_the_window verbatim."""
    from sico_ui.service_connection import attach_service_connection as attach

    with remote_window(tmp_path, monkeypatch) as (_service, window):
        attach(window)
        window.close_choice.ask()
        box = window.close_choice.dialog
        print("\nboundary-style platform:", QApplication.platformName())
        print("boundary-style default :", box.defaultButton().text())
        print("boundary-style buttons :", [b.text() for b in box.buttons()])
