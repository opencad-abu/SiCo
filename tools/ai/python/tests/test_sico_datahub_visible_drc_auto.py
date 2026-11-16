"""Automatic previous-job discovery for visible-DRC rechecks."""

import json

from visible_drc_fixture import FakeClient, FakeRunner, request_fixture

from cadai.mcp_definitions import TOOLS_BY_NAME
from cadai.visible_drc import NAME, START_NAME, VisibleDrcService


def _workspace(tmp_path):
    path = tmp_path / "workspace"
    path.mkdir()
    return path


def _service(tmp_path, export, flow, workspace, runners):
    def factory(*_args):
        runners.append(FakeRunner())
        return runners[-1]

    return VisibleDrcService(
        client=FakeClient(export),
        runner_factory=factory,
        flow_resolver=lambda *args, **kwargs: flow,
        workspace=workspace,
    )


def test_auto_previous_reuses_the_latest_matching_job(tmp_path):
    arguments, export, flow, _candidate, _work_root = request_fixture(tmp_path)
    workspace = _workspace(tmp_path)
    runners = []
    service = _service(tmp_path, export, flow, workspace, runners)
    started = service.start(arguments)
    service.get(started["job_ref"])
    record = json.loads(
        (workspace / ".sico_datahub/visible/jobs" /
         (started["job_ref"] + ".json")).read_text()
    )
    assert record["state"] == "violations"
    ok, detail = service.check(arguments)
    assert ok is True
    assert detail["reused"] is True
    assert detail["previous_job_ref"] == started["job_ref"]
    assert len(runners) == 1
    service.close()


def test_auto_previous_can_be_disabled(tmp_path):
    arguments, export, flow, _candidate, _work_root = request_fixture(tmp_path)
    workspace = _workspace(tmp_path)
    runners = []
    service = _service(tmp_path, export, flow, workspace, runners)
    started = service.start(arguments)
    service.get(started["job_ref"])
    ok, detail = service.check({**arguments, "auto_previous": False})
    assert ok is True
    assert detail["reused"] is False
    assert detail["previous_job_ref"] is None
    assert "delta" not in detail
    assert len(runners) == 2
    service.close()


def test_auto_previous_skips_a_nonterminal_job(tmp_path):
    arguments, export, flow, _candidate, _work_root = request_fixture(tmp_path)
    workspace = _workspace(tmp_path)
    runners = []
    service = _service(tmp_path, export, flow, workspace, runners)
    service.start(arguments)
    ok, detail = service.check(arguments)
    assert ok is True
    assert detail["reused"] is False
    assert detail["previous_job_ref"] is None
    assert len(runners) == 2
    service.close()


def test_auto_previous_skips_a_corrupt_locator(tmp_path):
    arguments, export, flow, _candidate, _work_root = request_fixture(tmp_path)
    workspace = _workspace(tmp_path)
    runners = []
    service = _service(tmp_path, export, flow, workspace, runners)
    started = service.start(arguments)
    service.get(started["job_ref"])
    corrupt = (
        workspace
        / ".sico_datahub/visible/jobs"
        / ("visible-" + "0" * 32 + ".json")
    )
    corrupt.write_text("not json", encoding="utf-8")
    ok, detail = service.check(arguments)
    assert ok is True
    assert detail["reused"] is True
    assert detail["previous_job_ref"] == started["job_ref"]
    assert len(runners) == 1
    service.close()


def test_auto_previous_is_available_on_the_sync_check_only():
    check = TOOLS_BY_NAME[NAME]["inputSchema"]["properties"]["auto_previous"]
    assert check == {"type": "boolean", "default": True}
    assert "auto_previous" not in TOOLS_BY_NAME[START_NAME]["inputSchema"]["properties"]
