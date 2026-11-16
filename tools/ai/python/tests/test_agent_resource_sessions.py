"""Selection persistence, default differences and evidence-only legacy migration."""

import json
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest
from sico.agent_resources import snapshot, store
from sico.agent_resources.schema import upgrade_manifest
from sico.codex.backend import CodexBackend
from sico.codex.resources import ExternalResources
from sico.codex.rpc import RpcError
from sico.core.tools import ToolRegistry
from sico.storage.journal import SessionJournal
from test_agent_codex_native import CONTEXT
from test_codex_resources_runtime import make_resources


@contextmanager
def session(project, name="selected", path=None):
    provider = SimpleNamespace(options={"external_resources": str(path)} if path else {},
                               environment={})
    with SessionJournal(project, name) as journal:
        backend = CodexBackend(provider, ToolRegistry(), journal, CONTEXT)
        try:
            yield backend
        finally:
            backend.close()


def selection_events(backend):
    return [e for e in backend.journal.events() if e["kind"] == snapshot.EVENT]


def test_frozen_session_a_and_new_session_b_have_distinct_defaults(tmp_path):
    _, config = make_resources(tmp_path)
    saved = store.save_project(tmp_path, config, expected_revision=None)
    with session(tmp_path, "a") as backend:
        backend.resources.prepare()
        first = backend.resources.published["configuration"]["effective"]
        assert len(selection_events(backend)) == 1
    config["skill_roots"] = []
    store.save_project(tmp_path, config, expected_revision=saved["revision"])
    with session(tmp_path, "a") as backend:
        backend.resources.prepare()
        view = backend.resources.published["configuration"]
        assert view["state"] == "frozen" and view["effective"] == first
        assert view["pending"] and view["revision_changed"]
        assert view["changes"] == [{"kind": "skill_roots", "id": "user", "change": "removed"}]
        assert len(selection_events(backend)) == 1
    with session(tmp_path, "b") as backend:
        backend.resources.prepare()
        assert not backend.resources.config.ready("skill_roots")
        assert not backend.resources.published["configuration"]["pending"]


@pytest.mark.parametrize("change", ["missing", "corrupt"])
def test_bad_current_defaults_do_not_break_a_but_stop_new_b(tmp_path, change):
    path, _ = make_resources(tmp_path)
    with session(tmp_path, "a", path) as backend:
        backend.resources.prepare()
    path.unlink() if change == "missing" else path.write_text("invalid")
    with session(tmp_path, "a", path) as backend:
        backend.resources.prepare()
        view = backend.resources.published["configuration"]
        assert view["defaults"]["status"] == "unavailable" and view["pending"] is None
        assert len(backend.resources.config.rows) == 3
    with session(tmp_path, "b", path) as backend:
        with pytest.raises(RpcError, match="清单不可读"):
            backend.resources.prepare()
        assert not selection_events(backend) and backend.runtime is None


def test_status_is_preview_until_first_start(tmp_path):
    path, config = make_resources(tmp_path)
    with session(tmp_path, path=path) as backend:
        first = backend.resources.status()
        assert first["configuration"]["state"] == "preview"
        assert not selection_events(backend) and backend.runtime is None
        config["skill_roots"] = []
        path.write_text(json.dumps(config))
        backend.resources.prepare()
        assert not backend.resources.config.ready("skill_roots")
        assert len(selection_events(backend)) == 1


def test_representation_only_change_is_not_pending_and_views_cannot_change_selection(tmp_path):
    path, config = make_resources(tmp_path)
    with session(tmp_path, path=path) as backend:
        backend.resources.prepare()
        view = backend.resources.status()
        view["configuration"]["effective"]["manifest"]["plugins"].clear()
        path.write_text(json.dumps(upgrade_manifest(config), indent=4))
        backend.resources.prepare()
        current = backend.resources.published["configuration"]
        assert current["revision_changed"] and not current["pending"]
        assert len(current["effective"]["manifest"]["plugins"]) == 1


def test_empty_selection_remains_empty_after_defaults_added(tmp_path):
    with session(tmp_path) as backend:
        backend.resources.prepare()
    _, value = make_resources(tmp_path)
    store.save_project(tmp_path, value, expected_revision=None)
    with session(tmp_path) as backend:
        backend.resources.prepare()
        assert not backend.resources.config.configured
        assert backend.resources.config.rows == []
        assert backend.resources.published["configuration"]["pending"]


@pytest.mark.parametrize("corruption", ["hash", "conflict", "identity", "missing"])
def test_damaged_snapshot_cannot_fall_back_to_defaults(tmp_path, corruption):
    path, _ = make_resources(tmp_path)
    with session(tmp_path, path=path) as backend:
        backend.resources.prepare()
        records = backend.journal.events()
        payload = next(e["payload"] for e in records if e["kind"] == snapshot.EVENT)
        if corruption == "hash":
            payload["selection"]["manifest"]["plugins"].clear()
        elif corruption == "conflict":
            records.append({"kind": snapshot.EVENT, "payload": {}})
        elif corruption == "identity":
            payload["fingerprints"].clear()
            payload["sha256"] = snapshot.digest({k: v for k, v in payload.items() if k != "sha256"})
        else:
            records = [e for e in records if e["kind"] != snapshot.EVENT]
            backend._bind_thread("start", {"id": "native"}, expected=None)
            records.append({"kind": "codex.thread", "payload": {"capability_roots": []}})
        backend.resources = ExternalResources(backend, records)
        with pytest.raises(RpcError):
            backend.resources.prepare()
        assert backend.runtime is None


def legacy_records(backend, *, empty=False):
    backend.resources.prepare(capture=False)
    inventory = deepcopy(backend.resources.published)
    inventory.pop("configuration")
    for row in inventory["rows"]:
        row.pop("enabled")
        row.pop("mcp_sha256", None)
    roots = backend.resources.config.selected_roots()
    backend._bind_thread("start", {"id": "old-thread"}, expected=None, roots=roots)
    binding = {"kind": "codex.thread", "payload": {"capability_roots": roots}}
    if empty:
        return [{"kind": "codex.resources.updated", "payload": inventory}, binding]
    inventory["status"] = "checked"
    return [binding, {"kind": "codex.resources.updated", "payload": inventory}]


@pytest.mark.parametrize("empty", [False, True])
def test_evidenced_legacy_migrates_once_without_reading_new_defaults(tmp_path, empty):
    path = None if empty else make_resources(tmp_path)[0]
    with session(tmp_path, path=path) as backend:
        records = legacy_records(backend, empty=empty)
        if path:
            path.write_text('{"version": 2}')
        backend.resources = ExternalResources(backend, records)
        backend.resources.prepare()
        value = selection_events(backend)[0]["payload"]
        assert value["origin"] == "legacy"
        assert len(value["selection"]["manifest"]["plugins"]) == (0 if empty else 1)
        assert backend.resources.published["configuration"]["drift"] == ([] if empty else [
            {"id": "plugin", "kind": "plugins", "reason": "legacy_tools_unverified",
             "blocked": False}])
        backend.resources.prepare()
        assert len(selection_events(backend)) == 1


def test_legacy_preview_survives_another_process_before_migration(tmp_path):
    path, _ = make_resources(tmp_path)
    with session(tmp_path, path=path) as backend:
        records = legacy_records(backend)
        backend.resources = ExternalResources(backend, records)
        preview = backend.resources.status()
        assert preview["configuration"]["state"] == "preview"
        assert not selection_events(backend)
        records.append({"kind": "codex.resources.updated", "payload": preview})
        backend.resources = ExternalResources(backend, records)
        backend.resources.prepare()
        assert selection_events(backend)[0]["payload"]["origin"] == "legacy"


@pytest.mark.parametrize("last", [None, "pending", "partial", "unavailable"])
def test_legacy_without_final_complete_evidence_is_rejected(tmp_path, last):
    path, _ = make_resources(tmp_path)
    with session(tmp_path, path=path) as backend:
        records = legacy_records(backend)
        if last is None:
            records = records[:1]
        else:
            failed = deepcopy(records[-1])
            failed["payload"]["status"] = last
            records.append(failed)
        backend.resources = ExternalResources(backend, records)
        with pytest.raises(RpcError, match="无法可靠还原"):
            backend.resources.prepare()
        assert not selection_events(backend)


def test_symbolic_root_retarget_is_blocked_before_runtime(tmp_path):
    path, config = make_resources(tmp_path)
    link = tmp_path / "linked"
    link.symlink_to(tmp_path / "skills", target_is_directory=True)
    value = upgrade_manifest(config)
    value["skill_roots"][0]["path"] = str(link)
    path.write_text(json.dumps(value))
    with session(tmp_path, path=path) as backend:
        backend.resources.prepare()
    link.unlink()
    link.symlink_to(tmp_path / "capability", target_is_directory=True)
    with session(tmp_path, path=path) as backend:
        with pytest.raises(RpcError, match="发生变化"):
            backend.resources.prepare()
        assert backend.resources.published["configuration"]["drift"][0]["reason"] == "path_changed"
        assert backend.runtime is None


@pytest.mark.parametrize("change", ["version", "tools", "became_available", "capability_missing"])
def test_external_changes_do_not_silently_expand_a_frozen_session(tmp_path, change):
    path, config = make_resources(tmp_path)
    if change == "became_available":
        config["skill_roots"][0]["path"] = str(tmp_path / "later")
        path.write_text(json.dumps(config))
    with session(tmp_path, path=path) as backend:
        backend.resources.prepare()
    if change == "version":
        target = tmp_path / "market/plugins/b-plugin/.codex-plugin/plugin.json"
        value = json.loads(target.read_text())
        value["version"] = "2.0.0"
        target.write_text(json.dumps(value))
    elif change == "tools":
        (tmp_path / "market/plugins/b-plugin/.mcp.json").write_text('{"mcpServers":{}}')
    elif change == "became_available":
        (tmp_path / "later").mkdir()
    else:
        (tmp_path / "capability").rename(tmp_path / "retained")
    with session(tmp_path, path=path) as backend:
        with pytest.raises(RpcError):
            backend.resources.prepare()
        assert backend.resources.published["configuration"]["drift"]
        assert backend.runtime is None
