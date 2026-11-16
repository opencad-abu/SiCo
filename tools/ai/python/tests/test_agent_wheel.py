"""Chat wheel routing in both EDA Qt delivery paths, including streamed updates."""

from __future__ import annotations

import os

import pytest
from agent_gui_wait import settle

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QPoint, QPointF, Qt
from PyQt5.QtGui import QWheelEvent
from PyQt5.QtWidgets import QApplication, QTreeWidgetItem

from sico.adapters.virtuoso import context_tools
from sico.core.contracts import BoundContext
from sico.service.python_backend import PythonBackend
from sico.providers.mock import ContextDemoProvider
from sico.storage.journal import SessionJournal
from agent_window_fixture import create_window as AssistantWindow


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "wheel") as journal:
        loop = PythonBackend(
            ContextDemoProvider(),
            context_tools(None),
            journal,
            BoundContext("instance", "generation", "bound", {}),
        )
        view = AssistantWindow(loop, persistent=True)
        try:
            view.show()
            view.renderer.flush()
            # Activation installs the session's own Transcript object.
            settle(view)
            view.presentation.transcript.append(
                "assistant", "\n\n".join(f"历史消息 {i}：测试长文本" for i in range(180))
            )
            view.renderer.flush()
            settle(view)
            yield view
        finally:
            view.lifecycle.request()
            view.close()
            assert view.sessions.wait(5)
            view.close()
            view.deleteLater()
            app.processEvents()


def wheel(
    window, view, *, target="viewport", pixel=0, angle=-120, outside=False, modifiers=Qt.NoModifier
):
    local = view.viewport().rect().center()
    if outside:
        local = QPoint(-100, -100)
    event = QWheelEvent(
        QPointF(local),
        QPointF(view.viewport().mapToGlobal(local)),
        QPoint(0, pixel),
        QPoint(0, angle),
        Qt.NoButton,
        modifiers,
        Qt.ScrollUpdate,
        False,
    )
    watched = view.viewport() if target == "viewport" else window.windowHandle()
    QApplication.sendEvent(watched, event)
    QApplication.processEvents()


@pytest.mark.parametrize("target", ["viewport", "window"])
@pytest.mark.parametrize("pixel,angle", [(0, -120), (0, -15), (-40, 0)])
def test_conversation_scrolls_once_and_preserves_position_during_stream(
    window, target, pixel, angle
):
    bar = window.display.verticalScrollBar()
    assert bar.maximum() > 0
    bar.setValue(0)
    wheel(window, window.display, target=target, pixel=pixel, angle=angle)
    expected = -pixel or int(-angle / 120 * QApplication.wheelScrollLines() * bar.singleStep())
    assert bar.value() == expected
    window.renderer.receive({"kind": "model.delta", "payload": {"text": "\n\n新内容"}})
    window.renderer.flush()
    assert bar.value() == expected
    bar.setValue(bar.maximum())
    wheel(window, window.display, target=target, angle=120)
    position = bar.value()
    assert position < bar.maximum() - 20
    window.renderer.receive({"kind": "model.delta", "payload": {"text": "\n\n继续输出"}})
    window.renderer.flush()
    assert bar.value() == position


def test_only_visible_pane_scrolls_and_hidden_window_removes_filter(window):
    # 窗口要给足宽度：窄窗口会自动把侧栏收成条状，被收起的面板本来就不参与滚动。
    window.resize(1280, 800)
    QApplication.processEvents()
    chat = window.display.verticalScrollBar()
    chat.setValue(0)
    window.tabs.setCurrentWidget(window.detail)
    view = window.detail.document
    view.setPlainText("\n".join(str(i) for i in range(200)))
    QApplication.processEvents()
    wheel(window, view, target="window")
    assert view.verticalScrollBar().value() > 0 and chat.value() == 0
    window.centers.tabs.setCurrentWidget(window.centers.audit)
    fields = window.centers.audit.detail
    fields.setPlainText("\n".join(str(i) for i in range(200)))
    QApplication.processEvents()
    wheel(window, fields, target="window")
    assert fields.verticalScrollBar().value() == (
        QApplication.wheelScrollLines() * fields.verticalScrollBar().singleStep()
    )
    window.close()
    assert window.wheel_router.application is None
    window.restore()
    assert window.wheel_router.application is QApplication.instance()
    fields.verticalScrollBar().setValue(0)
    wheel(window, fields, target="window", outside=True)
    assert fields.verticalScrollBar().value() == 0


def test_small_scroll_near_bottom_does_not_snap_to_live_text(window):
    bar = window.display.verticalScrollBar()
    bar.setValue(bar.maximum())
    assert not window.latest.isEnabled()
    wheel(window, window.display, pixel=4, angle=0)
    position = bar.value()
    assert position == bar.maximum() - 4
    assert window.latest.isEnabled()
    window.renderer.receive({"kind": "model.delta", "payload": {"text": "\n\n继续输出"}})
    window.renderer.flush()
    assert bar.value() == position
    window.latest.click()
    assert bar.value() == bar.maximum() and not window.latest.isEnabled()
    window.renderer.receive({"kind": "model.delta", "payload": {"text": "\n\n自动跟随"}})
    window.renderer.flush()
    assert bar.value() == bar.maximum()


def test_input_and_tool_list_scroll_independently(window):
    window.input.setPlainText("\n".join(str(i) for i in range(100)))
    QApplication.processEvents()
    wheel(window, window.input, target="window")
    assert window.input.verticalScrollBar().value() > 0
    window.show_panel("tools")
    view = window.centers.data.list
    for i in range(50):
        view.addTopLevelItem(QTreeWidgetItem(["归档数据 " + str(i), "已记录"]))
    QApplication.processEvents()
    view.verticalScrollBar().setValue(0)
    wheel(window, view, target="window")
    assert view.verticalScrollBar().value() > 0


def test_modified_wheel_passes_through_without_routing(window):
    local = window.display.viewport().rect().center()
    event = QWheelEvent(
        QPointF(local),
        QPointF(window.display.viewport().mapToGlobal(local)),
        QPoint(),
        QPoint(0, -120),
        Qt.NoButton,
        Qt.ShiftModifier,
        Qt.ScrollUpdate,
        False,
    )
    # Modified gestures remain Qt's responsibility (Qt may itself scroll or zoom).
    assert window.wheel_router.eventFilter(window.windowHandle(), event) is False
