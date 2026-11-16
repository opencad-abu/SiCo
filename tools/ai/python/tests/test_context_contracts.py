"""P0 context payload, completeness and shadow-manifest contracts."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest
from sico.codex.task_driver import TaskDriver
from sico.context import (
    build_envelope,
    build_manifest,
    build_payload,
    render_card,
    section,
    validate_payload,
)
from sico.context.shadow import record_entry_context_shadow
from sico.core.contracts import BoundContext


def entry():
    return {
        "ok": True,
        "valid": True,
        "schema": "cad.entry.context.v1",
        "session_ref": "fixture",
        "captured_at": "2026-11-01T12:00:00Z",
        "source": {
            "kind": "schematic",
            "window_ref": "wctx_fixture_1",
            "is_current": True,
            "selection_count": 2,
            "display_cellview": {
                "lib": "worklib",
                "cell": "OTA",
                "view_name": "schematic",
                "oa_view_type": "schematic",
                "oa_view_type_source": "platform_api",
            },
        },
        "design": {
            "cellview": {
                "lib": "worklib",
                "cell": "OTA",
                "view_name": "schematic",
                "oa_view_type": "schematic",
                "oa_view_type_source": "platform_api",
                "mode": "a",
                "modified": False,
            },
            "bbox": [[0, 0], [10, 10]],
            "port_count": 3,
            "ports": [["A", "input"], ["B", "output"]],
            "instance_count": 1,
            "instances": [["I0", ["worklib", "leaf", "schematic"]]],
            "net_count": 8,
            "nets": ["N1", "N2"],
            "layer_count": 0,
            "layers": [],
        },
    }


def context_record():
    return {
        "contract": "virtuoso_context.v1",
        "instance_id": "instance",
        "generation": "generation",
        "target_id": "target",
        "snapshot": {
            "window_ref": "wctx_fixture_1",
            "project_id": "project",
            "work": {"work_id": "adc_module"},
            "stage": {"stage_id": "spec_interpretation"},
        },
    }


def test_section_distinguishes_empty_complete_summary_and_truncated():
    assert section(0, 0)["state"] == "complete"
    assert section(0, 8)["state"] == "truncated"
    assert section(0, 8)["omitted"] == 8
    assert section(2, 2, detail_omitted=True)["state"] == "summary"
    assert section(0, None)["state"] == "unknown"
    assert section(0, None)["next_action"] == "ask_user"


def test_payload_maps_view_identity_and_per_section_completeness():
    payload = build_payload(
        entry(), context_record(), turn_step="session_start",
        snapshot_id="snapshot-1",
    )
    target = payload["active_target"]
    assert target["view_name"] == "schematic"
    assert target["oa_view_type"] == "schematic"
    assert target["target_state"] == "cellview"
    assert "view_type" not in target
    sections = payload["completeness"]["sections"]
    assert sections["nets"]["state"] == "truncated"
    assert sections["nets"]["returned"] == 2
    assert sections["nets"]["total"] == 8
    assert sections["nets"]["omitted"] == 6
    assert sections["selection"]["state"] == "truncated"
    assert sections["selection"]["next_action"] == "inspect_selection"
    assert payload["cellview"]["section_completeness"]["nets"]["state"] == "truncated"
    assert payload["completeness"]["state"] == "truncated"


def test_ade_session_and_invalid_cellview_are_distinct():
    ade = entry()
    ade["source"] = {"kind": "ade", "window_ref": "wctx_ade_1"}
    ade["ade"] = {"test_count": 1}
    ade["design"] = None
    payload = build_payload(ade, context_record(), snapshot_id="snapshot-ade")
    assert payload["active_target"]["target_state"] == "ade_session"
    assert payload["active_target"]["oa_view_type"] is None
    assert payload["cellview"] is None

    invalid = entry()
    invalid["design"]["cellview"]["oa_view_type"] = None
    invalid["source"]["display_cellview"]["oa_view_type"] = None
    payload = build_payload(invalid, context_record(), snapshot_id="snapshot-invalid")
    assert payload["active_target"]["target_state"] == "invalid_view"
    assert payload["active_target"]["oa_view_type"] is None


def test_payload_preserves_unknown_view_type_provenance():
    value = entry()
    for cellview in (
        value["source"]["display_cellview"],
        value["design"]["cellview"],
    ):
        cellview["oa_view_type_source"] = "unknown"
    payload = build_payload(value, context_record(), snapshot_id="snapshot-unknown")
    assert payload["active_target"]["oa_view_type"] == "schematic"
    assert payload["active_target"]["oa_view_type_source"] == "unknown"


def test_manifest_writes_refs_not_payload_content():
    class Journal:
        def __init__(self):
            self.events = []
            self.journal = SimpleNamespace(session_id="session")
            self.state = SimpleNamespace(task={"id": "task"})

        def artifact(self, data):
            return {
                "path": "artifacts/context.json",
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
                "complete": True,
            }

        def emit(self, kind, payload):
            self.events.append((kind, payload))

    journal = Journal()
    manifest = record_entry_context_shadow(
        journal, context_record(), entry(), turn_step="session_start"
    )
    assert [kind for kind, _ in journal.events] == [
        "context.payload.snapshot", "context.manifest",
    ]
    snapshot_event = journal.events[0][1]
    assert snapshot_event["payload_ref"]["complete"] is True
    assert "payload" not in snapshot_event
    assert manifest["payload_sha256"] == snapshot_event["payload_sha256"]
    assert manifest["work_id"] == "adc_module"
    assert manifest["stage_id"] == "spec_interpretation"
    assert manifest["layer_summary"][0]["kind"] == "session"


def test_envelope_and_manifest_are_bounded_metadata():
    payload = build_payload(entry(), context_record(), snapshot_id="snapshot-1")
    envelope = build_envelope(
        payload, turn_id="turn-1", session_id="session-1", project_id="project-1",
        layers=[{"kind": "session", "id": "entry", "authority": "source"}],
    )
    ref = {"path": "artifacts/context.json", "sha256": "a" * 64, "size": 12,
           "complete": True}
    manifest = build_manifest(payload, envelope, ref, renderer_version="renderer.v1")
    assert envelope["schema_version"] == "sico.context.envelope.v1"
    assert manifest["schema_version"] == "sico.context.manifest.v1"
    assert manifest["payload_ref"] == ref
    assert "content" not in manifest


def test_compact_card_and_canonical_validation():
    payload = build_payload(entry(), context_record(), snapshot_id="snapshot-1")
    assert validate_payload(payload) is True
    card = render_card(payload)
    assert "[TARGET] worklib/OTA/schematic oa_view_type=schematic state=cellview" in card
    assert "nets=truncated(2/8)" in card
    assert "{" not in card and "}" not in card
    payload["active_target"]["view"] = "schematic"
    with pytest.raises(ValueError, match="legacy view"):
        validate_payload(payload)


def test_task_driver_records_shadow_without_starting_a_model_turn():
    class Journal:
        def __init__(self):
            self.events = []
            self.journal = SimpleNamespace(session_id="session")
            self.state = SimpleNamespace(task={"id": "task"})

        def artifact(self, data):
            return {
                "path": "artifacts/context.json",
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
                "complete": True,
            }

        def emit(self, kind, payload):
            self.events.append((kind, payload))

        def drain(self):
            return iter(())

    class Rpc:
        def __init__(self):
            self.requests = []

        def request(self, method, payload):
            self.requests.append((method, payload))
            return {"ok": True}

    class Options:
        catalog = {"skills": []}

        def needs_catalog(self, turn_options):
            return False

        def check_inputs(self, inputs, turn_options):
            return None

    journal, rpc = Journal(), Rpc()
    facts = {"content": [{"type": "text", "text": json.dumps({
        "status": "ok", "data": entry(), "untrusted": True,
    })}], "isError": False}
    finished = []
    driver = TaskDriver(
        journal, Options(), SimpleNamespace(), SimpleNamespace(thread_id="thread"),
        connection=lambda: rpc,
        source_status=lambda: (False, ""),
        execute=lambda name, args: facts,
        schemas=lambda: [{"name": "get_entry_context"}],
        context_method=lambda context: "get_entry_context",
        finish=lambda *args: finished.append(args),
        close=lambda: None,
        consume_turn=lambda *args: iter(()),
    )
    context = BoundContext("instance", "generation", "target", {
        "window_ref": "wctx_fixture_1",
        "project_id": "project",
        "work": {"work_id": "adc_module"},
        "stage": {"stage_id": "spec_interpretation"},
    })
    list(driver.drive(
        "inspect", lambda: False, context=context, input_id="input",
        origin="startup", attachments=[], inputs=[], turn_options=None,
        operation=None,
    ))
    assert [kind for kind, _ in journal.events] == [
        "context.payload.snapshot", "context.manifest",
    ]
    assert rpc.requests[0][0] == "thread/inject_items"
    assert finished == [("completed",)]


def test_task_driver_records_shadow_after_project_context_fallback():
    class Journal:
        def __init__(self):
            self.events = []
            self.journal = SimpleNamespace(session_id="session")
            self.state = SimpleNamespace(task={"id": "task"})

        def artifact(self, data):
            return {
                "path": "artifacts/context.json",
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
                "complete": True,
            }

        def emit(self, kind, payload):
            self.events.append((kind, payload))

        def drain(self):
            return iter(())

    class Rpc:
        def __init__(self):
            self.requests = []

        def request(self, method, payload):
            self.requests.append((method, payload))
            return {"ok": True}

    class Options:
        catalog = {"skills": []}

        def needs_catalog(self, turn_options):
            return False

        def check_inputs(self, inputs, turn_options):
            return None

    def facts(data):
        return {
            "content": [{"type": "text", "text": json.dumps({
                "status": "ok", "data": data, "untrusted": True,
            })}],
            "isError": False,
        }

    journal, rpc, calls = Journal(), Rpc(), []
    entry_facts = facts(entry())

    def execute(name, args):
        calls.append(name)
        if name == "get_project_context":
            return facts({"source_valid": True, "project_id": "project"})
        return entry_facts

    driver = TaskDriver(
        journal, Options(), SimpleNamespace(), SimpleNamespace(thread_id="thread"),
        connection=lambda: rpc,
        source_status=lambda: (False, ""),
        execute=execute,
        schemas=lambda: [{"name": "get_project_context"}, {"name": "get_entry_context"}],
        context_method=lambda context: "get_project_context",
        finish=lambda *args: None,
        close=lambda: None,
        consume_turn=lambda *args: iter(()),
    )
    context = BoundContext("instance", "generation", "target", {
        "window_ref": "wctx_fixture_1",
        "project_id": "project",
        "work": {"work_id": "adc_module"},
        "stage": {"stage_id": "spec_interpretation"},
    })
    list(driver.drive(
        "inspect", lambda: False, context=context, input_id="input",
        origin="startup", attachments=[], inputs=[], turn_options=None,
        operation=None,
    ))
    assert calls == ["get_project_context", "get_entry_context"]
    assert [kind for kind, _ in journal.events] == [
        "context.payload.snapshot", "context.manifest",
    ]
    injected = rpc.requests[0][1]["items"][0]["content"][0]["text"]
    assert json.dumps(entry_facts, ensure_ascii=False) in injected
