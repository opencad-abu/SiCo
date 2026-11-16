"""Frontend transfer failures preserve request identity, budgets and Qt responsiveness."""

import threading
import time
from types import SimpleNamespace

import pytest
from agent_gui_wait import wait_until
from agent_service_business_fixtures import business_service, exchange, open_session
from agent_worker_fixtures import heartbeat
from PyQt5.QtWidgets import QLabel
from sico.core.cad_ref import object_ref
from sico.service.frontend_codec import (
    PAYLOAD_BYTES,
    TransferResult,
    decode_payload,
    digest,
    encode_chunk,
    encode_payload,
)
from sico.service.frontend_transport import FrontendLane
from sico.service.frontend_wire import FrontendWire
from sico.service.remote_frontend import RemoteFrontend
from sico.service.service_channel import ServiceChannel
from sico.service.service_errors import RequestUnsent, ResultUnknown, is_unknown_outcome
from sico.service.service_messages import ServiceRequest
from sico.transport.framing import ProtocolError

TARGET = {
    "library": "worklib", "cell": "OTA", "view_name": "schematic",
    "oa_view_type": "schematic", "oa_view_type_source": "platform_api",
}
INSTANCE = {
    "kind": "instance", "name": "NM1",
    "master": {"library": "smic18", "cell": "nmos4", "view_name": "symbol"},
    "hierarchy": [],
}


@pytest.mark.parametrize("damage", ["offset", "digest", "method", "operation", "boolean"])
def test_upload_rejects_damage_before_dispatch(damage):
    started = []
    hub = SimpleNamespace(start=lambda *args: started.append(args), retire=lambda *_a: None)
    wire = FrontendWire(hub, None)
    method, key = "frontend.query", "operation"
    wire.reply(method, dict(step="reset"), key)
    data = encode_payload(dict(operation="catalog", params={}))
    if damage == "offset":
        request = dict(step="put", offset=2, data=encode_chunk(data))
    else:
        wire.reply(method, dict(step="put", offset=0, data=encode_chunk(data)), key)
        request = dict(step="run", size=len(data), digest=digest(data))
        if damage == "digest":
            request["digest"] = "f" * 64
        elif damage == "method":
            method = "frontend.command"
        elif damage == "operation":
            key = "other"
        elif damage == "boolean":
            request["size"] = True
    with pytest.raises(ProtocolError):
        validated = ServiceRequest(method, key, request)
        wire.reply(method, validated.params, key)
    assert not started


@pytest.mark.parametrize("value", [object(), {"many": [0] * 200001},
                                  {"wide": "x" * (PAYLOAD_BYTES + 1)}, {"n": 2**64}])
def test_oversize_or_resources_fail_before_json_copy(value, monkeypatch):
    def no_dump(*_args, **_kwargs):
        raise AssertionError("Unbounded value reached JSON serialization")

    monkeypatch.setattr("sico.service.frontend_codec.json.dumps", no_dump)
    with pytest.raises(ProtocolError):
        encode_payload(value)


@pytest.mark.parametrize("row", [None, [], {"state": True}, {"state": "ready", "x": object()}])
def test_invalid_transfer_response_is_protocol_error(row):
    with pytest.raises(ProtocolError):
        TransferResult(row)


def test_socket_reply_wait_does_not_block_qt_other_clients_or_detach(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = ServiceChannel.exchange

    def paused(channel, request, **kwargs):
        reply = original(channel, request, **kwargs)
        if request.method == "frontend.query" and request.params["step"] == "read":
            entered.set()
            assert release.wait(5)
        return reply

    with business_service(tmp_path, monkeypatch) as service:
        lane = FrontendLane(service.descriptor, "a" * 32, "frontend.query")
        widget = QLabel("network paused")
        monkeypatch.setattr(ServiceChannel, "exchange", paused)
        try:
            receipt = lane.request("catalog", dict(version=None))
            wait_until(entered.is_set)
            heartbeat(widget)
            assert exchange(service, ServiceRequest()).pending.sessions == 0
            lane.close()
            heartbeat(widget)
            assert receipt.cancelled() and not release.is_set()
        finally:
            release.set()
            lane.close()
            lane.thread.join(5)
            widget.close()


def test_receipt_lost_after_durable_acceptance_queries_original_id(tmp_path, monkeypatch):
    original = ServiceChannel.exchange
    failed = []

    def lost(channel, request, **kwargs):
        reply = original(channel, request, **kwargs)
        if (not failed and request.method == "frontend.command"
                and request.params["step"] == "read" and reply.status.value["state"] == "ready"):
            failed.append(request.operation_id)
            raise RequestUnsent("Reply observation lost")
        return reply

    with business_service(tmp_path, monkeypatch) as service:
        token = open_session(service).session
        api = RemoteFrontend(service.descriptor, tmp_path, "live")
        try:
            api.open_initial("attach", dict(session_id="live")).result(5)
            api.control.request(token, "acquire").result(5)
            epoch = api._commands.epoch
            monkeypatch.setattr(ServiceChannel, "exchange", lost)
            with pytest.raises(ResultUnknown) as captured:
                api.command(token, "submit", "once only").result(5)
            assert captured.value.operation_id == failed[0]
            assert api._commands.epoch > epoch
            result = api.query_input(token, failed[0]).result(5).status
            assert result.outcome == "accepted"
            assert service.owner.controllers["live"].wait(5)
            assert service.provider.seen == ["once only"]
        finally:
            api.close_desktop()


def test_business_unknown_preserves_connection_and_reconcile_authority(tmp_path, monkeypatch):
    from test_agent_interrupted_reconcile import interrupt, status_tool

    with business_service(tmp_path, monkeypatch) as service:
        token = open_session(service).session
        api = RemoteFrontend(service.descriptor, tmp_path, "live")
        try:
            api.open_initial("attach", dict(session_id="live")).result(5)
            grant = api.control.request(token, "acquire").result(5)
            lane = api._commands
            connection, epoch = lane.connection_id, lane.epoch
            controller = service.owner.controllers["live"]
            task = interrupt(controller, calls=1)
            observations = []
            status_tool(controller, observations)
            failures = []

            def unavailable():
                failures.append(True)
                raise RuntimeError("Native runtime is unavailable")

            monkeypatch.setattr(controller.backend_commands, "resource_status", unavailable)
            with pytest.raises(ResultUnknown) as captured:
                api.command(token, "resource_status").result(5)
            error = captured.value
            assert is_unknown_outcome(error) and error.operation_id
            assert error.method == "resource_status"
            assert lane.connection_id == connection and lane.epoch == epoch
            assert api.can_control(token) and len(failures) == 1
            assert api.control.request(token).result(5).generation == grant.generation
            api.command(token, "query_interrupted", "call-0", expected_task=task).result(5)
            api.command(token, "resolve_interrupted", "call-0", "completed",
                        expected_task=task, evidence="Original read-only receipt").result(5)
            assert not controller.paused and controller._task["status"] == "cancelled"
            assert len(observations) == 1 and not service.provider.seen
            assert len(failures) == 1
            assert lane.connection_id == connection and lane.epoch == epoch
        finally:
            api.close_desktop()


def test_open_object_target_requires_control_and_uses_read_only_mode(tmp_path, monkeypatch):
    from sico.service import object_instances as cross

    with business_service(tmp_path, monkeypatch) as service:
        token = open_session(service).session
        api = RemoteFrontend(service.descriptor, tmp_path, "live")
        calls = []

        def opened(_project, _instance, ref, **_kwargs):
            calls.append(dict(ref))
            return {
                "window": "window:9", "window_ref": "wctx_fixture_1",
            }

        monkeypatch.setattr(cross, "open_target_on_instance", opened)
        try:
            api.open_initial("attach", dict(session_id="live")).result(5)
            ref = object_ref(TARGET, INSTANCE)
            with pytest.raises(ValueError, match="控制权"):
                api.open_object_target(token, ref)
            api.control.request(token, "acquire").result(5)
            result = api.open_object_target(token, ref).result(5)
            assert result["window_ref"] == "wctx_fixture_1"
            assert result["target"] == TARGET
            assert calls and calls[0]["target"]["library"] == "worklib"
            api.control.request(token, "release").result(5)
            with pytest.raises(ValueError, match="控制权"):
                api.open_object_target(token, ref)
        finally:
            api.close_desktop()


def test_open_object_target_on_explicit_candidate_requires_control(tmp_path, monkeypatch):
    from sico.service import object_instances as cross

    candidate = "b" * 64
    with business_service(tmp_path, monkeypatch) as service:
        token = open_session(service).session
        api = RemoteFrontend(service.descriptor, tmp_path, "live")
        calls = []

        def opened(_project, selected, ref, **_kwargs):
            calls.append((selected, dict(ref)))
            return {
                "window": "window:9", "window_ref": "wctx_fixture_1",
            }

        monkeypatch.setattr(cross, "open_target_on_candidate", opened)
        try:
            api.open_initial("attach", dict(session_id="live")).result(5)
            ref = object_ref(TARGET, INSTANCE)
            with pytest.raises(ValueError, match="控制权"):
                api.open_object_target(token, ref, candidate)
            api.control.request(token, "acquire").result(5)
            result = api.open_object_target(token, ref, candidate).result(5)
            assert result["window_ref"] == "wctx_fixture_1"
            assert calls[0][0] == candidate
            assert calls[0][1]["target"] == TARGET
        finally:
            api.close_desktop()


def test_object_query_preserves_target_not_open_code(tmp_path, monkeypatch):
    from sico.service.targets import BridgeTargetProvider, TargetBridgeError

    with business_service(tmp_path, monkeypatch) as service:
        token = open_session(service).session
        api = RemoteFrontend(service.descriptor, tmp_path, "live")
        controller = service.owner.controllers["live"]
        original_broker = controller.bindings.broker
        controller.bindings.broker = SimpleNamespace()

        def missing(_provider, _ref, _window_ref=None):
            raise TargetBridgeError(
                "The requested cellview is not open", "target_not_open",
            )

        monkeypatch.setattr(BridgeTargetProvider, "highlight", missing)
        source = None
        try:
            api.open_initial("attach", dict(session_id="live")).result(5)
            source = api.object_source(token)
            with pytest.raises(ValueError) as captured:
                source.highlight(object_ref(TARGET, INSTANCE)).result(5)
            assert captured.value.code == "target_not_open"
        finally:
            if source is not None:
                source.close()
            controller.bindings.broker = original_broker
            api.close_desktop()


def test_cross_instance_lookup_and_highlight_are_explicit(tmp_path, monkeypatch):
    from sico.service import object_instances as cross

    candidate = "b" * 64
    with business_service(tmp_path, monkeypatch) as service:
        token = open_session(service).session
        api = RemoteFrontend(service.descriptor, tmp_path, "live")
        calls = []

        monkeypatch.setattr(cross, "instance_windows", lambda *_args, **_kwargs: [{
            "candidate": candidate, "instance": "instance-2",
            "generation": "generation-2",
            "windows": [{
                "window_ref": "wctx_other_1", "window": "window:9",
                "title": "OTA schematic",
            }],
        }])
        highlighted = {
            "ok": True, "valid": True, "schema": "cad.cv.highlight.v1",
            "session_ref": "fixture", "target": TARGET, "kind": "instance",
            "name": "NM1", "window_ref": "wctx_other_1",
            "highlighted": True, "highlight_count": 1, "zoomed": True,
            "selection_preserved": False, "selected": True,
        }

        def locate(_project, selected, _ref, window_ref, **_kwargs):
            calls.append((selected, window_ref))
            return {
                "candidate": selected, "instance": "instance-2",
                "generation": "generation-2", "result": highlighted,
            }

        monkeypatch.setattr(cross, "highlight_instance", locate)
        try:
            api.open_initial("attach", dict(session_id="live")).result(5)
            ref = object_ref(TARGET, INSTANCE)
            found = api.object_instances(token, ref).result(5)
            assert found["instances"][0]["candidate"] == candidate
            with pytest.raises(ValueError, match="控制权"):
                api.highlight_object_instance(
                    token, ref, candidate, "wctx_other_1",
                )
            api.control.request(token, "acquire").result(5)
            result = api.highlight_object_instance(
                token, ref, candidate, "wctx_other_1",
            ).result(5)
            assert result["instance"] == "instance-2"
            assert result["result"]["selected"] is True
            assert calls == [(candidate, "wctx_other_1")]
        finally:
            api.close_desktop()


def test_object_instances_accepts_a_reachable_instance_without_a_window(tmp_path, monkeypatch):
    from sico.service import object_instances as cross

    candidate = "b" * 64
    with business_service(tmp_path, monkeypatch) as service:
        token = open_session(service).session
        api = RemoteFrontend(service.descriptor, tmp_path, "live")
        monkeypatch.setattr(cross, "instance_windows", lambda *_args, **_kwargs: [{
            "candidate": candidate, "instance": "instance-2",
            "generation": "generation-2", "windows": [],
        }])
        try:
            api.open_initial("attach", dict(session_id="live")).result(5)
            found = api.object_instances(token, object_ref(TARGET, INSTANCE)).result(5)
            assert found["instances"] == [{
                "candidate": candidate, "instance": "instance-2",
                "generation": "generation-2", "windows": [],
            }]
        finally:
            api.close_desktop()


def test_query_transfer_recovers_after_connection_failure_without_old_replay(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service:
        token = open_session(service).session
        api = RemoteFrontend(service.descriptor, tmp_path, "live")
        try:
            api.open_initial("attach", dict(session_id="live")).result(5)
            prepared = api.prepare_replay(token, 1, 200).result(5)
            first_connection = api._events.connection_id
            with pytest.raises(ProtocolError):
                api._events.request("invalid", {}).result(5)
            with pytest.raises(ValueError):
                prepared.stream.validate_current()
            fresh = api.prepare_replay(token, 2, 200).result(5)
            assert api._events.connection_id != first_connection
            fresh.stream.validate_current()
            wait_until(lambda: len(service.dispatch.frontend.replay._feeds) == 1)
        finally:
            api.close_desktop()


def test_result_chunk_cannot_change_manifest_or_digest():
    # A corrupt service response must never be reported as an explicit business rejection.
    lane = object.__new__(FrontendLane)
    lane.timeout, lane.method, lane.closed = 1, "frontend.query", threading.Event()
    data = encode_payload(dict(ok=True, value=None))

    class Damaged:
        def exchange(self, request, **kwargs):
            p = request.params
            value = {"reset": dict(state="empty"),
                     "put": dict(state="upload", size=p.get("offset", 0)
                                 + len(__import__("base64").b64decode(p.get("data", "")))),
                     "run": dict(state="pending", instructions=""),
                     "read": dict(state="ready", size=len(data), digest="f" * 64,
                                  offset=0, data=encode_chunk(data))}[p["step"]]
            return SimpleNamespace(status=TransferResult(value))

    item = SimpleNamespace(key="key", operation="catalog", params={},
                           timeout=None,
                           ready=SimpleNamespace(cancelled=lambda: False))
    with pytest.raises(ProtocolError, match="digest"):
        lane._exchange(Damaged(), item)
    assert decode_payload(data)["ok"] is True


def test_declared_request_timeout_outlives_the_lane_default(tmp_path, monkeypatch):
    """长操作在请求上声明期限：同一车道里其余请求仍用默认期限。"""
    original = ServiceChannel.exchange

    def slow(channel, request, **kwargs):
        if request.params.get("step") == "read":
            time.sleep(0.4)
        return original(channel, request, **kwargs)

    with business_service(tmp_path, monkeypatch) as service:
        lane = FrontendLane(service.descriptor, "b" * 32, "frontend.query", timeout=0.1)
        monkeypatch.setattr(ServiceChannel, "exchange", slow)
        try:
            with pytest.raises(ResultUnknown):
                lane.request("home", {}).result(5)          # 默认期限：如实报告超时
            value = lane.request("home", {}, timeout=8).result(8)
            assert value == {"platforms": ["CDNS-IC"]}      # 声明的期限放宽本次请求
        finally:
            lane.close()
            lane.thread.join(5)
