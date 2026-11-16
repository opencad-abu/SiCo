"""Host PDK questions and real user replies use the original Codex input request."""

import json
import queue
import threading
import time
from types import SimpleNamespace

import pytest

from cadai.mcp import McpServer
from cadai.pdk_session import PdkSession
from sico.adapters.virtuoso import context_tools
from sico.codex.rpc import RpcError
from sico.service.backend import create_backend
from sico.storage.journal import SessionJournal
from pdk_fixture import Bridge
from test_agent_audit import _output, invoke
from test_agent_codex_native import answer, gateway, native_codex, settings  # noqa: F401
from test_agent_project import bound
from test_pdk_preparation import context_with_pdk


@pytest.mark.parametrize("option_label", ["pdk_a", "pdk_a (Recommended)"])
def test_pdk_recommended_suffix_is_not_part_of_the_choice_value(option_label):
    from sico.core.audit_questions import bounded
    from sico.service.audit_decisions import normalize_answers

    questions = [{
        "id": "pdk_library",
        "options": [{"label": option_label, "description": ""}],
    }]
    result = normalize_answers(
        {"pdk_library": {"choice": "pdk_a (Recommended)", "text": ""}},
        questions, ["pdk_a"], bounded=bounded,
    )
    assert result["pdk_library"]["choice"] == "pdk_a"


@pytest.fixture
def pdk_backend(tmp_path, monkeypatch, gateway, native_codex):  # noqa: F811
    bridge = Bridge()
    context_with_pdk(bridge, ("pdk_a", "pdk_b"))
    sessions = []

    def session(server):
        if server._pdk_session is None:
            server._pdk_session = PdkSession(bridge=bridge, workspace=tmp_path, environment={})
            sessions.append(server._pdk_session)
        return server._pdk_session

    monkeypatch.setattr(McpServer, "pdk_session", session)

    class Broker:
        def get_context(self, context):
            return {"ok": True, "valid": True, "source_valid": True}

        def read(self, context, method, args=None):
            assert method == "get_project_context"
            return {"ok": True, "valid": True, "source_valid": True}

    with SessionJournal(tmp_path, "pdk_choice") as journal:
        backend = create_backend(settings(gateway, native_codex, "flat"),
                                 context_tools(Broker(), journal), journal, bound(tmp_path))
        backend.tool_profile.select("desktop_compat")
        yield backend, bridge, sessions
        backend.close()


def tool_data(backend, name, arguments=None):
    response = backend._execute(name, arguments or {})
    assert not response["isError"], response
    return json.loads(response["content"][0]["text"])["data"]


def start(backend):
    backend._begin("Design with an explicitly selected PDK", backend.state.context, None, "chat")
    backend.active, backend.cancelled = True, lambda: False
    backend.runtime = SimpleNamespace(rpc=SimpleNamespace(send=lambda message, **kwargs: None),
                                      close=lambda: None)


def bind_input(backend, row, *, blocking=True):
    backend.audit.open({"id": 41, "params": {
        "threadId": "original_thread", "turnId": "original_turn", "itemId": "original_item",
        "isBlocking": blocking, "questions": row["questions"],
    }})


def test_host_question_precedes_native_request_and_rejects_model_confirmation(pdk_backend):
    backend, bridge, _ = pdk_backend
    start(backend)
    info = tool_data(backend, "get_pdk_preparation")
    row = next(iter(backend.audit.index.audits.values()))
    assert row["status"] == "pending" and row["stage_id"] == ""
    assert backend.audit.pending[row["id"]]["rpc_id"] is None
    assert not backend.audit.waiting_for_input()
    blocked = backend._execute("prepare_pdk_data", {
        "library": "pdk_a", "choice_confirmed": True,
    })
    assert blocked["isError"] and json.loads(blocked["content"][0]["text"])["status"] == "waiting_user"
    assert all(op == "libraries" for op, _ in bridge.calls)
    assert len(backend.audit.index.audits) == 1
    assert not backend.audit.pdk_choice("pdk_a", info, backend.state.context)
    with pytest.raises(ValueError, match="执行任务"):
        backend.audit.answer(row["id"], {}, "bad", "test", "other_task")
    with pytest.raises(ValueError, match="精确名称"):
        backend.audit.answer(row["id"], {"pdk_library": {"choice": "", "text": "invented"}},
                             "invalid", "test", row["task_id"])
    reply = {"pdk_library": {"choice": "pdk_a", "text": ""}}
    backend.audit.answer(row["id"], reply, "reply", "test", row["task_id"])
    backend.audit.deliver_host()
    assert backend.audit.pdk_choice("pdk_a", info, backend.state.context)
    assert not backend.audit.waiting_for_host()
    assert not backend.audit.pdk_choice("pdk_b", info, backend.state.context)
    assert not backend.audit.pdk_choice("pdk_a", {"selection_ref": "changed"}, backend.state.context)
    assert not backend.audit.answer(row["id"], reply, "reply", "test", row["task_id"])
    assert tool_data(backend, "prepare_pdk_data", {"library": "pdk_a", "choice_confirmed": True})["status"] == "collecting"
    backend._finish("completed")
    start(backend)
    assert not backend.audit.pdk_choice("pdk_a", info, backend.state.context)
    assert tool_data(backend, "prepare_pdk_data", {"library": "pdk_a", "choice_confirmed": True})["selection_required"]


def test_single_process_is_used_without_a_review_event(pdk_backend):
    backend, bridge, _ = pdk_backend
    context_with_pdk(bridge, ("pdk_a",))
    start(backend)
    info = tool_data(backend, "get_pdk_preparation")
    assert info["selection_required"] is False
    assert info["choice_confirmed"] is True
    assert info["selected_library"] == "pdk_a"
    assert info["selection_source"] == "single_candidate"
    assert not backend.audit.pending and not backend.audit.index.audits
    assert tool_data(backend, "prepare_pdk_data", {})["library"] == "pdk_a"


def test_mismatched_blocking_request_does_not_replace_host_pdk_question(pdk_backend):
    backend, bridge, _ = pdk_backend
    start(backend)
    tool_data(backend, "get_pdk_preparation")
    row = next(iter(backend.audit.index.audits.values()))
    combined = [{
        "id": "pdk_library", "header": "选择 PDK", "question": "本次电路任务使用哪个 PDK？",
        "options": row["questions"][0]["options"],
    }, {
        "id": "presentation", "header": "执行方式", "question": "前台还是后台？",
        "options": [
            {"label": "前台", "description": "在 Virtuoso 界面中执行。"},
            {"label": "后台", "description": "不依赖界面执行。"},
        ],
    }]
    with pytest.raises(ValueError, match="conflicts with the host-opened audit"):
        backend.audit.open({"id": 99, "params": {
            "threadId": "thread", "turnId": "turn", "itemId": "item",
            "isBlocking": True, "questions": combined,
        }})
    assert list(backend.audit.pending) == [row["id"]]
    assert len(backend.audit.index.audits) == 1
    assert all(op == "libraries" for op, _ in bridge.calls)


def test_mismatched_request_then_host_answer_continues_same_task(pdk_backend):
    backend, bridge, _ = pdk_backend
    start(backend)

    class Rpc:
        def __init__(self):
            self.events = queue.Queue()
            self.sent = []

        def send(self, message, **kwargs):
            self.sent.append(message)

        def request(self, method, params, **kwargs):
            self.sent.append({"method": method, "params": params})
            if method == "turn/start":
                self.events.put({"method": "item/completed", "params": {
                    "threadId": "thread", "turnId": "continued",
                    "item": {"id": "answer", "type": "agentMessage", "text": "已记录工艺选择。"},
                }})
                self.events.put({"method": "turn/completed", "params": {
                    "threadId": "thread", "turnId": "continued",
                    "turn": {"id": "continued", "status": "completed"},
                }})
                return {"turn": {"id": "continued"}}
            return {}

        def next(self):
            try:
                return self.events.get(timeout=0.05)
            except queue.Empty:
                raise

    rpc = Rpc()
    from codex_thread_fixture import bind_thread
    bind_thread(backend)
    backend.runtime = SimpleNamespace(rpc=rpc, close=lambda: None)
    tool_data(backend, "get_pdk_preparation")
    row = next(iter(backend.audit.index.audits.values()))
    combined = [
        row["questions"][0],
        {"id": "presentation", "header": "执行方式", "question": "前台还是后台？",
         "options": [{"label": "前台", "description": "在 Virtuoso 界面中执行。"},
                     {"label": "后台", "description": "不依赖界面执行。"}]},
    ]

    rpc.events.put({"id": 99, "method": "item/tool/requestUserInput", "params": {
        "threadId": "thread", "turnId": "turn", "itemId": "item", "isBlocking": True,
        "questions": combined,
    }})
    rpc.events.put({"method": "turn/completed", "params": {
        "threadId": "thread", "turnId": "turn",
        "turn": {"id": "turn", "status": "completed"},
    }})
    events = []
    worker = threading.Thread(target=lambda: events.extend(backend._turn("turn", lambda: False)))
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not any(
            message.get("id") == 99 and "error" in message for message in rpc.sent
        ):
            time.sleep(0.01)
        assert any(message.get("id") == 99 and "error" in message for message in rpc.sent)
        backend.audit.answer(
            row["id"], {"pdk_library": {"choice": "pdk_a", "text": ""}},
            "reply", "test", backend.state.task["id"],
        )
        worker.join(5)
        assert not worker.is_alive()
        assert events[-1]["kind"] == "task.completed", events
        assert len(backend.audit.index.audits) == 1
        assert any(message.get("method") == "turn/start" for message in rpc.sent)
    finally:
        if worker.is_alive():
            backend.close()
            worker.join(5)
    assert all(op == "libraries" for op, _ in bridge.calls)


@pytest.mark.parametrize("fault", ["cancel", "source", "evidence"])
def test_invalid_or_cancelled_reply_never_authorizes_collection(pdk_backend, fault):
    backend, bridge, _ = pdk_backend
    start(backend)
    info = tool_data(backend, "get_pdk_preparation")
    row = next(iter(backend.audit.index.audits.values()))
    sent = []
    backend.runtime.rpc.send = lambda message, **kwargs: sent.append(message)
    bind_input(backend, row)
    backend.audit.answer(row["id"], {"pdk_library": {"choice": "pdk_a", "text": ""}},
                         "reply", "test", row["task_id"])
    if fault == "cancel":
        backend.cancelled = lambda: True
    elif fault == "source":
        backend.state.context.snapshot["changed"] = True
    else:
        backend.audit.index.data[row["evidence"][0]["id"]]["digest"] = "changed"
    backend.audit.deliver()
    assert all("error" in reply for reply in sent)
    assert not backend.audit.pdk_choice("pdk_a", info, backend.state.context)
    assert all(op == "libraries" for op, _ in bridge.calls)


@pytest.mark.parametrize("binding", ["host", "blocking", "nonblocking"])
def test_pending_question_suspends_model_idle_timeout(pdk_backend, monkeypatch, binding):
    """未答复的问题（宿主/阻塞/非阻塞原生）期间不得按模型空闲判死任务。

    现场证据：PDK 非阻塞提问被 15 分钟空闲超时误杀成待核对
    （.cad/ai/mcp-management-20261009/m6-rerun/interrupt-recovery-20261013.json）。
    """
    backend, bridge, _ = pdk_backend
    start(backend)
    tool_data(backend, "get_pdk_preparation")
    row = next(iter(backend.audit.index.audits.values()))
    bound = binding != "host"
    if bound:
        bind_input(backend, row, blocking=(binding == "blocking"))
    backend.provider.idle_timeout = 1
    elapsed = [0]
    monkeypatch.setattr("sico.codex.backend.time", SimpleNamespace(
        monotonic=lambda: elapsed[0], strftime=time.strftime, gmtime=time.gmtime,
    ))

    requests = []
    cancel = [False]

    def request(method, params, **kwargs):
        requests.append((method, params))

    def next_event():
        elapsed[0] += 2
        if elapsed[0] == 2:
            # 模型流静默 2 秒：已超过 idle_timeout(1)，看门狗不得在等待用户时判死。
            raise queue.Empty
        cancel[0] = True
        return {"method": "turn/completed", "params": {
            "threadId": backend.thread_id, "turnId": "turn",
            "turn": {"id": "turn", "status": "completed"},
        }}

    backend.runtime.rpc.next, backend.runtime.rpc.request = next_event, request
    events = list(backend._turn("turn", lambda: cancel[0]))
    assert elapsed[0] >= 4  # 越过静默窗口，没有被模型空闲看门狗中止
    assert events[-1]["kind"] == "task.cancelled"
    assert events[-1]["payload"]["message"] in {
        "Task cancelled while waiting for the user's answer",
        "Generation stopped; no external job was stopped",
    }
    assert not requests  # 取消前没有向原生进程发起新回合
    assert all(op == "libraries" for op, _ in bridge.calls)


def test_turn_keeps_unbound_pdk_question_alive_until_cancelled(pdk_backend, gateway):  # noqa: F811
    backend, bridge, _ = pdk_backend
    gateway.responses.put(invoke("mcp__sico_ic__get_pdk_preparation", {}, "inspect"))
    gateway.responses.put(answer("Please choose the PDK."))
    cancelled = threading.Event()
    events = []
    worker = threading.Thread(
        target=lambda: events.extend(backend.run("Prepare the project PDK", cancelled.is_set))
    )
    worker.start()
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not backend.audit.pending:
            time.sleep(0.02)
        assert backend.audit.pending
        assert backend.state.task["status"] == "executing"
        assert backend.state.task["waiting_audits"]
        cancelled.set()
        worker.join(10)
        assert not worker.is_alive()
        assert events[-1]["kind"] == "task.cancelled"
        assert all(op == "libraries" for op, _ in bridge.calls)
    finally:
        if worker.is_alive():
            backend.close()
            cancelled.set()
            worker.join(10)


def test_actual_codex_pdk_choice_continues_same_turn(pdk_backend, gateway):  # noqa: F811
    backend, bridge, sessions = pdk_backend
    state = {}

    def ask(request):
        data = _output(request, "inspect")["data"]
        state["question"] = data["selection_question"]
        return invoke("request_user_input", {"questions": [state["question"]]}, "question", request=request)

    def prepare(request):
        state["answer"] = _output(request, "question")
        return invoke("mcp__sico_ic__prepare_pdk_data",
                      {"library": "pdk_a", "choice_confirmed": True}, "prepare", request=request)

    def advance(request):
        data = _output(request, "prepare")["data"]
        assert data["status"] == "collecting"
        return invoke("mcp__sico_ic__advance_pdk_collection",
                      {"collection_ref": data["collection_ref"], "next_batch": 0}, "advance", request=request)

    for response in (invoke("mcp__sico_ic__get_pdk_preparation", {}, "inspect"), ask,
                     prepare, advance, answer("Selected PDK data is ready.")):
        gateway.responses.put(response)
    events = []
    worker = threading.Thread(target=lambda: events.extend(backend.run("Choose a PDK and prepare it")))
    worker.start()
    try:
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline and not backend.audit.pending:
            time.sleep(0.02)
        row = next(iter(backend.audit.index.audits.values()))
        task = backend.state.task["id"]
        assert all(op == "libraries" for op, _ in bridge.calls)
        assert not sessions[0].preparation.jobs
        backend.audit.answer(row["id"], {"pdk_library": {"choice": "pdk_a", "text": ""}},
                             "reply", "test", task)
        worker.join(15)
        assert not worker.is_alive()
        assert events[-1]["kind"] == "task.completed", events[-3:]
        assert state["answer"]["answers"]["pdk_library"]["answers"] == ["pdk_a"]
        assert len({e["task_id"] for e in backend.journal.events() if e["task_id"]}) == 1
        assert sum(op == "directory" for op, _ in bridge.calls) == 1
        record = backend.audit.index.sync().audits[row["id"]]
        assert record["status"] == "answered" and record["binding"]["threadId"] == backend.thread_id
    finally:
        if worker.is_alive():
            backend.close()
            worker.join(10)


def test_standard_update_uses_host_answer_and_persists_overlay(pdk_backend, tmp_path):
    from pdk_standard_fixture import package, replace
    backend, _, sessions = pdk_backend
    effective, bridge, _, _ = package(tmp_path)
    start(backend)
    # Initialize the same host-owned service used for tool calls.
    tool_data(backend, 'get_pdk_data', {'library': 'synthetic', 'include_unconfirmed': True})
    from cadai.pdk_standard.freshness import check
    sessions[0].standard.updates.source_validator = lambda package, diffs: check(bridge, package, diffs)
    preview = tool_data(backend, 'prepare_pdk_data_update', {
        'library': 'synthetic', 'revision': effective.revision,
        'changes': [{'file': 'device.json', 'ops': [replace('/items/cell0/use/circuit', 'allow')]}]})
    row = next(r for r in backend.audit.index.audits.values() if r.get('pdk_update_ref'))
    assert row['status'] == 'pending'
    from sico.service.pdk_update_review import markdown
    assert 'allow' in markdown(backend.audit.index, row)
    assert backend.audit.pdk_update_confirmation(preview, backend.state.context) is None
    blocked = backend._execute('apply_pdk_data_update', {'update_ref': preview['update_ref']})
    assert blocked['isError']
    backend.audit.answer(row['id'], {'pdk_data_update': {'choice': '确认更新', 'text': ''}},
                         'reply_update', 'test-owner', row['task_id'])
    backend.audit.deliver_host()
    proof = backend.audit.pdk_update_confirmation(preview, backend.state.context)
    assert proof['decision'] == 'confirm' and proof['actor'] == 'test-owner'
    committed = tool_data(backend, 'apply_pdk_data_update', {'update_ref': preview['update_ref']})
    assert committed['status'] == 'ok'
    assert tool_data(backend, 'get_pdk_data', {'library': 'synthetic', 'include_unconfirmed': True})['items'][0]['cell'] == 'cell0'
