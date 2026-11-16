"""The explicit bridge hosts Calibre catalog and bounded job actions."""

import json
import signal
import subprocess
import sys
import time
from pathlib import Path

from sico_calibre_fixture import (
    drc_script,
    make_calibre,
    make_flow,
    write_script,
)
from sico_datahub.endpoint import UnixEnvelopeClient
from sico_datahub.envelope import Envelope

ROOT = Path(__file__).resolve().parents[4]
BRIDGE = ROOT / "tools/common/python/sico_datahub_bridge.py"
MARKER = "SICO_DATAHUB_BRIDGE "


def _start(tmp_path, flow, calibre_body=None, lvs_body="exit 0\n"):
    calibre = make_calibre(tmp_path)
    if calibre_body is not None:
        write_script(calibre / "bin/calibre", calibre_body)
    drc = write_script(
        tmp_path / "bin/drc",
        drc_script(
            flow.run_dir,
            summary="TOTAL RESULT COUNT = 0\n",
            results="# Calibre DRC Results Database\n",
        ),
    )
    lvs = write_script(tmp_path / "bin/lvs", lvs_body)
    token = tmp_path / "token"
    token.write_text("bridge-token\n", encoding="ascii")
    token.chmod(0o600)
    descriptor = tmp_path / "bridge.json"
    socket = tmp_path / "bridge.sock"
    process = subprocess.Popen(
        [
            sys.executable, str(BRIDGE),
            "--socket", str(socket),
            "--token-file", str(token),
            "--session-ref", "bridge-session",
            "--descriptor", str(descriptor),
            "--calibre-root", str(calibre),
            "--calibre-work-root", str(tmp_path),
            "--drc-command", str(drc),
            "--lvs-command", str(lvs),
            "--calibre-timeout", "10",
        ],
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    line = process.stdout.readline()
    assert line.startswith(MARKER), line + process.stderr.read()
    return process, descriptor, socket, json.loads(line[len(MARKER):])


def _stop(process):
    if process.poll() is None:
        process.send_signal(signal.SIGTERM)
    try:
        return process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_bridge_catalog_submit_status_and_result(tmp_path):
    flow = make_flow(tmp_path)
    process, descriptor, socket, info = _start(tmp_path, flow)
    try:
        assert info["calibre_jobs"] is True
        assert info["calibre_work_root"] == str(tmp_path)
        assert descriptor.stat().st_mode & 0o777 == 0o600
        with UnixEnvelopeClient(
            socket, "bridge-token", "bridge-session"
        ) as client:
            catalog = client.exchange(Envelope(
                contract="sico.calibre.drc.v1",
                version=1,
                action="catalog",
                id="catalog-1",
                payload={"runset_path": str(flow.runset)},
            ))
            assert catalog.status == "ok"
            assert catalog.payload["schema"] == "calibre.drc.catalog.v1"
            assert catalog.payload["layers"] == ["M1"]
            assert catalog.payload["counts"] == {"G1": 1}
            assert catalog.page.total == 1
            submit = client.exchange(Envelope(
                contract="sico.calibre.drc.v1",
                version=1,
                action="submit",
                id="submit-1",
                payload={
                    "run_ref": "bridge-drc-1",
                    "config_path": str(flow.config),
                    "work_root": str(tmp_path),
                },
            ))
            assert submit.status == "ok"
            assert submit.payload["state"] == "queued"
            for index in range(100):
                status = client.exchange(Envelope(
                    contract="sico.calibre.drc.v1",
                    version=1,
                    action="status",
                    id=f"status-{index}",
                    payload={"run_ref": "bridge-drc-1"},
                ))
                if status.payload["state"] not in {"queued", "running"}:
                    break
                time.sleep(0.02)
            assert status.payload["state"] == "clean"
            result = client.exchange(Envelope(
                contract="sico.calibre.drc.v1",
                version=1,
                action="result",
                id="result-1",
                payload={
                    "run_ref": "bridge-drc-1",
                    "offset": 0,
                    "limit": 20,
                },
            ))
            assert result.status == "ok"
            assert result.payload["result"]["status"] == "clean"
            assert result.payload["result"]["signoff_eligible"] is True
            assert result.page.total == 0
            assert result.page.truncated is False
    finally:
        assert _stop(process) == 0
    assert not socket.exists()
    assert not descriptor.exists()


def test_bridge_candidate_check_is_non_signoff(tmp_path):
    flow = make_flow(tmp_path, kind="drc")
    candidate = tmp_path / "candidate.gds"
    candidate.write_text("CANDIDATE\n", encoding="ascii")
    calibre_body = (
        'if [ "$1" = "-version" ]; then\n'
        '  printf "Calibre 2026.3 fixture\\n"\n'
        "  exit 0\n"
        "fi\n"
        "deck=\n"
        'for argument in "$@"; do deck=$argument; done\n'
        'results=$(sed -n \'s/^DRC RESULTS DATABASE "\\(.*\\)" ASCII$/\\1/p\' "$deck")\n'
        'summary=$(sed -n \'s/^DRC SUMMARY REPORT "\\(.*\\)"$/\\1/p\' "$deck")\n'
        'mkdir -p "$(dirname "$results")" "$(dirname "$summary")"\n'
        'printf "TOTAL RESULT COUNT = 0\\n" > "$summary"\n'
        'printf "# Calibre DRC Results Database\\n" > "$results"\n'
        "exit 0\n"
    )
    process, _descriptor, socket, _descriptor_path = _start(
        tmp_path, flow, calibre_body=calibre_body
    )
    try:
        with UnixEnvelopeClient(
            socket, "bridge-token", "bridge-session"
        ) as client:
            response = client.exchange(Envelope(
                contract="sico.calibre.drc.v1",
                version=1,
                action="check",
                id="check-1",
                payload={
                    "run_ref": "bridge-candidate-1",
                    "config_path": str(flow.config),
                    "work_root": str(tmp_path),
                    "candidate_path": str(candidate),
                    "bbox": [0, 0, 100, 100],
                },
            ))
            assert response.status == "ok"
            assert response.payload["schema"] == "calibre.drc.check.v1"
            assert response.payload["scope"] == "candidate"
            for index in range(100):
                status = client.exchange(Envelope(
                    contract="sico.calibre.drc.v1",
                    version=1,
                    action="status",
                    id=f"check-status-{index}",
                    payload={"run_ref": "bridge-candidate-1"},
                ))
                if status.payload["state"] not in {"queued", "running"}:
                    break
                time.sleep(0.02)
            assert status.payload["state"] == "clean"
            result = client.exchange(Envelope(
                contract="sico.calibre.drc.v1",
                version=1,
                action="result",
                id="check-result-1",
                payload={"run_ref": "bridge-candidate-1", "offset": 0, "limit": 20},
            ))
            assert result.status == "ok"
            assert result.payload["result"]["scope"] == "candidate"
            assert result.payload["result"]["signoff_eligible"] is False
            assert result.payload["result"]["provenance"]["candidate"]["path"] == str(candidate)
    finally:
        assert _stop(process) == 0


def test_bridge_export_reuses_existing_lvs_flow(tmp_path):
    flow = make_flow(tmp_path, kind="lvs")
    lvs_body = (
        'if [ "$1" = "run" ] && [ "$4" = "gds" ]; then\n'
        f'  mkdir -p {flow.run_dir / "db/gds"} {flow.run_dir / "db/cdl"}\n'
        f'  printf "GDS\\n" > {flow.run_dir / "db/gds/inv.gds"}\n'
        f'  printf "CDL\\n" > {flow.run_dir / "db/cdl/inv.cdl"}\n'
        "  exit 0\n"
        "fi\n"
        "exit 1\n"
    )
    process, _descriptor, socket, _descriptor_path = _start(
        tmp_path, flow, lvs_body=lvs_body
    )
    try:
        with UnixEnvelopeClient(
            socket, "bridge-token", "bridge-session"
        ) as client:
            response = client.exchange(Envelope(
                contract="sico.calibre.lvs.v1",
                version=1,
                action="export",
                id="export-1",
                payload={
                    "export_ref": "bridge-export-1",
                    "config_path": str(flow.config),
                    "work_root": str(tmp_path),
                },
            ))
            assert response.status == "ok"
            assert response.payload["schema"] == "calibre.lvs.export.v1"
            assert response.payload["gds"]["path"].endswith("/db/gds/inv.gds")
            assert response.payload["cdl"]["path"].endswith("/db/cdl/inv.cdl")
    finally:
        assert _stop(process) == 0
