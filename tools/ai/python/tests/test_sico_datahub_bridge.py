"""Explicit bridge process hosts the read-only site capability handler."""

import json
import signal
import subprocess
import sys
from pathlib import Path

import pytest
from sico_datahub.endpoint import UnixEnvelopeClient
from sico_datahub.envelope import Envelope
from sico_datahub.errors import HubError

ROOT = Path(__file__).resolve().parents[4]
BRIDGE = ROOT / "tools/common/python/sico_datahub_bridge.py"
MARKER = "SICO_DATAHUB_BRIDGE "
CALIBRE_ROOT = Path(
    "/software/eda/siemens/installs/calibre/aok_cal_2026.3_16.11"
)
XCELIUM_ROOT = Path(
    "/software/eda/cadence/installs/XCELIUM26.03.004"
)


def _token(path, token="bridge-token"):
    path.write_text(token + "\n", encoding="ascii")
    path.chmod(0o600)
    return path


def _start(tmp_path, *, descriptor=True, token_file=None, extra=()):
    socket = tmp_path / "bridge.sock"
    descriptor_path = tmp_path / "bridge.json" if descriptor else None
    command = [
        sys.executable,
        str(BRIDGE),
        "--socket", str(socket),
        "--token-file", str(token_file or _token(tmp_path / "token")),
        "--session-ref", "bridge-session",
        "--site-python", sys.executable,
    ]
    if descriptor_path is not None:
        command.extend(["--descriptor", str(descriptor_path)])
    command.extend(extra)
    process = subprocess.Popen(
        command,
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    line = process.stdout.readline()
    assert line.startswith(MARKER), (
        line + process.stderr.read()
    )
    return process, json.loads(line[len(MARKER):]), socket, descriptor_path


def _stop(process):
    if process.poll() is None:
        process.send_signal(signal.SIGTERM)
    try:
        return process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_bridge_hosts_site_probe_and_cleans_up(tmp_path):
    process, descriptor, socket, descriptor_path = _start(tmp_path)
    try:
        assert descriptor["schema"] == "datahub.bridge.v1"
        assert descriptor["endpoint"] == str(socket)
        assert descriptor_path.stat().st_mode & 0o777 == 0o600
        with UnixEnvelopeClient(
            socket, "bridge-token", "bridge-session"
        ) as client:
            response = client.exchange(
                Envelope(
                    contract="sico.site.capability.v1",
                    version=1,
                    action="probe",
                    id="bridge-probe",
                    payload={"scope": "bridge"},
                )
            )
            assert response.status == "ok"
            assert response.payload["scope"] == "bridge"
            assert response.payload["runtime"]["version"]
    finally:
        assert _stop(process) == 0
    assert not socket.exists()
    assert not descriptor_path.exists()


def test_bridge_rejects_public_token_file(tmp_path):
    token = _token(tmp_path / "token")
    token.chmod(0o644)
    process = subprocess.run(
        [
            sys.executable, str(BRIDGE),
            "--socket", str(tmp_path / "bridge.sock"),
            "--token-file", str(token),
            "--session-ref", "bridge-session",
            "--site-python", sys.executable,
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert process.returncode == 2
    assert "private regular file" in process.stderr
    assert not (tmp_path / "bridge.sock").exists()


def test_bridge_rejects_wrong_token(tmp_path):
    process, _descriptor, socket, _descriptor_path = _start(tmp_path, descriptor=False)
    try:
        with pytest.raises(HubError) as failure:
            UnixEnvelopeClient(socket, "wrong", "bridge-session").connect()
        assert failure.value.code == "capability_not_available"
    finally:
        assert _stop(process) == 0


def test_bridge_exposes_read_only_tool_probes(tmp_path):
    calibre = tmp_path / "calibre"
    xcelium = tmp_path / "xcelium"
    for root, executable in ((calibre, "calibre"), (xcelium, "xrun")):
        binary = root / "bin" / executable
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        binary.chmod(0o700)

    process, descriptor, socket, _descriptor_path = _start(
        tmp_path,
        extra=(
            "--calibre-root", str(calibre),
            "--xcelium-root", str(xcelium),
        ),
    )
    try:
        assert descriptor["calibre_root"] == str(calibre)
        assert descriptor["xcelium_root"] == str(xcelium)
        with UnixEnvelopeClient(
            socket, "bridge-token", "bridge-session"
        ) as client:
            for contract, schema in (
                ("sico.calibre.lvs.v1", "calibre.lvs.probe.v1"),
                ("sico.calibre.drc.v1", "calibre.drc.probe.v1"),
                ("sico.xcelium.run.v1", "xcelium.run.probe.v1"),
            ):
                root = calibre if contract.startswith("sico.calibre") else xcelium
                response = client.exchange(Envelope(
                    contract=contract,
                    version=1,
                    action="probe",
                    id=contract,
                    payload={"tool_root": str(root)},
                ))
                assert response.status == "ok"
                assert response.payload["schema"] == schema
                assert response.payload["available"] is True
            mismatch = client.exchange(Envelope(
                contract="sico.calibre.lvs.v1",
                version=1,
                action="probe",
                id="mismatch",
                payload={"tool_root": str(tmp_path / "other")},
            ))
            assert mismatch.status == "error"
            assert mismatch.error.code == "invalid_arguments"
    finally:
        assert _stop(process) == 0


def test_bridge_probes_installed_tool_roots(tmp_path):
    xrun = next((
        path for path in (
            XCELIUM_ROOT / "tools/bin/xrun",
            XCELIUM_ROOT / "tools.lnx86/bin/xrun",
            XCELIUM_ROOT / "bin/xrun",
        ) if path.is_file()
    ), None)
    if not (CALIBRE_ROOT / "bin/calibre").is_file() or xrun is None:
        pytest.skip("installed Calibre/Xcelium roots are unavailable")
    process, _descriptor, socket, _descriptor_path = _start(
        tmp_path,
        extra=(
            "--calibre-root", str(CALIBRE_ROOT),
            "--xcelium-root", str(XCELIUM_ROOT),
        ),
    )
    try:
        with UnixEnvelopeClient(
            socket, "bridge-token", "bridge-session"
        ) as client:
            for contract, root in (
                ("sico.calibre.lvs.v1", CALIBRE_ROOT),
                ("sico.xcelium.run.v1", XCELIUM_ROOT),
            ):
                response = client.exchange(Envelope(
                    contract=contract,
                    version=1,
                    action="probe",
                    id=contract,
                    payload={"tool_root": str(root)},
                ))
                assert response.status == "ok"
                assert response.payload["available"] is True
    finally:
        assert _stop(process) == 0
