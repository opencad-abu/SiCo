"""Durable CAD object identity, selection, and bridge contracts."""

from __future__ import annotations

import base64
import copy
import json
from pathlib import Path

import pytest
from sico.core.cad_linkify import (
    extract_cad_refs,
    linkify_cad_refs,
)
from sico.core.cad_ref import (
    decode_ref,
    encode_ref,
    normalize_item,
    normalize_target,
    object_link,
    object_ref,
    object_signature,
    parse_object_link,
    signature_matches,
    target_from_snapshot,
)
from sico.service.cad_highlight import validate_highlight
from sico.service.cad_object_list import validate_object_list
from sico.service.cad_windows import validate_windows
from sico.service.selection import validate_selection
from sico.service.targets import BridgeTargetProvider, TargetBridgeError
from sico.storage.input_assets import prepare_inputs, reference_text, wire_inputs
from sico.storage.journal import SessionJournal

TARGET = {
    "library": "worklib",
    "cell": "OTA",
    "view_name": "schematic",
    "oa_view_type": "schematic",
    "oa_view_type_source": "platform_api",
    "library_path": "/project/worklib",
}
INSTANCE = {
    "kind": "instance",
    "name": "NM1",
    "master": {"library": "smic18", "cell": "nmos4", "view_name": "symbol"},
    "hierarchy": [],
    "bbox": [[1.0, 2.0], [3.0, 4.0]],
}


def selection(items=None):
    return {
        "ok": True,
        "valid": True,
        "schema": "cad.cv.selection.v1",
        "session_ref": "fixture",
        "window_ref": "wctx_fixture_1",
        "target": copy.deepcopy(TARGET),
        "items": copy.deepcopy(items if items is not None else [INSTANCE]),
        "selection_count": 1 if items is None else len(items),
        "unsupported_count": 0,
        "max_items": 50,
        "offset": 0,
        "truncated": False,
    }


def test_object_ref_roundtrip_is_canonical_and_session_bound():
    ref = object_ref(TARGET, INSTANCE)
    assert ref["schema"] == "cad.object_ref.v1"
    assert ref["kind"] == "instance"
    assert ref["master"] == INSTANCE["master"]
    assert "bbox" not in ref and ref["signature"] == object_signature(ref, INSTANCE["bbox"])
    token = encode_ref(ref)
    assert decode_ref(token) == ref
    link = object_link("session_1", ref)
    session, decoded = parse_object_link(link)
    assert session == "session_1" and decoded == ref
    assert link.startswith("copilot://cad/session_1/")
    assert signature_matches(ref, INSTANCE["bbox"]) is True
    assert signature_matches(ref, [[0, 0], [1, 1]]) is False


def test_encoded_tokens_are_bounded_before_a_link_can_be_generated():
    ref = object_ref(TARGET, {
        **INSTANCE,
        "hierarchy": ["H" * 256] * 20,
    })
    with pytest.raises(ValueError, match="too large"):
        encode_ref(ref)
    with pytest.raises(ValueError, match="too large"):
        object_link("session_1", ref)


def test_object_list_all_scope_is_instances_nets_and_pins():
    source = (
        Path(__file__).resolve().parents[3]
        / "ai/skill/AI_selection_context.il"
    ).read_text()
    assert 'member(aslKind list("all" "instance"))' in source
    assert 'member(aslKind list("all" "net"))' in source
    assert 'member(aslKind list("all" "terminal"))' in source
    assert 'member(aslKind list("all" "shape"))' not in source


@pytest.mark.parametrize(
    "value",
    [
        {"kind": "instance", "name": "NM1"},
        {"kind": "instance", "name": "NM1", "master": {"library": "L", "cell": "C"}},
        {"kind": "net", "name": "VOUT", "master": {"library": "L", "cell": "C", "view_name": "symbol"}},
        {"kind": "bogus", "name": "S1"},
        {"kind": "net", "name": "VOUT", "extra": True},
        {"kind": "net", "name": ""},
        {"kind": "net", "name": "VOUT", "bbox": [[0, 0], [1, float("nan")]]},
    ],
)
def test_invalid_items_are_rejected(value):
    with pytest.raises(ValueError):
        normalize_item(value)


def test_layout_shape_identity_is_a_durable_name():
    item = normalize_item({
        "kind": "shape",
        "name": "shape:rect:y0:drawing:[[0,0],[1,1]]",
        "hierarchy": [],
    })
    assert item["kind"] == "shape" and "master" not in item


def test_target_never_infers_oa_view_type_from_view_name():
    target = target_from_snapshot({
        "cellview": {"lib": "worklib", "cell": "OTA", "view_name": "layout"},
    })
    assert target["oa_view_type"] is None
    assert target["oa_view_type_source"] == "unknown"
    with pytest.raises(ValueError, match="oa_view_type_source"):
        normalize_target({
            "library": "worklib", "cell": "OTA", "view_name": "layout",
            "oa_view_type": "maskLayout",
        })
    with pytest.raises(ValueError, match="Unknown CAD target fields"):
        normalize_target({
            "library": "worklib", "cell": "OTA", "view_name": "layout",
            "oa_view_type": "maskLayout", "oa_view_type_source": "platform_api",
            "view_type": "maskLayout",
        })


def test_links_reject_foreign_or_malformed_targets():
    ref = object_ref(TARGET, INSTANCE)
    token = encode_ref(ref)
    with pytest.raises(ValueError):
        parse_object_link("https://cad/session_1/" + token)
    with pytest.raises(ValueError):
        parse_object_link("copilot://data/session_1/" + token)
    with pytest.raises(ValueError):
        parse_object_link("copilot://cad/other/" + token + "/tail")
    bad = base64.urlsafe_b64encode(json.dumps({"schema": "wrong"}).encode()).decode().rstrip("=")
    with pytest.raises(ValueError):
        parse_object_link("copilot://cad/session_1/" + bad)


def test_selection_normalization_and_explicit_window_match():
    data = validate_selection(selection(), expected_window="wctx_fixture_1")
    assert data["target"] == TARGET
    assert data["items"][0]["name"] == "NM1"
    assert data["items"][0]["bbox"] == INSTANCE["bbox"]
    with pytest.raises(ValueError, match="mismatch"):
        validate_selection(selection(), expected_window="wctx_fixture_2")


@pytest.mark.parametrize(
    "change",
    [
        lambda value: value.update(schema="wrong"),
        lambda value: value.update(selection_count=0),
        lambda value: value.update(unsupported_count=2),
        lambda value: value.update(truncated=1),
        lambda value: value.update(items=[INSTANCE, INSTANCE]),
        lambda value: value.update(max_items=51),
    ],
)
def test_malformed_selection_is_rejected(change):
    value = selection()
    change(value)
    with pytest.raises(ValueError):
        validate_selection(value)


class Broker:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def circuit_call(self, context, params):
        self.calls.append((context, params))
        return self.reply


def test_target_provider_uses_the_bounded_selection_contract():
    broker = Broker({"circuit": selection()})
    provider = BridgeTargetProvider(broker, "ctx")
    data = provider.selection("wctx_fixture_1", 0, 50)
    assert data["items"][0]["name"] == "NM1"
    _context, call = broker.calls[0]
    assert call["function"] == "aiCopilotInspectSelected"
    assert call["values"] == ["wctx_fixture_1", 0, 50]
    assert call["claim"] is None and call["expected"] is None


def test_target_provider_rejects_a_mismatched_or_malformed_selection():
    with pytest.raises(ValueError, match="mismatch"):
        BridgeTargetProvider(Broker({"circuit": selection()}), "ctx").selection(
            "wctx_fixture_2", 0, 50
        )
    with pytest.raises(TargetBridgeError):
        BridgeTargetProvider(Broker({"unexpected": True}), "ctx").selection(
            "wctx_fixture_1", 0, 50
        )
    with pytest.raises(ValueError, match="limit"):
        BridgeTargetProvider(Broker({"circuit": selection()}), "ctx").selection(
            "wctx_fixture_1", 0, True
        )


def test_highlight_is_validated_and_uses_the_durable_target():
    ref = object_ref(TARGET, INSTANCE)
    reply = {
        "ok": True, "valid": True, "schema": "cad.cv.highlight.v1",
        "session_ref": "fixture", "target": TARGET, "kind": "instance", "name": "NM1",
        "window_ref": "wctx_fixture_1", "highlighted": True, "highlight_count": 1,
        "zoomed": True, "selection_preserved": True,
    }
    broker = Broker({"circuit": reply})
    data = BridgeTargetProvider(broker, "ctx").highlight(ref)
    assert data["name"] == "NM1" and data["selection_preserved"] is True
    _context, call = broker.calls[0]
    assert call["function"] == "aiCopilotHighlightObject"
    assert call["values"][:5] == ["worklib", "OTA", "schematic", "instance", "NM1"]
    assert call["values"][6] == ["smic18", "nmos4", "symbol"]
    assert validate_highlight(reply)["zoomed"] is True
    with pytest.raises(ValueError):
        validate_highlight({**reply, "schema": "bad"})
    focus = {
        **reply, "kind": "cellview", "name": "worklib/OTA/schematic",
        "highlighted": False, "highlight_count": 0, "bbox": [[0, 0], [1, 1]],
    }
    assert validate_highlight(focus)["highlighted"] is False


def test_highlight_selection_flag_is_optional_and_boolean():
    value = {
        "ok": True, "valid": True, "schema": "cad.cv.highlight.v1",
        "session_ref": "fixture", "target": TARGET, "kind": "instance",
        "name": "NM1", "window_ref": "wctx_fixture_1", "highlighted": True,
        "highlight_count": 1, "zoomed": True, "selection_preserved": False,
        "selected": True,
    }
    assert validate_highlight(value)["selected"] is True
    with pytest.raises(ValueError):
        validate_highlight({**value, "selected": "yes"})


def test_object_list_provider_uses_server_side_query_and_paging():
    result = {
        "ok": True, "valid": True, "schema": "cad.cv.objects.v1",
        "session_ref": "fixture", "window_ref": "wctx_fixture_1", "target": TARGET,
        "items": [INSTANCE], "total": 1, "max_items": 50, "offset": 0,
        "truncated": False, "selection_count": 1,
    }
    broker = Broker({"circuit": result})
    provider = BridgeTargetProvider(broker, "ctx")
    data = provider.objects("wctx_fixture_1", "instance", "NM", 0, 50)
    assert data["items"][0]["name"] == "NM1"
    assert broker.calls[0][1]["function"] == "aiCopilotListObjects"
    assert broker.calls[0][1]["values"] == ["wctx_fixture_1", "instance", "NM", 0, 50]
    assert validate_object_list(result)["total"] == 1


def test_window_choice_and_explicit_highlight_window_are_bounded():
    ref = object_ref(TARGET, INSTANCE)
    windows = {
        "ok": True, "valid": True, "schema": "cad.cv.windows.v1",
        "session_ref": "fixture", "target": TARGET,
        "windows": [{"window_ref": "wctx_fixture_1", "window": "win:1", "title": "OTA"}],
    }
    broker = Broker({"circuit": windows})
    data = BridgeTargetProvider(broker, "ctx").windows(ref)
    assert data["windows"][0]["title"] == "OTA"
    assert broker.calls[0][1]["values"] == ["worklib", "OTA", "schematic"]
    encoded = {
        "ok": True, "valid": True, "schema": "cad.cv.windows.v1",
        "session_ref": "fixture", "target": TARGET,
        "windows": [{"window_ref": "wctx_fixture_1", "window": "win:1",
                     "title_bytes_hex": "ae"}],
    }
    assert validate_windows(encoded)["windows"][0]["title"] is None

    highlight = {
        "ok": True, "valid": True, "schema": "cad.cv.highlight.v1",
        "session_ref": "fixture", "target": TARGET, "kind": "instance", "name": "NM1",
        "window_ref": "wctx_fixture_1", "highlighted": True, "highlight_count": 1,
        "zoomed": False, "selection_preserved": True,
    }
    broker = Broker({"circuit": highlight})
    BridgeTargetProvider(broker, "ctx").highlight(ref, "wctx_fixture_1")
    assert len(broker.calls[0][1]["values"]) == 8
    assert broker.calls[0][1]["values"][7] == "wctx_fixture_1"
    assert validate_windows(windows)["windows"][0]["window"] == "win:1"
    all_windows = {
        "ok": True, "valid": True, "schema": "cad.windows.v1",
        "session_ref": "fixture", "pid": 123, "max_items": 200,
        "total": 1, "offset": 0, "next_offset": None, "truncated": False,
        "snapshot_atomic": False, "read_scope": "current Virtuoso process only",
        "windows": [{
            "id": "window:5", "window_ref": "wctx_fixture_1",
            "kind": "schematic", "window_class": "window",
            "title_bytes_hex": "4f5441", "is_current": False,
            "cellview": {
                "lib": "worklib", "cell": "OTA", "view_name": "schematic",
                "oa_view_type": "schematic",
                "oa_view_type_source": "platform_api",
            },
        }, {
            "id": "text:1", "window_ref": None,
        }, {
            "id": "window:6", "window_ref": "not-a-reference",
        }, {
            "id": "window:7", "window_ref": "wctx_fixture_2",
            "cellview": {"lib": "worklib"},
        }],
    }
    broker = Broker({"circuit": all_windows})
    listed = BridgeTargetProvider(broker, "ctx").window_list()
    assert len(listed["windows"]) == 1
    assert listed["windows"][0]["window"] == "window:5"
    assert listed["windows"][0]["target"]["library"] == "worklib"
    assert listed["windows"][0]["target"]["view_name"] == "schematic"
    assert broker.calls[0][1]["function"] == "aiCopilotListWindows"
    assert broker.calls[0][1]["values"] == [0, 200]


def test_bridge_refusal_keeps_the_ambiguous_target_code():
    refusal = {"circuit": {
        "ok": False, "code": "ambiguous_target", "message": "two windows",
    }}
    with pytest.raises(TargetBridgeError) as failure:
        BridgeTargetProvider(Broker(refusal), "ctx").highlight(
            object_ref(TARGET, INSTANCE)
        )
    assert failure.value.code == "ambiguous_target"


def test_native_selection_reader_is_read_only_and_registered():
    root = Path(__file__).resolve().parents[4]
    native = (root / "tools/ai/skill/AI_selection_context.il").read_text()
    highlight_native = (root / "tools/ai/skill/AI_selection_highlight.il").read_text()
    wrapper = (root / "tools/ai/skill/AI_copilot_targets.il").read_text()
    dispatch = (root / "tools/ai/skill/AI_copilot_circuit.il").read_text()
    transport = (root / "tools/sico/python/sico/transport/circuit.py").read_text()
    assert "geGetSelSet(asxWindow)" in native
    selection_reader = native.split("procedure(aiSelectionInspect", 1)[1].split(
        "procedure(aiSelectionHighlight", 1
    )[0]
    for forbidden in ("hiRaiseWindow", "hiZoomIn", "geSelectFig", "dbSave"):
        assert forbidden not in selection_reader
    assert "geCreateHilightSet" in highlight_native and "hiRaiseWindow" in native
    assert "procedure(aiCopilotInspectSelected" in wrapper
    assert '"aiCopilotInspectSelected"' in wrapper
    assert "procedure(aiCopilotListObjects" in wrapper
    assert "procedure(aiCopilotHighlightObject" in wrapper
    assert "aiCopilotInspectSelected" in dispatch
    assert "aiCopilotListObjects" in dispatch
    assert '"aiCopilotInspectSelected": (3, 3)' in transport
    assert '"aiCopilotListObjects": (5, 5)' in transport


def test_native_selection_reader_covers_mosaics_and_terminal_pin_figures():
    root = Path(__file__).resolve().parents[4]
    native = (root / "tools/ai/skill/AI_selection_context.il").read_text()
    highlight = (root / "tools/ai/skill/AI_selection_highlight.il").read_text()
    assert 'asxType=="mosaic"' in native
    assert "aslCv~>mosaics" in native
    assert "procedure(aiSelectionTerminalFigures" in native
    assert "atfTerm~>pins" in native
    assert "aiSelectionTerminalFigures(assObject)" in highlight


def test_object_ref_is_a_model_text_fact_not_an_archived_asset(tmp_path):
    ref = object_ref(TARGET, INSTANCE)
    part = {
        "type": "object_ref", "name": "NM1", "ref": ref,
        "window_ref": "wctx_fixture_1",
        "window_label": "worklib/OTA/schematic · window:2 · OTA",
    }
    with SessionJournal(tmp_path, "object-ref") as journal:
        prepared = prepare_inputs(journal, [part])
        assert "asset" not in prepared[0]
        assert wire_inputs(journal, prepared) == [part]
        text = reference_text(prepared)
        assert "CAD objects" in text
        assert "NM1" in text and "worklib" in text
        assert "wctx_fixture_1" in text and "window:2" in text


def test_linkify_uses_the_captured_target_and_skips_code():
    target = target_from_snapshot({
        "cellview": {
            "lib": "worklib", "cell": "OTA", "view_name": "schematic",
            "oa_view_type": "schematic",
            "oa_view_type_source": "platform_api",
        },
    })
    text = "改 inst:/NM1，检查 net:/VOUT；`inst:/CODE`；\n```\ninst:/FENCE\n```\n"
    linked = linkify_cad_refs(text, target, "session_1")
    assert "[NM1](copilot://cad/session_1/" in linked
    assert "[VOUT](copilot://cad/session_1/" in linked
    assert "`inst:/CODE`" in linked
    assert "inst:/FENCE" in linked
    session, ref = parse_object_link(
        linked.split("[NM1](", 1)[1].split(")", 1)[0]
    )
    assert session == "session_1" and ref["kind"] == "instance" and ref["name"] == "NM1"
    refs = extract_cad_refs(text, target, "session_1")
    assert [(row["kind"], row["name"]) for row in refs] == [
        ("instance", "NM1"), ("net", "VOUT")
    ]
    existing = (
        "see [inst:/NM1](https://example.invalid/a) and "
        "https://example.invalid/inst:/VOUT and inst:/I1"
    )
    linked_existing = linkify_cad_refs(existing, target, "session_1")
    assert linked_existing.count("copilot://cad/") == 1
    assert "inst:/NM1" in linked_existing and "inst:/VOUT" in linked_existing
    assert "[I1](copilot://cad/session_1/" in linked_existing


def test_cellview_token_links_the_requested_cellview_target():
    target = target_from_snapshot({
        "cellview": {
            "lib": "worklib", "cell": "OTA", "view_name": "schematic",
            "oa_view_type": "schematic",
            "oa_view_type_source": "platform_api",
        },
    })
    text = "open cv:/worklib/OTA/schematic and cv:/worklib/OTA/layout"
    linked = linkify_cad_refs(text, target, "session_1")
    refs = extract_cad_refs(text, target, "session_1")
    assert [row["kind"] for row in refs] == ["cellview", "cellview"]
    assert refs[0]["target"] == target
    assert refs[1]["target"]["oa_view_type"] is None
    assert refs[1]["target"]["oa_view_type_source"] == "unknown"
    assert "cv:/worklib/OTA/schematic" not in linked
    assert "cv:/worklib/OTA/layout" not in linked


def test_chat_projection_links_completed_answer_to_its_task_target():
    from sico.service.chat_projection import ChatProjection

    projection = ChatProjection()
    projection.receive({
        "sequence": 1, "session_id": "session_1", "task_id": "task_1",
        "kind": "task.started", "timestamp": "2026-10-21T00:00:00",
        "payload": {
            "origin": "chat", "text": "fix it", "input_id": "input_1",
            "context": {
                "snapshot": {
                    "cellview": {
                        "lib": "worklib", "cell": "OTA", "view_name": "schematic",
                        "oa_view_type": "schematic",
                        "oa_view_type_source": "platform_api",
                    },
                }
            },
        },
    })
    projection.receive({
        "sequence": 2, "session_id": "session_1", "task_id": "task_1",
        "kind": "model.completed", "timestamp": "2026-10-21T00:00:01",
        "payload": {"text": "updated inst:/NM1"},
    })
    assert "[NM1](copilot://cad/session_1/" in projection.messages[-1]["text"]


def test_report_render_links_the_captured_task_target():
    from sico.service.detail_content import DetailContent

    class Index:
        session_id = "session_1"
        executions = {"task_1": {"context": {
            "snapshot": {
                "cellview": {
                    "lib": "worklib", "cell": "OTA", "view_name": "schematic",
                    "oa_view_type": "schematic",
                    "oa_view_type_source": "platform_api",
                },
            }
        }}}
        data = {}
        reports = {}
        stages = {"stage_1": {"stage_title": "S", "objective": "O", "acceptance": "A"}}
        works = {"work_1": {"title": "W"}}

        @staticmethod
        def verify_evidence(_rows):
            return None

        @staticmethod
        def report_list():
            return []

        @staticmethod
        def resolve(_kind, _key):
            return {}

    row = {
        "kind": "published", "version": 1, "outcome": "completed",
        "markdown": "changed inst:/NM1", "next_steps": "", "evidence": [],
        "task_id": "task_1", "work_id": "work_1", "stage_id": "stage_1",
        "title": "Report", "timestamp": "",
        "object_refs": [object_ref(TARGET, INSTANCE)],
    }
    result = DetailContent(Index()).report(row)
    assert "[NM1](copilot://cad/session_1/" in result["markdown"]
    old = {key: value for key, value in row.items() if key != "object_refs"}
    old_result = DetailContent(Index()).report(old)
    assert old_result["markdown"] == "changed inst:/NM1"
