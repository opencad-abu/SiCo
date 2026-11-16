"""Durable recovery evidence plus real frontend sockets; no implicit replay."""

import uuid

from agent_service_business_fixtures import Bridge, Provider
from sico.core.contracts import BoundContext, RunState
from sico.service.inbox import SessionInbox
from sico.service.project_service import ProjectServiceLease
from sico.service.project_sessions import ProjectSessionOwner
from sico.service.remote_frontend import RemoteFrontend
from sico.storage.journal import SessionJournal


def seed(project, monkeypatch, *, config=None, environment=None, status=None):
    context = BoundContext("demo-instance", "captured-generation", "bound", dict(
        source="simulated", valid=True, cwd=str(project),
        cellview=dict(lib="demo", cell="amp", view="schematic")))
    monkeypatch.setattr("sico.service.project_sessions.provider_from_config",
                        lambda *_a, **_k: Provider())
    with ProjectServiceLease.acquire(project) as lease:
        with ProjectSessionOwner(project, environment={}, bridge_factory=Bridge) as owner:
            controller = owner.open("old", {}, context, provider_config=config,
                                    environment=environment or {})
            address = dict(project_id=lease.project_id, service_id=lease.service_id,
                           session_id="old", runtime_id=controller.runtime_id)
            # A resumable history must retain a real user input. The empty
            # startup-only session is intentionally discarded at service close.
            controller.inbox.enqueue(dict(
                kind="submit", id="prior-input", text="prior user input",
                context=context.record()), context, "chat")
            prior = controller.inbox.pending.pop()
            prior["status"] = "completed"
            controller.inbox._save(prior)
        if status:
            with SessionJournal(project, "old") as journal:
                inbox = SessionInbox(journal, context)
                inbox.enqueue(dict(kind="submit", id="original", text="captured input",
                                   context=context.record()), context, service_address=address)
                if status != "queued":
                    inbox.take()
                    state = RunState(context)
                    state.task = dict(id="old-task", status="executing", pending_calls=[])
                    journal.append("task.started", dict(input_id="original", text="captured input",
                                                        context=context.record()), state)
                    if status == "tool":
                        journal.append("tool.started", dict(id="call", name="read_cellview"), state)
                    if status == "approval":
                        journal.append("audit.requested", dict(id="approval"), state)
    return context, address


def client(stack, service, project):
    api = RemoteFrontend(service.descriptor, project, "old")
    stack.callback(api.close_desktop)
    return api


def restore(api, view=None, operation_id=None, credentials=None):
    view = view or api.recovery.inspect("old").result(5)
    operation_id = operation_id or uuid.uuid4().hex
    return api.recovery.restore(view, operation_id, credentials).result(5)
