"""Partial bootstrap construction rolls resources back on the startup worker."""

import threading

import agent_sessions_harness as sessions_module
import pytest
from agent_gui_wait import wait_until
from agent_local_startup_fixtures import local_setup as local_setup

import sico.service.backend as backend_module
import sico.service.workbench as workbench_module
from sico.service.startup_result import StartupFailure

SERVICES = ("WorkbenchService", "EventService", "BackgroundService", "TargetService",
            "CatalogService", "MetadataService", "WorkerApi")


@pytest.mark.parametrize("failed", SERVICES)
def test_partial_session_services_close_once_before_backend_and_journal(
    local_setup, monkeypatch, failed,
):
    created, closed = [], []

    def factory(name, original):
        def construct(*args, **kwargs):
            assert threading.get_ident() != local_setup.owner
            if name == failed:
                raise OSError("service unavailable: " + name)
            service = original(*args, **kwargs)
            close = service.close

            def retire():
                assert threading.get_ident() != local_setup.owner
                assert not any(kind == "backend" for kind, _ in local_setup.calls)
                closed.append(name)
                close()

            service.close = retire
            created.append((name, service))
            return service

        return construct

    for name in SERVICES:
        monkeypatch.setattr(sessions_module, name, factory(name, getattr(sessions_module, name)))
    monkeypatch.setattr("sico_ui.startup.notice", lambda *args, **kwargs: None)
    startup, view = local_setup.create()
    wait_until(lambda: view.control.closing)
    assert startup.wait(5) and view.window is None
    with pytest.raises(StartupFailure, match="service unavailable: " + failed):
        startup.ready.result()
    assert [name for name, _ in created] == list(SERVICES[:SERVICES.index(failed)])
    assert closed == [name for name, _ in reversed(created)]
    assert all(service.wait(0) for _, service in created)
    assert local_setup.calls.count(("backend", local_setup.loops[0])) == 1
    assert local_setup.calls.count(("journal", local_setup.journals[0])) == 1
    assert local_setup.journals[0]._fd == local_setup.journals[0]._lock == -1


def test_workbench_construction_failure_closes_allocated_backend(local_setup, monkeypatch):
    created, closed = [], []
    original = backend_module.PythonBackend

    def backend(*args, **kwargs):
        loop = original(*args, **kwargs)

        def close():
            assert threading.get_ident() != local_setup.owner
            assert loop.journal._fd >= 0
            closed.append(loop)

        loop.close = close
        created.append(loop)
        return loop

    def fail(loop):
        assert threading.get_ident() != local_setup.owner
        raise OSError("workbench unavailable")

    monkeypatch.setattr(backend_module, "PythonBackend", backend)
    monkeypatch.setattr(workbench_module, "attach_workbench", fail)
    monkeypatch.setattr("sico_ui.startup.notice", lambda *args, **kwargs: None)
    startup, view = local_setup.create()
    wait_until(lambda: view.control.closing)
    assert startup.wait(5)
    with pytest.raises(StartupFailure, match="workbench unavailable"):
        startup.ready.result()
    assert len(created) == 1 and closed == created
    assert local_setup.journals[0]._fd == -1 and view.window is None
