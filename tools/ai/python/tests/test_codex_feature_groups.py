"""Built-in feature groups: frozen defaults, per-group overrides, recovery."""

from types import SimpleNamespace
import sys

import pytest
from sico.codex import feature_groups as groups
from sico.codex.settings import CodexSettings
from sico.core.contracts import RunState
from sico.storage.journal import SessionJournal
from test_agent_project import bound

# The exact option values the desktop runtime started with before the
# registry existed; any change here is a deliberate capability decision.
FROZEN_DEFAULTS = {
    "features.goals": True,
    "project_doc_max_bytes": 32 * 1024,
    "project_doc_fallback_filenames": ["AGENT.md"],
    "features.shell_tool": True,
    "features.shell_snapshot": True,
    "features.unified_exec": True,
    "features.unified_exec_tty": True,
    "features.default_mode_request_user_input": True,
    "features.multi_agent": True,
    "features.multi_agent_v2": True,
    "features.view_image": True,
    "features.image_generation": True,
    "features.browser_use": True,
    "features.computer_use": True,
    "features.sleep_tool": True,
    "features.apps": True,
    "features.plugins": False,
    "features.auth_elicitation": True,
    "features.tool_call_mcp_elicitation": True,
    "features.skill_mcp_dependency_install": True,
    "web_search": "live",
    "features.enable_request_compression": False,
    "features.unbounded_connection_retries": False,
    "analytics.enabled": False,
    "approval_policy": "never",
    "approvals_reviewer": "user",
    "sandbox_mode": "workspace-write",
}


def test_registry_covers_every_frozen_default_exactly_once():
    assert groups.option_defaults() == FROZEN_DEFAULTS
    ids = [group["id"] for group in groups.GROUPS]
    assert len(ids) == len(set(ids))
    for group in groups.GROUPS:
        assert group["necessity"] in {"required", "default_on", "review", "required_off"}
        assert group["purpose"] and group["recovery"] and group["evidence"]
        if not group["configurable"]:
            assert group["necessity"] == "required_off" or group["id"] == "sandbox"


def test_memories_stay_owned_by_the_thread_memory_module():
    keys = set(groups.option_defaults())
    assert not any(key.startswith("memories.") or key == "features.memories"
                   for key in keys)


def test_group_overrides_change_only_that_group_and_recover():
    disabled = groups.runtime_options({"view_image": {"features.view_image": False}})
    assert disabled["features.view_image"] is False
    for key in set(FROZEN_DEFAULTS) - {"features.view_image"}:
        assert disabled[key] == FROZEN_DEFAULTS[key]
    # Turning it back on (and any later event) restores the declared default.
    assert groups.runtime_options()["features.view_image"] is True
    assert groups.runtime_options(
        {"view_image": {"features.view_image": True}}) == FROZEN_DEFAULTS


def test_invalid_overrides_fail_loudly():
    with pytest.raises(ValueError, match="unknown feature group"):
        groups.runtime_options({"nope": {"features.goals": False}})
    with pytest.raises(ValueError, match="fixed"):
        groups.runtime_options({"sandbox": {"sandbox_mode": "read-only"}})
    with pytest.raises(ValueError, match="does not belong"):
        groups.runtime_options({"goals": {"features.apps": False}})
    with pytest.raises(ValueError, match="expects bool"):
        groups.runtime_options({"view_image": {"features.view_image": "no"}})
    with pytest.raises(ValueError, match="group map"):
        groups.runtime_options(["view_image"])


def test_describe_marks_overrides_and_defaults():
    plain = {row["id"]: row for row in groups.describe()}
    assert plain["view_image"]["overridden"] is False
    described = {row["id"]: row for row in groups.describe(
        {"view_image": {"features.view_image": False}})}
    assert described["view_image"]["options"] == {"features.view_image": False}
    assert described["view_image"]["overridden"] is True
    assert described["shell"]["necessity"] == "required"
    assert plain["plugins"]["options"] == {"features.plugins": False}
    assert "GitHub" in plain["plugins"]["recovery"]
    assert groups.runtime_options({"plugins": {"features.plugins": True}})[
        "features.plugins"] is True


def test_configured_overrides_round_trip_and_later_events_win(tmp_path):
    with SessionJournal(tmp_path, "features") as journal:
        assert groups.configured_overrides(journal) is None
        journal.append(groups.CONFIGURED_EVENT,
                       {"groups": {"browser": {"features.browser_use": False}}},
                       RunState(bound(tmp_path)))
        assert groups.configured_overrides(journal) == {
            "browser": {"features.browser_use": False}}
        journal.append(groups.CONFIGURED_EVENT,
                       {"groups": {"browser": {"features.browser_use": True}}},
                       RunState(bound(tmp_path)))
        assert groups.runtime_options(groups.configured_overrides(journal)) == FROZEN_DEFAULTS
    with SessionJournal(tmp_path, "features") as journal:
        journal.append(groups.CONFIGURED_EVENT, {"wrong": {}},
                       RunState(bound(tmp_path)))
        with pytest.raises(ValueError, match="groups"):
            groups.configured_overrides(journal)


@pytest.mark.project_memory
def test_runtime_command_uses_the_registry_and_session_overrides(tmp_path, monkeypatch):
    from sico.codex import runtime as runtime_module

    captured = {}

    class App:
        def __init__(self, command, environment, cwd, **kwargs):
            captured["command"] = command
            self.child = SimpleNamespace(returncode=0)

        def close(self):
            pass

    monkeypatch.setattr(runtime_module, "AppServer", App)
    monkeypatch.setattr(runtime_module.process_monitor, "track", lambda *_a, **_k: None)
    monkeypatch.setattr(runtime_module.process_monitor, "finish", lambda *_a, **_k: None)
    settings = CodexSettings(
        dict(provider="responses", base_url="https://upstream.example/v1", model="m"),
        {"SICO_API_KEY": "upstream-private", "SICO_PYTHON": sys.executable},
    )
    monkeypatch.setattr(settings, "executable", lambda: "/bin/true")
    with SessionJournal(tmp_path, "runtime-features") as journal:
        journal.append(groups.CONFIGURED_EVENT,
                       {"groups": {"view_image": {"features.view_image": False}}},
                       RunState(bound(tmp_path)))
        process = runtime_module.CodexRuntime(
            settings, journal, SimpleNamespace(port=12, token="mcp", timeout=3),
            enabled_tools=())
        process.close()
    command = captured["command"]
    assert 'features.view_image=false' in command
    assert 'features.shell_tool=true' in command
    assert 'features.plugins=false' in command
    assert 'web_search="live"' in command
    assert 'sandbox_mode="workspace-write"' in command
    assert 'project_doc_max_bytes=32768' in command
    assert 'project_doc_fallback_filenames=["AGENT.md"]' in command
    # The memory module still owns its own options.
    assert 'features.memories=true' in command
    assert 'memories.generate_memories=true' in command
    assert 'memories.use_memories=true' in command
    # With no session event, the project default feeds the next runtime,
    # including the startup task that creates it.
    groups.write_project_default(
        groups.project_default_path(tmp_path / ".sico"),
        {"view_image": {"features.view_image": False}},
    )
    captured.clear()
    with SessionJournal(tmp_path, "runtime-project") as journal:
        process = runtime_module.CodexRuntime(
            settings, journal, SimpleNamespace(port=12, token="mcp", timeout=3),
            enabled_tools=())
        process.close()
    assert 'features.view_image=false' in captured["command"]
