"""Project resource defaults: one schema, revision conflicts and durable writes."""

import hashlib
import json
import os
import stat
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from sico.agent_resources import store
from sico.agent_resources.manifest import read_selection
from sico.agent_resources.schema import KINDS, MAX_MANIFEST, upgrade_manifest
from sico.codex.resource_config import ResourceConfig


def config(root, name="docs"):
    return {"version": 1, "skill_roots": [
        {"id": name, "path": str(root / name), "source": "project", "access": "read"},
    ]}


def test_missing_defaults_read_without_state_or_agent_process(tmp_path):
    snapshot = store.read_project(tmp_path)
    assert snapshot["revision"] is None
    assert snapshot["manifest"] == {"version": 1, **{kind: [] for kind in KINDS}}
    assert snapshot["editable"] and snapshot["source"] == "project"
    assert not (tmp_path / ".sico").exists()


def test_saved_defaults_are_private_and_consumed_by_codex(tmp_path):
    (tmp_path / "docs").mkdir()
    saved = store.save_project(tmp_path, config(tmp_path), expected_revision=None)
    path = Path(saved["path"])
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert saved["revision"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert store.read_project(tmp_path) == saved
    runtime = ResourceConfig(read_selection(SimpleNamespace(options={}, environment={}), tmp_path))
    assert runtime.digest == saved["revision"]
    assert runtime.selection == {kind: upgrade_manifest(saved["manifest"])[kind] for kind in KINDS}
    assert len(runtime.ready("skill_roots")) == 1
    assert not (tmp_path / ".sico/ai/agent").exists()
    saved["manifest"]["skill_roots"].clear()
    assert len(store.read_project(tmp_path)["manifest"]["skill_roots"]) == 1


def test_stale_editor_and_operator_edits_cannot_be_overwritten(tmp_path):
    first = store.save_project(tmp_path, config(tmp_path), expected_revision=None)
    with pytest.raises(store.ResourceConflictError):
        store.save_project(tmp_path, config(tmp_path, "stale"), expected_revision=None)
    second = store.save_project(tmp_path, config(tmp_path, "current"),
                                expected_revision=first["revision"])
    with pytest.raises(store.ResourceConflictError):
        store.save_project(tmp_path, config(tmp_path), expected_revision=first["revision"])
    path = Path(second["path"])
    operator = json.dumps(config(tmp_path, "operator")).encode()
    path.write_bytes(operator)
    with pytest.raises(store.ResourceConflictError):
        store.save_project(tmp_path, config(tmp_path), expected_revision=second["revision"])
    assert path.read_bytes() == operator


@pytest.mark.parametrize("invalid", [[], None, {"version": True}, {"version": 3},
                                      {"version": 1, "mcp_servers": []}])
def test_validation_precedes_filesystem_creation(tmp_path, invalid):
    with pytest.raises(ValueError):
        store.save_project(tmp_path, invalid, expected_revision=None)
    assert not (tmp_path / ".sico").exists()


@pytest.mark.parametrize("revision", ["", "old", 1, True, "A" * 64])
def test_invalid_revision_never_creates_a_default(tmp_path, revision):
    with pytest.raises(ValueError, match="revision"):
        store.save_project(tmp_path, {"version": 1}, expected_revision=revision)
    assert not (tmp_path / ".sico").exists()


def test_oversized_document_rejected_before_write(tmp_path):
    value = {"version": 1, "skill_roots": [
        {"id": str(i), "path": "/" + "a" * 3000, "source": "project", "access": "read"}
        for i in range(32)
    ]}
    with pytest.raises(ValueError, match="64 KiB"):
        store.save_project(tmp_path, value, expected_revision=None)
    assert not (tmp_path / ".sico").exists()


@pytest.mark.parametrize("bad", [b'{"version":1,"version":1}', b'{"version":',
                                b"x" * (MAX_MANIFEST + 1)])
def test_corrupt_default_is_not_replaced_with_empty_config(tmp_path, bad):
    saved = store.save_project(tmp_path, {"version": 1}, expected_revision=None)
    path = Path(saved["path"])
    path.write_bytes(bad)
    with pytest.raises(ValueError, match="清单不可读"):
        store.read_project(tmp_path)
    with pytest.raises(ValueError, match="清单不可读"):
        store.save_project(tmp_path, config(tmp_path), expected_revision=saved["revision"])
    assert path.read_bytes() == bad


def test_failed_replace_keeps_file_and_removes_temporary(tmp_path, monkeypatch):
    saved = store.save_project(tmp_path, config(tmp_path), expected_revision=None)
    before = Path(saved["path"]).read_bytes()
    def reject(*args):
        raise OSError("disk write failed")
    monkeypatch.setattr(store.os, "replace", reject)
    with pytest.raises(OSError, match="disk write"):
        store.save_project(tmp_path, config(tmp_path, "next"),
                           expected_revision=saved["revision"])
    assert Path(saved["path"]).read_bytes() == before
    assert not list(Path(saved["path"]).parent.glob("*.tmp"))


def test_uncertain_directory_sync_requires_reread(tmp_path, monkeypatch):
    saved = store.save_project(tmp_path, config(tmp_path), expected_revision=None)
    replace, replaced = store.os.replace, []
    def mark_replace(*args):
        replace(*args)
        replaced.append(True)
    def sync(_path):
        if replaced:
            raise OSError("directory sync failed")
    monkeypatch.setattr(store.os, "replace", mark_replace)
    monkeypatch.setattr(store, "sync_directory", sync)
    with pytest.raises(store.ResourceSaveUncertainError, match="重新读取"):
        store.save_project(tmp_path, config(tmp_path, "next"),
                           expected_revision=saved["revision"])
    observed = store.read_project(tmp_path)
    assert observed["manifest"]["skill_roots"][0]["id"] == "next"
    assert observed["revision"] != saved["revision"]


def test_competing_process_cannot_enter_write_transaction(tmp_path, monkeypatch):
    saved = store.save_project(tmp_path, config(tmp_path), expected_revision=None)
    entered, release = threading.Event(), threading.Event()
    replace = store._replace
    def hold(path, raw):
        entered.set()
        assert release.wait(10)
        replace(path, raw)
    monkeypatch.setattr(store, "_replace", hold)
    script = """import json, sys
from sico.agent_resources.store import save_project
try:
    save_project(sys.argv[1], {'version': 1}, expected_revision=sys.argv[2])
except BlockingIOError:
    print('writer_busy')
else:
    raise AssertionError('concurrent save was admitted')
"""
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(store.save_project, tmp_path, config(tmp_path, "winner"),
                              expected_revision=saved["revision"])
        try:
            assert entered.wait(5)
            child = subprocess.run([sys.executable, "-c", script, str(tmp_path), saved["revision"]],
                                   text=True, capture_output=True, timeout=5)
            assert child.returncode == 0, child.stderr
            assert child.stdout.strip() == "writer_busy"
        finally:
            release.set()
        pending.result(timeout=5)
    assert store.read_project(tmp_path)["manifest"]["skill_roots"][0]["id"] == "winner"


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "writable"])
def test_unsafe_default_target_is_not_replaced(tmp_path, kind):
    saved = store.save_project(tmp_path, config(tmp_path), expected_revision=None)
    path, other = Path(saved["path"]), tmp_path / "other.json"
    before = path.read_bytes()
    if kind == "symlink":
        path.rename(other)
        path.symlink_to(other)
    elif kind == "hardlink":
        os.link(path, other)
    else:
        path.chmod(0o666)
    with pytest.raises(ValueError, match="普通文件"):
        store.save_project(tmp_path, {"version": 1}, expected_revision=saved["revision"])
    assert path.read_bytes() == before


def test_external_precedence_is_read_only_and_missing_does_not_fall_back(tmp_path):
    store.save_project(tmp_path, config(tmp_path), expected_revision=None)
    external = tmp_path / "site.json"
    external.write_text('{"version":1}')
    provider = SimpleNamespace(options={}, environment={"SICO_EXTERNAL_RESOURCES": str(external)})
    selected = read_selection(provider, tmp_path)
    assert selected["source"] == "environment" and not selected["editable"]
    assert selected["manifest"]["skill_roots"] == []
    external.unlink()
    with pytest.raises(ValueError, match="清单不可读"):
        read_selection(provider, tmp_path)
    assert store.read_project(tmp_path)["manifest"]["skill_roots"]


def test_legacy_state_is_readable_but_not_silently_written(tmp_path):
    path = tmp_path / ".cad/ai/agent-resources.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"version":1}')
    snapshot = store.read_project(tmp_path)
    assert not snapshot["editable"]
    assert not read_selection(SimpleNamespace(), tmp_path)["editable"]
    with pytest.raises(ValueError, match="migrate"):
        store.save_project(tmp_path, config(tmp_path), expected_revision=snapshot["revision"])
    assert path.read_text() == '{"version":1}'
    assert not (tmp_path / ".sico").exists()
