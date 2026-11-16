"""Native auxiliary library ownership; no user-library writes or editor creation."""

import json

import pytest
from test_skill_output_capture import AI, run_native


def load_helper():
    return f'load({json.dumps(str(AI / "skill/AI_test_workspace.il"))})'


def test_library_created_in_workspace_and_reused_without_gui(tmp_path):
    run_native(tmp_path, [
        load_helper(),
        'testLibrary=aiTestWorkspaceLibrary()',
        'unless(and(testLibrary==ddGetObj("SicoTest") '
        'testLibrary==aiTestWorkspaceLibrary() '
        'simplifyFilename(ddGetObjReadPath(testLibrary))=='
        + json.dumps(str(tmp_path / "SicoTest")) + ') error("library differs"))',
        'testView=dbOpenCellViewByType("SicoTest" "probe" "schematic" "schematic" "a")',
        'unless(testView error("background create failed"))',
        'dbSave(testView) dbClose(testView)',
        'unless(ddGetObj("SicoTest" "probe" "schematic") error("view missing"))',
    ])
    assert (tmp_path / "SicoTest/probe/schematic").is_dir()
    assert "SicoTest" in (tmp_path / "cds.lib").read_text()


@pytest.mark.parametrize("kind", ["foreign_mapping", "unregistered", "symlink"])
def test_conflicting_library_is_never_rebound_or_written(tmp_path, kind):
    outside = tmp_path / "other"
    setup = []
    if kind == "foreign_mapping":
        setup.append('ddCreateLib("SicoTest" ' + json.dumps(str(outside)) + ')')
    elif kind == "unregistered":
        (tmp_path / "SicoTest").mkdir()
    else:
        outside.mkdir()
        (tmp_path / "SicoTest").symlink_to(outside, target_is_directory=True)
    run_native(tmp_path, [load_helper(), *setup,
        'testLibraryBefore=ddGetObj("SicoTest")',
        'testCapture=aiEvalCaptureCall(\'aiTestWorkspaceLibrary nil)',
        'when(car(testCapture) error("conflict accepted"))',
        'unless(ddGetObj("SicoTest")==testLibraryBefore error("library mapping changed"))',
        'aiWriteTextFile("conflict.txt" cadr(testCapture))',
    ])
    assert "SicoTest" in (tmp_path / "conflict.txt").read_text()
    assert not (outside / "probe").exists()
