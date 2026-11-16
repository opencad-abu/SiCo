"""Archive maintenance preserves journals when host retirement is uncertain."""

import json
from pathlib import Path
import socket

import pytest

from test_agent_router_archive import fingerprints, retired
from sico.transport import router_archive as archive
from sico.transport import router_archive_records as records


def owner():
    return {"hostname": socket.gethostname(), "boot_id": "boot",
            "process": {"pid": 123, "start": "456", "group": 123, "state": "S"}}


@pytest.mark.parametrize("scenario", [
    "active", "remote", "invalid_process", "invalid_pid", "invalid_start",
    "missing_boot", "permission", "malformed_stat", "short_stat", "boot_permission",
])
def test_uncertain_retirement_keeps_all_files(tmp_path, monkeypatch, scenario):
    evidence = owner()
    monkeypatch.setattr(archive, "boot_id", lambda: "boot")
    monkeypatch.setattr(archive, "process_identity", lambda pid: owner()["process"])
    if scenario == "remote":
        evidence["hostname"] = socket.gethostname() + ".remote"
    elif scenario == "invalid_process":
        evidence["process"] = "unknown"
    elif scenario == "invalid_pid":
        evidence["process"]["pid"] = True
    elif scenario == "invalid_start":
        evidence["process"]["start"] = "unknown"
    elif scenario == "missing_boot":
        evidence.pop("boot_id")
    elif scenario in ("permission", "malformed_stat", "short_stat", "boot_permission"):
        error = {"permission": PermissionError, "malformed_stat": ValueError,
                 "short_stat": IndexError, "boot_permission": PermissionError}[scenario]

        def unavailable(*args):
            raise error("unavailable evidence")

        monkeypatch.setattr(archive, "boot_id" if scenario == "boot_permission"
                            else "process_identity", unavailable)
    path = retired(tmp_path)
    descriptor = json.loads(path.read_text())
    descriptor["archive_owner"] = evidence
    path.write_text(json.dumps(descriptor))
    before = fingerprints(tmp_path)
    report = archive.maintain_router_logs(tmp_path, apply=True, retention_days=0, max_bytes=1)
    assert not report["errors"]
    assert report["generations"][0]["status"] == "host_active"
    assert fingerprints(tmp_path) == before


@pytest.mark.parametrize("scenario", ["boot_changed", "pid_reused", "zombie", "process_missing"])
def test_known_retirement_evidence_keeps_the_existing_decision(monkeypatch, scenario):
    monkeypatch.setattr(archive, "boot_id", lambda: "new" if scenario == "boot_changed" else "boot")
    current = owner()["process"]
    if scenario == "pid_reused":
        current["start"] = "457"
    if scenario == "zombie":
        current["state"] = "Z"

    def read_process(pid):
        assert pid == 123
        if scenario == "process_missing":
            raise FileNotFoundError("exited")
        return current

    monkeypatch.setattr(archive, "process_identity", read_process)
    assert archive._host_gone(owner())


def test_registered_archive_aliases_keep_their_single_owner():
    root = Path(__file__).resolve().parents[4]
    entries = json.loads((root / "tools/utility/compatibility/ai.json").read_text())
    entry = next(row for row in entries["compatibility"] if row["legacy"] ==
                 "sico.transport.router_archive exports from sico.transport.router_archive_records")
    for name, replacement in entry["exports"].items():
        assert getattr(archive, name) is getattr(records, replacement)
