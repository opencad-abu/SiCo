"""Private execution boundary capability checks and report validation."""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import threading

import pytest

from aivworkbench.qualification.private_boundary import (
    BOUNDARY_KIND,
    BoundaryError,
    QUALIFIED_STATUS,
    license_hosts_from_environment,
    probe,
    verify_report_record,
)
from aivworkbench.workspace import sha256_file


@pytest.fixture
def home_canary():
    canary = Path(os.path.expanduser("~"))/f"aivw-boundary-canary-{secrets.token_hex(6)}.txt"
    canary.write_text("canary\n")
    try:
        yield canary
    finally:
        for path in (canary, canary.with_name(canary.name + ".write")):
            if path.exists():
                path.unlink()


def _reservation(tmp_path):
    private = tmp_path/"private"
    private.mkdir(mode=0o700)
    record = {"schema_version": 1, "kind": "reserved_whole_case_holdout",
              "cases": [{"id": "reserved_0"}], "policy_digest": "a"*64, "domain_digest": "b"*64}
    (private/"reservation.json").write_text(json.dumps(record))
    return private, record


@pytest.fixture
def license_listener():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    threading.Thread(target=lambda: server.accept(), daemon=True).start()
    try:
        yield "127.0.0.1:%d" % server.getsockname()[1]
    finally:
        server.close()


def test_boundary_blocks_outside_files_network_and_home(tmp_path, home_canary, license_listener):
    if shutil.which("bwrap") is None:
        pytest.skip("bubblewrap unavailable")
    private, _ = _reservation(tmp_path)
    result = probe(private, spool=tmp_path/"spool", canary=home_canary, license_hosts=(license_listener,),
                   visibility=str(tmp_path/"hidden-tree"), expected_sha256=sha256_file(private/"reservation.json"))
    assert {item["name"]: item["status"] for item in result["checks"]} == {
        "reservation_readable": "PASS",
        "reservation_hash_matches_commitment": "PASS",
        "outside_file_not_visible": "PASS",
        "license_endpoints_reachable": "PASS",
        "spool_writable": "PASS",
        "outside_write_forbidden": "PASS",
        "public_tree_not_visible": "PASS",
    }
    assert result["reservation"]["case_count"] == 1
    assert (tmp_path/"spool"/"boundary-spool.json").is_file()
    assert home_canary.read_text() == "canary\n"
    assert not home_canary.with_name(home_canary.name + ".write").exists()


def test_boundary_reports_commitment_mismatch(tmp_path, home_canary, license_listener):
    if shutil.which("bwrap") is None:
        pytest.skip("bubblewrap unavailable")
    private, _ = _reservation(tmp_path)
    result = probe(private, spool=tmp_path/"spool", canary=home_canary, license_hosts=(license_listener,),
                   visibility=str(tmp_path/"hidden-tree"), expected_sha256="0"*64)
    statuses = {item["name"]: item["status"] for item in result["checks"]}
    assert statuses["reservation_hash_matches_commitment"] == "FAIL"


def test_unreachable_license_endpoint_fails_the_probe(tmp_path, home_canary):
    if shutil.which("bwrap") is None:
        pytest.skip("bubblewrap unavailable")
    private, _ = _reservation(tmp_path)
    result = probe(private, spool=tmp_path/"spool", canary=home_canary, license_hosts=("127.0.0.1:9",),
                   visibility=str(tmp_path/"hidden-tree"), expected_sha256=sha256_file(private/"reservation.json"))
    statuses = {item["name"]: item["status"] for item in result["checks"]}
    assert statuses["license_endpoints_reachable"] == "FAIL"


def test_license_hosts_are_extracted_from_site_variables():
    environment = {"CDS_LIC_FILE": "5280@192.168.124.7", "LM_LICENSE_FILE": "27040@work-srv:5280@192.168.124.7",
                   "MGLS_LICENSE_FILE": "/software/ocad/tools/licsrv/licenses/mgc.license.dat"}
    assert license_hosts_from_environment(environment) == ["192.168.124.7:5280", "work-srv:27040"]


def _report(sha="c"*64):
    return {"kind": "private-execution-boundary-qualification", "status": QUALIFIED_STATUS,
            "boundary": BOUNDARY_KIND, "checks": [{"name": "reservation_readable", "status": "PASS"}],
            "reservation": {"commitment_sha256": sha, "policy_digest": "a"*64,
                            "domain_digest": "b"*64, "case_count": 3}}


def test_report_validation_rejects_foreign_or_failed_records():
    commitment = {"commitment_sha256": "c"*64, "policy_digest": "a"*64, "domain_digest": "b"*64, "case_count": 3}
    assert verify_report_record(_report(), commitment, run_status=QUALIFIED_STATUS)
    with pytest.raises(BoundaryError, match="another reservation"):
        verify_report_record(_report(sha="d"*64), commitment)
    failed = _report()
    failed["checks"] = [{"name": "reservation_readable", "status": "FAIL"}]
    with pytest.raises(BoundaryError, match="failed or missing checks"):
        verify_report_record(failed, commitment)
    with pytest.raises(BoundaryError, match="not a qualification"):
        verify_report_record({**_report(), "status": "NOT_QUALIFIED"}, commitment)


RESERVATION_RUN = "20261011T074029.321759Z-dynamic-holdout-reservation-df80ec2e30c2"


def test_real_reservation_qualifies_without_public_private_reads(monkeypatch):
    from aivworkbench.qualification import private_boundary as module
    root = Path(__file__).resolve().parents[3]
    control = root/".aivw/runs"/RESERVATION_RUN/"manifest.json"
    if not control.is_file() or shutil.which("bwrap") is None:
        pytest.skip("site reservation or bubblewrap unavailable")
    hosts = license_hosts_from_environment(os.environ)
    if not hosts:
        pytest.skip("site license environment unavailable")
    payload_root = Path(json.loads(control.read_text())["details"]["payload_root"])
    import builtins
    import io
    old_open, old_io_open = builtins.open, io.open

    def guard(original):
        def guarded(file, *args, **kwargs):
            if isinstance(file, (str, Path)):
                assert "/private/" not in str(file), f"public process opened {file}"
            return original(file, *args, **kwargs)
        return guarded

    monkeypatch.setattr(builtins, "open", guard(old_open))
    monkeypatch.setattr(io, "open", guard(old_io_open))
    report = module.qualify(control, payload_root=payload_root, timeout=60.0, license_hosts=hosts)
    assert report["status"] == module.QUALIFIED_STATUS
    assert report["reservation"]["case_count"] == 20
    assert all(item["status"] == "PASS" for item in report["checks"])
