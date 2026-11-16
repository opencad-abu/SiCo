"""Composed visible-window DRC tool and its MCP contract."""

from __future__ import annotations

import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from visible_drc_fixture import FakeClient, FakeRunner
from visible_drc_fixture import request_fixture as _request

from cadai.mcp import McpServer
from cadai.mcp_bindings import create_bindings
from cadai.mcp_definitions import TOOLS_BY_NAME
from cadai.visible_drc import (
    CANCEL_NAME,
    GET_NAME,
    NAME,
    START_NAME,
    VisibleDrcArgumentError,
    VisibleDrcError,
    VisibleDrcService,
    check_visible_layout_drc,
)


class RestartRunner(FakeRunner):
    def __init__(self):
        super().__init__(SimpleNamespace(
            terminal=False,
            state="running",
            scope="candidate",
            message="",
            result_snapshot=None,
        ))
        self.cancelled = False

    def status(self, _run_ref):
        if self.receipt.state == "running":
            self.receipt = SimpleNamespace(
                terminal=True,
                state="unknown",
                scope="candidate",
                message="Calibre job state is unknown after runner restart",
                result_snapshot=None,
            )
        return self.receipt

    def result(self, _run_ref):
        raise RuntimeError("result snapshot is unavailable")

    def cancel(self, _run_ref):
        self.cancelled = True
        return super().cancel(_run_ref)


def test_visible_drc_exports_then_checks_candidate(tmp_path):
    arguments, export, flow, candidate, work_root = _request(tmp_path)
    client = FakeClient(export)
    runner = FakeRunner()
    calls = []

    def factory(*args):
        calls.append(args)
        return runner

    ok, detail = check_visible_layout_drc(
        arguments,
        client=client,
        runner_factory=factory,
        flow_resolver=lambda *args, **kwargs: flow,
    )

    assert ok is True
    assert detail["schema"] == "cad.visible_drc.v1"
    assert detail["export"]["path"] == str(candidate)
    assert detail["export"]["visible_bbox"] == [0, 0, 100, 100]
    assert detail["export"]["connectivity_preserved"] is False
    assert detail["export"]["lvs_eligible"] is False
    assert detail["result"]["signoff_eligible"] is False
    assert "aiLayoutExportVisibleOasis(" in client.code
    assert str(candidate) not in client.code
    assert str(work_root) in client.code
    assert calls == [(str(work_root), str(tmp_path / "calibre"), 10.0)]
    submitted, kwargs = runner.submitted
    assert submitted[0].startswith("visible-")
    assert submitted[3] == str(candidate)
    assert submitted[4] == [0, 0, 100, 100]
    assert kwargs == {"kind": "drc"}
    assert runner.closed is True


def test_visible_drc_rejects_a_different_source_cell(tmp_path):
    arguments, export, flow, _candidate, _work_root = _request(tmp_path)
    export["source"]["cell"] = "other"
    with pytest.raises(VisibleDrcError) as failure:
        check_visible_layout_drc(
            arguments,
            client=FakeClient(export),
            runner_factory=lambda *_args: FakeRunner(),
            flow_resolver=lambda *args, **kwargs: flow,
        )
    assert failure.value.code == "invalid_arguments"


def test_visible_drc_validates_arguments_and_times_out(tmp_path):
    arguments, export, flow, _candidate, _work_root = _request(tmp_path)
    with pytest.raises(VisibleDrcArgumentError):
        check_visible_layout_drc(
            {**arguments, "unexpected": "x"},
            client=FakeClient(export),
            flow_resolver=lambda *args, **kwargs: flow,
        )
    with pytest.raises(VisibleDrcArgumentError):
        check_visible_layout_drc(
            {**arguments, "window_ref": "current"},
            client=FakeClient(export),
            flow_resolver=lambda *args, **kwargs: flow,
        )

    never = SimpleNamespace(
        terminal=False, state="running", scope="candidate", message=""
    )
    ticks = iter([0.0, 0.0, 2.0])

    def factory(*_args):
        return FakeRunner(never)

    request = {**arguments, "timeout_seconds": 1}
    with pytest.raises(VisibleDrcError) as failure:
        check_visible_layout_drc(
            request,
            client=FakeClient(export),
            runner_factory=factory,
            flow_resolver=lambda *args, **kwargs: flow,
            sleep=lambda _seconds: None,
            clock=lambda: next(ticks),
        )
    assert request["timeout_seconds"] == 1
    assert failure.value.code == "timeout"


def test_visible_drc_tool_is_registered():
    for name in (NAME, START_NAME):
        tool = TOOLS_BY_NAME[name]
        assert tool["inputSchema"]["required"] == [
            "window_ref",
            "config_path",
            "work_root",
            "calibre_root",
        ]
        assert tool["annotations"]["readOnlyHint"] is False
        assert tool["annotations"]["destructiveHint"] is False
    for name in (GET_NAME, CANCEL_NAME):
        assert name in TOOLS_BY_NAME
    assert "previous_job_ref" in TOOLS_BY_NAME[NAME]["inputSchema"]["properties"]
    assert (
        "previous_job_ref"
        not in TOOLS_BY_NAME[START_NAME]["inputSchema"]["properties"]
    )
    for name in (NAME, START_NAME):
        assert "region_user_units" in TOOLS_BY_NAME[name]["inputSchema"]["properties"]


def test_visible_drc_service_supports_start_get_and_cancel(tmp_path):
    arguments, export, flow, _candidate, _work_root = _request(tmp_path)
    service = VisibleDrcService(
        client=FakeClient(export),
        runner_factory=lambda *_args: FakeRunner(),
        flow_resolver=lambda *args, **kwargs: flow,
    )
    started = service.start(arguments)
    assert started["schema"] == "cad.visible_drc.start.v1"
    assert started["job_ref"].startswith("visible-")
    final = service.get(started["job_ref"])
    assert final["terminal"] is True
    assert final["job"]["state"] == "violations"
    assert final["result"]["scope"] == "candidate"
    service.close()

    running = SimpleNamespace(
        terminal=False, state="running", scope="candidate", message=""
    )
    cancel_service = VisibleDrcService(
        client=FakeClient(export),
        runner_factory=lambda *_args: FakeRunner(running),
        flow_resolver=lambda *args, **kwargs: flow,
    )
    started = cancel_service.start(arguments)
    cancelled = cancel_service.cancel(started["job_ref"])
    assert cancelled["job"]["state"] == "cancelled"
    assert cancelled["terminal"] is True
    cancel_service.close()


def test_visible_drc_recheck_reuses_an_unchanged_export(tmp_path):
    arguments, export, flow, _candidate, _work_root = _request(tmp_path)
    runners = []

    def factory(*_args):
        runners.append(FakeRunner())
        return runners[-1]

    service = VisibleDrcService(
        client=FakeClient(export),
        runner_factory=factory,
        flow_resolver=lambda *args, **kwargs: flow,
    )
    started = service.start(arguments)
    ok, detail = service.check(
        {**arguments, "previous_job_ref": started["job_ref"]}
    )
    assert ok is True
    assert detail["reused"] is True
    assert detail["job_ref"] == started["job_ref"]
    assert detail["previous_job_ref"] == started["job_ref"]
    assert detail["delta"]["basis"] == "reused:unchanged_geometry_same_config"
    assert detail["delta"]["total_delta"] == 0
    assert len(runners) == 1
    service.close()


def test_visible_drc_recheck_reports_changed_checks(tmp_path):
    arguments, export, flow, _candidate, _work_root = _request(tmp_path)
    first = FakeRunner(result={
        "status": "violations",
        "total": 3,
        "checks": {"A": 1, "B": 2},
        "violations": [],
    })
    second = FakeRunner(result={
        "status": "violations",
        "total": 3,
        "checks": {"A": 2, "C": 1},
        "violations": [],
    })
    runners = [first, second]
    service = VisibleDrcService(
        client=FakeClient(export),
        runner_factory=lambda *_args: runners.pop(0),
        flow_resolver=lambda *args, **kwargs: flow,
    )
    started = service.start(arguments)
    export["sha256"] = "c" * 64
    ok, detail = service.check(
        {**arguments, "previous_job_ref": started["job_ref"]}
    )
    assert ok is True
    assert detail["reused"] is False
    assert detail["previous_job_ref"] == started["job_ref"]
    assert detail["delta"]["basis"] == "geometry_changed"
    assert detail["delta"]["changed_count"] == 3
    assert detail["delta"]["new_check_count"] == 1
    assert detail["delta"]["resolved_check_count"] == 1
    assert detail["delta"]["total_delta"] == 0
    service.close()


def test_visible_drc_recheck_marks_config_changes(tmp_path):
    arguments, export, flow, _candidate, _work_root = _request(tmp_path)
    runners = [FakeRunner(), FakeRunner()]
    service = VisibleDrcService(
        client=FakeClient(export),
        runner_factory=lambda *_args: runners.pop(0),
        flow_resolver=lambda *args, **kwargs: flow,
    )
    started = service.start(arguments)
    Path(arguments["config_path"]).write_text("[drc]\n# changed\n", encoding="utf-8")
    ok, detail = service.check(
        {**arguments, "previous_job_ref": started["job_ref"]}
    )
    assert ok is True
    assert detail["reused"] is False
    assert detail["delta"]["basis"] == "config_changed"
    service.close()


def test_visible_drc_recheck_rejects_a_running_previous_job(tmp_path):
    arguments, export, flow, _candidate, _work_root = _request(tmp_path)
    running = SimpleNamespace(
        terminal=False, state="running", scope="candidate", message=""
    )
    service = VisibleDrcService(
        client=FakeClient(export),
        runner_factory=lambda *_args: FakeRunner(running),
        flow_resolver=lambda *args, **kwargs: flow,
    )
    started = service.start(arguments)
    with pytest.raises(VisibleDrcError) as failure:
        service.check({**arguments, "previous_job_ref": started["job_ref"]})
    assert failure.value.code == "stale_context"
    service.close()


def test_visible_drc_explicit_region_is_sent_to_the_skill(tmp_path):
    arguments, export, flow, _candidate, _work_root = _request(tmp_path)
    export["region_source"] = "explicit"
    client = FakeClient(export)
    service = VisibleDrcService(
        client=client,
        runner_factory=lambda *_args: FakeRunner(),
        flow_resolver=lambda *args, **kwargs: flow,
    )
    detail = service.start({
        **arguments,
        "region_user_units": [10, 20, 30, 40],
    })
    assert "list(10.0 20.0 30.0 40.0)" in client.code
    assert detail["export"]["region_source"] == "explicit"
    service.close()


@pytest.mark.parametrize(
    "region_value",
    [[10, 20, 30], [30, 20, 10, 40], [10, 20, 30, "40"]],
)
def test_visible_drc_rejects_an_invalid_explicit_region(tmp_path, region_value):
    arguments, export, flow, _candidate, _work_root = _request(tmp_path)
    service = VisibleDrcService(
        client=FakeClient(export),
        runner_factory=lambda *_args: FakeRunner(),
        flow_resolver=lambda *args, **kwargs: flow,
    )
    with pytest.raises(VisibleDrcArgumentError):
        service.start({**arguments, "region_user_units": region_value})
    service.close()


def test_recheck_discard_export_keeps_the_previous_artifact(tmp_path):
    from cadai.visible_drc_recheck import discard_export

    current = tmp_path / "new.oasis"
    sidecar = tmp_path / "new.oasis.sico-visible.json"
    previous = tmp_path / "old.oasis"
    current.write_text("new\n", encoding="ascii")
    sidecar.write_text("{}\n", encoding="ascii")
    previous.write_text("old\n", encoding="ascii")
    discard_export({"candidate": current}, previous)
    assert current.exists() is False
    assert sidecar.exists() is False
    assert previous.exists() is True


def _workspace(tmp_path):
    path = tmp_path / "workspace"
    path.mkdir(parents=True)
    return path


def test_visible_drc_job_survives_service_restart(tmp_path):
    arguments, export, flow, _candidate, _work_root = _request(tmp_path)
    workspace = _workspace(tmp_path)
    first = VisibleDrcService(
        client=FakeClient(export),
        runner_factory=lambda *_args: FakeRunner(),
        flow_resolver=lambda *args, **kwargs: flow,
        workspace=workspace,
    )
    started = first.start(arguments)
    first.close()
    index = (
        workspace
        / ".sico_datahub/visible/jobs"
        / (started["job_ref"] + ".json")
    )
    assert index.is_file()
    assert stat.S_IMODE(index.stat().st_mode) == 0o600
    assert stat.S_IMODE(index.parent.stat().st_mode) == 0o700
    second = VisibleDrcService(
        client=FakeClient(export),
        runner_factory=lambda *_args: FakeRunner(),
        flow_resolver=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("restored jobs must not resolve the flow again")
        ),
        workspace=workspace,
    )
    final = second.get(started["job_ref"])
    assert final["terminal"] is True
    assert final["job"]["state"] == "violations"
    assert final["result"]["scope"] == "candidate"
    assert final["export"]["visible_bbox"] == [0, 0, 100, 100]
    assert final["export"]["lvs_eligible"] is False
    second.close()


def test_restored_running_job_becomes_unknown_and_is_not_cancelled(tmp_path):
    arguments, export, flow, _candidate, _work_root = _request(tmp_path)
    workspace = _workspace(tmp_path)
    first = VisibleDrcService(
        client=FakeClient(export),
        runner_factory=lambda *_args: FakeRunner(),
        flow_resolver=lambda *args, **kwargs: flow,
        workspace=workspace,
    )
    started = first.start(arguments)
    first.close()
    runner = RestartRunner()
    second = VisibleDrcService(
        client=FakeClient(export),
        runner_factory=lambda *_args: runner,
        workspace=workspace,
    )
    detail = second.get(started["job_ref"])
    assert detail["terminal"] is True
    assert detail["job"]["state"] == "unknown"
    assert detail["result_error"]["code"] == "source_not_captured"
    assert second.cancel(started["job_ref"])["job"]["state"] == "unknown"
    assert runner.cancelled is False
    second.close()


def test_visible_drc_restart_rejects_corrupt_or_missing_registry(tmp_path):
    arguments, export, flow, _candidate, _work_root = _request(tmp_path)
    workspace = _workspace(tmp_path)
    first = VisibleDrcService(
        client=FakeClient(export),
        runner_factory=lambda *_args: FakeRunner(),
        flow_resolver=lambda *args, **kwargs: flow,
        workspace=workspace,
    )
    started = first.start(arguments)
    first.close()
    index = (
        workspace
        / ".sico_datahub/visible/jobs"
        / (started["job_ref"] + ".json")
    )
    index.write_text("not json", encoding="utf-8")
    for service in (
        VisibleDrcService(
            client=FakeClient(export),
            runner_factory=lambda *_args: FakeRunner(),
            workspace=workspace,
        ),
        VisibleDrcService(
            client=FakeClient(export),
            runner_factory=lambda *_args: FakeRunner(),
            workspace=_workspace(tmp_path / "other"),
        ),
    ):
        with pytest.raises(VisibleDrcError) as failure:
            service.get(started["job_ref"])
        assert failure.value.code == "stale_context"
        service.close()


def test_visible_drc_start_fails_closed_when_registry_is_unavailable(tmp_path):
    arguments, export, flow, _candidate, _work_root = _request(tmp_path)

    class BrokenRegistry:
        def save(self, _record):
            raise OSError("registry is unavailable")

    service = VisibleDrcService(
        client=FakeClient(export),
        runner_factory=lambda *_args: (_ for _ in ()).throw(
            AssertionError("runner must not start without a registry record")
        ),
        flow_resolver=lambda *args, **kwargs: flow,
        registry=BrokenRegistry(),
    )
    with pytest.raises(VisibleDrcError) as failure:
        service.start(arguments)
    assert failure.value.code == "read_failed"
    service.close()


def test_visible_drc_tool_has_an_execution_binding(tmp_path):
    bindings = create_bindings(
        client=object(),
        workspace=tmp_path,
        skill_reference=object(),
        get_pdk_session=lambda: None,
        get_pdk_bindings=lambda: None,
        get_snapshot_store=lambda: None,
        get_candidate_ledger=lambda: None,
    )
    assert NAME in bindings


def test_mcp_server_visible_drc_uses_the_workspace_registry(tmp_path):
    service = McpServer(object(), workspace=tmp_path).visible_drc_service()
    assert service.registry is not None
    assert service.registry.directory == tmp_path / ".sico_datahub/visible/jobs"
