"""Retained preparation hooks must participate in real selection/collection calls."""

from contextlib import contextmanager

import pytest
from pdk_fixture import Bridge
from test_pdk_preparation import context_with_pdk

from cadai import pdk_preparation, pdk_selection
from cadai.pdk_preparation import PdkPreparation
from cadai.pdk_schema import PdkUnavailable


@pytest.mark.parametrize("entry,hook", [("prepare", "_prepare"), ("advance", "_advance")])
def test_entry_dispatches_overridden_hook_inside_collection_lock(tmp_path, entry, hook):
    service = PdkPreparation(Bridge(), workspace=tmp_path, environment={})
    locked = []
    args, result = {}, object()

    @contextmanager
    def lock():
        locked.append(True)
        try:
            yield
        finally:
            locked.pop()

    def override(received):
        assert locked and received is args
        return result

    service._collection_lock = lock()
    setattr(service, hook, override)
    assert getattr(service, entry)(args) is result
    assert not locked


def test_details_override_runs_on_directory_completion_and_next_detail_batch(tmp_path):
    calls = []

    class Preparation(PdkPreparation):
        def _details(self, job):
            calls.append(job["collection_ref"])
            job["status"] = "collecting"

    bridge = Bridge()
    context_with_pdk(bridge)
    service = Preparation(bridge, workspace=tmp_path, environment={})
    job = service.prepare({})
    for batch in range(2):
        job = service.advance({"collection_ref": job["collection_ref"], "next_batch": batch})
        assert job["next_batch"] == batch + 1
    assert calls == [job["collection_ref"]] * 2
    assert not any(op == "device" for op, _ in bridge.calls)
    service.close()


def test_captures_override_failure_retains_checkpoint_without_publishing(tmp_path):
    class Preparation(PdkPreparation):
        def _captures(self, job, items):
            assert items
            raise PdkUnavailable("hook_capture_failed", "Injected capture hook failure")

    bridge = Bridge()
    context_with_pdk(bridge)
    service = Preparation(bridge, workspace=tmp_path, environment={})
    job = service.prepare({})
    failed = service.advance({"collection_ref": job["collection_ref"], "next_batch": 0})
    assert failed["status"] == "failed" and failed["code"] == "hook_capture_failed"
    assert failed["detailed_devices"] == 0
    assert failed["automatic_resume_allowed"]
    assert not service.ready and not list(service.data.root.glob("*.pdk.json"))
    service.close()


def test_selection_uses_overridden_prompt_for_inspect_prepare_and_guard(tmp_path):
    prompt = {"id": "site_pdk", "question": "Select the site PDK"}

    class Preparation(PdkPreparation):
        @staticmethod
        def _selection_prompt(candidates):
            assert [row["library"] for row in candidates] == ["pdk_a", "pdk_b"]
            return prompt

    bridge = Bridge()
    context_with_pdk(bridge, ("pdk_a", "pdk_b"))
    service = Preparation(bridge, workspace=tmp_path, environment={})
    for response in (service.inspect({}), service.prepare({}), service.guard("bind_pdk_device")):
        assert response["selection_question"] is prompt
        assert response["selection_required"]
    assert not service.jobs and not service.ready


def test_policy_compatibility_exports_are_owner_objects():
    for name in ("BASE_LIBRARIES", "DESIGN_TOOLS", "INSTRUCTIONS"):
        assert getattr(pdk_preparation, name) is getattr(pdk_selection, name)
    assert PdkPreparation.BASE_LIBRARIES is pdk_selection.BASE_LIBRARIES


def test_live_captures_without_workspace_do_not_require_a_pool_directory():
    from cadai import pdk_collection

    bridge = Bridge()
    context_with_pdk(bridge)
    service = PdkPreparation(bridge, environment={})
    ref = service.prepare({})['collection_ref']
    job = service.jobs[ref]
    capture = bridge.capture('directory', {'library': job['library'], 'view': job['view']})
    pdk_collection.record_directory(job, capture)
    items = job['capture']['data']['items']
    pdk_collection.detail_tasks(job, items)
    assert service.data.root is None
    assert len(list(service._captures(job, items))) == len(items)
    assert not service._pools
    service.close()
