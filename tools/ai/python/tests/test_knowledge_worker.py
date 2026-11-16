"""Knowledge worker dispatch and bounded result envelope."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest

from sico.knowledge import worker
from sico.knowledge.contracts import KnowledgeError


def request(**extra):
    value = {
        "project": "project",
        "source": {"type": "local", "id": "local"},
        "seconds": 5,
        "action": "status",
        "params": {},
        "environment": {},
    }
    value.update(extra)
    return value


def test_adapter_registry_rejects_unknown_and_loads_known(monkeypatch):
    import sico.knowledge.openai_docs as docs

    assert worker.adapter({"adapter": "openai_docs_mcp"}) is docs
    with pytest.raises(KnowledgeError, match="尚未接通") as error:
        worker.adapter({"adapter": "other"})
    assert error.value.code == "unsupported"


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("build", "built"),
        ("status", "local-status"),
    ],
)
def test_local_index_actions_dispatch(monkeypatch, action, expected):
    from sico.knowledge import index

    monkeypatch.setattr(index, "build", lambda *_args: "built")
    monkeypatch.setattr(index, "status", lambda *_args: "local-status")
    assert worker.execute(request(action=action)) == expected


def test_local_search_and_read_dispatch(monkeypatch):
    from sico.knowledge import local_query

    monkeypatch.setattr(local_query, "search",
                        lambda *_args: (["hit"], {"status": "ready"}))
    monkeypatch.setattr(local_query, "read", lambda *_args: {"text": "read"})
    search = request(action="search", params={"tokens": ["x"], "limit": 3})
    assert worker.execute(search) == {"hits": ["hit"], "status": "ready"}
    assert worker.execute(request(action="read", params={"evidence_id": "e"})) == {
        "text": "read"
    }


def test_remote_actions_dispatch_to_the_selected_adapter(monkeypatch):
    calls = []
    adapter = SimpleNamespace(
        status=lambda *args: calls.append(("status", args)) or {"status": "ready"},
        search=lambda *args: (calls.append(("search", args)) or (["hit"], {"status": "ready"})),
        read=lambda *args: calls.append(("read", args)) or {"text": "remote"},
    )
    monkeypatch.setattr(worker, "adapter", lambda _source: adapter)
    source = {"type": "remote", "adapter": "fake"}
    assert worker.execute(request(source=source, action="status"))["status"] == "ready"
    assert worker.execute(request(
        source=source, action="search",
        params={"query": "q", "tokens": ["q"], "limit": 2},
    )) == {"hits": ["hit"], "status": "ready"}
    assert worker.execute(request(
        source=source, action="read", params={"evidence_id": "e"},
    )) == {"text": "remote"}
    assert [kind for kind, _args in calls] == ["status", "search", "read"]


def test_unsupported_source_or_action_fails_closed(monkeypatch):
    with pytest.raises(KnowledgeError, match="不支持"):
        worker.execute(request(action="bogus"))
    monkeypatch.setattr(worker, "adapter", lambda _source: SimpleNamespace())
    with pytest.raises(KnowledgeError, match="不支持"):
        worker.execute(request(source={"type": "remote", "adapter": "fake"}, action="bogus"))


def test_main_returns_bounded_success_error_and_diagnostic_id(monkeypatch, capsys):
    monkeypatch.setattr(worker.resource, "setrlimit", lambda *_args: None)
    monkeypatch.setattr(worker.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(worker, "execute", lambda _request: {"hits": [1]})
    monkeypatch.setattr(worker.sys, "stdin",
                        SimpleNamespace(buffer=io.BytesIO(
                            json.dumps({"diagnostic_id": "diag", "anything": True}).encode() + b"\n"
                        )))
    assert worker.main() == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "value": {"hits": [1]}}

    def rejected(_request):
        raise KnowledgeError("cancelled", "stop")

    monkeypatch.setattr(worker, "execute", rejected)
    monkeypatch.setattr(worker.sys, "stdin",
                        SimpleNamespace(buffer=io.BytesIO(
                            b'{"diagnostic_id":"diag"}\n'
                        )))
    assert worker.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": False, "code": "cancelled", "message": "stop", "diagnostic_id": "diag",
    }

    def failed(_request):
        raise OSError("offline")

    monkeypatch.setattr(worker, "execute", failed)
    monkeypatch.setattr(worker.sys, "stdin",
                        SimpleNamespace(buffer=io.BytesIO(b'{}\n')))
    assert worker.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["code"] == "unavailable"
    assert payload["exception"] == "OSError"


def test_main_rejects_oversized_request_and_bad_diagnostic(monkeypatch, capsys):
    monkeypatch.setattr(worker.resource, "setrlimit", lambda *_args: None)
    monkeypatch.setattr(worker.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(worker.sys, "stdin",
                        SimpleNamespace(buffer=io.BytesIO(b"x" * 131073 + b"\n")))
    assert worker.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["code"] == "unavailable" and payload["exception"] == "ValueError"
