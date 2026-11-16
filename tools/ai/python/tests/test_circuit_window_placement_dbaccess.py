"""Right-half window slot placement against the real screen-size return shape."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

AI_ROOT = Path(__file__).resolve().parents[2]


def test_right_half_slots_read_the_pixel_screen_size(tmp_path):
    executable = shutil.which(os.environ.get("CAD_AI_DBACCESS", "dbAccess"))
    if executable is None:
        pytest.skip("dbAccess unavailable")
    source = f"""
load({json.dumps(str(AI_ROOT / "skill" / "AI_circuit.il"))})

/* Window layer stubs: every placement decision becomes observable. */
procedure(testScreenNested() list(list(2544 1245) list(673 329)))
procedure(testScreenFlat() list(2544 1245))
procedure(testScreenError() error("no screen"))
procedure(testX(pt) car(pt))
procedure(testY(pt) cadr(pt))
procedure(testBBox(wnd) testBoxes[wnd])
procedure(testWindowList(context) testWindows)
procedure(testResize(wnd box) testEvents=append(testEvents list(list('resize wnd box))) t)
procedure(testMove(wnd pt) testEvents=append(testEvents list(list('move wnd pt))) t)
putd('hiGetScreenSize getd('testScreenNested))
putd('xCoord getd('testX))
putd('yCoord getd('testY))
putd('hiGetAbsWindowScreenBBox getd('testBBox))
putd('hiGetWindowList getd('testWindowList))
putd('hiResizeWindow getd('testResize))
putd('hiMoveWindow getd('testMove))
testBoxes=makeTable("testBoxes" nil)
testWindows=nil
testEvents=nil

/* Real nested screen value: the first window takes the upper-right slot. */
testWindows=list('target)
testBoxes['target]=list(list(100 30) list(600 400))
testEvents=nil
aiCircuitPlaceRightHalf('target)
unless(equal(testEvents list(
    list('resize 'target list(list(1272 622) list(2544 1245)))
    list('move 'target list(1272 622))))
  error("upper slot placement differs: %L" testEvents))
printf("WINDOW_SLOT_UPPER_PASS\\n")

/* Occupied upper slot: the next window falls back to the lower-right slot. */
testWindows=list('target 'other)
testBoxes['target]=list(list(100 30) list(600 400))
testBoxes['other]=list(list(1300 700) list(2000 1100))
testEvents=nil
aiCircuitPlaceRightHalf('target)
unless(equal(testEvents list(
    list('resize 'target list(list(1272 0) list(2544 646)))
    list('move 'target list(1272 0))))
  error("lower slot placement differs: %L" testEvents))
printf("WINDOW_SLOT_LOWER_PASS\\n")

/* A window already on the right half stays where the user put it. */
testWindows=list('target)
testBoxes['target]=list(list(1300 700) list(2544 1200))
testEvents=nil
aiCircuitPlaceRightHalf('target)
unless(null(testEvents) error("already placed window moved: %L" testEvents))
printf("WINDOW_SLOT_KEEP_PASS\\n")

/* Flat (width height) returns keep working. */
putd('hiGetScreenSize getd('testScreenFlat))
testWindows=list('target)
testBoxes['target]=list(list(100 30) list(600 400))
testEvents=nil
aiCircuitPlaceRightHalf('target)
unless(equal(testEvents list(
    list('resize 'target list(list(1272 622) list(2544 1245)))
    list('move 'target list(1272 622))))
  error("flat screen size rejected: %L" testEvents))
printf("WINDOW_SLOT_FLAT_PASS\\n")

/* A failing screen query leaves the window untouched. */
putd('hiGetScreenSize getd('testScreenError))
testEvents=nil
aiCircuitPlaceRightHalf('target)
unless(null(testEvents) error("placement ran without a screen size: %L" testEvents))
printf("WINDOW_SLOT_SCREEN_FAIL_PASS\\n")

printf("WINDOW_PLACEMENT_DBACCESS_OK\\n")
exit()
"""
    result = subprocess.run(
        [executable, "-nocdsinit"],
        input=source,
        text=True,
        capture_output=True,
        cwd=tmp_path,
        timeout=45,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    for marker in ("UPPER", "LOWER", "KEEP", "FLAT", "SCREEN_FAIL"):
        assert f"WINDOW_SLOT_{marker}_PASS" in output, output
    assert "WINDOW_PLACEMENT_DBACCESS_OK" in output, output
    assert "*Error*" not in output, output
