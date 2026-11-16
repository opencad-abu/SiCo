"""Ask routing uses real binding ownership and keeps each captured tool target."""

from sico.service.frontend_session import SessionToken

from types import SimpleNamespace

import pytest
from agent_sessions_harness import DesktopSessions

from sico.core.contracts import BoundContext
from sico.service.python_backend import PythonBackend
from sico.core.tools import ToolRegistry
from sico.demo import DemoPeer
from sico.providers.mock import ScriptedProvider
from sico.service.controller import SessionController
from sico.storage.journal import SessionJournal
from sico.transport.broker import ContextBroker


def target(source, key, *, library="Work", cell="Amp", view="schematic", **snapshot):
    return BoundContext(source.instance_id, source.generation, key, {
        **source.snapshot, "window": "window:" + key,
        "cellview": {"lib": library, "cell": cell, "view_name": view}, **snapshot,
    })


def message(context, text="Inspect this captured design"):
    return {"kind": "submit", "id": context.target_id,
            "text": text, "context": context.record()}


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    initial = BoundContext("instance", "generation", "bound", {
        "source": "simulated", "valid": True, "cwd": str(tmp_path),
        "window": "window:initial",
        "cellview": {"lib": "Work", "cell": "Amp", "view_name": "schematic"},
    })
    with ContextBroker(1) as broker, SessionJournal(tmp_path, "initial") as journal:
        peer = DemoPeer(broker, initial)
        loop = PythonBackend(ScriptedProvider([]), ToolRegistry(), journal, initial)
        loop._event("session.created", {})
        controller = SessionController(loop)
        controller.paused = True
        sessions = DesktopSessions(controller, broker=broker,
                                   tools_factory=lambda _journal: ToolRegistry())
        create = sessions.create

        def paused_create(context, **kwargs):
            created = create(context, **kwargs)
            created.paused = True
            return created

        monkeypatch.setattr(sessions, "create", paused_create)
        try:
            yield SimpleNamespace(controller=controller, sessions=sessions,
                                  broker=broker, context=initial)
        finally:
            sessions.close()
            assert sessions.wait(5)
            peer.close()


@pytest.fixture
def routing(desktop):
    desktop.sessions.worker.command(SessionToken(desktop.controller.session_id, desktop.controller.runtime_id), "attach_targets", desktop.broker).result(5)
    return desktop


def accept(env, context, *, preferred=None, text="Inspect this captured design"):
    return env.sessions.worker.accept_quick(SessionToken((preferred or env.controller).session_id, (preferred or env.controller).runtime_id),
                                            message(context, text)).result(5)


@pytest.mark.parametrize("view", [
    "schematic", "symbol", "layout", "maestro", "text", "schematic_v1", "schematic_v2",
    "maestro_tran1", "maestro_tran2", "config_1", "config_2",
])
def test_cell_views_and_variants_share_session_with_distinct_targets(routing, view):
    source = target(routing.context, "ask", view=view)
    result = accept(routing, source)
    assert result.controller is routing.controller and not result.created and result.accepted
    assert len(routing.sessions.controllers) == 1
    pending = routing.controller.inbox.pending
    assert len(pending) == 1 and pending[0]["message"]["context"] == source.record()
    assert routing.controller.current == routing.context
    routing.broker.bindings.require(routing.controller.session_id, source)


@pytest.mark.parametrize("library,cell", [("Work", "TB"), ("Other", "Amp"), ("Work", "amp")])
def test_unrelated_cell_opens_once_and_later_views_reuse_it(routing, library, cell):
    source = target(routing.context, "ask", library=library, cell=cell)
    first = accept(routing, source)
    assert first.created and first.controller is not routing.controller
    assert first.controller.current == source
    assert not routing.controller.inbox.pending
    second = target(source, "ask2", library=library, cell=cell, view="maestro_tran2")
    repeated = accept(routing, second)
    assert not repeated.created and repeated.controller is first.controller
    assert len(routing.sessions.controllers) == 2
    assert [row["message"]["context"] for row in first.controller.inbox.pending] == [
        source.record(), second.record(),
    ]
    # A lost acknowledgement and a changed active page never replay the request elsewhere.
    duplicate = accept(routing, source, preferred=first.controller)
    assert duplicate.controller is first.controller and not duplicate.accepted
    assert len(first.controller.inbox.pending) == 2


@pytest.mark.parametrize("relation", ["bound", "reserved"])
def test_explicit_other_cell_relationship_is_respected(routing, relation):
    related = target(routing.context, "tb", cell="TB", view="maestro_tran1")
    if relation == "bound":
        routing.broker.register_target(related)
        routing.broker.register_session(routing.controller.session_id, related)
    else:
        with routing.broker._changed:
            routing.broker.binding_events.reserve(
                routing.controller.session_id, related, "created_tb",
            )
    source = target(related, "ask", cell="TB", view="config_2")
    assert accept(routing, source).controller is routing.controller
    assert len(routing.sessions.controllers) == 1


def test_existing_exact_owner_precedes_related_current_page(routing):
    layout = target(routing.context, "layout", view="layout")
    routing.broker.register_target(layout)
    other = routing.sessions.worker.create_session(layout).result(5)
    source = target(layout, "ask", view="layout")
    assert accept(routing, source).controller is other
    assert not routing.controller.inbox.pending
    symbol = target(routing.context, "symbol", view="symbol")
    assert accept(routing, symbol).controller is routing.controller


def test_library_path_alias_is_the_same_cell(routing, tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(library, target_is_directory=True)
    bound = target(routing.context, "real", cell="PathCell", library_path=str(library))
    routing.broker.register_target(bound)
    routing.broker.register_session(routing.controller.session_id, bound)
    source = target(bound, "ask", library="Alias", cell="PathCell", view="symbol",
                    library_path=str(alias))
    assert accept(routing, source).controller is routing.controller


def test_ciwin_without_cell_keeps_current_session(routing):
    source = target(routing.context, "ciw", cellview=None)
    assert accept(routing, source).controller is routing.controller


@pytest.mark.parametrize("invalid", ["instance", "generation", "project", "closed", "view"])
def test_invalid_source_cannot_create_or_enqueue(routing, invalid):
    source = target(routing.context, "ask", cell="Other")
    record = source.record()
    if invalid in {"instance", "generation"}:
        record["instance_id" if invalid == "instance" else "generation"] = "foreign"
    elif invalid == "project":
        record["snapshot"]["cwd"] += "/another"
    elif invalid == "closed":
        record["snapshot"]["valid"] = False
    else:
        del record["snapshot"]["cellview"]["view_name"]
    source = BoundContext.from_record(record)
    with pytest.raises(ValueError):
        accept(routing, source)
    assert len(routing.sessions.controllers) == 1 and not routing.controller.inbox.pending


def test_full_matching_queue_does_not_redirect_to_new_session(routing):
    for index in range(16):
        routing.controller.submit(str(index))
    with pytest.raises(ValueError, match="16 waiting"):
        accept(routing, target(routing.context, "ask", view="schematic_v2"))
    assert len(routing.sessions.controllers) == 1


def test_failed_save_retains_routing_for_retry_after_page_switch(routing, monkeypatch):
    source = target(routing.context, "ask", cell="TB")
    save = None
    create = routing.sessions.create

    def failing_create(context):
        controller = create(context)
        nonlocal save
        save = controller.inbox._save

        def failed(_record):
            raise OSError("disk full")

        monkeypatch.setattr(controller.inbox, "_save", failed)
        return controller

    monkeypatch.setattr(routing.sessions, "create", failing_create)
    with pytest.raises(OSError, match="disk full"):
        accept(routing, source)
    destination = routing.sessions.quick_input.destinations[source.target_id]
    assert not destination.inbox.pending and len(routing.sessions.controllers) == 2
    monkeypatch.setattr(destination.inbox, "_save", save)
    result = accept(routing, source, preferred=destination)
    assert result.accepted and result.controller is destination
    assert len(routing.sessions.controllers) == 2
