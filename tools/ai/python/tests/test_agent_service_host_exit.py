"""A closed Virtuoso bridge stops the project service it owns."""

from types import SimpleNamespace

from sico.service import launch, service_discovery, service_force_stop


def test_host_exit_stops_a_local_service(tmp_path, monkeypatch):
    calls = []

    class Request:
        def __init__(self, project, descriptor):
            calls.append(("request", project, descriptor))
            self.ready = SimpleNamespace(result=lambda timeout: calls.append(
                ("wait", timeout)))

        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(
        service_discovery, "discover_project",
        lambda project: SimpleNamespace(state="local_candidate", service="svc"),
    )
    monkeypatch.setattr(service_force_stop, "ForceServiceStop", Request)
    launch.stop_project_service_on_host_exit(tmp_path)
    assert calls[0] == ("request", tmp_path, "svc")
    assert calls[1][0] == "wait" and 0 < calls[1][1] <= 3
    assert calls[-1] == ("close",)


def test_host_exit_is_a_noop_without_a_live_service(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        service_discovery, "discover_project",
        lambda project: SimpleNamespace(state="inactive", service=None),
    )
    monkeypatch.setattr(
        service_force_stop, "ForceServiceStop",
        lambda *args: calls.append(args),
    )
    launch.stop_project_service_on_host_exit(tmp_path)
    assert calls == []
