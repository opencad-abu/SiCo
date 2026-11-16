"""Context ring uses the current session snapshot and keeps the action grid aligned."""

import pytest
from agent_gui_wait import settle
from PyQt5.QtCore import QPoint
from test_codex_steering_gui import app, window  # noqa: F401
from test_codex_turn_inputs_gui import configure


@pytest.mark.parametrize("width", [980, 1500])
@pytest.mark.parametrize("extra", [False, True])
def test_context_ring_and_actions_form_two_aligned_columns(window, tmp_path, width, extra):
    options = configure(window)
    composer = window.turn_composer
    composer.use_catalog(options.status(busy=True))
    state = window.controller.updates.read_updates()["snapshot"]
    state["task"]["context_usage"] = {"used": 64000, "capacity": 256000}
    if extra:
        state["task"]["status"] = "needs_reconcile"
        state.update(busy=False, paused=True, pending=1)
    window.renderer.update_session(state)
    window.resize(width, 800)
    settle(window, tasks=False)
    composer.refresh()
    ring = composer.context_usage
    assert "25.0%" in ring.toolTip() and "64,000 / 256,000" in ring.toolTip()
    assert composer.layout().indexOf(ring) == composer.layout().indexOf(composer.effort) + 1
    assert ring.size() == composer.attachments.size()
    assert ring.size() == composer.goal.size() == composer.settings.size()
    assert ring.size() == window.stop.size() == window.latest.size()
    assert ring.width() == ring.height() == 30
    for upper, lower in ((ring, window.stop), (composer.settings, window.latest)):
        assert upper.mapTo(window, QPoint(0, 0)).x() == lower.mapTo(window, QPoint(0, 0)).x()
    assert window.width() == width
    assert composer.effort.geometry().right() < ring.geometry().left()
    assert ring.geometry().right() < composer.settings.geometry().left()
    assert composer.goal.geometry().right() < window.send.geometry().left()
    assert window.grab().save(str(tmp_path / "context-grid.png"))


def test_context_ring_clears_on_preview_loading_or_unknown_capacity(window):
    configure(window)
    composer = window.turn_composer
    state = window.controller.updates.read_updates()["snapshot"]
    state["task"]["context_usage"] = {"used": 960, "capacity": 1000}
    window.renderer.update_session(state)
    composer.refresh()
    assert "96.0%" in composer.context_usage.toolTip()
    window.page.reviewing = "history"
    composer.refresh()
    assert "暂无数据" in composer.context_usage.toolTip()
    window.page.reviewing = None
    window.presentation.state = None
    composer.refresh()
    assert "暂无数据" in composer.context_usage.toolTip()
    for invalid in ({"used": 0, "capacity": 0}, {"used": True, "capacity": 1000}, None):
        composer.context_usage.set_usage(invalid)
        assert "暂无数据" in composer.context_usage.toolTip()
    composer.context_usage.set_usage({"used": 0, "capacity": 1000})
    assert "0.0%" in composer.context_usage.toolTip()


def test_context_occupancy_crosses_service_snapshot_to_ring(tmp_path, monkeypatch):
    from agent_service_gui_fixtures import remote_window
    from agent_gui_wait import wait_until
    from sico.service.published import freeze

    with remote_window(tmp_path, monkeypatch) as (service, window):
        controller = service.owner.controllers["live"]
        with controller._lock:
            controller._task = freeze({**controller._task,
                                       "context_usage": {"used": 32000, "capacity": 128000}})
            controller.version += 1
        wait_until(lambda: "25.0%" in window.turn_composer.context_usage.toolTip())
