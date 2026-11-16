"""Project-level visible-DRC job listing."""

import pytest
from visible_drc_fixture import FakeClient, FakeRunner, request_fixture

from cadai.mcp_definitions import TOOLS_BY_NAME
from cadai.visible_drc import (
    LIST_NAME,
    VisibleDrcArgumentError,
    VisibleDrcError,
    VisibleDrcService,
)


def _service(export, flow, workspace=None):
    return VisibleDrcService(
        client=FakeClient(export),
        runner_factory=lambda *_args: FakeRunner(),
        flow_resolver=lambda *args, **kwargs: flow,
        workspace=workspace,
    )


def test_visible_drc_jobs_are_listed_with_paging(tmp_path):
    arguments, export, flow, _candidate, _work_root = request_fixture(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = _service(export, flow, workspace)
    first = service.start(arguments)
    second = service.start(arguments)
    service.get(second["job_ref"])
    page = service.list_jobs({"limit": 1})
    assert page["schema"] == "cad.visible_drc.jobs.v1"
    assert page["count"] == 1
    assert page["has_more"] is True
    next_page = service.list_jobs({"offset": 1, "limit": 1})
    assert next_page["count"] == 1
    refs = {page["jobs"][0]["job_ref"], next_page["jobs"][0]["job_ref"]}
    assert refs == {first["job_ref"], second["job_ref"]}
    states = {
        row["job_ref"]: row["state"]
        for row in page["jobs"] + next_page["jobs"]
    }
    assert states[second["job_ref"]] == "violations"
    service.close()


def test_visible_drc_list_rejects_invalid_paging(tmp_path):
    arguments, export, flow, _candidate, _work_root = request_fixture(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = _service(export, flow, workspace)
    for value in ({"limit": 0}, {"offset": -1}, {"unknown": 1}):
        with pytest.raises(VisibleDrcArgumentError):
            service.list_jobs(value)
    service.close()


def test_visible_drc_list_requires_a_registry(tmp_path):
    arguments, export, flow, _candidate, _work_root = request_fixture(tmp_path)
    service = _service(export, flow)
    with pytest.raises(VisibleDrcError) as failure:
        service.list_jobs({})
    assert failure.value.code == "capability_not_available"
    service.close()


def test_visible_drc_list_is_registered_as_read_only():
    tool = TOOLS_BY_NAME[LIST_NAME]
    assert tool["annotations"]["readOnlyHint"] is True
    assert tool["annotations"]["idempotentHint"] is True
    assert set(tool["inputSchema"]["properties"]) == {"offset", "limit"}
