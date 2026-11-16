"""Shared Ask admission retains the captured source and never retries mutations."""

from contextlib import closing
from types import SimpleNamespace

import pytest
from agent_service_business_fixtures import business_service
from test_agent_service_host import host_bridge, target

from sico.service.frontend_ask import FrontendAsk
from sico.storage.inbox_records import read_input
from sico.transport.bridge_client import BridgeClient


def test_related_views_share_one_owner_and_query_preserves_original_input(tmp_path, monkeypatch):
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    with host_bridge(tmp_path, target(tmp_path)) as (_, _client), business_service(
            tmp_path, monkeypatch, bridge_factory=BridgeClient) as service:
        captured, destination = [], None
        for view in ("schematic", "maestro", "layout", "schematic"):
            context = target(alias, "ask_" + str(len(captured)), view=view)
            captured.append(context.record())
            with closing(FrontendAsk(tmp_path, context, environment={})) as ask:
                result = ask.submit("Ask from " + view, context.target_id)
                assert result.outcome == "accepted"
                if destination is None:
                    destination = result.address
                assert result.address == destination
                owner = service.owner.controllers[destination.session.session_id]
                assert owner.wait(5)
                before = owner.journal.events()
                assert ask.query(context.target_id).address == destination
                assert owner.journal.events() == before
        assert len(service.owner.controllers) == 1
        assert [row["payload"]["context"] for row in owner.journal.events()
                if row["kind"] == "task.started"] == captured
        assert service.provider.seen == ["Ask from " + view for view in
                                         ("schematic", "maestro", "layout", "schematic")]


def test_invalid_project_or_request_is_rejected_before_service_discovery(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("sico.service.host_gateway.ServiceStartup",
                        lambda *_args, **_kw: calls.append("discovery"))
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(ValueError, match="project mismatch"):
        FrontendAsk(other, target(tmp_path))
    with closing(FrontendAsk(tmp_path, target(tmp_path), environment={})) as ask:
        for text, key in (("Ask", "foreign"), ("", "initial"), ("a\0b", "initial")):
            with pytest.raises(ValueError):
                ask.submit(text, key)
        with pytest.raises(ValueError, match="captured request"):
            ask.query("foreign")
    assert not calls


def test_caller_cannot_retarget_a_captured_ask(tmp_path, monkeypatch):
    context = target(tmp_path)
    with closing(FrontendAsk(tmp_path, context, environment={})) as ask:
        context.snapshot["cellview"]["cell"] = "Changed"
        seen = []
        monkeypatch.setattr(ask._gateway, "submit", lambda message: seen.append(message))
        ask.submit("Ask", "initial")
    assert seen[0]["context"]["snapshot"]["cellview"]["cell"] == "Amp"


@pytest.mark.parametrize("outcome", ["unknown", "rejected"])
def test_nonaccepted_receipt_is_returned_without_a_second_mutation(tmp_path, monkeypatch, outcome):
    from sico.service.service_business_dto import InputResult

    with host_bridge(tmp_path, target(tmp_path)) as (_, _client), business_service(
            tmp_path, monkeypatch, bridge_factory=BridgeClient) as service:
        calls = []

        def exchange(descriptor, request, **_kwargs):
            assert descriptor == service.descriptor
            calls.append(request.method)
            result = InputResult(None, "initial", outcome,
                                 "stale_session" if outcome == "rejected" else "")
            return SimpleNamespace(status=result)

        monkeypatch.setattr("sico.service.host_gateway.exchange_service", exchange)
        with closing(FrontendAsk(tmp_path, target(tmp_path), environment={})) as ask:
            assert ask.submit("Ask", "initial").outcome == outcome
        assert calls == ["host.submit"] and not service.owner.controllers


def test_query_of_an_unsubmitted_request_cannot_submit_it(tmp_path, monkeypatch):
    with host_bridge(tmp_path, target(tmp_path)) as (_, _client), business_service(
            tmp_path, monkeypatch, bridge_factory=BridgeClient) as service:
        with closing(FrontendAsk(tmp_path, target(tmp_path), environment={})) as ask:
            assert ask.query("initial").outcome == "unknown"
        assert not service.owner.controllers and not service.provider.seen


def test_attachment_reaches_the_task_once_and_survives_receipt_queries_and_restart(
        tmp_path, monkeypatch):
    context = target(tmp_path)
    attachment = "被捕获的编辑器资料\n" + "x" * 16001
    with host_bridge(tmp_path, context), business_service(
            tmp_path, monkeypatch, bridge_factory=BridgeClient) as service:
        with closing(FrontendAsk(tmp_path, context, environment={})) as ask:
            first = ask.submit("分析附件", context.target_id, attachment=attachment)
            owner = service.owner.controllers[first.address.session.session_id]
            assert first.outcome == "accepted" and owner.wait(5)
            row = read_input(owner.inbox.directory, context.target_id)
            manifest, = row["message"]["attachments"]
            assert owner.journal.artifact_text(manifest["path"], manifest["sha256"]) == attachment
            started, = [r for r in owner.journal.events() if r["kind"] == "task.started"]
            assert started["payload"]["attachments"] == [manifest]
            assert manifest["path"] in service.provider.seen[0]
            before = owner.journal.events()
            files = list(owner.journal.attachments.iterdir())
            assert ask.query(context.target_id).address == first.address
            assert ask.submit("分析附件", context.target_id, attachment).address == first.address
            for changed in (None, "修改后的附件"):
                assert ask.submit("分析附件", context.target_id, changed).code == "operation_conflict"
            assert owner.journal.events() == before
            assert list(owner.journal.attachments.iterdir()) == files
    with business_service(tmp_path, monkeypatch, bridge_factory=BridgeClient) as restarted:
        with closing(FrontendAsk(tmp_path, context, environment={})) as ask:
            assert ask.query(context.target_id).address == first.address
        assert not restarted.owner.controllers and not restarted.provider.seen


def test_attachment_retained_after_uncertain_ack_is_never_requeued(tmp_path, monkeypatch):
    initial, context = target(tmp_path), target(tmp_path, "uncertain")
    with host_bridge(tmp_path, initial), business_service(
            tmp_path, monkeypatch, bridge_factory=BridgeClient) as service:
        with closing(FrontendAsk(tmp_path, initial, environment={})) as ask:
            first = ask.submit("Initial", initial.target_id)
        owner = service.owner.controllers[first.address.session.session_id]
        assert owner.wait(5)
        before = list(service.provider.seen)
        with closing(FrontendAsk(tmp_path, context, environment={})) as ask:
            with monkeypatch.context() as fault:
                def fail(_directory):
                    raise OSError("inbox directory sync failed")
                fault.setattr("sico.service.inbox.sync_directory", fail)
                assert ask.submit("Ask", context.target_id, "附件").outcome == "unknown"
            receipt = ask.query(context.target_id)
            assert receipt.outcome == "accepted" and receipt.recovery_required
            owner = service.owner.controllers[receipt.address.session.session_id]
            row = read_input(owner.inbox.directory, context.target_id)
            manifest, = row["message"]["attachments"]
            assert owner.journal.artifact_text(manifest["path"], manifest["sha256"]) == "附件"
            files = list(owner.journal.attachments.iterdir())
            assert ask.submit("Ask", context.target_id, "附件").recovery_required
            assert list(owner.journal.attachments.iterdir()) == files
            assert not owner.inbox.pending and service.provider.seen == before


@pytest.mark.parametrize("attachment", [True, {}, [], 1, "bad\0text", "a" * (512 * 1024)])
def test_invalid_attachment_is_rejected_before_discovery(tmp_path, monkeypatch, attachment):
    monkeypatch.setattr("sico.service.host_gateway.ServiceStartup",
                        lambda *_args, **_kw: pytest.fail("invalid input reached discovery"))
    with closing(FrontendAsk(tmp_path, target(tmp_path), environment={})) as ask:
        with pytest.raises(ValueError):
            ask.submit("Ask", "initial", attachment)
