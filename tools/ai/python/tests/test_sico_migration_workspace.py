"""Qt layout migration validates snapshots without writing legacy settings."""

import pytest
from PyQt5.QtCore import QByteArray

from sico.migration.audit import audit
from sico.migration.prepare import prepare
from sico_ui.workspace_settings import open_settings
from test_sico_migration_audit import codes, write


@pytest.fixture(autouse=True)
def isolated_process_observation(monkeypatch):
    for module in ("audit", "prepare"):
        monkeypatch.setattr("sico.migration." + module + ".scan_local", lambda source: {
            "scope": "local", "referencing_pids": [], "unreadable_processes": 0})


def layout(project):
    path = write(project, "ai/agent/workspace.ini", {})
    path.unlink()
    settings = open_settings(path)
    settings.setValue("workspace/geometry", QByteArray(b"\x00\x01\xfflegacy geometry"))
    settings.setValue("workspace/state", QByteArray(b"\x00legacy docks"))
    settings.sync()
    del settings
    path.chmod(0o600)
    return path


def test_qt_layout_is_validated_and_copied_as_original_bytes(tmp_path):
    path = layout(tmp_path)
    before = path.read_bytes(), path.stat().st_mtime_ns
    report = audit(tmp_path)
    assert not report["blockers"]
    result = prepare(tmp_path, expected_inventory=report["inventory_sha256"], writers_stopped=True)
    from pathlib import Path

    copied = Path(result["transaction"]) / "staged/ai/agent/workspace.ini"
    assert copied.read_bytes() == before[0]
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert not path.with_suffix(".ini.lock").exists()


@pytest.mark.parametrize("damage", ["unknown_key", "string", "malformed", "large"])
def test_unsupported_layout_blocks_without_rewriting(tmp_path, damage):
    path = layout(tmp_path)
    if damage in {"unknown_key", "string"}:
        settings = open_settings(path)
        settings.setValue("workspace/extra" if damage == "unknown_key" else
                          "workspace/geometry", "/old/.cad/path")
        settings.sync()
        del settings
    else:
        path.write_bytes(b"[broken\nstate=x\n" if damage == "malformed" else b"x" * 1048577)
    before = path.read_bytes()
    assert "invalid_or_changed_record" in codes(audit(tmp_path))
    assert path.read_bytes() == before
