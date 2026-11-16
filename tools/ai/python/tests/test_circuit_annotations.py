"""Note content, digest, and native receipt contracts."""

import pytest

from cadai.circuit_annotations import NOTE_CHARACTER_BUDGET, decode_text_layout, note_text


@pytest.mark.parametrize("text,width", [
    ("Passive network | generic creation", 3.0),
    ("Bias path\nPhysical sizing and callback derived parameters", 2.0),
    ("这是分组注释应按宽度换行并保留完整文字", 1.0),
    ("LongUnbrokenComponentDescription", 0.5),
])
def test_notes_wrap_without_losing_content(text, width):
    wrapped = note_text(text, [[0, 0], [width, 1]])
    assert "\n" in wrapped
    assert "".join(wrapped.split()) == "".join(text.split())
    assert max(map(len, wrapped.splitlines())) <= max(1, int(width / NOTE_CHARACTER_BUDGET))


def test_short_note_and_explicit_paragraphs():
    assert note_text("Bias", [[0, 0], [3, 1]]) == "Bias"
    assert note_text("Input\n\nOutput", [[0, 0], [3, 1]]) == "Input\n\nOutput"


def test_receipt_does_not_turn_unmeasured_dynamic_text_into_visual_pass():
    receipt = decode_text_layout([
        "cad.schematic.text-layout.v1", "needs_visual_review", 3, [], 2, [], [],
        12, [["M1", "cdsParam(1)"]], False, True,
    ])
    assert receipt["dynamic_labels"] == 12
    assert receipt["dynamic_text_verified"] is False
    assert receipt["visual_review_required"] is True
    assert receipt["status"] == "static_text_checked"
    assert receipt["dynamic_text_policy"] == "cadence_expressions_not_blocking"


def test_unmeasured_proxy_is_observation_but_real_overlap_remains_unresolved():
    proxy = ["instance_text_extent_unverified", "VSS", [[0, 0], [1, 1]], [0, 0, 1]]
    conflict = ["wire_label_conflict", "OUT", [[0, 0], [1, 1]], [0, 1, 0]]
    result = decode_text_layout([
        "cad.schematic.text-layout.v1", "needs_visual_review", 2, [], 2,
        [proxy, conflict], [], 12, [["M1", "cdsParam(1)"]], False, True,
    ])
    assert result["unresolved"] == [conflict]
    assert result["observations"] == [proxy]
    assert result["status"] == "needs_visual_review"


def test_wrapped_note_is_part_of_preview_digest_and_native_payload():
    from circuit_geometry_fixture import sample_geometry

    from cadai.circuit_create import payload
    from cadai.circuit_geometry import preview_geometry
    from cadai.circuit_spec_plan import preview_circuit
    from cadai.circuit_spec_schema import digest

    args = sample_geometry()
    args["spec"]["notes"][0]["text"] = "Output load | preserve physical dimensions"
    args["preview_digest"] = preview_circuit(args["spec"], args["bindings"])["preview_digest"]
    preview = preview_geometry(**args)
    plan = preview["plan"]
    assert preview["geometry_plan_digest"] == digest(plan)
    compiled = payload({**args, "geometry_plan_digest": preview["geometry_plan_digest"]})
    assert compiled[4][0][0] == plan["notes"][0]["rendered_text"]
    assert "\n" in compiled[4][0][0]
    assert args["spec"]["notes"][0]["text"] == "Output load | preserve physical dimensions"
