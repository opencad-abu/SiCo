"""Real Virtuoso acceptance for the Ask @objects SKILL IPC handler."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("CAD_AI_RUN_NATIVE_SELECTION") != "1",
    reason="licensed Virtuoso selection acceptance",
)

from cadai.headless_session import session  # noqa: E402

ROOT = Path(__file__).resolve().parents[4]


def last_reply(bridge):
    return json.loads(bridge.skill("car(reverse(sicoProbeReplies))"))["value"]


def test_quick_objects_resolves_the_owner_window_and_always_replies(tmp_path):
    with session(tmp_path / "session") as (bridge, _server, _proc):
        bridge.skill("load(" + json.dumps(
            str(ROOT / "tools/sico/skill++/SICO.ils")) + ")")
        bridge.skill(
            'progn(cv=dbOpenCellViewByType("genericWork" "ask" "schematic" '
            '"schematic" "w") dbCreateNet(cv "IN") '
            'dbCreateInst(cv dbOpenCellViewByType("basic" "ipin" "symbol" "" "r") '
            '"I0" 0:2 "R0") dbSave(cv) dbClose(cv) t)'
        )
        bridge.skill(
            'progn(win=deOpenCellView("genericWork" "ask" "schematic" '
            '"schematic" nil "a") t)'
        )
        bridge.skill(
            'progn(sicoProbeReplies=nil '
            'putd(\'sicoQuickSend lambda((composer kind @optional message) '
            'sicoProbeReplies=cons(or(message "") sicoProbeReplies) t)) '
            'binding=makeTable("askProbe" nil) binding[\'window]=win '
            'composer=makeTable("askProbeComposer" nil) composer[\'target]=binding t)'
        )
        bridge.skill('sicoQuickObjects(composer "selection o1 - 0 50")')
        value = last_reply(bridge)
        assert value["ok"] is True
        assert value["window_ref"].startswith("wctx_")
        assert value["items"] == []

        bridge.skill('progn(sicoProbeReplies=nil '
                     'sicoQuickObjects(composer "objects o2 - all 0 50 494e") t)')
        value = last_reply(bridge)
        assert {item["kind"] for item in value["items"]} == {"instance", "net"}

        # Older clients omit the trailing empty query field entirely.
        bridge.skill('progn(sicoProbeReplies=nil '
                     'sicoQuickObjects(composer "objects o2b - all 0 50 ") t)')
        value = last_reply(bridge)
        assert value["ok"] is True
        assert {item["kind"] for item in value["items"]} == {"instance", "net"}

        bridge.skill('progn(sicoProbeReplies=nil '
                     'sicoQuickObjects(composer "objects o2c - all 0 50 -") t)')
        value = last_reply(bridge)
        assert value["ok"] is True
        assert {item["kind"] for item in value["items"]} == {"instance", "net"}

        bridge.skill('progn(sicoProbeReplies=nil '
                     'sicoQuickObjects(composer "bogus o3 x 0 50") t)')
        value = last_reply(bridge)
        assert value["ok"] is False
        assert value["code"] == "invalid_arguments"
        bridge.skill("hiCloseWindow(win)")
