"""DH1 request frames stay bounded and interoperable with Tcl."""

import pytest
import sico_datahub_wire as wire
from sico_datahub.dh1 import Dh1Request
from sico_datahub.envelope import Envelope, Meta
from sico_datahub.errors import HubError


def test_dh1_roundtrip_and_envelope_conversion():
    request = Dh1Request(
        "sico.xcelium.run.v1",
        1,
        "r1",
        "run",
        {"run_ref": "r1", "work_root": "/tmp/work", "command_ref": "cmd1"},
    )
    line = request.encode_line()
    assert line.startswith(b"DH1 sico.xcelium.run.v1 1 r1 run ")
    assert Dh1Request.decode(line) == request
    envelope = request.to_envelope(Meta(source="tcl", target="python"))
    assert envelope.contract == request.contract
    assert envelope.payload == request.payload
    assert Dh1Request.from_envelope(envelope) == request


def test_dh1_keeps_empty_and_unicode_payloads():
    request = Dh1Request("sico.calibre.lvs.v1", 1, "r2", "status", {"run_ref": "测试"})
    assert Dh1Request.decode(request.encode()).payload == {"run_ref": "测试"}
    assert Dh1Request("sico.calibre.drc.v1", 1, "r3", "probe", {}).payload == {}


@pytest.mark.parametrize(
    "line",
    [
        b"DH1 sico.xcelium.run.v1 1 r1 run !!!",
        b"DH1 sico.xcelium.run.v1 0 r1 run e30=",
        b"DH1 bad/name 1 r1 run e30=",
        b"DH1 sico.xcelium.run.v1 1 r1 run e30= extra",
        b"DH2 sico.xcelium.run.v1 1 r1 run e30=",
        b"DH1 sico.xcelium.run.v1 1 r1 run " + b"e30=\n" + b"e30=",
    ],
)
def test_dh1_rejects_malformed_frames(line):
    with pytest.raises(HubError):
        Dh1Request.decode(line)


def test_dh1_rejects_response_envelopes_and_oversized_payloads():
    error = Envelope.failure(
        "sico.xcelium.run.v1",
        "run",
        "e1",
        "internal",
        "failed",
    )
    with pytest.raises(HubError, match="request envelopes"):
        Dh1Request.from_envelope(error)
    payload = {"command_ref": "x" * wire.MAX_PAYLOAD_BYTES}
    with pytest.raises(HubError) as failure:
        Dh1Request("sico.xcelium.run.v1", 1, "r1", "run", payload)
    assert failure.value.code == "result_too_large"
