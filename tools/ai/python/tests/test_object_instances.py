"""Cross-instance object lookup stays bounded, explicit and read-only."""

from types import SimpleNamespace

import pytest
from sico.service import object_instances as module
from sico.service.targets import TargetBridgeError

REF = {
    "schema": "cad.object_ref.v1",
    "target": {
        "library": "worklib", "cell": "OTA", "view_name": "schematic",
        "oa_view_type": "schematic", "oa_view_type_source": "platform_api",
    },
    "kind": "instance", "name": "NM1",
    "master": {"library": "smic18", "cell": "nmos4", "view_name": "symbol"},
    "hierarchy": [],
}


class Bridge:
    def __init__(self):
        self.closed = False
        self.registered = []
        self.released = []

    def register_session(self, session_id, context):
        self.registered.append((session_id, context))

    def release_session(self, session_id):
        self.released.append(session_id)

    def close(self):
        self.closed = True


class Provider:
    def __init__(self, *, windows=None, failure=None):
        self.window_rows = windows or []
        self.failure = failure

    def windows(self, _ref):
        if self.failure is not None:
            raise self.failure
        return {"windows": self.window_rows}

    def highlight(self, _ref, _window_ref):
        if self.failure is not None:
            raise self.failure
        return {"ok": True, "kind": "instance", "name": "NM1"}

    def open_target(self, target):
        if self.failure is not None:
            raise self.failure
        assert target["read_only"] is True
        return {
            "library": target["library"], "cell": target["cell"],
            "view_name": target["view_name"],
            "oa_view_type": target["oa_view_type"],
            "oa_view_type_source": target["oa_view_type_source"],
            "window": "window:9", "window_ref": "wctx_other_1",
        }


def test_instance_windows_skips_current_and_closes_each_bridge(monkeypatch):
    bridges = {}
    providers = {
        "b" * 64: Provider(windows=[{
            "window_ref": "wctx_other_1", "window": "window:9",
            "title": None,
        }]),
    }

    def provider(project, candidate, *, cancelled):
        assert project == "project"
        bridge = bridges[candidate] = Bridge()
        context = SimpleNamespace(instance_id="instance-2", generation="g2")
        return bridge, context, providers[candidate]

    monkeypatch.setattr(module, "list_instances", lambda *_args, **_kwargs: [
        {"id": "a" * 64, "instance": "current", "generation": "g1"},
        {"id": "b" * 64, "instance": "instance-2", "generation": "g2"},
    ])
    monkeypatch.setattr(module, "_provider", provider)
    rows = module.instance_windows("project", REF, "current")
    assert rows == [{
        "candidate": "b" * 64,
        "instance": "instance-2",
        "generation": "g2",
        "windows": [{
            "window_ref": "wctx_other_1", "window": "window:9",
            "title": "",
        }],
    }]
    assert set(bridges) == {"b" * 64}
    assert bridges["b" * 64].closed


def test_instance_windows_keeps_reachable_instance_when_target_is_not_open(monkeypatch):
    bridge = Bridge()
    context = SimpleNamespace(instance_id="instance-2", generation="g2")
    provider = Provider(
        failure=TargetBridgeError("The requested cellview is not open", "target_not_open")
    )
    monkeypatch.setattr(module, "list_instances", lambda *_args, **_kwargs: [{
        "id": "b" * 64, "instance": "instance-2", "generation": "g2",
    }])
    monkeypatch.setattr(
        module, "_provider",
        lambda *_args, **_kwargs: (bridge, context, provider),
    )
    rows = module.instance_windows("project", REF, "current")
    assert rows == [{
        "candidate": "b" * 64,
        "instance": "instance-2",
        "generation": "g2",
        "windows": [],
    }]
    assert bridge.closed


def test_instance_windows_skips_instances_without_location_capability(monkeypatch):
    bridge = Bridge()
    context = SimpleNamespace(instance_id="instance-2", generation="g2")
    provider = Provider(
        failure=TargetBridgeError("highlight unavailable", "capability_not_available")
    )
    monkeypatch.setattr(module, "list_instances", lambda *_args, **_kwargs: [{
        "id": "b" * 64, "instance": "instance-2", "generation": "g2",
    }])
    monkeypatch.setattr(
        module, "_provider",
        lambda *_args, **_kwargs: (bridge, context, provider),
    )
    assert module.instance_windows("project", REF, "current") == []
    assert bridge.closed


def test_provider_registers_a_temporary_session_and_releases_it(monkeypatch):
    bridge = Bridge()
    context = SimpleNamespace(instance_id="instance-2", generation="g2")
    monkeypatch.setattr(
        module, "connect_candidate",
        lambda *_args, **_kwargs: (bridge, context),
    )
    selected, _context, provider = module._provider(
        "project", "b" * 64, cancelled=lambda: False,
    )
    assert selected is bridge
    assert bridge.registered[0][0] == provider.session_id
    module._close(bridge, provider)
    assert bridge.released == [provider.session_id]
    assert bridge.closed


def test_highlight_instance_returns_and_closes_the_selected_bridge(monkeypatch):
    bridge = Bridge()
    context = SimpleNamespace(instance_id="instance-2", generation="g2")
    provider = Provider()
    monkeypatch.setattr(
        module, "_provider",
        lambda *_args, **_kwargs: (bridge, context, provider),
    )
    result = module.highlight_instance(
        "project", "b" * 64, REF, "wctx_other_1",
    )
    assert result["instance"] == "instance-2"
    assert result["result"]["kind"] == "instance"
    assert bridge.closed


def test_highlight_instance_turns_a_native_refusal_into_a_readable_error(monkeypatch):
    bridge = Bridge()
    context = SimpleNamespace(instance_id="instance-2", generation="g2")
    provider = Provider(failure=TargetBridgeError("missing", "stale_object"))
    monkeypatch.setattr(
        module, "_provider",
        lambda *_args, **_kwargs: (bridge, context, provider),
    )
    with pytest.raises(ValueError, match="missing"):
        module.highlight_instance(
            "project", "b" * 64, REF, "wctx_other_1",
        )
    assert bridge.closed


def test_open_target_on_current_instance_uses_a_bounded_session(monkeypatch):
    bridge = Bridge()
    context = SimpleNamespace(instance_id="current", generation="g1")
    provider = Provider()
    monkeypatch.setattr(module, "list_instances", lambda *_args, **_kwargs: [{
        "id": "a" * 64, "instance": "current", "generation": "g1",
    }])
    monkeypatch.setattr(
        module, "_provider",
        lambda *_args, **_kwargs: (bridge, context, provider),
    )
    opened = module.open_target_on_instance("project", "current", REF)
    assert opened["window_ref"] == "wctx_other_1"
    assert bridge.closed


def test_open_target_on_candidate_opens_that_instance_read_only(monkeypatch):
    bridge = Bridge()
    context = SimpleNamespace(instance_id="instance-2", generation="g2")
    provider = Provider()
    seen = []

    def selected(_project, candidate, **_kwargs):
        seen.append(candidate)
        return bridge, context, provider

    monkeypatch.setattr(module, "_provider", selected)
    opened = module.open_target_on_candidate("project", "b" * 64, REF)
    assert seen == ["b" * 64]
    assert opened["window_ref"] == "wctx_other_1"
    assert bridge.closed


def test_open_target_on_candidate_rejects_a_missing_identity():
    with pytest.raises(ValueError, match="目标实例"):
        module.open_target_on_candidate("project", "", REF)


def test_window_actions_forwards_an_explicit_candidate():
    from sico_ui.window_actions import WindowActions

    seen = []
    actions = object.__new__(WindowActions)
    actions.page = SimpleNamespace(session="session")
    actions.api = SimpleNamespace(
        handle=lambda value: value,
        open_object_target=lambda token, ref, *, candidate=None: seen.append(
            (token, ref, candidate)
        ),
    )
    actions.open_object_target(REF)
    actions.open_object_target(REF, candidate="b" * 64)
    assert seen == [
        ("session", REF, None),
        ("session", REF, "b" * 64),
    ]
