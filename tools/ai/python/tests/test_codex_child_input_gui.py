"""Child origins in the real review center and late URL callback boundaries."""

from concurrent.futures import Future

import pytest
from agent_gui_wait import settle, wait_until
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import QRadioButton
from test_codex_child_input import ended, open_child, register
from test_codex_elicitation import running  # noqa: F401
from test_codex_elicitation_gui import app, fill, window  # noqa: F401
from test_codex_steering_gui import publish


@pytest.mark.parametrize("kind", ["native", "form", "url"])
def test_child_source_visible_in_main_review_center(window, kind, tmp_path):  # noqa: F811
    window.resize(1500, 850)
    backend = window.controller.loop
    register(backend)
    row = open_child(backend, kind)
    publish(window)
    settle(window, tasks=False)
    panel = window.centers.audit
    window.centers.tabs.setCurrentWidget(panel)
    wait_until(lambda: row["id"] in panel.audit_rows)
    panel.list.setCurrentItem(panel.list.topLevelItem(panel.list.topLevelItemCount() - 1))
    panel.select(panel.list.currentItem())
    settle(window, tasks=False)
    assert "/root/child" in panel.list.currentItem().text(1)
    if kind == "native":
        options = panel.choices.findChildren(QRadioButton)
        assert len(options) == 2 and all(option.isVisible() for option in options)
        assert "scope" in panel.text_edits
    assert window.grab().save(str(tmp_path / ("child-" + kind + ".png")))
    if kind == "form":
        fill(panel.elicitation_form)
        panel.elicitation_form.buttons[0].clicked.emit(True)
        wait_until(lambda: row["status"] == "answer_received")
        assert backend.audit.deliver() == 1
    assert not window.binding.failed


@pytest.mark.parametrize("boundary", ["unchanged", "switch", "ended"])
def test_child_url_drops_late_callback_after_view_or_lifecycle_change(
    window, monkeypatch, boundary,  # noqa: F811
):
    backend = window.controller.loop
    register(backend)
    row = open_child(backend, "url")
    publish(window)
    settle(window, tasks=False)
    panel = window.centers.audit
    window.centers.tabs.setCurrentWidget(panel)
    wait_until(lambda: row["id"] in panel.audit_rows)
    panel.list.setCurrentItem(panel.list.topLevelItem(panel.list.topLevelItemCount() - 1))
    panel.select(panel.list.currentItem())
    launches, pending = [], []
    future = Future()
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: launches.append(url.toString()))

    def command(*args, **kwargs):
        pending.append((args, kwargs))
        return future

    monkeypatch.setattr(window.api, "command", command)
    panel.elicitation_form.buttons[0].clicked.emit(True)
    assert len(pending) == 1 and not launches
    if boundary == "switch":
        window.page.activation += 1
    elif boundary == "ended":
        backend._notification(ended())
        publish(window)
        wait_until(lambda: panel.audit_rows.get(row["id"], {}).get("status") == "invalid")
    future.set_result("https://example.invalid/child")
    window.commands.poll()
    assert launches == (["https://example.invalid/child"] if boundary == "unchanged" else [])
    assert not window.elicitation_actions.pending
