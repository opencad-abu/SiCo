"""Bounded state events for project-level visible-DRC jobs."""

import stat
import time
from types import SimpleNamespace

import pytest
from visible_drc_fixture import FakeClient, FakeRunner, request_fixture

from cadai.visible_drc import VisibleDrcArgumentError, VisibleDrcService
from cadai.visible_drc_registry import VisibleDrcRegistry


def _service(export, flow, workspace):
    return VisibleDrcService(
        client=FakeClient(export),
        runner_factory=lambda *_args: FakeRunner(),
        flow_resolver=lambda *args, **kwargs: flow,
        workspace=workspace,
    )


def test_visible_drc_events_are_ordered_incremental_and_bounded(tmp_path):
    arguments, export, flow, _candidate, _work_root = request_fixture(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = _service(export, flow, workspace)
    started = service.start(arguments)
    assert [event["state"] for event in started["events"]] == ["queued"]
    assert started["event_cursor"] == 1
    final = service.get(started["job_ref"])
    assert [event["state"] for event in final["events"]] == [
        "queued",
        "violations",
    ]
    assert final["event_cursor"] == 2
    incremental = service.get(started["job_ref"], after=1)
    assert [event["state"] for event in incremental["events"]] == ["violations"]
    empty = service.get(started["job_ref"], after=2)
    assert empty["events"] == []
    assert empty["event_cursor"] == 2
    again = service.get(started["job_ref"])
    assert [event["seq"] for event in again["events"]] == [1, 2]
    service.close()


def test_visible_drc_events_survive_service_restart(tmp_path):
    arguments, export, flow, _candidate, _work_root = request_fixture(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first = _service(export, flow, workspace)
    started = first.start(arguments)
    first.get(started["job_ref"])
    first.close()
    second = _service(export, flow, workspace)
    restored = second.get(started["job_ref"])
    assert [event["state"] for event in restored["events"]] == [
        "queued",
        "violations",
    ]
    assert restored["event_cursor"] == 2
    second.close()


def test_visible_drc_events_ignore_corrupt_lines_and_keep_permissions(tmp_path):
    arguments, export, flow, _candidate, _work_root = request_fixture(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = _service(export, flow, workspace)
    started = service.start(arguments)
    service.get(started["job_ref"])
    path = (
        workspace
        / ".sico_datahub/visible/events"
        / (started["job_ref"] + ".jsonl")
    )
    path.write_text(path.read_text() + "not json\n", encoding="utf-8")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    second = _service(export, flow, workspace)
    restored = second.get(started["job_ref"], after=1)
    assert [event["state"] for event in restored["events"]] == ["violations"]
    second.close()
    service.close()


@pytest.mark.parametrize("after", [-1, "1", True])
def test_visible_drc_events_reject_invalid_cursors(tmp_path, after):
    arguments, export, flow, _candidate, _work_root = request_fixture(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = _service(export, flow, workspace)
    started = service.start(arguments)
    with pytest.raises(VisibleDrcArgumentError):
        service.get(started["job_ref"], after=after)
    service.close()


def test_project_cancel_request_is_applied_on_the_next_get(tmp_path):
    arguments, export, flow, _candidate, _work_root = request_fixture(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    running = SimpleNamespace(
        terminal=False, state="running", scope="candidate", message=""
    )
    service = VisibleDrcService(
        client=FakeClient(export),
        runner_factory=lambda *_args: FakeRunner(running),
        flow_resolver=lambda *args, **kwargs: flow,
        workspace=workspace,
    )
    started = service.start(arguments)
    registry = VisibleDrcRegistry(workspace)
    registry.request_cancel(started["job_ref"], "project-service")
    assert registry.cancel_requested(started["job_ref"]) is True
    detail = service.get(started["job_ref"])
    assert detail["job"]["state"] == "cancelled"
    assert detail["events"][-1]["state"] == "cancelled"
    assert registry.cancel_requested(started["job_ref"]) is False
    service.close()


def test_cancel_intent_is_applied_without_a_client_get(tmp_path):
    arguments, export, flow, _candidate, _work_root = request_fixture(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    running = SimpleNamespace(
        terminal=False, state="running", scope="candidate", message=""
    )
    service = VisibleDrcService(
        client=FakeClient(export),
        runner_factory=lambda *_args: FakeRunner(running),
        flow_resolver=lambda *args, **kwargs: flow,
        workspace=workspace,
    )
    started = service.start(arguments)
    registry = VisibleDrcRegistry(workspace)
    registry.request_cancel(started["job_ref"], "project-service")
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        events = registry.read_events(started["job_ref"])
        if events and events[-1]["state"] == "cancelled":
            break
        time.sleep(0.05)
    assert events[-1]["state"] == "cancelled"
    assert registry.cancel_requested(started["job_ref"]) is False
    service.close()


def test_owner_watcher_heartbeats_and_removes_the_lease(tmp_path):
    arguments, export, flow, _candidate, _work_root = request_fixture(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    running = SimpleNamespace(
        terminal=False, state="running", scope="candidate", message=""
    )
    service = VisibleDrcService(
        client=FakeClient(export),
        runner_factory=lambda *_args: FakeRunner(running),
        flow_resolver=lambda *args, **kwargs: flow,
        workspace=workspace,
    )
    started = service.start(arguments)
    registry = VisibleDrcRegistry(workspace)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and registry.read_lease(started["job_ref"]) is None:
        time.sleep(0.05)
    lease = registry.read_lease(started["job_ref"])
    assert lease is not None
    assert lease["owner_pid"] > 0
    service.close()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and registry.read_lease(started["job_ref"]) is not None:
        time.sleep(0.05)
    assert registry.read_lease(started["job_ref"]) is None
