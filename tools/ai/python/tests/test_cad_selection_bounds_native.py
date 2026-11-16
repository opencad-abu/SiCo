"""Real Virtuoso acceptance for large selections and bounded responses."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("CAD_AI_RUN_NATIVE_SELECTION") != "1",
    reason="licensed Virtuoso selection acceptance",
)

from cadai.headless_session import session  # noqa: E402

ROOT = Path(__file__).resolve().parents[4]
MODULES = (
    "AI_protocol.il",
    "AI_pdk_core.il",
    "AI_datahub_contracts.il",
    "AI_datahub.il",
    "AI_window_context.il",
    "AI_selection_context.il",
    "AI_selection_highlight.il",
)
PAGE_LIMIT = 55000
MAX_PAGE_SECONDS = 8.0


def _load(bridge):
    for name in MODULES:
        bridge.skill("load(" + json.dumps(str(ROOT / "tools/ai/skill" / name)) + ")")


def _create_instances(bridge, cell, names):
    values = " ".join(json.dumps(name) for name in names)
    bridge.skill(
        'progn(cv=dbOpenCellViewByType("genericWork" ' + json.dumps(cell)
        + ' "schematic" "schematic" "w") master=dbOpenCellViewByType("basic" '
        '"ipin" "symbol" "" "r") foreach(name list(' + values + ') '
        'schCreateInst(cv master name 0:0 "R0")) '
        'dbSave(cv) dbClose(cv) dbClose(master) t)'
    )


def _open_and_select_all(bridge, cell):
    bridge.skill(
        'progn(win=deOpenCellView("genericWork" ' + json.dumps(cell)
        + ' "schematic" "schematic" nil "a") t)'
    )
    bridge.skill(
        'progn(geDeselectAll(win) foreach(inst '
        'geGetWindowCellView(win)~>instances geSelectFig(inst)) t)'
    )
    return bridge.skill("aiWindowCapture(win)")


def _query(bridge, expression):
    started = time.perf_counter()
    raw = bridge.skill(expression)
    elapsed = time.perf_counter() - started
    data = json.loads(raw)
    assert len(raw.encode()) < PAGE_LIMIT
    assert elapsed < MAX_PAGE_SECONDS
    print(expression.split("(")[0] + " %.3fs %dB" % (elapsed, len(raw.encode())))
    return data


def _page(ref, kind, query, offset, limit):
    return (
        'aiSelectionList(%s %s %s %d %d)'
        % (json.dumps(ref), json.dumps(kind), json.dumps(query), offset, limit)
    )


def test_large_selection_and_long_names_stay_bounded(tmp_path):
    with session(tmp_path / "session") as (bridge, _server, _proc):
        _load(bridge)
        many = ["N%03d_%s" % (index, "x" * 40) for index in range(600)]
        _create_instances(bridge, "many", many)
        ref = _open_and_select_all(bridge, "many")
        inspect = _query(
            bridge, "aiSelectionInspect(%s 0 50)" % json.dumps(ref)
        )
        assert inspect["ok"] and inspect["selection_count"] == 600
        assert len(inspect["items"]) == 50 and inspect["truncated"] is True
        listed = _query(bridge, _page(ref, "instance", "", 0, 50))
        assert listed["ok"] and listed["total"] == 600
        assert len(listed["items"]) == 50 and listed["truncated"] is True
        searched = _query(bridge, _page(ref, "instance", "N00", 0, 50))
        assert searched["total"] == 10 and len(searched["items"]) == 10
        assert searched["truncated"] is False
        tail = _query(bridge, _page(ref, "instance", "", 550, 50))
        assert len(tail["items"]) == 50 and tail["truncated"] is False
        bridge.skill("hiCloseWindow(win)")

        long_names = ["L%03d_%s" % (index, "x" * 507) for index in range(60)]
        _create_instances(bridge, "long", long_names)
        ref = _open_and_select_all(bridge, "long")
        first = _query(bridge, "aiSelectionInspect(%s 0 50)" % json.dumps(ref))
        assert first["ok"] and first["selection_count"] == 60
        assert len(first["items"]) == 50 and first["truncated"] is True
        assert all(len(item["name"]) == 512 for item in first["items"])
        second = _query(bridge, "aiSelectionInspect(%s 50 50)" % json.dumps(ref))
        assert len(second["items"]) == 10 and second["truncated"] is False
        listed = _query(bridge, _page(ref, "instance", "", 0, 50))
        assert listed["total"] == 60 and len(listed["items"]) == 50

        invalid = _query(
            bridge, "aiSelectionInspect(%s 0 51)" % json.dumps(ref)
        )
        assert invalid["code"] == "invalid_arguments"
        invalid = _query(
            bridge, _page(ref, "instance", "", 0, 51)
        )
        assert invalid["code"] == "invalid_arguments"
        recovered = _query(
            bridge, "aiSelectionInspect(%s 0 10)" % json.dumps(ref)
        )
        assert recovered["ok"] and len(recovered["items"]) == 10
        bridge.skill("hiCloseWindow(win)")
