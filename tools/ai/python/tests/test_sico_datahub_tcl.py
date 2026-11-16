"""Real Tcl 8.6 adapters: DH1 for Xcelium and JSON fast path for Calibre."""

import json
import os
import socket
import subprocess
import threading
from pathlib import Path

import pytest
from sico_datahub.dh1 import Dh1Request

ROOT = Path(__file__).resolve().parents[4]
ADAPTER = ROOT / "tools/common/tcl/datahub.tcl"
XCELIUM_TCL = Path(os.environ.get(
    "SICO_XCELIUM_TCLSH",
    "/software/eda/cadence/installs/XCELIUM26.03.004/tools.lnx86/"
    "tcltk-8.6.8/bin/64bit/tclsh",
))
CALIBRE_TCL = Path(os.environ.get(
    "SICO_CALIBRE_TCLSH",
    "/software/eda/siemens/installs/calibre/aok_cal_2026.3_16.11/"
    "pkgs/icv_comp.aok/bin/tclsh86",
))
MARKER = "TCL_DATAHUB_PASS"


def _run(interpreter, tmp_path, body):
    if not interpreter.is_file():
        pytest.skip(f"Tcl interpreter unavailable: {interpreter}")
    source = tmp_path / "datahub-test.tcl"
    source.write_text(
        f"source {json.dumps(str(ADAPTER))}\n"
        + body
        + f'\nputs "{MARKER}"\n'
    )
    env = dict(os.environ)
    env["TMPDIR"] = "/tmp"
    result = subprocess.run(
        [str(interpreter), str(source)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    output = result.stdout + result.stderr
    assert MARKER in output, output
    assert "*Error*" not in output and "error:" not in output, output
    return output


def test_xcelium_tcl_is_dh1_only_and_bounded(tmp_path):
    output = _run(XCELIUM_TCL, tmp_path, r'''
if {[::sico::datahub::json_available]} {error "Xcelium unexpectedly has json"}
puts "COUNT [llength $::datahubContractRows]"
set payload {{"run_ref":"r1","work_root":"/tmp/w","command_ref":"cmd1"}}
set line [::sico::datahub::dh1_encode sico.xcelium.run.v1 1 r1 run $payload]
puts "LINE $line"
set decoded [::sico::datahub::dh1_decode $line]
puts "CONTRACT [dict get $decoded contract]"
puts "PAYLOAD [dict get $decoded payload]"
if {[catch {::sico::datahub::dh1_decode "DH1 sico.xcelium.run.v1 1 r1 run !!!"} err opts]} {
    puts "INVALID [dict get $opts -errorcode]"
} else {
    error "malformed DH1 was accepted"
}
set channel [open "|sleep 1" r]
if {[catch {::sico::datahub::receive_line $channel 20} err opts]} {
    puts "TIMEOUT [dict get $opts -errorcode]"
} else {
    error "empty channel did not time out"
}
catch {close $channel}
''')
    assert "LINE DH1 sico.xcelium.run.v1 1 r1 run " in output
    assert "COUNT 13" in output
    assert 'CONTRACT sico.xcelium.run.v1' in output
    assert '"command_ref":"cmd1"' in output
    assert "INVALID SICO_DATAHUB invalid_arguments" in output
    assert "TIMEOUT SICO_DATAHUB timeout" in output


def test_calibre_tcl_uses_json_fast_path(tmp_path):
    output = _run(CALIBRE_TCL, tmp_path, r'''
if {![::sico::datahub::json_available]} {error "Calibre json package unavailable"}
set payload {{"run_ref":"r1"}}
set envelope [::sico::datahub::json_envelope \
    sico.calibre.lvs.v1 1 r1 status ok $payload]
set parsed [::sico::datahub::json_decode $envelope]
puts "STATUS [dict get $parsed status]"
puts "CONTRACT [dict get $parsed contract]"
puts "PAYLOAD [dict get $parsed payload]"
set spec [::sico::datahub::check_request \
    sico.calibre.lvs.v1 1 status {{"run_ref":"r1"}}]
puts "ACTION [lindex $spec 0]"
if {[catch {
    ::sico::datahub::check_request sico.calibre.lvs.v1 1 status \
        {{"run_ref":"r1","extra":"x"}}
} err opts]} {
    puts "REJECT [dict get $opts -errorcode]"
} else {
    error "extra request field was accepted"
}
set page [::sico::datahub::json_page 0 50 120 50 1]
set pageValue [::sico::datahub::json_decode $page]
puts "PAGE [dict get $pageValue next]"
''')
    assert "STATUS ok" in output
    assert "CONTRACT sico.calibre.lvs.v1" in output
    assert "PAYLOAD run_ref r1" in output
    assert "ACTION status" in output
    assert "REJECT SICO_DATAHUB invalid_arguments" in output
    assert "PAGE 50" in output


def test_python_and_tcl_dh1_cross_decode(tmp_path):
    payload = {
        "run_ref": "跨语言",
        "work_root": "/tmp/工程",
        "command_ref": "cmd",
        "artifact": {"path": "/tmp/xrun.log", "sha256": "a" * 64, "size": 12},
    }
    request = Dh1Request("sico.xcelium.run.v1", 1, "x1", "run", payload)
    line = request.encode_line().decode("utf-8")
    tcl_payload = json.dumps('{"run_ref":"tcl-run"}')
    output = _run(XCELIUM_TCL, tmp_path, f'''
set decoded [::sico::datahub::dh1_decode {json.dumps(line)}]
puts "PYTHON_TO_TCL [dict get $decoded payload]"
set tclLine [::sico::datahub::dh1_encode sico.xcelium.evidence.v1 1 e1 summary \
    {tcl_payload}]
puts "TCL_LINE $tclLine"
''')
    assert '"run_ref":"跨语言"' in output
    tcl_line = next(
        line.split(" ", 1)[1] for line in output.splitlines()
        if line.startswith("TCL_LINE ")
    )
    decoded = Dh1Request.decode(tcl_line)
    assert decoded.contract == "sico.xcelium.evidence.v1"
    assert decoded.payload == {"run_ref": "tcl-run"}


def test_calibre_tcl_socket_roundtrip(tmp_path):
    if not CALIBRE_TCL.is_file():
        pytest.skip(f"Tcl interpreter unavailable: {CALIBRE_TCL}")
    try:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
    except PermissionError:
        pytest.skip("loopback sockets are unavailable")
    server.listen(1)
    port = server.getsockname()[1]
    received = []

    def serve():
        try:
            connection, _address = server.accept()
            with connection:
                line = connection.makefile("rb").readline().decode("utf-8").strip()
                received.append(line)
                connection.sendall(b'{"run_ref":"socket-ok"}\n')
        finally:
            server.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    tcl_payload = json.dumps('{"run_ref":"socket-run"}')
    output = _run(CALIBRE_TCL, tmp_path, f'''
set channel [::sico::datahub::connect 127.0.0.1 {port}]
set request [::sico::datahub::dh1_encode sico.calibre.lvs.v1 1 s1 status \
    {tcl_payload}]
::sico::datahub::send_line $channel $request
set reply [::sico::datahub::receive_line $channel 2000]
set decodedReply [::sico::datahub::json_decode $reply]
puts "SOCKET [dict get $decodedReply run_ref]"
::sico::datahub::close $channel
''')
    thread.join(3)
    assert not thread.is_alive()
    assert "SOCKET socket-ok" in output
    assert received == [
        "DH1 sico.calibre.lvs.v1 1 s1 status "
        "eyJydW5fcmVmIjoic29ja2V0LXJ1biJ9"
    ]


def test_calibre_tcl_hello_and_exchange_over_channel(tmp_path):
    if not CALIBRE_TCL.is_file():
        pytest.skip(f"Tcl interpreter unavailable: {CALIBRE_TCL}")
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    failures = []

    def serve():
        try:
            connection, _address = server.accept()
            with connection:
                stream = connection.makefile("rwb", buffering=0)
                hello = json.loads(stream.readline())
                assert hello["contract"] == "sico.datahub.hello.v1"
                reply = {
                    "hub": "sico.datahub.v1",
                    "contract": hello["contract"],
                    "version": 1,
                    "id": hello["id"],
                    "action": "hello",
                    "status": "ok",
                    "payload": {
                        "schema": "datahub.hello.v1",
                        "session_ref": "endpoint-session",
                        "capabilities": ["site.python"],
                    },
                    "meta": hello["meta"],
                    "error": None,
                }
                stream.write((json.dumps(reply) + "\n").encode())
                request = json.loads(stream.readline())
                assert request["action"] == "probe"
                reply.update(
                    id=request["id"],
                    action=request["action"],
                    contract=request["contract"],
                    payload={
                        "schema": "site.capability.v1",
                        "scope": request["payload"]["scope"],
                        "read_only": True,
                    },
                )
                stream.write((json.dumps(reply) + "\n").encode())
        except Exception as exc:
            failures.append(exc)
        finally:
            server.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    payload_tcl = json.dumps(json.dumps({"scope": "tcl-endpoint"}))
    output = _run(CALIBRE_TCL, tmp_path, f'''
set channel [::sico::datahub::connect 127.0.0.1 {port}]
set hello [::sico::datahub::endpoint_hello \
    $channel endpoint-token endpoint-session]
puts "HELLO [dict get [dict get $hello payload] session_ref]"
set reply [::sico::datahub::endpoint_exchange \
    $channel sico.site.capability.v1 1 tcl-probe probe \
    {payload_tcl}]
puts "SCOPE [dict get [dict get $reply payload] scope]"
::sico::datahub::close $channel
''')
    thread.join(3)
    assert not thread.is_alive()
    assert not failures, failures
    assert "HELLO endpoint-session" in output
    assert "SCOPE tcl-endpoint" in output
