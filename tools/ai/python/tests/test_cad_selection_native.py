"""Real Virtuoso acceptance for selection listing and non-mutating highlight."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("CAD_AI_RUN_NATIVE_SELECTION") != "1",
    reason="licensed Virtuoso selection acceptance",
)

from cadai.headless_session import session  # noqa: E402

ROOT = Path(__file__).resolve().parents[4]


def open_layout_window(bridge, cell="lay"):
    """Open Layout L, dismissing the Layout XL confirmation dialog if raised."""
    result = []
    processes, commands = {}, {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
            ppid = int(stat.rsplit(")", 1)[1].split()[1])
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode()
        except (OSError, ValueError, IndexError):
            continue
        processes[int(entry.name)] = ppid
        commands[int(entry.name)] = command

    def descendant(pid):
        seen = set()
        while pid not in seen and pid != 1:
            seen.add(pid)
            if processes.get(pid) == bridge.process.pid:
                return True
            pid = processes.get(pid)
        return False

    child = next((
        pid for pid, command in commands.items()
        if "virtuoso " in command and "-restore" in command and descendant(pid)
    ), None)
    process_env = {}
    if child is not None:
        try:
            raw = Path(f"/proc/{child}/environ").read_bytes().split(b"\0")
            process_env = {
                key: value for key, value in (
                    item.decode("utf-8", "replace").split("=", 1)
                    for item in raw if b"=" in item
                ) if key in {"DISPLAY", "XAUTHORITY"}
            }
        except OSError:
            pass
    if not process_env.get("DISPLAY") or not process_env.get("XAUTHORITY"):
        raise AssertionError("Could not identify the owned Xvfb display")
    xdotool = shutil.which("xdotool")
    if xdotool is None:
        raise AssertionError("xdotool is required for the layout dialog probe")

    def open_window():
        result.append(bridge.skill(
            'progn(win=deOpenCellView("genericWork" ' + json.dumps(cell)
            + ' "layout" "maskLayout" nil "a") t)'
        ))

    thread = threading.Thread(target=open_window, daemon=True)
    thread.start()
    for _ in range(40):
        if not thread.is_alive():
            break
        try:
            found = subprocess.run(
                [xdotool, "search", "--name", "Layout XL"],
                env={**os.environ, **process_env},
                capture_output=True, text=True, timeout=2,
            )
            if found.stdout.strip():
                subprocess.run(
                    [xdotool, "key", "--window", found.stdout.split()[0], "Return"],
                    env={**os.environ, **process_env},
                    timeout=2,
                )
                break
        except (OSError, subprocess.SubprocessError):
            pass
        time.sleep(0.25)
    thread.join(60)
    if thread.is_alive():
        raise AssertionError("Layout window open did not complete")
    if not result:
        raise AssertionError("Layout window open did not return a result")
    return result


def test_selection_and_highlight_in_real_virtuoso(tmp_path):
    with session(tmp_path / "session") as (bridge, _server, _proc):
        for name in ("AI_protocol.il", "AI_pdk_core.il",
                     "AI_datahub_contracts.il", "AI_datahub.il",
                     "AI_window_context.il",
                     "AI_selection_context.il", "AI_selection_highlight.il"):
            bridge.skill("load(" + json.dumps(str(ROOT / "tools/ai/skill" / name)) + ")")
        bridge.skill(
            'progn(cv=dbOpenCellViewByType("genericWork" "sel" "schematic" '
            '"schematic" "w") net=dbCreateNet(cv "IN") '
            'wire=car(schCreateWire(cv "draw" "direct" list(0:0 1:0) '
            '0.0625 0.0625 0)) dbAddFigToNet(wire net) '
            'dbCreateLabel(cv list("text" "drawing") 0.5:0.5 "LBL" '
            '"lowerLeft" "R0" "stick" 0.1) '
            'master=dbOpenCellViewByType("basic" "ipin" "symbol" "" "r") '
            'schCreateInst(cv master "I0" 0:2 "R0") '
            'schCreatePin(cv master "PORT" "input" nil 2:2 "R0") '
            'dbSave(cv) dbClose(cv) t)'
        )
        bridge.skill(
            'progn(win=deOpenCellView("genericWork" "sel" "schematic" '
            '"schematic" nil "a") t)'
        )
        ref = bridge.skill("aiWindowCapture(win)")
        window_list = json.loads(bridge.skill("aiWindowList(0 50)"))
        assert any(
            row["window_ref"] == ref for row in window_list["windows"]
        )
        windows = json.loads(bridge.skill(
            'aiSelectionWindows("genericWork" "sel" "schematic")'
        ))
        assert windows["windows"][0]["window_ref"]
        bridge.skill(
                'progn(geDeselectAll(win) geSelectFig(car(setof(x '
                'geGetWindowCellView(win)~>shapes x~>objType=="line"))) t)'
        )
        selected = json.loads(bridge.skill(
            "aiSelectionInspect(" + json.dumps(ref) + " 0 50)"
        ))
        assert selected["ok"] and selected["items"][0]["kind"] == "net"
        listed = json.loads(bridge.skill(
            "aiSelectionList(" + json.dumps(ref) + ' "net" "" 0 50)'
        ))
        assert listed["items"][0]["name"] == "IN"
        highlighted = json.loads(bridge.skill(
            'aiSelectionHighlight("genericWork" "sel" "schematic" "net" '
            '"IN" list() nil ' + json.dumps(ref) + ")"
        ))
        assert highlighted["highlighted"] and highlighted["highlight_count"] == 1
        assert highlighted["selected"] is True
        assert highlighted["selection_preserved"] is False
        after = json.loads(bridge.skill(
            "aiSelectionInspect(" + json.dumps(ref) + " 0 50)"
        ))
        assert any(item["kind"] == "net" and item["name"] == "IN"
                   for item in after["items"])
        bridge.skill(
                'progn(geDeselectAll(win) geSelectFig(car(setof(x '
                'geGetWindowCellView(win)~>instances x~>name=="I0"))) t)'
        )
        instance = json.loads(bridge.skill(
            "aiSelectionInspect(" + json.dumps(ref) + " 0 50)"
        ))
        assert instance["items"][0]["kind"] == "instance"
        assert instance["items"][0]["master"]["cell"] == "ipin"
        instance_list = json.loads(bridge.skill(
            "aiSelectionList(" + json.dumps(ref) + ' "instance" "" 0 50)'
        ))
        assert "I0" in {item["name"] for item in instance_list["items"]}
        all_items = json.loads(bridge.skill(
            "aiSelectionList(" + json.dumps(ref) + ' "all" "" 0 50)'
        ))
        kinds = {item["kind"] for item in all_items["items"]}
        assert {"instance", "net"} <= kinds
        assert "shape" not in kinds
        instance_highlight = json.loads(bridge.skill(
            'aiSelectionHighlight("genericWork" "sel" "schematic" "instance" '
            '"I0" list() list("basic" "ipin" "symbol") ' + json.dumps(ref) + ")"
        ))
        assert instance_highlight["highlighted"] is True
        assert instance_highlight["selected"] is True
        assert instance_highlight["selection_preserved"] is False
        after = json.loads(bridge.skill(
            "aiSelectionInspect(" + json.dumps(ref) + " 0 50)"
        ))
        assert any(item["kind"] == "instance" and item["name"] == "I0"
                   for item in after["items"])
        shapes = json.loads(bridge.skill(
            "aiSelectionList(" + json.dumps(ref) + ' "shape" "" 0 50)'
        ))
        shape_names = [item["name"] for item in shapes["items"]]
        wire = next(name for name in shape_names
                    if name.startswith("shape:line:wire:"))
        label = next(name for name in shape_names
                     if name.startswith("shape:label:text:"))
        for shape in (wire, label):
            shape_highlight = json.loads(bridge.skill(
                'aiSelectionHighlight("genericWork" "sel" "schematic" "shape" '
                + json.dumps(shape) + " list() nil " + json.dumps(ref) + ")"
            ))
            assert shape_highlight["highlighted"] is True
            assert shape_highlight["selected"] is True
            assert shape_highlight["selection_preserved"] is False
        terminals = json.loads(bridge.skill(
            "aiSelectionList(" + json.dumps(ref) + ' "terminal" "" 0 50)'
        ))
        terminal = next(item for item in terminals["items"]
                        if item["name"] == "PORT")
        assert terminal["bbox"]
        terminal_highlight = json.loads(bridge.skill(
            'aiSelectionHighlight("genericWork" "sel" "schematic" "terminal" '
            '"PORT" list() nil ' + json.dumps(ref) + ")"
        ))
        assert terminal_highlight["highlighted"] is True
        assert terminal_highlight["selected"] is True
        assert terminal_highlight["selection_preserved"] is False
        assert int(bridge.skill("length(geGetSelSet(win))")) >= 1
        missing = json.loads(bridge.skill(
            'aiSelectionHighlight("genericWork" "sel" "schematic" "instance" '
            '"NOPE" list() nil ' + json.dumps(ref) + ')'
        ))
        assert missing["code"] == "stale_object"
        bridge.skill("geDeselectAll(win)")
        empty = json.loads(bridge.skill(
            "aiSelectionInspect(" + json.dumps(ref) + " 0 50)"
        ))
        assert empty["ok"] and empty["items"] == [] and empty["selection_count"] == 0
        bridge.skill("hiCloseWindow(win)")
        stale = json.loads(bridge.skill(
            "aiSelectionInspect(" + json.dumps(ref) + " 0 50)"
        ))
        assert stale["code"] == "stale_context"
        stale_highlight = json.loads(bridge.skill(
            'aiSelectionHighlight("genericWork" "sel" "schematic" "instance" '
            '"I0" list() nil ' + json.dumps(ref) + ')'
        ))
        assert stale_highlight["code"] == "stale_context"


def test_layout_shape_selection_listing_and_highlight(tmp_path):
    with session(tmp_path / "session") as (bridge, _server, _proc):
        for name in ("AI_protocol.il", "AI_pdk_core.il",
                     "AI_datahub_contracts.il", "AI_datahub.il",
                     "AI_window_context.il",
                     "AI_selection_context.il", "AI_selection_highlight.il"):
            bridge.skill("load(" + json.dumps(str(ROOT / "tools/ai/skill" / name)) + ")")
        bridge.skill('envSetVal("graphic" "maskLayoutDefaultApp" \'string "Layout")')
        bridge.skill(
            'progn(cv=dbOpenCellViewByType("genericWork" "lay" "layout" '
            '"maskLayout" "w") dbCreateRect(cv list("device" "drawing") '
            'list(0:0 1:1)) dbSave(cv) dbClose(cv) t)'
        )
        open_layout_window(bridge)
        ref = bridge.skill("aiWindowCapture(win)")
        bridge.skill(
            'progn(geDeselectAll(win) geSelectObjectNoFilter(car('
            'geGetWindowCellView(win)~>shapes)) t)'
        )
        selected = json.loads(bridge.skill(
            "aiSelectionInspect(" + json.dumps(ref) + " 0 50)"
        ))
        assert selected["ok"] and selected["selection_count"] == 1
        assert selected["items"] == [] and selected["unsupported_count"] == 1
        listed = json.loads(bridge.skill(
            "aiSelectionList(" + json.dumps(ref) + ' "shape" "" 0 50)'
        ))
        assert listed["ok"] and listed["items"][0]["kind"] == "shape"
        shape = listed["items"][0]["name"]
        highlighted = json.loads(bridge.skill(
            'aiSelectionHighlight("genericWork" "lay" "layout" "shape" '
            + json.dumps(shape) + " list() nil " + json.dumps(ref) + ")"
        ))
        assert highlighted["highlighted"] is True
        assert highlighted["selected"] is True
        assert highlighted["selection_preserved"] is False
        assert int(bridge.skill("length(geGetSelSet(win))")) >= 1


def test_layout_mosaic_is_listed_selected_and_highlighted(tmp_path):
    with session(tmp_path / "session") as (bridge, _server, _proc):
        for name in ("AI_protocol.il", "AI_pdk_core.il",
                     "AI_datahub_contracts.il", "AI_datahub.il",
                     "AI_window_context.il", "AI_selection_context.il",
                     "AI_selection_highlight.il"):
            bridge.skill("load(" + json.dumps(str(ROOT / "tools/ai/skill" / name)) + ")")
        bridge.skill('envSetVal("graphic" "maskLayoutDefaultApp" \'string "Layout")')
        bridge.skill(
            'progn(mcv=dbOpenCellViewByType("genericWork" "mosaic_master" '
            '"layout" "maskLayout" "w") '
            'dbCreateRect(mcv list("device" "drawing") list(0:0 1:1)) '
            'dbSave(mcv) '
            'cv=dbOpenCellViewByType("genericWork" "mosaic" "layout" '
            '"maskLayout" "w") '
            'inst=dbCreateInst(cv mcv "I0" 0:0 "R0") '
            'car(leConvertInstToMosaic(inst)) '
            'dbSave(cv) dbClose(cv) dbClose(mcv) t)'
        )
        open_layout_window(bridge, "mosaic")
        ref = bridge.skill("aiWindowCapture(win)")
        listing = json.loads(bridge.skill(
            "aiSelectionList(" + json.dumps(ref) + ' "instance" "" 0 50)'
        ))
        assert "I0" in {item["name"] for item in listing["items"]}
        everything = json.loads(bridge.skill(
            "aiSelectionList(" + json.dumps(ref) + ' "all" "" 0 50)'
        ))
        assert "I0" in {item["name"] for item in everything["items"]}
        bridge.skill(
            'progn(geDeselectAll(win) geSelectFig(car(setof(x '
            'geGetWindowCellView(win)~>mosaics x~>name=="I0"))) t)'
        )
        selected = json.loads(bridge.skill(
            "aiSelectionInspect(" + json.dumps(ref) + " 0 50)"
        ))
        assert selected["items"][0]["kind"] == "instance"
        assert selected["items"][0]["master"]["cell"] == "mosaic_master"
        highlighted = json.loads(bridge.skill(
            'aiSelectionHighlight("genericWork" "mosaic" "layout" "instance" '
            '"I0" list() list("genericWork" "mosaic_master" "layout") '
            + json.dumps(ref) + ")"
        ))
        assert highlighted["highlighted"] is True
        assert highlighted["selected"] is True
        assert highlighted["selection_preserved"] is False


def test_read_only_open_target_returns_window_ref_and_can_highlight(tmp_path):
    with session(tmp_path / "session") as (bridge, _server, _proc):
        for name in ("AI_protocol.il", "AI_pdk_core.il",
                     "AI_datahub_contracts.il", "AI_datahub.il",
                     "AI_circuit.il", "AI_window_context.il",
                     "AI_selection_context.il", "AI_selection_highlight.il",
                     "AI_copilot_targets.il"):
            bridge.skill("load(" + json.dumps(str(ROOT / "tools/ai/skill" / name)) + ")")
        bridge.skill(
            'progn(cv=dbOpenCellViewByType("genericWork" "open_read" '
            '"schematic" "schematic" "w") '
            'master=dbOpenCellViewByType("basic" "ipin" "symbol" "" "r") '
            'schCreateInst(cv master "I0" 0:0 "R0") '
            'dbSave(cv) dbClose(cv) t)'
        )
        opened = json.loads(bridge.skill(
            'aiCopilotOpenTarget("genericWork" "open_read" "schematic" '
            '"schematic" "r")'
        ))
        assert opened["ok"] is True
        assert opened["window_ref"].startswith("wctx_")
        assert bridge.skill("hiGetCurrentWindow()~>cellView~>mode") == "r"
        highlighted = json.loads(bridge.skill(
            'aiSelectionHighlight("genericWork" "open_read" "schematic" '
            '"instance" "I0" list() list("basic" "ipin" "symbol") '
            + json.dumps(opened["window_ref"]) + ")"
        ))
        assert highlighted["highlighted"] is True
        assert highlighted["selected"] is True


def test_hierarchical_instance_path_is_resolved_for_highlight(tmp_path):
    with session(tmp_path / "session") as (bridge, _server, _proc):
        for name in ("AI_protocol.il", "AI_pdk_core.il",
                     "AI_datahub_contracts.il", "AI_datahub.il",
                     "AI_window_context.il",
                     "AI_selection_context.il", "AI_selection_highlight.il"):
            bridge.skill("load(" + json.dumps(str(ROOT / "tools/ai/skill" / name)) + ")")
        bridge.skill(
            'progn(leaf=dbOpenCellViewByType("genericWork" "leaf" "symbol" '
            '"schematicSymbol" "w") master=dbOpenCellViewByType("basic" '
            '"ipin" "symbol" "" "r") dbCreateInst(leaf master "M1" 0:0 "R0") '
            'dbSave(leaf) dbClose(leaf) dbClose(master) t)'
        )
        bridge.skill(
            'progn(top=dbOpenCellViewByType("genericWork" "top" "schematic" '
            '"schematic" "w") leaf=dbOpenCellViewByType("genericWork" "leaf" '
            '"symbol" "" "r") schCreateInst(top leaf "I0" 0:0 "R0") dbSave(top) '
            'dbClose(top) dbClose(leaf) t)'
        )
        bridge.skill(
            'progn(win=deOpenCellView("genericWork" "top" "schematic" '
            '"schematic" nil "a") t)'
        )
        ref = bridge.skill("aiWindowCapture(win)")
        highlighted = json.loads(bridge.skill(
            'aiSelectionHighlight("genericWork" "top" "schematic" "instance" '
            '"M1" list("I0") nil ' + json.dumps(ref) + ')'
        ))
        assert highlighted["highlighted"] is True
        assert highlighted["selected"] is False
        assert highlighted["selection_preserved"] is True
        focused = json.loads(bridge.skill(
            'aiSelectionHighlight("genericWork" "top" "schematic" "cellview" '
            '"top" list() nil ' + json.dumps(ref) + ')'
        ))
        assert focused["highlighted"] is False
        assert focused["highlight_count"] == 0
        assert focused["selected"] is False
        assert focused["selection_preserved"] is True
        assert focused["bbox"] is not None
        assert bridge.skill("hiGetCurrentWindow()==win") == "t"
