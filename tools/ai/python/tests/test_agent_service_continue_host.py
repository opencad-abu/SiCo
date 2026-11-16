"""Continue moves future tools to this project's current host without replaying work."""

import json
from contextlib import ExitStack

import pytest
from agent_gui_wait import wait_until
from agent_service_business_fixtures import business_service
from agent_service_recovery_fixtures import client, seed

from sico.core.contracts import BoundContext, RunState, ToolCall
from sico.demo import DemoPeer
from sico.service.continuation_host import current_host
from sico.service.recovery_facts import read_facts
from sico.service.session_recovery import SessionRecovery
from sico.service.service_errors import ResultUnknown
from sico.storage.journal import SessionJournal
from sico.transport.bridge_client import BridgeClient
from sico.transport.instance_bridge import InstanceBridge


def live_host(stack, project, generation="current"):
    context = BoundContext("virtuoso", generation, "bound", dict(
        source="simulated", valid=True, cwd=str(project), cellview=None, entry="ciw"))
    host = stack.enter_context(InstanceBridge(project, context))
    peer = DemoPeer(host.broker, context)
    stack.callback(peer.close)
    return host, context


def read_tool(controller):
    result = controller.loop.tools.execute(ToolCall("probe", "get_context", {}), controller.current)
    assert result.status == "ok", result
    assert result.data["cwd"] == controller.current.snapshot["cwd"]


@pytest.mark.parametrize("status", [None, "queued", "tool", "approval"])
def test_continue_uses_live_project_host_and_preserves_old_evidence(tmp_path, monkeypatch, status):
    original, _ = seed(tmp_path, monkeypatch, status=status)
    path = tmp_path / ".sico/ai/agent/sessions/old/events.jsonl"
    history = path.read_bytes()
    with ExitStack() as hosts:
        host, context = live_host(hosts, tmp_path)
        with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
            api = client(stack, service, tmp_path)
            token = api.recovery.continue_session("old").result(5)
            controller = service.owner.controllers["old"]
            assert api.can_control(token)
            if status in {"tool", "approval"}:
                # The interrupted task is not executing. Recovery must use the
                # live project host instead of leaving the conversation on the
                # dead captured generation until reconciliation.
                assert controller.paused and controller.current == context
                read_tool(controller)
                api.command(token, "resolve_interrupted", "call" if status == "tool" else "",
                            "completed" if status == "tool" else "no_calls", expected_task="old-task",
                            evidence="verified read-only fixture").result(5)
                assert controller.current == context and not controller.paused
            assert controller.current == context
            assert not controller.paused and not controller.inbox.pending and not service.provider.seen
            assert path.read_bytes().startswith(history)
            facts = read_facts(tmp_path, "old")
            assert facts.context == context and not facts.inputs
            assert facts.snapshot["context"] == original.record()
            assert facts.bridge_identity == {k: host.descriptor[k] for k in ("bridge_id", "router_id")}
            read_tool(controller)
            assert api.recovery.continue_session("old").result(5) == token
            api.command(token, "submit", "use current CIW").result(5)
            wait_until(lambda: not controller.busy)
            assert service.provider.seen == ["use current CIW"]
            api.end_session(token, "end-current").result(5)
            wait_until(lambda: "old" not in service.owner.controllers)
        # Reopening after complete service teardown validates the durable host transition.
        with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
            api = client(stack, service, tmp_path)
            api.recovery.continue_session("old").result(5)
            assert service.owner.controllers["old"].current == context
            read_tool(service.owner.controllers["old"])
            assert not service.provider.seen


def test_already_open_offline_conversation_reconnects_without_losing_credentials(tmp_path, monkeypatch):
    config = dict(provider="openai", base_url="https://model.example/v1", model="test",
                  api_key_env="CUSTOM_KEY")
    seed(tmp_path, monkeypatch, config=config)
    def offline(*_args):
        raise ConnectionRefusedError("old host ended")
    monkeypatch.setattr(SessionRecovery, "_bridge", offline)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        service.owner._default_config = config
        service.owner._default_environment = {"CUSTOM_KEY": "memory-only-key"}
        api = client(stack, service, tmp_path)
        old = api.recovery.continue_session("old").result(5)
        service.owner._default_environment = {}
        with ExitStack() as hosts:
            _, context = live_host(hosts, tmp_path)
            new = api.recovery.continue_session("old").result(5)
            assert new.runtime_id != old.runtime_id and api.can_control(new)
            controller = service.owner.controllers["old"]
            assert controller.current == context
            assert service.owner.dependencies_for("old").environment["CUSTOM_KEY"] == "memory-only-key"
            read_tool(controller)
            assert not service.provider.seen


def test_interrupted_task_recorded_as_needs_reconcile_uses_the_current_host(tmp_path, monkeypatch):
    """Regression: a dead generation must not strand recovery before reconciliation."""

    original, _ = seed(tmp_path, monkeypatch, status="tool")
    with SessionJournal(tmp_path, "old") as journal:
        state = journal.state
        state.task["status"] = "needs_reconcile"
        journal.append("task.needs_reconcile", {"message": "interrupted"}, state)
    with ExitStack() as hosts:
        _, context = live_host(hosts, tmp_path)
        with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
            api = client(stack, service, tmp_path)
            token = api.recovery.continue_session("old").result(5)
            controller = service.owner.controllers["old"]
            assert original != context
            assert controller.paused and controller.current == context
            read_tool(controller)
            api.command(token, "resolve_interrupted", "call", "completed",
                        expected_task="old-task", evidence="late receipt").result(5)
            assert not controller.paused and controller.current == context
            read_tool(controller)


def test_continue_rebinds_an_interrupted_runtime_after_a_later_host_appears(tmp_path, monkeypatch):
    original, _ = seed(tmp_path, monkeypatch, status="tool")
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        old = api.recovery.continue_session("old").result(5)
        controller = service.owner.controllers["old"]
        assert controller.paused and controller.current == original
        with ExitStack() as hosts:
            _, context = live_host(hosts, tmp_path)
            new = api.recovery.continue_session("old").result(5)
            controller = service.owner.controllers["old"]
            assert new.runtime_id != old.runtime_id
            assert controller.paused and controller.current == context
            read_tool(controller)
            api.command(new, "resolve_interrupted", "call", "completed",
                        expected_task="old-task", evidence="late receipt").result(5)
            assert not controller.paused and controller.current == context
            read_tool(controller)


def test_current_window_host_wins_and_other_project_or_machine_is_ignored(tmp_path, monkeypatch):
    seed(tmp_path, monkeypatch)
    with ExitStack() as hosts:
        preferred, context = live_host(hosts, tmp_path, "window")
        live_host(hosts, tmp_path, "newer")
        other = tmp_path / "other-project"
        other.mkdir()
        foreign, _ = live_host(hosts, other, "foreign")
        directory = tmp_path / ".sico/ai/agent/bridges"
        (directory / foreign.path.name).write_bytes(foreign.path.read_bytes())
        (directory / "remote-machine.json").write_bytes(preferred.path.read_bytes())
        with business_service(tmp_path, monkeypatch) as service:
            bridge = BridgeClient(preferred.descriptor)
            source = service.owner.open("window", preferred.descriptor, context, bridge=bridge)
            selected = current_host(service.owner, source.session_id)
            assert selected[1] == context
            selected[0].close()


def test_live_bridge_dies_then_continue_replaces_only_idle_runtime(tmp_path, monkeypatch):
    seed(tmp_path, monkeypatch)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        with ExitStack() as first:
            live_host(first, tmp_path, "before")
            old = api.recovery.continue_session("old").result(5)
        with ExitStack() as second:
            _, context = live_host(second, tmp_path, "after")
            token = api.recovery.continue_session("old").result(5)
            assert token.runtime_id != old.runtime_id
            assert service.owner.controllers["old"].current == context
            read_tool(service.owner.controllers["old"])
            assert not service.provider.seen


def test_transition_committed_before_publication_can_reopen(tmp_path, monkeypatch):
    seed(tmp_path, monkeypatch)
    from sico.service.binding_commands import SessionBindings
    original = SessionBindings.attach_targets
    def fail(*_args):
        raise RuntimeError("publication interrupted")
    with ExitStack() as hosts:
        _, context = live_host(hosts, tmp_path)
        with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
            api = client(stack, service, tmp_path)
            monkeypatch.setattr(SessionBindings, "attach_targets", fail)
            with pytest.raises(ResultUnknown):
                api.recovery.continue_session("old").result(5)
            assert not service.owner.controllers
            assert read_facts(tmp_path, "old").context == context
            monkeypatch.setattr(SessionBindings, "attach_targets", original)
            api.recovery.continue_session("old").result(5)
            read_tool(service.owner.controllers["old"])
            assert not service.provider.seen


def test_unjournaled_host_change_is_rejected(tmp_path, monkeypatch):
    seed(tmp_path, monkeypatch)
    with ExitStack() as hosts:
        live_host(hosts, tmp_path)
        with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
            client(stack, service, tmp_path).recovery.continue_session("old").result(5)
        path = tmp_path / ".sico/ai/agent/sessions/old/events.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        transition = next(row for row in rows if row["kind"] == "session.host_changed")
        transition["kind"] = "session.unverified"
        path.write_text("".join(json.dumps(row)+"\n" for row in rows))
        with pytest.raises(ValueError, match="without transition"):
            read_facts(tmp_path, "old")


def test_host_transition_folds_rows_from_before_the_text_split(tmp_path, monkeypatch):
    """行内状态剥离任务文本后，旧格式行到新格式行的迁移仍须可折叠。

    回归：旧行带 task.text、新行（host_changed 起）不带，逐字段比较此前会
    把整篇日志的折叠判为证据无效，让会话在恢复层被卡死。
    """
    context, _address = seed(tmp_path, monkeypatch)
    with monkeypatch.context() as legacy:
        legacy.setattr(SessionJournal, "durable_state",
                       staticmethod(lambda state: state.record()))
        with SessionJournal(tmp_path, "old") as journal:
            state = journal.state or RunState(context)
            state.task = dict(id="legacy-task", status="cancelled", pending_calls=[],
                              text="旧行携带的展示文本")
            journal.append("session.note", {"text": "legacy"}, state)
    with ExitStack() as hosts:
        host, context = live_host(hosts, tmp_path)
        with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
            api = client(stack, service, tmp_path)
            api.recovery.continue_session("old").result(5)
            facts = read_facts(tmp_path, "old")
            assert facts.context == context
            assert facts.bridge_identity == {k: host.descriptor[k]
                                             for k in ("bridge_id", "router_id")}


def test_continue_replaces_the_current_page_with_live_tools(tmp_path, monkeypatch):
    from test_agent_service_recovery_gui import window_for
    seed(tmp_path, monkeypatch)
    def offline(*_args):
        raise ConnectionRefusedError("old host ended")
    monkeypatch.setattr(SessionRecovery, "_bridge", offline)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        old = api.recovery.continue_session("old").result(5)
        window = window_for(stack, api, old)
        with ExitStack() as hosts:
            _, context = live_host(hosts, tmp_path)
            window.navigation._continue_session("old")
            wait_until(lambda: window.page.session.runtime_id != old.runtime_id and not window.page.opening)
            assert window.input.isEnabled() and api.can_control(window.page.session)
            assert api.view(window.page.session).context == context
            assert window.service_connection.recovery.dialog is None
            read_tool(service.owner.controllers["old"])


def test_no_other_project_or_remote_machine_fallback(tmp_path, monkeypatch):
    seed(tmp_path, monkeypatch)
    other = tmp_path / "other-project"
    other.mkdir()
    with ExitStack() as hosts:
        foreign, _ = live_host(hosts, other, "foreign")
        with business_service(tmp_path, monkeypatch) as service:
            directory = tmp_path / ".sico/ai/agent/bridges"
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            (directory / foreign.path.name).write_bytes(foreign.path.read_bytes())
            (directory / foreign.path.name).chmod(0o600)
            assert current_host(service.owner) is None
            _, _ = live_host(hosts, tmp_path, "remote")
            path = next(path for path in directory.glob("*.json") if path.name != foreign.path.name)
            path.rename(directory / "remote-machine.json")
            assert current_host(service.owner) is None


def test_continue_preserves_a_running_task_when_a_new_host_appears(tmp_path, monkeypatch):
    import threading
    from agent_service_business_fixtures import Provider
    entered, release = threading.Event(), threading.Event()
    provider = Provider(entered, release)
    seed(tmp_path, monkeypatch)
    with business_service(tmp_path, monkeypatch, provider=provider) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        token = api.recovery.continue_session("old").result(5)
        original = service.owner.controllers["old"].current
        api.command(token, "submit", "keep running").result(5)
        wait_until(entered.is_set)
        with ExitStack() as hosts:
            live_host(hosts, tmp_path)
            assert api.recovery.continue_session("old").result(5) == token
            assert service.owner.controllers["old"].current == original
            assert not service.owner.controllers["old"]._cancel.is_set()
        release.set()
        wait_until(lambda: not service.owner.controllers["old"].busy)


def test_another_window_cannot_replace_the_controlled_runtime(tmp_path, monkeypatch):
    seed(tmp_path, monkeypatch)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        owner = client(stack, service, tmp_path)
        observer = client(stack, service, tmp_path)
        token = owner.recovery.continue_session("old").result(5)
        with ExitStack() as hosts:
            live_host(hosts, tmp_path)
            with pytest.raises(ValueError, match="其他窗口正在使用"):
                observer.recovery.continue_session("old").result(5)
            assert service.owner.controllers["old"].runtime_id == token.runtime_id
            assert not service.owner.controllers["old"].closing
            assert owner.can_control(token)


def test_live_replay_window_is_bounded_and_seated(tmp_path, monkeypatch):
    """直播回放同样受行数上限约束；窗口从任务中途开始时，游标按行状态播种。"""

    context, _address = seed(tmp_path, monkeypatch)
    with SessionJournal(tmp_path, "old") as journal:
        state = RunState(context)
        state.task = dict(id="old-task", status="executing", pending_calls=[])
        journal.append("task.started", {"text": "继续", "context": context.record()}, state)
        for index in range(60):
            journal.append("model.delta", {"text": f"delta {index}"}, state)
    monkeypatch.setattr("sico.storage.journal.REPLAY_WINDOW_ROWS", 32)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        with ExitStack() as hosts:
            live_host(hosts, tmp_path)
            token = api.recovery.continue_session("old").result(5)
        prepared = api.prepare_replay(token, 1, 200).result(30)
        feeds = service.dispatch.frontend.replay._feeds
        wait_until(lambda: len(feeds) == 1)
        stream = next(iter(feeds.values()))[2]
        cursor = stream.cursor
        assert prepared.committed_sequence - cursor.sequence == 32   # 行数上限
        assert cursor.task_id == "old-task"          # 起点在任务中途：已播种
        assert cursor.task_context is not None       # 任务上下文来自前缀 origin 索引


def test_live_reconnect_resumes_the_captured_window(tmp_path, monkeypatch):
    """重连原样重放捕获的窗口：起点相同，游标仍按行状态播种。"""

    context, _address = seed(tmp_path, monkeypatch)
    with SessionJournal(tmp_path, "old") as journal:
        state = RunState(context)
        state.task = dict(id="old-task", status="executing", pending_calls=[])
        journal.append("task.started", {"text": "继续", "context": context.record()}, state)
        for index in range(60):
            journal.append("model.delta", {"text": f"delta {index}"}, state)
    monkeypatch.setattr("sico.storage.journal.REPLAY_WINDOW_ROWS", 32)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        with ExitStack() as hosts:
            live_host(hosts, tmp_path)
            token = api.recovery.continue_session("old").result(5)
        before = api.prepare_replay(token, 1, 200).result(30)
        assert api.reconnect_session(token, before.stream) is True
        after = api.prepare_replay(token, 2, 200).result(30)
        assert after.stream.cursor.sequence == before.stream.cursor.sequence
        feeds = service.dispatch.frontend.replay._feeds
        wait_until(lambda: len(feeds) == 2)
        cursors = [entry[2].cursor for entry in feeds.values()]
        assert all(cursor.task_id == "old-task" for cursor in cursors)
        assert all(cursor.task_context is not None for cursor in cursors)
