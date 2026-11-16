"""PDK ambiguity is a prerequisite even when directories or jobs already exist."""

import json

import pytest

from cadai.mcp import McpServer
from cadai.pdk_preparation import PdkPreparation
from cadai.pdk_session import PdkSession
from cadai.pdk_schema import PdkUnavailable
from sico.service.audit import questions
from pdk_fixture import Bridge
from test_pdk_preparation import context_with_pdk


def preparation(tmp_path, names=("pdk_a", "pdk_b"), bound=False):
    bridge = Bridge()
    context_with_pdk(bridge, names)
    entry = (lambda: {
        "ok": True, "valid": True, "design": {"technology_library": "pdk_b"},
    }) if bound else None
    service = PdkPreparation(bridge, workspace=tmp_path, environment={}, entry=entry)
    return bridge, service


@pytest.mark.parametrize("source", ["environment", "workspace"])
def test_cached_data_does_not_override_existing_editor_technology(tmp_path, source):
    bridge, service = preparation(tmp_path, bound=True)
    data = bridge.capture("directory", {})
    published = service.data.publish(data, "pdk_a", "symbol")
    if source == "environment":
        service.data.environment["SICO_PDK_DATA"] = published["path"]
    bridge.calls.clear()
    info = service.inspect({})
    assert info["recommended_library"] == "pdk_b"
    assert info["candidates"][0]["library"] == "pdk_b"
    assert info["candidates"][1]["data_source"] == source
    assert not info["selection_required"]
    assert info["selected_library"] == "pdk_b"
    assert info["selection_source"] == "technology_binding"
    assert service.prepare({"library": "pdk_a"})["selection_required"]
    assert not service.jobs and not service.ready
    assert all(op in {"libraries", "skill"} for op, _ in bridge.calls)
    assert [value for op, value in bridge.calls if op == "skill"] == [
        'aiPdkInitializeLibrary("pdk_b")']


def test_host_answer_is_required_and_selected_directory_cannot_be_bypassed(tmp_path):
    bridge, service = preparation(tmp_path)
    answered = {}
    service.choice_validator = lambda lib, info: answered.get(info["selection_ref"]) == lib
    info = service.inspect({})
    assert service.prepare({"library": "pdk_b", "choice_confirmed": True})["selection_required"]
    assert not service.jobs and all(op == "libraries" for op, _ in bridge.calls)
    answered[info["selection_ref"]] = "pdk_a"
    job = service.prepare({"library": "pdk_a", "choice_confirmed": True})
    assert job["status"] == "collecting"
    service.advance({"collection_ref": job["collection_ref"], "next_batch": 0})
    assert not service.inspect({})["selection_required"]
    assert service.directory({})[0] is not None
    assert service.prepare({"library": "pdk_b", "choice_confirmed": True})["selection_required"]
    answered.clear()  # New task or cancelled answer, with the same directory in memory.
    assert service.directory({"library": "pdk_a"})[0] is None
    assert service.inspect({})["selection_required"]


def test_changed_selection_blocks_active_batch_but_allows_cancel(tmp_path):
    bridge, service = preparation(tmp_path)
    job = service.prepare({"library": "pdk_a", "choice_confirmed": True})
    context_with_pdk(bridge, ("pdk_a", "pdk_b", "pdk_c"))
    result = service.advance({"collection_ref": job["collection_ref"], "next_batch": 0})
    assert result["selection_required"]
    # The confirmed-choice registry read is allowed; device/directory scans are not.
    assert all(op in {"libraries", "categories"} for op, _ in bridge.calls)
    assert service.cancel({"collection_ref": job["collection_ref"]})["status"] == "cancelled"


@pytest.mark.parametrize("names", [(), tuple("pdk_%d" % n for n in range(7)), ("x" * 210, "pdk_b")])
def test_no_candidates_or_large_choice_sets_use_complete_free_text_question(tmp_path, names):
    bridge, service = preparation(tmp_path)
    context_with_pdk(bridge, names or ("pdk_a",))
    if not names:
        bridge.ctx["libraries"] = []
    info = service.inspect({})
    assert info["selection_required"] and info["user_input_required"]
    assert not info["selection_question"]["options"]
    assert set(row["library"] for row in info["candidates"]) == set(names)
    questions([info["selection_question"]])


def test_shared_mcp_blocks_design_but_leaves_context_recovery_and_stop_available(tmp_path):
    bridge, service = preparation(tmp_path)
    session = PdkSession(bridge=bridge, workspace=tmp_path)
    session.preparation = service

    class Client:
        def call(self, *args):
            pytest.fail("Unselected PDK reached native dispatch")

    server = McpServer(Client(), workspace=tmp_path)
    server._pdk_session = session
    service.inspect({})
    for name in ("preview_circuit_spec", "execute_circuit_operation", "bind_pdk_device"):
        response = server.handle({"method": "tools/call", "id": name,
                                  "params": {"name": name, "arguments": {}}})
        assert json.loads(response["result"]["content"][0]["text"])["selection_required"]
    for name in ("begin_circuit_task", "get_project_context", "get_circuit_operation",
                 "stop_simulation_recipe", "cancel_pdk_collection", "inspect_created_circuit"):
        assert server.pdk_selection_guard(name) is None
    assert not service.jobs


def test_complete_device_preserves_the_pending_selection_question(tmp_path):
    bridge, service = preparation(tmp_path)
    session = PdkSession(bridge=bridge, workspace=tmp_path, environment={})
    session.preparation = service
    accepted = {}
    service.choice_validator = lambda lib, info: accepted.get(info["selection_ref"]) == lib
    info = service.inspect({})
    accepted[info["selection_ref"]] = "pdk_a"
    job = service.prepare({"library": "pdk_a", "choice_confirmed": True})
    service.advance({"collection_ref": job["collection_ref"], "next_batch": 0})
    first = session.call("search_pdk_devices", {"page_size": 1})
    accepted.clear()
    with pytest.raises(PdkUnavailable) as caught:
        session.complete_device(
            first["context"]["snapshot_ref"], first["items"][0]["device_ref"]
        )
    assert caught.value.code == "selection_required"
    assert caught.value.data["selection_required"] is True
    assert caught.value.data["selection_question"]["id"] == "pdk_library"


def test_pdk_handler_returns_selection_data_as_a_user_question():
    from cadai.mcp_pdk_handler import dispatch_pdk

    class Bindings:
        def bind(self, _arguments):
            raise PdkUnavailable(
                "selection_required",
                "本次电路任务使用哪个 PDK？",
                data={
                    "ok": True,
                    "selection_required": True,
                    "selection_question": {"id": "pdk_library", "options": []},
                },
            )

    ok, detail = dispatch_pdk(
        "bind_pdk_device", {}, session=None, bindings=Bindings(), client=None
    )
    assert ok is True
    assert detail["selection_required"] is True
    assert detail["selection_question"]["id"] == "pdk_library"


def test_single_candidate_can_be_prepared_without_synthetic_confirmation(tmp_path):
    _, service = preparation(tmp_path, ("pdk_a",))
    assert service.prepare({})["library"] == "pdk_a"


def test_builtin_lookup_does_not_choose_or_bypass_a_process_library(tmp_path):
    bridge, service = preparation(tmp_path)
    context_with_pdk(bridge, ("analogLib", "pdk_a", "pdk_b"))
    assert service.prepare({"library": "analogLib"})["source"] == "builtin"
    assert service.prepare({"library": "pdk_a"})["selection_required"]
    assert not service.jobs
    assert all(op == "libraries" for op, _ in bridge.calls)


@pytest.mark.parametrize("explicit", [False, True])
def test_cached_pages_and_device_details_require_current_choice(tmp_path, explicit):
    bridge, service = preparation(tmp_path)
    session = PdkSession(bridge=bridge, workspace=tmp_path, environment={})
    session.preparation = service
    accepted = {}
    service.choice_validator = lambda lib, info: accepted.get(info["selection_ref"]) == lib
    info = service.inspect({})
    accepted[info["selection_ref"]] = "pdk_a"
    job = service.prepare({"library": "pdk_a", "choice_confirmed": True})
    service.advance({"collection_ref": job["collection_ref"], "next_batch": 0})
    query = {"page_size": 1, **({"library": "pdk_a"} if explicit else {})}
    first = session.call("search_pdk_devices", query)
    page = {**query, "snapshot_ref": first["context"]["snapshot_ref"],
            "cursor": first["page"]["next_cursor"]}
    detail = {"snapshot_ref": first["context"]["snapshot_ref"],
              "device_ref": first["items"][0]["device_ref"]}
    accepted.clear()
    bridge.calls.clear()
    assert session.call("search_pdk_devices", page)["selection_required"]
    assert session.call("get_pdk_device", detail)["selection_required"]
    assert all(op == "libraries" for op, _ in bridge.calls)


def test_unqualified_snapshot_cannot_change_selected_pdk(tmp_path):
    bridge, service = preparation(tmp_path)
    session = PdkSession(bridge=bridge, workspace=tmp_path, environment={})
    session.preparation = service
    job = service.prepare({"library": "pdk_a", "choice_confirmed": True})
    service.advance({"collection_ref": job["collection_ref"], "next_batch": 0})
    first = session.call("search_pdk_devices", {"page_size": 1})
    bridge.data["identity"]["target"]["library"] = "pdk_b"
    job = service.prepare({"library": "pdk_b", "choice_confirmed": True})
    service.advance({"collection_ref": job["collection_ref"], "next_batch": 0})
    bridge.calls.clear()
    with pytest.raises(PdkUnavailable, match="Selected PDK changed"):
        session.call("search_pdk_devices", {"snapshot_ref": first["context"]["snapshot_ref"],
                     "page_size": 1, "cursor": first["page"]["next_cursor"]})
    with pytest.raises(PdkUnavailable, match="Selected PDK changed"):
        session.call("get_pdk_device", {"snapshot_ref": first["context"]["snapshot_ref"],
                     "device_ref": first["items"][0]["device_ref"]})
    assert all(op == "libraries" for op, _ in bridge.calls)
