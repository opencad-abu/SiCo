"""Binding controls remain asynchronous, session-scoped and usable after timeout."""

import os
import threading

import pytest
from agent_gui_wait import settle, wait_until
from test_agent_session_bindings import target

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from agent_sessions_harness import DesktopSessions
from agent_window_fixture import create_window as AssistantWindow
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from sico.core.contracts import BoundContext, ModelTurn, TextDelta, ToolCall, ToolResult
from sico.service.python_backend import PythonBackend
from sico.core.tools import Tool, ToolRegistry, no_arguments
from sico.demo import DemoPeer
from sico.providers.mock import ScriptedProvider
from sico.service.controller import SessionController
from sico.storage.journal import SessionJournal
from sico.storage.session_snapshot import capture, save
from sico.transport.bridge_client import BridgeClient
from sico.transport.broker import skill_call_context
from sico.transport.instance_bridge import InstanceBridge


@pytest.fixture
def workspace(tmp_path):
    app = QApplication.instance() or QApplication([])
    ciw = target()
    ciw = BoundContext(ciw.instance_id, ciw.generation, ciw.target_id,
                       dict(ciw.snapshot, capabilities=["get_context", "circuit_call", "assistant_call"]))
    with InstanceBridge(tmp_path, ciw) as bridge, SessionJournal(tmp_path, "a") as journal:
        peer = DemoPeer(bridge.broker, ciw)
        try:
            with BridgeClient.discover(tmp_path, ciw) as client:
                loop = PythonBackend(ScriptedProvider([]), ToolRegistry(), journal, ciw)
                loop._event("session.created", {"entry": "test"})
                save(journal.directory, capture(journal.session_id, ciw, None, {}, {}))
                controller = SessionController(loop)
                controller.bindings.attach_targets(client)
                sessions = DesktopSessions(controller, broker=client)
                window = AssistantWindow(controller, sessions=sessions)
                window.show()
                try:
                    settle(window)
                    yield window, bridge, tmp_path
                finally:
                    sessions.close()
                    assert sessions.wait(5)
                    window.close()
                    window.deleteLater()
                    app.processEvents()
        finally:
            peer.close()


def host_proposal(window, bridge):
    sch = target("sch", window="window:2")
    result = bridge.publish_binding(
        "a", sch, task_id="task", origin_request_id="request", created_by_workflow=True
    )
    wait_until(lambda: window.binding_panel.proposal is not None)
    return result


@pytest.mark.parametrize("legacy_bridge", [False, True])
def test_host_operation_event_keeps_running_task_and_desktop_registered(
    workspace, monkeypatch, legacy_bridge,
):
    """An assistant_call receipt must survive Qt polling before the next host call."""
    window, bridge, _ = workspace
    controller = window.controller
    entered, release = threading.Event(), threading.Event()
    if legacy_bridge:
        publish = bridge.broker.binding_events._publish

        def legacy_publish(kind, payload, candidate=None):
            if kind.startswith("binding.operation_"):
                payload = {key: value for key, value in payload.items()
                           if key not in {"instance_id", "generation"}}
            return publish(kind, payload, candidate)

        monkeypatch.setattr(bridge.broker.binding_events, "_publish", legacy_publish)

    def prepare(_inputs, context):
        with skill_call_context(session_id=controller.session_id):
            controller.bindings.broker.assistant_call(context, "eval_skill", "t")
            entered.set()
            assert release.wait(5)
            observed = controller.bindings.broker.get_context(context)
        return ToolResult("ok", "PDK 预取完成", observed)

    controller.loop.tools.register(Tool(
        "get_pdk_preparation", "读取工艺信息", {"type": "object"}, no_arguments, prepare,
    ))
    controller.loop.provider = ScriptedProvider([
        [ModelTurn("", (ToolCall("pdk", "get_pdk_preparation", {}),))],
        [TextDelta("请提供 LDO 的输入电压、输出电压和最大负载电流。"),
         ModelTurn("请提供 LDO 的输入电压、输出电压和最大负载电流。")],
        [TextDelta("已收到后续输入"), ModelTurn("已收到后续输入")],
    ])
    window.input.setPlainText("我想设计一个LDO")
    window.actions.submit()
    try:
        wait_until(entered.is_set)
        wait_until(lambda: any(row["kind"] == "binding.operation_started"
                              for row in controller.journal.events()))
        window.binding.poll()
        assert not window.binding.failed
        assert not controller.closing and not controller._cancel.is_set()
        assert controller.session_id in controller.bindings.broker.bridge_snapshot()["sessions"]
        ticks = []
        timer = QTimer(window)
        timer.timeout.connect(lambda: ticks.append(True))
        timer.start(5)
        QTest.qWait(60)
        timer.stop()
        assert len(ticks) >= 2
    finally:
        release.set()
    settle(window)
    assert controller.loop.state.task["status"] == "completed"
    wait_until(lambda: window.input.isEnabled() and window.send.isEnabled())
    window.input.setPlainText("输出 1.8V")
    window.actions.submit()
    settle(window)
    assert controller.loop.state.task["status"] == "completed"
    assert "已收到后续输入" in window.display.toPlainText()
    assert not any(row["kind"] in {"task.cancelled", "task.needs_reconcile"}
                   for row in controller.journal.events())
    assert "bridge.session_release" not in bridge.path.with_suffix(".router.jsonl").read_text()


def test_display_event_fault_preserves_other_sessions_and_registration(workspace, monkeypatch):
    window, bridge, _ = workspace
    first = window.controller
    second = window.sessions.worker.create_session(first.current).result(3)
    batch = first.updates.read_updates()
    with monkeypatch.context() as changed:
        changed.setattr(first.updates, "read_updates", lambda *args, **kwargs: {
            **batch, "runtime_id": "retired-runtime",
        })
        window.binding.poll()
        wait_until(lambda: window.binding.failed)
    assert window.binding.failed and first.paused
    assert not first.closing and not second.closing
    assert set(window.controller.bindings.broker.bridge_snapshot()["sessions"]) >= {
        first.session_id, second.session_id,
    }
    window.page.activate_session(second.session_id)
    settle(window)
    assert window.controller is second and not window.binding.failed
    assert window.input.isEnabled()
    assert "bridge.session_release" not in bridge.path.with_suffix(".router.jsonl").read_text()


def test_reconciliation_command_is_async_and_preserves_task_state(workspace):
    window, bridge, directory = workspace
    controller = window.controller
    request_id = "a" * 32
    binding_id = bridge.broker._sessions["a"].binding_id
    bridge.broker._sessions["a"].holds[request_id] = "unknown"
    bridge.broker._sessions["a"].operations[request_id] = dict(
        request_id=request_id, binding_id=binding_id, state="unknown",
        function="aiCreateExecute", method="circuit_call", source=target().record())
    entered, release = threading.Event(), threading.Event()
    result = dict(request_id=request_id, state="partial_or_failed", hold_cleared=True,
                  evidence_digest="b" * 64, ownership_released=False,
                  automatic_resume_allowed=False)

    def reconcile(session_id, lifetime, original):
        assert threading.current_thread() is not threading.main_thread()
        assert (session_id, lifetime, original) == ("a", binding_id, request_id)
        entered.set()
        assert release.wait(5)
        bridge.broker._sessions["a"].holds.pop(request_id)
        return result

    controller.bindings.broker.reconcile_binding_operation = reconcile
    controller.bindings.poll_binding_events()
    window.binding.poll()
    panel = window.binding_panel
    wait_until(lambda: panel.operations.count() == 1)
    window.navigation.setCurrentWidget(panel)
    prior = dict(controller._task)
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(10)
    try:
        panel.reconcile.click()
        wait_until(entered.is_set)
        wait_until(lambda: len(ticks) >= 3)
        assert panel.pending_command and not panel.reconcile.isEnabled()
        assert controller.updates.read_updates(blocking=False)["snapshot"] is not None
        release.set()
        settle(window)
        assert "部分完成或失败" in panel.notice.text()
        assert controller._task == prior
        assert not bridge.broker._sessions["a"].holds
        window.grab().save(str(directory / "binding-m4-reconcile.png"))
    finally:
        release.set()
        timer.stop()


def test_panel_answers_selects_and_releases_without_retargeting_loop(workspace):
    window, bridge, _ = workspace
    host_proposal(window, bridge)
    panel = window.binding_panel
    assert window.navigation.currentWidget() is panel
    assert panel.targets.topLevelItemCount() == 1
    panel.tick()
    assert "自动加入并切换" in panel.countdown.text()
    panel.choices["add_only"].click()
    settle(window)
    assert panel.targets.topLevelItemCount() == 2
    assert window.presentation.displayed_context.target_id == "ciw"
    panel.targets.setCurrentItem(panel.targets.topLevelItem(1))
    panel.select.click()
    settle(window)
    assert window.presentation.submission_context.target_id == "sch"
    assert window.presentation.displayed_context.target_id == "ciw"
    assert window.controller.loop.state.context.target_id == "ciw"
    assert not panel.release.isEnabled()
    panel.targets.setCurrentItem(panel.targets.topLevelItem(0))
    panel.select.click()
    settle(window)
    panel.targets.setCurrentItem(panel.targets.topLevelItem(1))
    panel.release.click()
    settle(window)
    assert panel.targets.topLevelItemCount() == 1
    assert not window.binding.failed


def test_timeout_result_and_policy_disable_are_visible(workspace):
    window, bridge, _ = workspace
    now = [0]
    bridge.broker.binding_events.clock = lambda: now[0]
    proposal = host_proposal(window, bridge)
    now[0] = 30
    wait_until(lambda: window.presentation.submission_context.target_id == "sch")
    assert "超时策略" in window.binding_panel.notice.text()
    assert window.binding_panel.proposal is None
    assert any(
        row["payload"].get("event_id") == proposal["event_id"] and row["kind"] == "binding.resolved"
        for row in window.controller.journal.events()
    )
    window.binding_panel.auto_bind.click()
    settle(window)
    assert bridge.broker.binding_snapshot("a")["policy"]["auto_bind"] is False


def test_opening_page_disables_binding_commands_until_return(workspace):
    window, bridge, _ = workspace
    host_proposal(window, bridge)
    panel = window.binding_panel
    assert panel.auto_bind.isEnabled() and panel.choices["decline"].isEnabled()
    window.page.begin()
    panel.tick()
    assert not panel.auto_bind.isEnabled()
    assert not any(button.isEnabled() for button in panel.choices.values())
    window.page.return_from_preview()
    panel.tick()
    assert panel.auto_bind.isEnabled() and panel.choices["decline"].isEnabled()


def test_slow_durable_resolution_keeps_qt_responsive(workspace, monkeypatch):
    window, bridge, _ = workspace
    host_proposal(window, bridge)
    entered, release = threading.Event(), threading.Event()
    append = bridge.binding_journal.append
    owner = threading.get_ident()

    def slow(event):
        if event["kind"] == "binding.resolved":
            assert threading.get_ident() != owner
            entered.set()
            assert release.wait(5)
        append(event)

    monkeypatch.setattr(bridge.broker.binding_events, "persist", slow)
    window.binding_panel.choices["add_and_select"].click()
    try:
        assert entered.wait(3)
        assert not window.binding_panel.choices["decline"].isEnabled()
        ticks = []
        timer = QTimer(window)
        timer.timeout.connect(lambda: ticks.append(True))
        timer.start(5)
        QTest.qWait(80)
        timer.stop()
        assert len(ticks) >= 2
        assert window.presentation.displayed_context.target_id == "ciw"
    finally:
        release.set()
    settle(window)
    assert window.presentation.submission_context.target_id == "sch"


def test_session_switch_and_history_do_not_accept_foreign_answer(workspace):
    window, bridge, _ = workspace
    host_proposal(window, bridge)
    other = window.sessions.worker.create_session(window.controller.current).result(3)
    window.page.activate_session(other.session_id)
    settle(window)
    assert window.binding_panel.proposal is None
    assert window.binding_panel.scope[0] == other.session_id
    window.page.reviewing = "a"
    window.binding_panel.tick()
    assert not window.binding_panel.auto_bind.isEnabled()
    window.page.reviewing = None
    window.page.activate_session("a")
    settle(window)
    assert window.binding_panel.proposal is not None


@pytest.mark.parametrize("size", [(1440, 900), (1024, 768)])
def test_binding_panel_layout_has_visible_controls(workspace, size):
    window, bridge, tmp_path = workspace
    host_proposal(window, bridge)
    window.resize(*size)
    QApplication.processEvents()
    panel = window.binding_panel
    for widget in [panel.question, panel.countdown, *panel.choices.values(), panel.auto_bind]:
        assert widget.isVisible()
        assert widget.width() >= 100 and widget.height() >= 15
        assert panel.rect().contains(widget.mapTo(panel, widget.rect().center()))
    # Private pytest output is a visual acceptance artifact, never a release asset.
    assert window.grab().save(str(tmp_path / f"binding-panel-{size[0]}.png"))
    assert panel.targets.currentItem().data(0, Qt.UserRole) == "ciw"


def test_closed_target_and_reserved_resource_controls(workspace):
    from sico.core.contracts import BoundContext

    window, bridge, tmp_path = workspace
    host_proposal(window, bridge)
    panel = window.binding_panel
    panel.choices["add_and_select"].click()
    settle(window)
    sch = target("sch", window="window:2")
    bridge.native_binding_event(dict(kind="binding.native_closed", context=sch.record()))
    wait_until(lambda: window.presentation.submission_context.target_id == "ciw")
    panel.targets.setCurrentItem(panel.targets.topLevelItem(1))
    assert "已关闭" in panel.targets.currentItem().text(1)
    assert not panel.select.isEnabled() and panel.release.isEnabled()
    resource = BoundContext(
        "instance",
        "g",
        "resource",
        dict(
            cwd="/project",
            cellview=dict(lib="Work", cell="Background", view="schematic"),
            library_path="/worklib",
        ),
    )
    with bridge.broker._changed:
        bridge.broker.binding_events.reserve("a", resource, "reserve")
    wait_until(lambda: panel.targets.topLevelItemCount() == 3)
    panel.targets.setCurrentItem(panel.targets.topLevelItem(2))
    assert "资源已预留" in panel.targets.currentItem().text(1)
    assert not panel.select.isEnabled() and panel.release.isEnabled()
    assert window.grab().save(str(tmp_path / "binding-m3-resources.png"))
    panel.release.click()
    settle(window)
    assert panel.targets.topLevelItemCount() == 2
    assert not bridge.broker.binding_snapshot("a")["reservations"]
