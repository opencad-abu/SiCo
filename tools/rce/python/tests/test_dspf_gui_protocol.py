from __future__ import annotations

import io
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from dspf_gui_test_support import application
from rcepy.dspf_gui.bridge import StdioBridge
from rcepy.dspf_gui.protocol import ProtocolError, decode_line, encode_message


def test_special_net_name_is_json_data() -> None:
    name = 'VDD"); system("touch /tmp/not-executed")'
    line = encode_message({"id": 7, "method": "oa.highlight_net", "params": {"name": name}})
    assert decode_line(line)["params"]["name"] == name
    assert json.loads(line)["params"]["name"] == name


def test_oa_bus_name_selection_prefers_exact_then_dspf_brackets() -> None:
    from rcepy.dspf_gui.oa_actions import OaBridgeMixin

    class Harness(OaBridgeMixin):
        _last_select_error = ""

        def __init__(self, available):
            self.available = available
            self.attempts = []

        def select_net_by_name(self, name):
            self.attempts.append(name)
            self._last_select_error = f"missing {name}"
            return name in self.available

    exact = Harness({"DATA<3>"})
    assert exact._select_oa_net_name("DATA<3>") == "DATA<3>"
    assert exact.attempts == ["DATA<3>"]

    mapped = Harness({"DATA[3]"})
    assert mapped._select_oa_net_name("DATA<3>") == "DATA[3]"
    assert mapped.attempts == ["DATA<3>", "DATA[3]"]


def test_highlight_regions_are_normalized_as_finite_boxes() -> None:
    message = decode_line(
        '{"id":4,"method":"oa.highlight_net","params":'
        '{"name":"A","regions":[[1,2.5,3,4]]}}'
    )
    assert message["params"]["regions"] == [[1.0, 2.5, 3.0, 4.0]]


@pytest.mark.parametrize(
    "regions",
    [
        "null",
        "{}",
        "[[0,0,1]]",
        "[[1,0,0,1]]",
        "[[0,true,1,1]]",
        "[[0,0,1e16,1]]",
        "[" + ",".join("[0,0,1,1]" for _ in range(257)) + "]",
    ],
)
def test_protocol_rejects_invalid_highlight_regions(regions: str) -> None:
    with pytest.raises(ProtocolError) as caught:
        decode_line(
            '{"id":4,"method":"oa.highlight_net","params":'
            f'{{"name":"A","regions":{regions}}}}}'
        )
    assert caught.value.code == "invalid_params"
    assert caught.value.request_id == 4


@pytest.mark.parametrize(
    "line",
    [
        '{"id":1,"method":"bridge.ping","params":{"name":"A"}}',
        '{"id":1,"method":"oa.current_net","params":{"regions":[]}}',
        '{"id":1,"method":"gui.select_net","params":{"name":"A","regions":[]}}',
    ],
)
def test_protocol_rejects_method_specific_extra_params(line: str) -> None:
    with pytest.raises(ProtocolError, match="unsupported params field"):
        decode_line(line)


@pytest.mark.parametrize(
    "line",
    [
        "[]",
        '{"id":0,"method":"bridge.ping","params":{}}',
        '{"id":1,"method":"unknown","params":{}}',
        '{"event":"oa.selection_changed","params":{"name":7}}',
        '{"id":2,"ok":true,"result":7}',
        '{"id":2,"ok":true,"result":{"name":7}}',
    ],
)
def test_protocol_rejects_malformed_messages(line: str) -> None:
    with pytest.raises(ProtocolError):
        decode_line(line)


@pytest.mark.parametrize(
    ("field", "value", "error_code", "request_id"),
    [
        ("method", [], "invalid_method", 17),
        ("method", {}, "invalid_method", 17),
        ("event", [], "invalid_event", None),
        ("event", {}, "invalid_event", None),
    ],
)
def test_protocol_rejects_non_string_dispatch_fields(
    field: str,
    value: object,
    error_code: str,
    request_id: int | None,
) -> None:
    payload = {field: value, "params": {}}
    if field == "method":
        payload["id"] = 17
    with pytest.raises(ProtocolError) as caught:
        decode_line(json.dumps(payload))
    assert caught.value.code == error_code
    assert caught.value.request_id == request_id


def test_stdio_bridge_routes_requests_and_responses() -> None:
    application()
    output = io.StringIO()
    bridge = StdioBridge(reader=io.BytesIO(), writer=output, install_notifier=False)
    requests = []
    responses = []
    bridge.requestReceived.connect(requests.append)
    bridge.responseReceived.connect(responses.append)

    bridge.feed_line('{"id":1,"method":"gui.select_net","params":{"name":"OUT"}}')
    bridge.feed_line('{"id":2,"ok":true,"result":{"name":"OUT"}}')
    assert requests[0]["params"]["name"] == "OUT"
    assert responses[0]["id"] == 2

    bridge.feed_line('{"id":3,"method":"bridge.ping","params":{}}')
    assert json.loads(output.getvalue().splitlines()[-1]) == {
        "id": 3,
        "ok": True,
        "result": {"alive": True},
    }


def test_stdio_bridge_writes_explicit_utf8_bytes() -> None:
    application()
    output = io.BytesIO()
    bridge = StdioBridge(reader=io.BytesIO(), writer=output, install_notifier=False)
    bridge.request("oa.highlight_net", {"name": "电源"})
    assert json.loads(output.getvalue().decode("utf-8"))["params"]["name"] == "电源"


@pytest.mark.parametrize(
    ("invalid", "expected_error", "expected_message"),
    [
        (
            '{"id":7,"method":[],"params":{}}',
            {
                "id": 7,
                "ok": False,
                "error": {"code": "invalid_method", "message": "method must be a string"},
            },
            "method must be a string",
        ),
        (
            '{"id":7,"method":{},"params":{}}',
            {
                "id": 7,
                "ok": False,
                "error": {"code": "invalid_method", "message": "method must be a string"},
            },
            "method must be a string",
        ),
        ('{"event":[],"params":{}}', None, "event must be a string"),
        ('{"event":{},"params":{}}', None, "event must be a string"),
    ],
)
def test_stdio_bridge_recovers_after_invalid_dispatch(
    invalid: str,
    expected_error: dict[str, object] | None,
    expected_message: str,
) -> None:
    application()
    output = io.StringIO()
    errors = []
    bridge = StdioBridge(reader=io.BytesIO(), writer=output, install_notifier=False)
    bridge.protocolError.connect(errors.append)

    bridge.feed_data(
        (invalid + '\n{"id":8,"method":"bridge.ping","params":{}}\n').encode()
    )

    messages = [json.loads(line) for line in output.getvalue().splitlines()]
    if expected_error is not None:
        assert messages[0] == expected_error
    assert messages[-1] == {"id": 8, "ok": True, "result": {"alive": True}}
    assert len(errors) == 1
    assert isinstance(errors[0], ProtocolError)
    assert errors[0].code == (
        "invalid_method" if expected_error is not None else "invalid_event"
    )
    assert str(errors[0]) == expected_message


def test_disconnected_bridge_is_made_unavailable() -> None:
    from rcepy.dspf_gui.oa_actions import OaBridgeMixin

    class Harness(OaBridgeMixin):
        bridge = object()
        _pending = {1: "selection"}
        highlight_queries = type("Queries", (), {"invalidate": lambda self: None})()
        status = ""
        updated = False

        def _set_status(self, text):
            self.status = text

        def _update_actions(self):
            self.updated = True

    harness = Harness()
    harness._bridge_disconnected()
    assert harness.bridge is None
    assert harness._pending == {}
    assert harness.updated is True


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        ("resistance_regions", "R-region highlight updated (3 shapes)"),
        ("full_net_fallback", "full-net highlight updated (3 shapes"),
        ("full_net", "full-net highlight updated (3 shapes)"),
    ],
)
def test_highlight_response_reports_scope(scope: str, expected: str) -> None:
    from rcepy.dspf_gui.oa_actions import OaBridgeMixin

    class Harness(OaBridgeMixin):
        _pending = {5: "highlight"}
        status = ""

        def _set_status(self, text):
            self.status = text

        def _show_error(self, text):
            raise AssertionError(text)

    harness = Harness()
    harness._bridge_response({
        "id": 5,
        "ok": True,
        "result": {"name": "A", "figure_count": 3, "scope": scope},
    })
    assert expected in harness.status


def test_unmatched_bridge_response_does_not_report_an_operation() -> None:
    from rcepy.dspf_gui.oa_actions import OaBridgeMixin

    class Harness(OaBridgeMixin):
        _pending = {}
        status = ""

        def _set_status(self, text):
            self.status = text

        def _show_error(self, _text):
            raise AssertionError("an unmatched response must not reach an operation")

    harness = Harness()
    harness._bridge_response({"id": 99, "ok": True, "result": {"name": "OUT"}})
    assert harness.status == "Bridge: unmatched response id 99"
