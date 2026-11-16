"""Real annotation placement preserves every wire and disconnected net label."""

import json
import os

import pytest
from circuit_create_support import session

from cadai.circuit_annotations import decode_text_layout, note_text
from cadai.circuit_create import skill_literal

pytestmark = pytest.mark.skipif(
    os.environ.get("SICO_AI_RUN_GUI_TESTS") != "1", reason="licensed text placement acceptance"
)


def test_native_labels_notes_and_saved_snapshot(tmp_path):
    with session(tmp_path) as (bridge, _, _):
        bridge.skill('v=dbOpenCellViewByType("genericWork" "text" "schematic" "schematic" "a")')
        bridge.skill('e=makeTable("textTest" nil)')
        # Two disconnected short stubs with the same name both need a label.
        # Their horizontal text overlaps even though the wires do not touch.
        for name, x in (("VSS", 0), ("VSS", 0.125), ("OTHER", 0.25)):
            bridge.skill(
                f'w=car(schCreateWire(v "draw" "full" list({x}:0 {x}:1) '
                '0.0625 0.0625 0.0)) '
                f'aiCrTextWire(v e w list({x} 1) "{name}")'
            )
        bridge.skill('before=foreach(mapcar sh setof(s v~>shapes s~>objType=="line") sh~>points)')
        bridge.skill('aiCrTextFinish(v e)')
        receipt = decode_text_layout(json.loads(bridge.skill('aiCircuitJson(e[\'textLayout])')))
        assert receipt["initial_conflicts"] > 0
        assert receipt["labels_moved"]
        assert not receipt["unresolved"], receipt
        assert json.loads(bridge.skill(
            'aiCircuitJson(sort(foreach(mapcar sh setof(s v~>shapes s~>objType=="label") '
            'sh~>theLabel) \'alphalessp))'
        )) == ["OTHER", "VSS", "VSS"]
        assert bridge.skill(
            'equal(before foreach(mapcar sh setof(s v~>shapes s~>objType=="line") sh~>points))'
        ) == "t"
        # No label may leave the parent segment, lose its parent or disappear.
        assert bridge.skill(
            'forall(l e[\'textLabels] and(l~>isVisible l~>parent '
            'aiCrContains(l~>parent~>bBox l~>xy)))'
        ) == "t"
        box = [[2, -1], [5, 0]]
        wrapped = note_text("Passive network | generic creation", box)
        bridge.skill('aiCrTextNote(v e ' + skill_literal([wrapped, box]) + ')')
        notes = json.loads(bridge.skill('aiCircuitJson(e[\'textNotes])'))
        assert notes[0][0] == wrapped and "\n" in notes[0][0]
        assert notes[0][3:] == [True, True]
        # A completely obstructed candidate stays visible and must report failure.
        bridge.skill('w=car(schCreateWire(v "draw" "full" list(10:0 10:0.5) '
                     '0.0625 0.0625 0.0)) bad=aiCrTextWire(v e w 10:0.5 "BLOCKED")')
        bridge.skill('dbCreateLabel(v list("text" "drawing") 8:-2 '
                     '"OBSTRUCTION" "lowerLeft" "R0" "fixed" 8.0)')
        bridge.skill('aiCrTextFinish(v e)')
        receipt = decode_text_layout(json.loads(bridge.skill('aiCircuitJson(e[\'textLayout])')))
        assert any(i[1] == "BLOCKED" for i in receipt["unresolved"])
        assert bridge.skill('and(bad~>isVisible equal(bad~>xy 10:0.5))') == "t"
        # Persist/reopen, then verify that a display-only edit invalidates snapshot.
        bridge.skill('dbSave(v) snap=aiCrSnapshot(v) dbClose(v) '
                     'v=dbOpenCellViewByType("genericWork" "text" "schematic" "" "a")')
        assert bridge.skill('equal(snap aiCrSnapshot(v))') == "t"
        bridge.skill('l=car(setof(sh v~>shapes sh~>objType=="label")) l~>isVisible=nil')
        assert bridge.skill('equal(snap aiCrSnapshot(v))') == "nil"
