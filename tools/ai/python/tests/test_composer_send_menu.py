"""The split send control only schedules a later task on explicit menu selection."""

import pytest
from agent_gui_wait import settle, wait_until
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QPushButton
from test_codex_steering import rpc_for
from test_codex_steering_gui import app as app
from test_codex_steering_gui import publish
from test_codex_steering_gui import window as window


@pytest.mark.parametrize("width", [980, 1500])
@pytest.mark.parametrize("blocked", [False, True])
def test_arrow_opens_menu_and_selecting_it_queues_a_later_task(window, tmp_path, width, blocked):
    backend = window.controller.loop
    calls = rpc_for(backend)
    if blocked:
        backend.steering.end()
        publish(window)
        wait_until(lambda: not window.send.isEnabled())
    window.resize(width, 800)
    window.input.setPlainText("根据当前分析结果，提出下一阶段方案")
    control = window.steering_input
    menu = control.menu_button.menu()
    observed = {}

    def select_later():
        observed.update(visible=menu.isVisible(), pending=len(window.controller.inbox.pending),
                        steering=backend.steering.pending,
                        text=[action.text() for action in menu.actions()])
        observed["screenshot"] = menu.grab().save(str(tmp_path / "send-menu.png"))
        position = menu.actionGeometry(control.queue_action).center()
        QTest.mouseClick(menu, Qt.LeftButton, pos=position)

    assert control.menu_button.isEnabled()
    assert not any(button.text() == "加入队列" for button in window.findChildren(QPushButton))
    assert window.grab().save(str(tmp_path / "send-control.png"))
    # InstantPopup runs a nested Qt event loop; select the real menu item inside it.
    QTimer.singleShot(20, select_later)
    QTest.mouseClick(control.menu_button, Qt.LeftButton)
    settle(window, tasks=False)
    assert observed == {"visible": True, "pending": 0, "steering": None,
                        "text": ["当前任务完成后执行"], "screenshot": True}
    assert not calls and not backend.steering.pending
    assert len(window.controller.inbox.pending) == 1
    queued = window.controller.inbox.pending[0]["message"]
    assert queued["text"] == "根据当前分析结果，提出下一阶段方案"
    assert not window.input.toPlainText()
    assert not menu.isVisible()
    assert window.width() <= width
    assert window.send.geometry().right() + 1 == control.menu_button.geometry().left()
    assert window.send.height() == control.menu_button.height()


@pytest.mark.parametrize("selection", ["dismiss", "queue"])
def test_menu_does_not_change_the_default_enter_action(window, selection):
    backend = window.controller.loop
    calls = rpc_for(backend, response={"turnId": "turn"})
    window.input.setPlainText("重点关注增益不足的原因")
    draft = window.input.draft()
    control = window.steering_input
    menu = control.menu_button.menu()
    def close_menu():
        if selection == "queue":
            menu.setActiveAction(control.queue_action)
        QTest.keyClick(menu, Qt.Key_Return if selection == "queue" else Qt.Key_Escape)

    QTimer.singleShot(20, close_menu)
    QTest.mouseClick(control.menu_button, Qt.LeftButton)
    settle(window, tasks=False)
    count = int(selection == "queue")
    assert len(window.controller.inbox.pending) == count
    if selection == "dismiss":
        assert window.input.draft() == draft
    else:
        assert not window.input.toPlainText()
        window.input.setPlainText("补充当前分析的要求")
    assert not calls
    QTest.keyClick(window.input, Qt.Key_Return)
    wait_until(lambda: backend.steering.pending is not None)
    assert not menu.isVisible() and len(window.controller.inbox.pending) == count
    backend.steering.dispatch()
    publish(window)
    settle(window, tasks=False)
    assert [call[0] for call in calls] == ["turn/steer"]
    assert not window.input.toPlainText()


@pytest.mark.parametrize("boundary", ["unsupported", "paused", "full", "read_only"])
def test_default_send_never_implicitly_queues_when_unavailable(window, boundary):
    backend = window.controller.loop
    calls = rpc_for(backend)
    if boundary == "unsupported":
        window.controller.updates.steering_supported = False
        publish(window)
        settle(window, tasks=False)
    elif boundary == "paused":
        backend.steering.end()
        window.controller._running = False
        window.controller.paused = True
        publish(window)
        settle(window, tasks=False)
    elif boundary == "full":
        backend.steering.end()
        for index in range(16):
            window.controller.submit(f"后续任务 {index}")
        publish(window)
        settle(window, tasks=False)
    else:
        window.input.setReadOnly(True)
    window.steering_input.refresh()
    window.input.setPlainText("保留这一条输入")
    count = len(window.controller.inbox.pending)
    assert not window.send.isEnabled()
    assert window.steering_input.menu_button.isEnabled() == (boundary in {"unsupported", "paused"})
    QTest.keyClick(window.input, Qt.Key_Return)
    settle(window, tasks=False)
    assert not any(method in {"turn/start", "turn/steer"} for method, *_ in calls)
    assert not backend.steering.pending
    assert len(window.controller.inbox.pending) == count
    assert window.input.toPlainText() == "保留这一条输入"


def test_empty_cancelled_session_can_start_a_new_task_with_enter(window, monkeypatch):
    backend = window.controller.loop
    backend.steering.end()
    backend.state.task["status"] = "cancelled"
    window.controller._running = False
    window.controller.paused = True
    submitted = []
    monkeypatch.setattr(window.controller, "submit",
                        lambda text, **kwargs: submitted.append(text))
    publish(window)
    wait_until(lambda: window.send.isEnabled() and window.send.text() == "发送")
    window.input.setPlainText("开始新的分析")
    QTest.keyClick(window.input, Qt.Key_Return)
    settle(window, tasks=False)
    assert submitted == ["开始新的分析"]
    assert not window.input.toPlainText()
